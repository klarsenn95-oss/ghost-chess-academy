"""Imports openings_tree.json (built by build_openings_tree.py) into the
ghost_openings table. Run once; safe to re-run only after truncating the
table first (parent_id chains would otherwise duplicate).

Inserts level-by-level (by ply, parent before child) since Supabase assigns
real UUIDs on insert and each row's parent_id must reference the already-
inserted parent's real id.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dotenv import load_dotenv
load_dotenv()
import opening_service as os_svc

BATCH_SIZE = 500


def main():
    with open("openings_tree.json", encoding="utf-8") as f:
        nodes = json.load(f)
    print(f"Loaded {len(nodes)} nodes from openings_tree.json")

    by_ply: dict[int, list] = {}
    for n in nodes:
        by_ply.setdefault(n["ply"], []).append(n)
    max_ply = max(by_ply)

    idx_to_uuid: dict[int, str] = {}
    client = os_svc.get_supabase_client()
    total_inserted = 0

    for ply in range(max_ply + 1):
        level_nodes = by_ply.get(ply, [])
        if not level_nodes:
            continue
        for batch_start in range(0, len(level_nodes), BATCH_SIZE):
            batch = level_nodes[batch_start:batch_start + BATCH_SIZE]
            payload = []
            for n in batch:
                parent_uuid = idx_to_uuid.get(n["parent_idx"]) if n["parent_idx"] is not None else None
                payload.append({
                    "parent_id": parent_uuid,
                    "fen": n["fen"],
                    "move_san": n["move_san"],
                    "ply": n["ply"],
                    "eco": n["eco"],
                    "name": n["name"],
                    "family": n["family"],
                    "variation": n["variation"] or None,
                    "subvariation": n["subvariation"] or None,
                    "source": "lichess-chess-openings",
                })
            res = client.table("ghost_openings").insert(payload).execute()
            rows = res.data or []
            if len(rows) != len(batch):
                raise RuntimeError(f"insert mismatch at ply {ply}: sent {len(batch)}, got {len(rows)}")
            for n, row in zip(batch, rows):
                idx_to_uuid[n["idx"]] = row["id"]
            total_inserted += len(rows)
        print(f"ply {ply}: {len(level_nodes)} nodes inserted (total so far: {total_inserted})")

    print("Done. Total inserted:", total_inserted)


if __name__ == "__main__":
    main()
