import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import server


class PracticePositionTest(unittest.TestCase):
    def test_resume_is_per_account_and_ignores_older_writes(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(server, "DB_PATH", root / "app.db"))
        self.enterContext(patch.object(server, "DATA_DIR", root))
        self.enterContext(patch.dict(os.environ, {"INVITE_CODES": "fixture"}))
        server.init_db()
        client = TestClient(server.app)
        path = "/api/practice-position"
        position = {"bank": "C", "question": "MC1-0003", "scope": "all", "category": "all",
                    "order": "random", "seed": 42, "selected": ["A"], "updated": 10}
        self.assertEqual(client.put(path, json=position).status_code, 401)
        for user_id in (1, 2):
            user = {"id": user_id, "username": f"learner-{user_id}"}
            server.execute("INSERT INTO users(id, username, password_hash, created_at) VALUES (?, ?, ?, ?)",
                           (user_id, user["username"], "unused", server.utc_now().isoformat()))
            with patch.dict(server.app.dependency_overrides, {server.current_user: lambda: user}):
                self.assertIsNone(client.get("/api/me").json()["position"])
                self.assertEqual(client.put(path, json=position).status_code, 200)
                older = {**position, "updated": 9, "selected": []}
                self.assertEqual(client.put(path, json=older).status_code, 200)
                self.assertEqual(client.get("/api/me").json()["position"], position)
                self.assertEqual(client.get("/api/progress?bank=C").json(), {"answers": {}, "wrong": []})
