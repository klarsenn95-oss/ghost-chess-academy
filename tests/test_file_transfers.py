import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as ghost_app
from werkzeug.security import generate_password_hash


class FileTransferTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data_file = root / "ghost_data.json"
        self.upload_dir = root / "uploads"
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        seed = {
            "students": [
                {
                    "name": "Ghost Test",
                    "devoirs": [],
                    "client_feedback": [],
                }
            ],
            "users": [
                {
                    "id": "ghost-user",
                    "name": "Ghost Test",
                    "email": "ghost@example.com",
                    "password_hash": generate_password_hash("secret123"),
                    "student_index": 0,
                    "active": True,
                }
            ],
            "exercises": [],
            "client_notifications": [],
        }
        self.data_file.write_text(json.dumps(seed), encoding="utf-8")
        self.patches = [
            patch.object(ghost_app, "DATA_FILE", str(self.data_file)),
            patch.object(ghost_app, "CLIENT_UPLOAD_FOLDER", str(self.upload_dir)),
            patch.object(ghost_app, "UPLOAD_FOLDER", str(self.upload_dir)),
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

    def upload_files(self, url, files):
        payload = {
            "files": [
                (io.BytesIO(content), filename)
                for filename, content in files
            ]
        }
        return self.client.post(url, data=payload, content_type="multipart/form-data")

    def read_data(self):
        return json.loads(self.data_file.read_text(encoding="utf-8"))

    def write_data(self, data):
        self.data_file.write_text(json.dumps(data), encoding="utf-8")

    def login_student(self):
        with self.client.session_transaction() as session:
            session["client_user_id"] = "ghost-user"

    def test_coach_can_send_pdf_and_word_files_to_student(self):
        upload = self.upload_files(
            "/api/admin/upload",
            [
                ("fiche.pdf", b"%PDF-1.4 test"),
                ("plan.docx", b"PK\x03\x04docx test"),
            ],
        )
        self.assertEqual(upload.status_code, 200)
        urls = upload.get_json()["urls"]
        self.assertEqual(len(urls), 2)
        self.assertTrue(urls[0].endswith(".pdf"))
        self.assertTrue(urls[1].endswith(".docx"))

        created = self.client.post(
            "/api/students/devoir",
            json={
                "student_index": 0,
                "action": "add",
                "title": "Documents a lire",
                "attachments": urls,
            },
        )
        self.assertEqual(created.status_code, 200)

        self.login_student()
        page = self.client.get("/client")
        html = page.get_data(as_text=True)
        self.assertIn("fiche.pdf", html)
        self.assertIn("plan.docx", html)
        self.assertIn("Guide 1/6", html)
        self.assertIn("startOnboarding(true)", html)

        saved = self.read_data()
        self.assertEqual(saved["students"][0]["devoirs"][0]["attachments"], urls)

    def test_student_can_return_pdf_and_word_files_to_coach(self):
        self.login_student()
        upload = self.upload_files(
            "/api/client/upload",
            [
                ("reponse.pdf", b"%PDF-1.4 answer"),
                ("analyse.docx", b"PK\x03\x04answer docx"),
            ],
        )
        self.assertEqual(upload.status_code, 200)
        urls = upload.get_json()["urls"]
        self.assertEqual(len(urls), 2)

        data = self.read_data()
        data["students"][0]["devoirs"].append({"title": "A rendre", "status": "a faire"})
        self.write_data(data)

        submitted = self.client.post(
            "/api/client/homework/submit",
            json={
                "devoir_index": 0,
                "text": "Voici mon rendu",
                "attachments": urls,
                "image_url": urls[0],
            },
        )
        self.assertEqual(submitted.status_code, 200)

        saved = self.read_data()
        submission = saved["students"][0]["devoirs"][0]["student_submission"]
        self.assertEqual(submission["attachments"], urls)
        self.assertTrue(submission["attachments"][0].endswith(".pdf"))
        self.assertTrue(submission["attachments"][1].endswith(".docx"))

    def test_coach_can_update_included_monthly_usage_visible_to_student(self):
        updated = self.client.post(
            "/api/admin/included-usage/update",
            json={
                "user_id": "ghost-user",
                "period": "2026-07",
                "games_used": 6,
                "games_total": 15,
                "coaching_minutes_used": 45,
                "coaching_minutes_total": 120,
                "note": "Suivi juillet",
            },
        )
        self.assertEqual(updated.status_code, 200)
        usage = updated.get_json()["included_usage"]
        self.assertEqual(usage["games_remaining"], 9)
        self.assertEqual(usage["coaching_remaining_label"], "1h15")

        self.login_student()
        page = self.client.get("/client")
        html = page.get_data(as_text=True)
        self.assertIn("2026-07", html)
        self.assertIn("6/15", html)
        self.assertIn("45 min/2h", html)
        self.assertIn("1h15", html)

        admin_page = self.client.get("/admin/clients")
        admin_html = admin_page.get_data(as_text=True)
        self.assertIn("Socle inclus mensuel", admin_html)
        self.assertIn("inc-games-used-ghost-user", admin_html)

    def test_legacy_certification_passing_routes_are_not_public(self):
        self.assertEqual(self.client.get("/certifications").status_code, 404)
        self.assertEqual(self.client.get("/certifications/bank").status_code, 404)
        self.assertEqual(self.client.post("/api/students/certification_result", json={}).status_code, 404)


if __name__ == "__main__":
    unittest.main()
