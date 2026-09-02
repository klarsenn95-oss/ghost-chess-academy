import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as ghost_app
from werkzeug.security import generate_password_hash


class PuzzleFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.data_file = root / "ghost_data.json"
        seed = {
            "students": [{"name": "Ghost Test", "devoirs": [], "client_feedback": []}],
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
            "puzzles": [
                {
                    "id": "mate-in-1",
                    "title": "Mat en 1",
                    "theme": "Mat en 1",
                    "difficulty": "Facile",
                    "fen": "6k1/5ppp/8/8/8/8/8/R6K w - - 0 1",
                    "moves": ["Ra8#"],
                },
                {
                    "id": "calc-1",
                    "title": "Prise + reprise",
                    "theme": "Calcul",
                    "difficulty": "Intermédiaire",
                    "fen": "4k3/3q4/8/8/Q7/8/8/4K3 w - - 0 1",
                    "moves": ["Qxd7+", "Kxd7"],
                },
            ],
        }
        self.data_file.write_text(json.dumps(seed), encoding="utf-8")
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

    def login_student(self):
        with self.client.session_transaction() as session:
            session["client_user_id"] = "ghost-user"

    def test_puzzle_list_hides_solution_and_supports_theme_filter(self):
        self.login_student()
        resp = self.client.get("/api/client/puzzle/list")
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(len(body["puzzles"]), 2)
        self.assertNotIn("fen", body["puzzles"][0])
        self.assertNotIn("moves", body["puzzles"][0])
        self.assertEqual(body["theme_counts"], {"Mat en 1": 1, "Calcul": 1})

        resp = self.client.get("/api/client/puzzle/list?theme=Calcul")
        body = resp.get_json()
        self.assertEqual(len(body["puzzles"]), 1)
        self.assertEqual(body["puzzles"][0]["id"], "calc-1")

    def test_start_returns_fen_and_full_solution(self):
        self.login_student()
        resp = self.client.post("/api/client/puzzle/start", json={"puzzle_id": "mate-in-1"})
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["puzzle"]["fen"], "6k1/5ppp/8/8/8/8/8/R6K w - - 0 1")
        self.assertEqual(body["puzzle"]["moves"], ["Ra8#"])

    def test_solving_awards_xp_once_and_persists(self):
        self.login_student()
        resp = self.client.post("/api/client/puzzle/solve", json={"puzzle_id": "mate-in-1", "success": True})
        body = resp.get_json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["xp_gained"], 10)  # Facile
        self.assertFalse(body["already_solved"])
        self.assertEqual(body["total_xp"], 10)

        # Solving the same puzzle again must not double-award XP.
        resp = self.client.post("/api/client/puzzle/solve", json={"puzzle_id": "mate-in-1", "success": True})
        body = resp.get_json()
        self.assertEqual(body["xp_gained"], 0)
        self.assertTrue(body["already_solved"])
        self.assertEqual(body["total_xp"], 10)

        # A harder puzzle awards more XP and adds on top.
        resp = self.client.post("/api/client/puzzle/solve", json={"puzzle_id": "calc-1", "success": True})
        body = resp.get_json()
        self.assertEqual(body["xp_gained"], 20)  # Intermédiaire
        self.assertEqual(body["total_xp"], 30)

        resp = self.client.get("/api/client/puzzle/list")
        body = resp.get_json()
        self.assertEqual(body["xp"], 30)
        solved_ids = {p["id"] for p in body["puzzles"] if p["solved"]}
        self.assertEqual(solved_ids, {"mate-in-1", "calc-1"})

    def test_failed_attempt_awards_no_xp(self):
        self.login_student()
        resp = self.client.post("/api/client/puzzle/solve", json={"puzzle_id": "mate-in-1", "success": False})
        body = resp.get_json()
        self.assertEqual(body["xp_gained"], 0)
        self.assertEqual(body["total_xp"], 0)

    def test_admin_can_create_and_delete_a_puzzle(self):
        resp = self.client.post("/api/admin/puzzle/create", json={
            "title": "Nouveau",
            "theme": "Fourchette",
            "difficulty": "Avancé",
            "fen": "6k1/5ppp/8/8/8/8/8/R6K w - - 0 1",
            "moves": ["Ra8#"],
        })
        body = resp.get_json()
        self.assertTrue(body["ok"])
        new_id = body["puzzle"]["id"]

        self.login_student()
        resp = self.client.get("/api/client/puzzle/list")
        self.assertEqual(len(resp.get_json()["puzzles"]), 3)

        resp = self.client.post("/api/admin/puzzle/delete", json={"puzzle_id": new_id})
        self.assertTrue(resp.get_json()["ok"])
        resp = self.client.get("/api/client/puzzle/list")
        self.assertEqual(len(resp.get_json()["puzzles"]), 2)

    def test_admin_create_rejects_invalid_theme_or_missing_moves(self):
        resp = self.client.post("/api/admin/puzzle/create", json={
            "theme": "Pas un theme valide", "difficulty": "Facile",
            "fen": "6k1/5ppp/8/8/8/8/8/R6K w - - 0 1", "moves": ["Ra8#"],
        })
        self.assertFalse(resp.get_json()["ok"])

        resp = self.client.post("/api/admin/puzzle/create", json={
            "theme": "Mat en 1", "difficulty": "Facile",
            "fen": "6k1/5ppp/8/8/8/8/8/R6K w - - 0 1", "moves": [],
        })
        self.assertFalse(resp.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()
