import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as ghost_app


class CertificationConfigurationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.data_file = Path(self.tmp.name) / "ghost_data.json"
        self.data_file.write_text(json.dumps({"students": [{"name": "Ryan Mbarga"}]}), encoding="utf-8")
        self.patches = [
            patch.object(ghost_app, "DATA_FILE", str(self.data_file)),
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

    def test_custom_threshold_and_sections_drive_the_result(self):
        configured = self.client.post("/api/certifications/save", json={
            "grade_key": "cert_i",
            "grade_label": "Certification I",
            "pass_pct": 85,
            "sections": [
                {"key": "fundamentals", "label": "Fondamentaux", "points": 30},
                {"key": "strategy", "label": "Strategie", "points": 20},
            ],
            "questions": [],
        })
        self.assertEqual(configured.status_code, 200)
        self.assertTrue(configured.get_json()["ok"])

        result = self.client.post("/api/students/certification_result", json={
            "student_index": 0,
            "grade_key": "cert_i",
            "sections": [
                {"key": "fundamentals", "label": "Fondamentaux", "points": 30, "score": 25},
                {"key": "strategy", "label": "Strategie", "points": 20, "score": 15},
            ],
            "documents": ["/uploads/rapport.pdf", "/uploads/copie.docx"],
        })
        payload = result.get_json()["result"]
        self.assertEqual(result.status_code, 200)
        self.assertEqual(payload["score"], 80.0)
        self.assertFalse(payload["passed"])
        self.assertEqual(payload["retake_fee"], 1000)
        self.assertEqual(len(payload["documents"]), 2)

        data = json.loads(self.data_file.read_text(encoding="utf-8"))
        self.assertEqual(data["certification_bank"]["cert_i"]["pass_pct"], 85)
        self.assertEqual(len(data["certification_bank"]["cert_i"]["sections"]), 2)


if __name__ == "__main__":
    unittest.main()
