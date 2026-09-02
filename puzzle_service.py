"""Puzzle data access — dedicated Supabase tables (ghost_puzzles /
ghost_puzzle_attempts), separate from the app's big ghost_app_state JSON
blob. A puzzle bank can hold tens of thousands of rows; stuffing that into
the single-document blob (re-read/deep-copied on nearly every request) would
reintroduce the exact performance problem already fixed elsewhere in this
app. See supabase_puzzles_schema.sql for the table definitions.

Falls back to nothing gracefully when Supabase isn't configured — callers
should check backend_ready() first and use the legacy JSON-blob puzzle
storage (data["puzzles"]) for local dev without Supabase.
"""
from __future__ import annotations

from typing import Any, Optional

from supabase_backend import supabase_configured, get_supabase_client

TABLE_PUZZLES = "ghost_puzzles"
TABLE_ATTEMPTS = "ghost_puzzle_attempts"


def backend_ready() -> bool:
    return supabase_configured()


def _row_to_public(row: dict, solved_ids: set, xp_by_difficulty) -> dict:
    themes = row.get("themes") or []
    return {
        "id": row["id"],
        "title": row.get("title") or "",
        "theme": themes[0] if themes else "",
        "themes": themes,
        "difficulty": row.get("difficulty"),
        "rating": row.get("rating"),
        "xp": xp_by_difficulty.get(row.get("difficulty"), 10),
        "solved": row["id"] in solved_ids,
    }


def list_puzzles(user_id: str, theme: Optional[str], difficulty: Optional[str], xp_by_difficulty, limit: int = 300) -> dict:
    client = get_supabase_client()
    q = client.table(TABLE_PUZZLES).select("id,title,themes,difficulty,rating")
    if theme:
        q = q.contains("themes", [theme])
    if difficulty:
        q = q.eq("difficulty", difficulty)
    res = q.limit(limit).execute()
    rows = res.data or []

    solved_ids = _solved_puzzle_ids(user_id)

    theme_counts: dict[str, int] = {}
    all_res = client.table(TABLE_PUZZLES).select("themes").limit(20000).execute()
    for r in all_res.data or []:
        for t in (r.get("themes") or []):
            theme_counts[t] = theme_counts.get(t, 0) + 1

    return {
        "puzzles": [_row_to_public(r, solved_ids, xp_by_difficulty) for r in rows],
        "theme_counts": theme_counts,
    }


def get_puzzle_with_solution(puzzle_id: str) -> Optional[dict]:
    client = get_supabase_client()
    res = client.table(TABLE_PUZZLES).select("*").eq("id", puzzle_id).limit(1).execute()
    rows = res.data or []
    if not rows:
        return None
    row = rows[0]
    themes = row.get("themes") or []
    return {
        "id": row["id"], "title": row.get("title") or "",
        "theme": themes[0] if themes else "", "difficulty": row.get("difficulty"),
        "fen": row.get("fen"), "moves": row.get("solution_moves") or [],
    }


def create_puzzle(title: str, themes: list[str], difficulty: str, fen: str, moves: list[str],
                   rating: Optional[int] = None, source: str = "coach", source_id: Optional[str] = None,
                   metadata: Optional[dict] = None) -> dict:
    client = get_supabase_client()
    payload = {
        "title": title, "themes": themes, "difficulty": difficulty, "fen": fen,
        "solution_moves": moves, "rating": rating, "source": source,
        "source_id": source_id, "metadata": metadata or {},
    }
    res = client.table(TABLE_PUZZLES).insert(payload).execute()
    return (res.data or [{}])[0]


def delete_puzzle(puzzle_id: str) -> bool:
    client = get_supabase_client()
    res = client.table(TABLE_PUZZLES).delete().eq("id", puzzle_id).execute()
    return bool(res.data)


def _user_attempts(user_id: str) -> list[dict]:
    client = get_supabase_client()
    res = (
        client.table(TABLE_ATTEMPTS)
        .select("puzzle_id,success,xp_awarded")
        .eq("user_id", user_id)
        .limit(20000)
        .execute()
    )
    return res.data or []


def _solved_puzzle_ids(user_id: str) -> set:
    return {a["puzzle_id"] for a in _user_attempts(user_id) if a.get("success")}


def student_puzzle_stats(user_id: str) -> dict:
    attempts = _user_attempts(user_id)
    xp = sum(a.get("xp_awarded") or 0 for a in attempts)
    solved_ids = {a["puzzle_id"] for a in attempts if a.get("success")}
    return {"xp": xp, "solved_count": len(solved_ids), "solved_ids": list(solved_ids)}


def record_attempt(user_id: str, puzzle_id: str, success: bool, difficulty: str, xp_by_difficulty) -> dict:
    """Records an attempt. XP is only ever granted once per (user, puzzle) —
    enforced both here and by a unique partial index in Postgres as a
    second line of defense against a race between two simultaneous requests."""
    client = get_supabase_client()
    already_rewarded = bool(
        client.table(TABLE_ATTEMPTS).select("id")
        .eq("user_id", user_id).eq("puzzle_id", puzzle_id)
        .gt("xp_awarded", 0).limit(1).execute().data
    )
    xp_gained = 0
    if success and not already_rewarded:
        xp_gained = xp_by_difficulty.get(difficulty, 10)
    try:
        client.table(TABLE_ATTEMPTS).insert({
            "user_id": user_id, "puzzle_id": puzzle_id, "success": success, "xp_awarded": xp_gained,
        }).execute()
    except Exception:
        # Le partial unique index a bloqué une double récompense (course entre
        # deux requêtes quasi simultanées) : on ne recompte pas l'XP, mais on
        # ne casse pas la réponse pour l'élève.
        xp_gained = 0
    stats = student_puzzle_stats(user_id)
    return {"xp_gained": xp_gained, "total_xp": stats["xp"], "already_solved": already_rewarded}
