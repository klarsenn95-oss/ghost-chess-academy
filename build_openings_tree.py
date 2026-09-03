"""
Parses the Lichess chess-openings TSV dataset (CC0) into a deduplicated
position tree ready for import into ghost_openings.

Source: https://github.com/lichess-org/chess-openings (CC0-1.0, verified via
GitHub API license field before use).

Each row is "eco, name, pgn" where pgn is a SAN move list from the start
position. Many rows share a common prefix (e.g. every Sicilian line starts
1.e4 c5) — we replay every row's moves with python-chess and keep exactly
one node per unique position, linked to its parent, so shared prefixes are
stored once instead of duplicated per variation.
"""
import csv
import json
import os
import re

import chess

TSV_FILES = ["a.tsv", "b.tsv", "c.tsv", "d.tsv", "e.tsv"]
TSV_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_tsv_tmp")


def parse_name(name):
    """'Sicilian Defense: Najdorf Variation, English Attack' ->
    (family='Sicilian Defense', variation='Najdorf Variation',
     subvariation='English Attack')"""
    if ":" in name:
        family, rest = name.split(":", 1)
        family = family.strip()
        parts = [p.strip() for p in rest.split(",")]
        variation = parts[0] if parts else ""
        subvariation = ", ".join(parts[1:]) if len(parts) > 1 else ""
    else:
        family, variation, subvariation = name.strip(), "", ""
    return family, variation, subvariation


def strip_move_numbers(pgn):
    # "1. Nh3 d5 2. g3" -> ["Nh3", "d5", "g3"]
    return re.sub(r"\d+\.(\.\.)?", " ", pgn).split()


def main():
    rows = []
    for fname in TSV_FILES:
        path = os.path.join(TSV_DIR, fname)
        with open(path, encoding="utf-8") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for r in reader:
                rows.append(r)

    print(f"Loaded {len(rows)} named entries from {len(TSV_FILES)} files")

    # nodes keyed by the tuple of SAN moves from the start (the "path")
    nodes = {}  # path_tuple -> dict
    nodes[()] = {
        "path": (), "fen": chess.STARTING_FEN, "move_san": None,
        "parent_path": None, "ply": 0,
        "eco": None, "name": None, "family": None, "variation": None, "subvariation": None,
    }

    skipped = 0
    for r in rows:
        eco = (r.get("eco") or "").strip()
        name = (r.get("name") or "").strip()
        pgn = (r.get("pgn") or "").strip()
        if not eco or not name or not pgn:
            continue
        sans = strip_move_numbers(pgn)
        board = chess.Board()
        path = []
        try:
            for san in sans:
                move = board.parse_san(san)
                board.push(move)
                path.append(san)
                key = tuple(path)
                if key not in nodes:
                    parent_key = tuple(path[:-1])
                    nodes[key] = {
                        "path": key, "fen": board.fen(), "move_san": san,
                        "parent_path": parent_key, "ply": len(key),
                        "eco": None, "name": None, "family": None,
                        "variation": None, "subvariation": None,
                    }
        except Exception as e:
            skipped += 1
            continue
        # Attach the name/eco to the FINAL position of this row.
        final_key = tuple(path)
        family, variation, subvariation = parse_name(name)
        node = nodes[final_key]
        node["eco"] = eco
        node["name"] = name
        node["family"] = family
        node["variation"] = variation
        node["subvariation"] = subvariation

    print(f"Skipped {skipped} rows with illegal/unparseable move sequences")
    print(f"Built {len(nodes)} unique positions (including start position)")

    # Serialize with stable integer ids in parent-before-child order (BFS by ply).
    ordered = sorted(nodes.values(), key=lambda n: (n["ply"], n["path"]))
    path_to_idx = {n["path"]: i for i, n in enumerate(ordered)}

    out = []
    for n in ordered:
        parent_idx = path_to_idx[n["parent_path"]] if n["parent_path"] is not None else None
        out.append({
            "idx": path_to_idx[n["path"]],
            "parent_idx": parent_idx,
            "fen": n["fen"],
            "move_san": n["move_san"],
            "ply": n["ply"],
            "eco": n["eco"],
            "name": n["name"],
            "family": n["family"],
            "variation": n["variation"],
            "subvariation": n["subvariation"],
        })

    named_count = sum(1 for n in out if n["name"])
    print(f"{named_count} positions carry a name/ECO (the rest are unnamed transpositions)")

    with open("openings_tree.json", "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    print("Wrote openings_tree.json —", os.path.getsize("openings_tree.json"), "bytes")


if __name__ == "__main__":
    main()
