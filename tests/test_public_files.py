import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import server


class PublicFilesTest(unittest.TestCase):
    def test_stylesheet_url_changes_when_its_contents_change(self):
        client = TestClient(server.app)
        with tempfile.TemporaryDirectory() as directory, patch.object(server, "ROOT", Path(directory).resolve()):
            (server.ROOT / "login.html").write_text('<link href="/styles.css">')
            for content in (b"body { color: red }", b"body { color: teal }"):
                (server.ROOT / "styles.css").write_bytes(content)
                version = hashlib.sha256(content).hexdigest()[:16]
                url = f"/assets/{version}/styles.css"
                page = client.get("/")
                self.assertIn("no-store", page.headers["cache-control"])
                self.assertIn(f'href="{url}"', page.text)
                asset = client.get(url)
                self.assertEqual(asset.content, content)
                self.assertIn("immutable", asset.headers["cache-control"])
            self.assertEqual(client.get("/assets/incorrect/styles.css").status_code, 404)
            self.assertEqual(client.get(f"/assets/{version}/login.html").status_code, 404)

    def test_only_published_assets_are_served(self):
        client = TestClient(server.app)
        for name in server.PUBLIC_FILES:
            with self.subTest(name=name):
                self.assertEqual(client.get("/" + name).status_code, 200)
        with tempfile.TemporaryDirectory() as directory, patch.object(server, "ROOT", Path(directory).resolve()):
            root = server.ROOT
            (root / "private.txt").write_text("private fixture")
            (root / "styles.css").symlink_to(root / "private.txt")
            (root / "figures").mkdir()
            (root / "figures" / "unlisted.jpg").write_bytes(b"fixture")
            for path in ("/private.txt", "/styles.css", "/app.js", "/figures/unlisted.jpg", "/unknown"):
                self.assertEqual(client.get(path).status_code, 404)

    def test_page_aliases_use_the_same_authentication(self):
        client = TestClient(server.app, follow_redirects=False)
        for path in ("/", "/login.html"):
            self.assertEqual(client.get(path).status_code, 200)
        for path in ("/app", "/index.html"):
            response = client.get(path)
            self.assertEqual(response.status_code, 302)
            self.assertEqual(response.headers["location"], "/")


if __name__ == "__main__":
    unittest.main()
