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

from postgrest.exceptions import APIError

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


_FAMILIES_CACHE: list[dict] | None = None
_FAMILIES_CACHE_AT = 0.0
_FAMILIES_CACHE_TTL_SECONDS = 3600  # la liste de familles ne change qu'au rebuild de la bibliothèque


def list_families() -> list[dict]:
    """Distinct opening families for the browse screen (section 7), each
    with a representative ECO and how many named variations it holds.
    Measured at ~700ms uncached (pages through all 8653 rows of
    ghost_openings just to count) — this was hit on every visit to the
    Découvrir tab, a real contributor to the "clicking feels slow"
    complaint. The data is effectively static outside of a library
    rebuild, so an in-process cache with a generous TTL is safe."""
    global _FAMILIES_CACHE, _FAMILIES_CACHE_AT
    import time
    if _FAMILIES_CACHE is not None and time.monotonic() - _FAMILIES_CACHE_AT < _FAMILIES_CACHE_TTL_SECONDS:
        return _FAMILIES_CACHE
    rows = _paginate_select(TABLE_OPENINGS, "family,eco",
                             lambda q: q.not_.is_("family", "null"))
    counts: dict[str, dict] = {}
    for r in rows:
        fam = r.get("family")
        if not fam:
            continue
        entry = counts.setdefault(fam, {"family": fam, "eco": r.get("eco"), "count": 0})
        entry["count"] += 1
    _FAMILIES_CACHE = sorted(counts.values(), key=lambda e: e["family"])
    _FAMILIES_CACHE_AT = time.monotonic()
    return _FAMILIES_CACHE


def warm_caches() -> None:
    """Called once at server startup (background thread, best-effort) so
    the first real visitor after a deploy/cold-start doesn't pay the ~700ms
    families scan themselves."""
    try:
        if backend_ready():
            list_families()
    except Exception:
        pass


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


MAX_COURSE_SIZE = 20
MIN_COURSE_PLY = 14  # au moins 7 coups complets — le coach a comparé la
# profondeur des chapitres à ChessReps et les a trouvés trop courts avec le
# seuil précédent (8). La ladder de repli ci-dessous garantit quand même
# un résultat pour les familles peu profondes plutôt que de renvoyer rien.


def build_family_course(family: str) -> list[dict]:
    """The curriculum for a whole family, capped and deduplicated by variation
    name. A deep family like the Sicilian has 390+ named lines — nowhere near
    learnable as one course — so this keeps only the shallowest-but-still-
    substantial named branches (>= MIN_COURSE_PLY half-moves), one per
    distinct variation name, up to MAX_COURSE_SIZE. Falls back to shorter
    lines only if a family genuinely has nothing deeper (rare, obscure
    families) — never returns nothing just because the family is shallow.
    This is a structural approximation (no popularity data available yet):
    it favours breadth over judging which line matters most."""
    client = get_supabase_client()
    rows = (client.table(TABLE_OPENINGS)
            .select("id,name,eco,variation,subvariation,ply")
            .eq("family", family).not_.is_("name", "null")
            .order("ply").limit(MAX_COURSE_SIZE * 25).execute().data or [])

    def _curate(min_ply: int) -> list[dict]:
        seen = set()
        course = []
        for r in rows:
            if r["ply"] < min_ply:
                continue
            key = r.get("variation") or r["name"]
            if key in seen:
                continue
            seen.add(key)
            course.append({"id": r["id"], "name": r["name"], "eco": r.get("eco")})
            if len(course) >= MAX_COURSE_SIZE:
                break
        return course

    for threshold in (MIN_COURSE_PLY, 10, 8, 6, 4, 0):
        course = _curate(threshold)
        if course:
            return course
    return []


def assign_family(student_index: int, family: str, side: str) -> dict:
    course = build_family_course(family)
    if not course:
        raise ValueError("Aucune variante trouvée pour cette famille.")
    line = _line_for_node(course[0]["id"])
    client = get_supabase_client()
    payload = {
        "student_index": student_index, "family": family,
        "opening_id": course[0]["id"],
        "side": side if side in ("white", "black") else "white",
        "line": line, "course": course, "course_position": 0, "clean_streak": 0,
        "status": "active",
    }
    res = (client.table(TABLE_REPERTOIRE)
           .upsert(payload, on_conflict="student_index,family").execute())
    return (res.data or [payload])[0]


