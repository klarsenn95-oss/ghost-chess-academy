import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import BLIND_TACTICS  # noqa: E402


def fail(message):
    print(f"[blind tactics] FAIL: {message}", file=sys.stderr)
    raise SystemExit(1)


def main():
    puzzles = BLIND_TACTICS
    if len(puzzles) < 10:
        fail("at least 10 coherent puzzles are required")

    ids = [p.get("id") for p in puzzles]
    if len(ids) != len(set(ids)):
        fail("duplicate puzzle ids")
    pgns = [p.get("pgn") for p in puzzles]
    if len(pgns) != len(set(pgns)):
        fail("duplicate PGNs")

    js = r"""
const { Chess } = require("./static/chess.js");
const puzzles = JSON.parse(process.argv[1]);
let ok = 0;
for (const p of puzzles) {
  for (const key of ["id", "title", "pgn", "ply", "color", "description", "xp"]) {
    if (!(key in p)) throw new Error(`${p.id || "unknown"} missing ${key}`);
  }
  const c = new Chess();
  if (!c.load_pgn(p.pgn, { sloppy: true })) throw new Error(`${p.id} invalid PGN`);
  const hist = c.history({ verbose: true });
  if (p.ply < 0 || p.ply >= hist.length) throw new Error(`${p.id} invalid ply ${p.ply}`);
  const position = new Chess();
  for (const m of hist.slice(0, p.ply)) position.move(m.san, { sloppy: true });
  const expectedTurn = p.color === "white" ? "w" : "b";
  if (position.turn() !== expectedTurn) throw new Error(`${p.id} wrong side to move at ply ${p.ply}`);
  const expected = hist[p.ply];
  const legal = position.moves({ verbose: true }).some(m => (
    m.from === expected.from && m.to === expected.to && (!expected.promotion || m.promotion === expected.promotion)
  ));
  if (!legal) throw new Error(`${p.id} expected move is not legal from the hidden position`);
  const finalMove = hist[hist.length - 1];
  if (!finalMove.san.includes("#")) throw new Error(`${p.id} does not finish by checkmate`);
  const replay = new Chess();
  for (const m of hist) replay.move(m.san, { sloppy: true });
  if (!replay.in_checkmate()) throw new Error(`${p.id} final position is not checkmate`);
  ok++;
}
console.log(`[blind tactics] ${ok} coherent mate puzzles validated`);
"""
    result = subprocess.run(
        ["node", "-e", js, json.dumps(puzzles, ensure_ascii=False)],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if result.returncode:
        print(result.stdout, end="")
        print(result.stderr, end="", file=sys.stderr)
        raise SystemExit(result.returncode)
    print(result.stdout, end="")


if __name__ == "__main__":
    main()
