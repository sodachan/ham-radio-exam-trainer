import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

import server


class PublicFilesTest(unittest.TestCase):
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