def student_start_family(student_index: int, family: str) -> dict:
    """Self-initiated start of a family from Découvrir. Unlike assign_family
    (coach action, always resets to variant 0), this resumes the existing
    repertoire entry untouched if the Ghost already started this family
    before — so revisiting Découvrir never wipes progress — and only
    creates a fresh entry the first time. Either way the family now shows
    up in 'Mon répertoire' with real progress, per the coach's request that
    self-started openings shouldn't just vanish into a free-play mode."""
    client = get_supabase_client()
    existing = (client.table(TABLE_REPERTOIRE).select("*")
                .eq("student_index", student_index).eq("family", family)
                .limit(1).execute().data)
    if existing:
        return existing[0]
    return assign_family(student_index, family, "white")


def complete_line(entry_id: str, had_mistake: bool) -> dict:
    """Called once a Ghost finishes a full run of the current variant in
    evaluation mode. 3 clean (mistake-free) runs in a row unlock the next
    variant in the family's course — a fresh line always resets the streak."""
    entry = get_repertoire_entry(entry_id)
    if not entry:
        raise ValueError("Entrée introuvable.")
    client = get_supabase_client()
    streak = 0 if had_mistake else (entry.get("clean_streak") or 0) + 1
    course = entry.get("course") or []
    pos = entry.get("course_position") or 0
    advanced = False
    finished = False
    payload = {"clean_streak": streak}
    if streak >= 3:
        if pos + 1 < len(course):
            pos += 1
            streak = 0
            advanced = True
            next_node = course[pos]
            payload = {
                "clean_streak": streak, "course_position": pos,
                "opening_id": next_node["id"], "line": _line_for_node(next_node["id"]),
            }
        else:
            finished = True
            payload = {"clean_streak": streak, "status": "completed"}
    res = client.table(TABLE_REPERTOIRE).update(payload).eq("id", entry_id).execute()
    updated = (res.data or [{**entry, **payload}])[0]
    return {"entry": updated, "advanced": advanced, "finished": finished}


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
    try:
        rows = client.table(TABLE_REPERTOIRE).select("*").eq("id", entry_id).limit(1).execute().data
    except APIError:
        # entry_id malformé (pas un UUID) : Postgres refuse la requête plutôt
        # que de renvoyer 0 ligne — un id invalide n'est pas différent d'un
        # id introuvable pour l'appelant, donc on le traite pareil.
        return None
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


CLEAN_STREAK_TARGET = 3  # doit rester en phase avec le seuil de complete_line()


def repertoire_progress(repertoire_entries: list[dict]) -> dict[str, dict]:
    """For each repertoire entry, how far through its family course the
    Ghost has progressed — variant N/total, plus the current mastery streak
    (X/3 clean runs) towards unlocking the next one. Le pourcentage compte
    aussi la série en cours sur la variante active (pas seulement les
    variantes déjà 100% terminées) : sinon la barre restait figée pendant
    tout le travail sur une variante et ne bougeait qu'à chaque déblocage,
    ce qui ne reflétait pas l'apprentissage réel de l'ouverture entière."""
    out: dict[str, dict] = {}
    for entry in repertoire_entries:
        course = entry.get("course") or []
        total = len(course)
        pos = entry.get("course_position") or 0
        streak = entry.get("clean_streak") or 0
        completed = pos + (1 if entry.get("status") == "completed" else 0)
        completed = min(completed, total)
        progress_units = pos + (1.0 if entry.get("status") == "completed" else min(streak, CLEAN_STREAK_TARGET) / CLEAN_STREAK_TARGET)
        progress_units = min(progress_units, total)
        out[entry["id"]] = {
            "total_variants": total, "completed_variants": completed,
            "current_variant_index": pos, "clean_streak": streak,
            "percent": round(100 * progress_units / total) if total else 0,
        }
    return out
