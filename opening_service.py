"""Openings library data access — dedicated Supabase tables (ghost_openings /
ghost_opening_repertoire / ghost_opening_progress), separate from the app's
big ghost_app_state JSON blob for the same reason as the puzzle bank: a
tree of thousands of positions shouldn't be reloaded/deep-copied on nearly
every request. See supabase_openings_schema.sql for the table definitions
and build_openings_tree.py for how the data is produced (from the CC0
lichess-org/chess-openings dataset — never from a coach-provided import).

Falls back to nothing gracefully when Supabase isn't configured — callers
should check backend_ready() first.
"""
from __future__ import annotations

from typing import Any, Optional

from supabase_backend import supabase_configured, get_supabase_client

TABLE_OPENINGS = "ghost_openings"
TABLE_REPERTOIRE = "ghost_opening_repertoire"
TABLE_PROGRESS = "ghost_opening_progress"


def backend_ready() -> bool:
    return supabase_configured()


def _paginate_select(table: str, select_cols: str, apply_filters=None, page_size: int = 1000) -> list[dict]:
    """Same PostgREST 1000-row cap workaround as puzzle_service._paginate_select."""
    client = get_supabase_client()
    rows: list[dict] = []
    offset = 0
    while True:
        q = client.table(table).select(select_cols)
        if apply_filters:
            q = apply_filters(q)
        q = q.range(offset, offset + page_size - 1)
        batch = q.execute().data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def opening_count() -> int:
    client = get_supabase_client()
    res = client.table(TABLE_OPENINGS).select("id", count="exact").limit(1).execute()
    return res.count or 0


def search_openings(query: str, limit: int = 30) -> list[dict]:
    """Coach/student search by name or ECO code (section 7: 'Sicilienne
    Najdorf', 'B90', 'Dragon' should all work)."""
    client = get_supabase_client()
    q = (query or "").strip()
    if not q:
        return []
    builder = client.table(TABLE_OPENINGS).select("id,eco,name,family,variation,subvariation,ply")
    if len(q) <= 3 and q[0].isalpha() and q[1:].isdigit():
        builder = builder.ilike("eco", q + "%")
    else:
        builder = builder.ilike("name", f"%{q}%")
    rows = builder.not_.is_("name", "null").order("ply").limit(limit).execute().data or []
    return rows


def list_families() -> list[dict]:
    """Distinct opening families for the browse screen (section 7), each
    with a representative ECO and how many named variations it holds."""
    rows = _paginate_select(TABLE_OPENINGS, "family,eco",
                             lambda q: q.not_.is_("family", "null"))
    counts: dict[str, dict] = {}
    for r in rows:
        fam = r.get("family")
        if not fam:
            continue
        entry = counts.setdefault(fam, {"family": fam, "eco": r.get("eco"), "count": 0})
        entry["count"] += 1
    return sorted(counts.values(), key=lambda e: e["family"])


def get_family_variations(family: str) -> list[dict]:
    client = get_supabase_client()
    rows = (client.table(TABLE_OPENINGS)
            .select("id,eco,name,variation,subvariation,ply")
            .eq("family", family).not_.is_("name", "null")
            .order("ply").execute().data or [])
    return rows


def _line_for_node(opening_id: str) -> list[dict]:
    """Walk parent_id back to the root, then reverse — the full sequence of
    moves (with FEN at each ply) needed to replay this line on a board."""
    client = get_supabase_client()
    chain: list[dict] = []
    current_id = opening_id
    seen = set()
    while current_id and current_id not in seen:
        seen.add(current_id)
        row = (client.table(TABLE_OPENINGS)
               .select("id,parent_id,fen,move_san,ply,name,eco")
               .eq("id", current_id).limit(1).execute().data)
        if not row:
            break
        node = row[0]
        chain.append(node)
        current_id = node.get("parent_id")
    chain.reverse()
    return chain


