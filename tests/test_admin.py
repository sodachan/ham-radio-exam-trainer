import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import server
import llm_settings as llm


class AdminTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(server, "ADMIN_USERNAME", "fixture-admin"))
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(server, "DB_PATH", tmp / "db"))
        self.enterContext(patch.object(server, "DATA_DIR", tmp))
        self.enterContext(patch.object(llm, "SETTINGS_PATH", tmp / "private" / "llm.sqlite3"))
        self.enterContext(patch.dict(os.environ, {"INVITE_CODES": "fixture"}))
        server.init_db()
        self.admin = TestClient(server.app)
        self.user = TestClient(server.app)
        for name, client in [("fixture-admin", self.admin), ("learner", self.user)]:
            server.execute("INSERT INTO users(username,password_hash,created_at) VALUES (?,?,?)",
                           (name, server.hash_password("testing123"), server.utc_now().isoformat()))
            self.assertEqual(client.post("/api/auth/login", json={"username": name, "password": "testing123"}).status_code, 200)
        self.headers = {"X-Admin-Request": "1"}

    def test_permissions_and_invites(self):
        self.assertTrue(self.admin.get("/api/me").json()["isAdmin"])
        self.assertFalse(self.user.get("/api/me").json()["isAdmin"])
        self.assertEqual(self.admin.get("/admin").status_code, 200)
        for client, status in [(TestClient(server.app), 401), (self.user, 403)]:
            for path in ["/admin", "/api/admin/settings"]:
                self.assertEqual(client.get(path).status_code, status)
            for path in ["/api/admin/models", "/api/admin/settings", "/api/admin/invites", "/api/llm/precache"]:
                self.assertEqual(client.post(path, headers=self.headers, json={"provider":"openai","model":"test"}).status_code, status)
        self.assertEqual(self.admin.post("/api/admin/invites").status_code, 403)
        code = self.admin.post("/api/admin/invites", headers=self.headers).json()["code"]
        new = TestClient(server.app)
        def register(name):
            return new.post("/api/auth/register", json={"invite_code":code, "username":name, "password":"test123"})
        self.assertEqual(register("fixture-admin").status_code, 409)
        self.assertEqual(register("new-user").status_code, 200)
        self.assertFalse(new.get("/api/me").json()["isAdmin"])
        self.assertEqual(register("another-user").status_code, 400)
        self.assertIsNone(server.one("SELECT id FROM users WHERE username='another-user'"))

    def test_providers_and_cache_isolation(self):
        from unittest.mock import Mock
        responses = Mock()
        def fake(url, key, body=None):
            responses(url, key, body)
            if url.endswith("/models"):
                return {"data":[{"id":"text-fixture"}, {"id":"text-embedding-fixture","output_modalities":["embedding"]}]}
            if url.endswith("/responses"):
                return {"output":[{"type":"message","content":[{"type":"output_text","text":"OpenAI fixture"}]}]}
            return {"choices":[{"message":{"content":"DeepSeek fixture"}}]}
        self.enterContext(patch.object(llm,"upstream",side_effect=fake))
        self.assertEqual(self.admin.get("/api/admin/settings").json()["active"],"legacy")
        question = {"question_type": next(iter(server.QUESTIONS_BY_TYPE))}
        for provider in ["openai","deepseek","openai"]:
            first = provider not in llm.read_profiles()
            payload = {"provider":provider, "model":"text-fixture"}
            if first:
                payload["api_key"] = "test-key-" + provider
            listed = self.admin.post("/api/admin/models", headers=self.headers,json=payload)
            self.assertEqual(listed.json()["models"], ["text-fixture"])
            bad = self.admin.post("/api/admin/settings",headers=self.headers,json={**payload,"model":"missing"})
            self.assertEqual(bad.status_code,400)
            self.assertEqual(self.admin.post("/api/admin/settings",headers=self.headers,json=payload).status_code,200)
            state = self.admin.get("/api/admin/settings")
            self.assertEqual(state.json()["active"],provider)
            self.assertNotIn("test-key",state.text)
            self.assertEqual(llm.SETTINGS_PATH.stat().st_mode & 0o777,0o600)
            reply = self.user.post("/api/llm/explain",json=question).json()
            self.assertEqual(reply["cached"],not first)
            self.assertEqual(reply["content"],"OpenAI fixture" if provider=="openai" else "DeepSeek fixture")
            self.assertTrue(self.user.post("/api/llm/explain",json=question).json()["cached"])
            self.assertEqual(self.user.get("/api/llm/precache").json()["cached"],1)
        urls = [call.args[0] for call in responses.call_args_list]
        self.assertIn("https://api.openai.com/v1/responses",urls)
        self.assertIn("https://api.deepseek.com/chat/completions",urls)
        self.assertNotIn("private-test", self.admin.post("/api/admin/settings",headers=self.headers,
            json={"provider":"invalid","api_key":"private-test"}).text)
