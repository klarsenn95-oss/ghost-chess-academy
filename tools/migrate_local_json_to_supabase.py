"""Migrate the current local JSON state to Supabase.

Usage:
  python tools/migrate_local_json_to_supabase.py
  python tools/migrate_local_json_to_supabase.py /path/to/.ghost_chess_data.json
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

from supabase_backend import save_state, supabase_configured

path = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else Path.home() / ".ghost_chess_data.json"
if not path.exists():
    raise SystemExit(f"Fichier introuvable : {path}")
if not supabase_configured():
    raise SystemExit("Supabase non configuré. Vérifie SUPABASE_URL et SUPABASE_SERVICE_ROLE_KEY.")

with path.open("r", encoding="utf-8") as f:
    data = json.load(f)

if save_state(data):
    print(f"✅ Migration terminée vers Supabase depuis {path}")
else:
    raise SystemExit("❌ Migration échouée.")