def get_node_detail(opening_id: str) -> Optional[dict]:
    client = get_supabase_client()
    row = (client.table(TABLE_OPENINGS)
           .select("id,parent_id,fen,move_san,ply,eco,name,family,variation,subvariation")
           .eq("id", opening_id).limit(1).execute().data)
    if not row:
        return None
    node = row[0]
    children = (client.table(TABLE_OPENINGS)
                .select("id,move_san,name,eco,ply")
                .eq("parent_id", opening_id).order("move_san").execute().data or [])
    node["line"] = _line_for_node(opening_id)
    node["children"] = children
    return node


def assign_repertoire(student_index: int, opening_id: str, side: str) -> dict:
    line = _line_for_node(opening_id)
    if not line:
        raise ValueError("Ouverture introuvable.")
    client = get_supabase_client()
    payload = {
        "student_index": student_index, "opening_id": opening_id,
        "side": side if side in ("white", "black") else "white",
        "line": line, "status": "active",
    }
    res = (client.table(TABLE_REPERTOIRE)
           .upsert(payload, on_conflict="student_index,opening_id").execute())
    return (res.data or [payload])[0]


def remove_repertoire(entry_id: str) -> bool:
    client = get_supabase_client()
    client.table(TABLE_REPERTOIRE).delete().eq("id", entry_id).execute()
    return True


def list_repertoire(student_index: int) -> list[dict]:
    client = get_supabase_client()
    rows = (client.table(TABLE_REPERTOIRE).select("*")
            .eq("student_index", student_index).order("created_at", desc=True).execute().data or [])
    return rows


def get_repertoire_entry(entry_id: str) -> Optional[dict]:
    client = get_supabase_client()
    rows = client.table(TABLE_REPERTOIRE).select("*").eq("id", entry_id).limit(1).execute().data
    return rows[0] if rows else None


def record_progress(user_id: str, opening_id: str, correct: bool) -> dict:
    client = get_supabase_client()
    existing = (client.table(TABLE_PROGRESS).select("*")
                .eq("user_id", user_id).eq("opening_id", opening_id).limit(1).execute().data)
    from datetime import datetime, timezone
    now_iso = datetime.now(timezone.utc).isoformat()
    if existing:
        row = existing[0]
        payload = {
            "correct_count": row.get("correct_count", 0) + (1 if correct else 0),
            "wrong_count": row.get("wrong_count", 0) + (0 if correct else 1),
            "last_seen_at": now_iso,
        }
        res = client.table(TABLE_PROGRESS).update(payload).eq("id", row["id"]).execute()
    else:
        payload = {
            "user_id": user_id, "opening_id": opening_id,
            "correct_count": 1 if correct else 0,
            "wrong_count": 0 if correct else 1,
            "last_seen_at": now_iso,
        }
        res = client.table(TABLE_PROGRESS).insert(payload).execute()
    return (res.data or [payload])[0]


def repertoire_progress(user_id: str, repertoire_entries: list[dict]) -> dict[str, dict]:
    """For each repertoire entry, mastery over the positions in its line —
    the numbers shown in section 17/18 (progression %, positions maîtrisées)."""
    all_opening_ids: set = set()
    for entry in repertoire_entries:
        for step in entry.get("line") or []:
            all_opening_ids.add(step["id"])
    if not all_opening_ids:
        return {}
    client = get_supabase_client()
    ids = list(all_opening_ids)
    progress_by_id: dict[str, dict] = {}
    for i in range(0, len(ids), 200):
        chunk = ids[i:i + 200]
        rows = (client.table(TABLE_PROGRESS).select("opening_id,correct_count,wrong_count")
                .eq("user_id", user_id).in_("opening_id", chunk).execute().data or [])
        for r in rows:
            progress_by_id[r["opening_id"]] = r

    out: dict[str, dict] = {}
    for entry in repertoire_entries:
        line = entry.get("line") or []
        total = len(line)
        mastered = 0
        for step in line:
            p = progress_by_id.get(step["id"])
            if p and p.get("correct_count", 0) >= 2 and p.get("correct_count", 0) > p.get("wrong_count", 0):
                mastered += 1
        percent = round(100 * mastered / total) if total else 0
        out[entry["id"]] = {"total_positions": total, "mastered_positions": mastered, "percent": percent}
    return out
