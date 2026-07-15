import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as ghost_app
from werkzeug.security import generate_password_hash


class CertificationFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_file = Path(self.tmp.name) / "ghost_data.json"
        self.data_file.write_text(json.dumps({
            "students": [{"name": "Ryan Mbarga", "certification_results": [], "client_feedback": []}],
            "users": [{"id": "ghost-user", "name": "Ryan Mbarga", "email": "ryan@example.com", "password_hash": generate_password_hash("secret123"), "student_index": 0, "active": True}],
            "certification_bank": {"cert_i": {"grade_key": "cert_i", "grade_label": "Certification I", "pass_pct": 85, "questions": []}},
            "client_notifications": [],
        }), encoding="utf-8")
        self.patches = [
            patch.object(ghost_app, "DATA_FILE", str(self.data_file)),
            patch.object(ghost_app, "ADMIN_PASSWORD", ""),
            patch.object(ghost_app, "storage_configured", lambda: False),
        ]
        for item in self.patches:
            item.start()
        ghost_app.app.config.update(TESTING=True)
        self.client = ghost_app.app.test_client()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def login(self):
        with self.client.session_transaction() as session:
            session["client_user_id"] = "ghost-user"

    def test_result_respects_configured_threshold_and_unlocks_documents(self):
        failed = self.client.post("/api/students/certification_result", json={
            "student_index": 0, "grade_key": "cert_i", "sections": [{"label": "Fondamentaux", "points": 100, "score": 80}],
        }).get_json()["result"]
        self.assertFalse(failed["passed"])

        passed = self.client.post("/api/students/certification_result", json={
            "student_index": 0, "grade_key": "cert_i", "sections": [{"label": "Fondamentaux", "points": 100, "score": 90}],
            "answers": [{"question": "Quel est le meilleur coup ?", "answer": "Cf3"}],
            "notes": "Très bonne maîtrise des fondamentaux.",
        }).get_json()["result"]
        self.assertTrue(passed["passed"])
        self.assertTrue(passed["certificate_number"].startswith("GCA-"))

        self.login()
        page = self.client.get("/client").get_data(as_text=True)
        self.assertIn("Certified GHOST I", page)
        self.assertIn("Voir ma copie", page)
        self.assertEqual(self.client.get(f"/client/certification/{passed['id']}/copy").status_code, 200)
        self.assertEqual(self.client.get(f"/client/certification/{passed['id']}/report").status_code, 200)
        self.assertEqual(self.client.get(f"/client/certification/{passed['id']}/certificate").status_code, 200)
        self.assertEqual(self.client.get(f"/client/certification/{failed['id']}/certificate").status_code, 403)


if __name__ == "__main__":
    unittest.main()
