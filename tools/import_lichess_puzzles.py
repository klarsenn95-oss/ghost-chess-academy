"""Import a curated slice of the Lichess puzzle database into ghost_puzzles.

Source: https://database.lichess.org/#puzzles — released under CC0 (public
domain), no attribution required. This script downloads the CSV once,
filters/transforms it locally, and pushes the result to Supabase. Puzzles
never live in the app's ghost_app_state JSON blob (see puzzle_service.py for
why) and the raw CSV/decompressed file are never shipped to the browser.

Usage:
    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python tools/import_lichess_puzzles.py

Re-running is safe: puzzles are upserted on (source, source_id), so already
imported rows are skipped/updated rather than duplicated.
"""
from __future__ import annotations

import csv
import io
import json
import os
import random
import sys
import time
from collections import defaultdict
from pathlib import Path

import chess
import requests
import zstandard

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as ghost_app  # noqa: E402  (for PUZZLE_RATING_BANDS / difficulty logic, no Flask side effects on import)
from supabase_backend import get_supabase_client, supabase_configured  # noqa: E402

LICHESS_CSV_URL = "https://database.lichess.org/lichess_db_puzzle.csv.zst"
CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "lichess_db_puzzle.csv.zst"
SELECTED_CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "selected_puzzles.json"

MIN_RATING = int(os.environ.get("PUZZLE_IMPORT_MIN_RATING", 600))
MAX_RATING = int(os.environ.get("PUZZLE_IMPORT_MAX_RATING", 2400))
TARGET_TOTAL = int(os.environ.get("PUZZLE_IMPORT_TARGET", 20000))

# Lichess theme tag -> Ghost theme (French, matches app.PUZZLE_THEMES). Tags
# with no clean equivalent (e.g. Lichess has no generic "overload"/"calcul"
# tag) are intentionally left unmapped rather than forced onto something
# inaccurate; those Ghost themes stay coach-only until a better match exists.
THEME_MAP = {
    "fork": "Fourchette",
    "pin": "Clouage",
    "skewer": "Enfilade",
    "sacrifice": "Sacrifice",
    "mateIn1": "Mat en 1",
    "mateIn2": "Mat en 2",
    "mateIn3": "Mat en 3",
    "discoveredAttack": "Découverte",
    "deflection": "Déviation",
    "attraction": "Attraction",
    "capturingDefender": "Élimination du défenseur",
    "kingsideAttack": "Attaque du roi",
    "queensideAttack": "Attaque du roi",
    "endgame": "Finale",
    "defensiveMove": "Défense",
    "long": "Calcul",
    "veryLong": "Calcul",
}

# Roughly even budget per Ghost theme so no single tag (fork/endgame tend to
# dominate the raw database) crowds out the rest.
PER_THEME_CAP = max(1, TARGET_TOTAL // len(set(THEME_MAP.values())))


def download_csv_zst() -> Path:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if CACHE_PATH.exists() and CACHE_PATH.stat().st_size > 0:
        print(f"Using cached download: {CACHE_PATH} ({CACHE_PATH.stat().st_size / 1e6:.0f} MB)")
        return CACHE_PATH
    print(f"Downloading {LICHESS_CSV_URL} ...")
    with requests.get(LICHESS_CSV_URL, stream=True, timeout=120) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        written = 0
        t0 = time.time()
        with open(CACHE_PATH, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
                written += len(chunk)
                if total:
                    print(f"\r  {written / 1e6:.0f}/{total / 1e6:.0f} MB", end="", flush=True)
        print(f"\nDownloaded in {time.time() - t0:.0f}s")
    return CACHE_PATH


def iter_rows(csv_zst_path: Path):
    dctx = zstandard.ZstdDecompressor()
    with open(csv_zst_path, "rb") as fh:
        with dctx.stream_reader(fh) as reader:
            text_stream = io.TextIOWrapper(reader, encoding="utf-8")
            yield from csv.DictReader(text_stream)


def ghost_themes_for(lichess_themes: str) -> list[str]:
    tags = lichess_themes.split()
    mapped = {THEME_MAP[t] for t in tags if t in THEME_MAP}
    return sorted(mapped)


def quick_check(row: dict) -> tuple[int, list[str]] | None:
    """Cheap pre-filter (no chess.Board work): rating range + at least one
    mapped theme. Called before the expensive FEN/SAN computation so rows
    that would be discarded anyway (e.g. a theme already at its cap) never
    pay for it."""
    try:
        rating = int(row["Rating"])
    except (KeyError, ValueError):
        return None
    if not (MIN_RATING <= rating <= MAX_RATING):
        return None
    themes = ghost_themes_for(row.get("Themes", ""))
    if not themes:
        return None
    return rating, themes


def transform_row(row: dict, rating: int, themes: list[str]) -> dict | None:
    """Applies Lichess's 'FEN is before the setup move' convention: the real
    puzzle position is FEN + Moves[0], and the actual solution is Moves[1:]
    (in SAN, since that's what the app's board/solver already expects)."""
    uci_moves = row["Moves"].split()
    if len(uci_moves) < 2:
        return None  # need at least the setup move + one real solution move
    board = chess.Board(row["FEN"])
    try:
        board.push_uci(uci_moves[0])
    except (ValueError, chess.IllegalMoveError):
        return None
    real_fen = board.fen()

    solution_san = []
    for uci in uci_moves[1:]:
        try:
            move = chess.Move.from_uci(uci)
            if move not in board.legal_moves:
                return None
            solution_san.append(board.san(move))
            board.push(move)
        except ValueError:
            return None

    return {
        "source": "lichess",
        "source_id": row["PuzzleId"],
        "title": "",
        "fen": real_fen,
        "solution_moves": solution_san,
        "rating": rating,
        "difficulty": ghost_app.puzzle_difficulty_for_rating(rating),
        "themes": themes,
        "metadata": {"game_url": row.get("GameUrl", ""), "popularity": row.get("Popularity", "")},
    }


def select_and_transform() -> list[dict]:
    csv_path = download_csv_zst()
    per_theme_count: dict[str, int] = defaultdict(int)
    selected: list[dict] = []
    seen_ids: set[str] = set()
    scanned = 0
    t0 = time.time()
    all_capped_streak = 0
    for row in iter_rows(csv_path):
        scanned += 1
        if scanned % 100000 == 0:
            rate = scanned / max(0.001, time.time() - t0)
            remaining = {t: PER_THEME_CAP - c for t, c in per_theme_count.items() if c < PER_THEME_CAP}
            still_needed = sorted(set(THEME_MAP.values()) - set(per_theme_count.keys())) + list(remaining.keys())
            still_needed = sorted(set(still_needed))
            print(f"  scanned {scanned} ({rate:.0f} rows/s), selected {len(selected)}, still short: {still_needed}", flush=True)
            # Every mapped theme is at its cap (some over-cap from co-tagging
            # with a theme that filled later) — no further row can possibly
            # be accepted, so stop instead of burning through the rest of a
            # multi-million-row file for nothing.
            all_capped_streak = all_capped_streak + 1 if not still_needed else 0
            if all_capped_streak >= 2:
                print(f"  all themes capped for {all_capped_streak} checkpoints in a row — stopping scan early.", flush=True)
                break
        if len(selected) >= TARGET_TOTAL:
            break
        pre = quick_check(row)
        if not pre:
            continue
        rating, themes = pre
        if row["PuzzleId"] in seen_ids:
            continue
        # Skip BEFORE the expensive FEN/SAN computation if every one of this
        # puzzle's themes is already at its cap — this is the whole point of
        # splitting quick_check() out from transform_row().
        if all(per_theme_count[t] >= PER_THEME_CAP for t in themes):
            continue
        # Light shuffling: don't just take the CSV's first N per theme
        # (the file is roughly rating-sorted), sample probabilistically once
        # a theme is more than half-full so difficulty spread stays varied.
        if not (any(per_theme_count[t] < PER_THEME_CAP // 2 for t in themes) or random.random() < 0.4):
            continue
        puzzle = transform_row(row, rating, themes)
        if not puzzle:
            continue
        selected.append(puzzle)
        seen_ids.add(puzzle["source_id"])
        for t in puzzle["themes"]:
            per_theme_count[t] += 1
    print(f"Scanned {scanned} rows, selected {len(selected)} puzzles.", flush=True)
    print("Per-theme counts:", dict(per_theme_count))
    return selected


def upload(puzzles: list[dict]):
    if not supabase_configured():
        print("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set — nothing uploaded.")
        return
    client = get_supabase_client()
    batch_size = 500
    # Plain insert for now: fine for a first import into an empty table.
    # Re-running safely (skip already-imported source_ids) needs the unique
    # constraint from supabase_puzzles_schema.sql applied first — see that
    # file's comment; not required for this initial load.
    for i in range(0, len(puzzles), batch_size):
        batch = puzzles[i : i + batch_size]
        client.table("ghost_puzzles").insert(batch).execute()
        print(f"  uploaded {min(i + batch_size, len(puzzles))}/{len(puzzles)}", flush=True)
    print("Done.")


if __name__ == "__main__":
    # Resume from a previous selection pass if one was cached (e.g. the
    # Supabase table wasn't ready yet last time) instead of re-scanning
    # several million CSV rows again.
    if SELECTED_CACHE_PATH.exists() and "--reselect" not in sys.argv:
        print(f"Reusing cached selection: {SELECTED_CACHE_PATH}")
        puzzles = json.loads(SELECTED_CACHE_PATH.read_text(encoding="utf-8"))
    else:
        puzzles = select_and_transform()
        SELECTED_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SELECTED_CACHE_PATH.write_text(json.dumps(puzzles, ensure_ascii=False), encoding="utf-8")
        print(f"Cached selection to {SELECTED_CACHE_PATH} ({len(puzzles)} puzzles)")
    upload(puzzles)
