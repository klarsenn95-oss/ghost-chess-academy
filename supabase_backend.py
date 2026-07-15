"""Supabase/PostgreSQL backend adapter for GHOST.

This keeps the current app logic stable by storing the complete application state
inside a PostgreSQL JSONB document. It is a safe first migration step before a
full relational refactor.
"""
from __future__ import annotations

import json
import mimetypes
import os
import copy
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

_STATE_CACHE: Optional[dict[str, Any]] = None
_STATE_CACHE_AT = 0.0
_CLIENT = None


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def backend_name() -> str:
    return (os.environ.get("GHOST_DB_BACKEND") or "auto").strip().lower()


def supabase_configured() -> bool:
    if backend_name() == "local":
        return False
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_ROLE_KEY"))


def strict_mode() -> bool:
    return backend_name() == "supabase" or _env_bool("GHOST_SUPABASE_STRICT", False)


def get_supabase_client():
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("SUPABASE_URL et SUPABASE_SERVICE_ROLE_KEY sont requis pour Supabase.")
    _CLIENT = create_client(url, key)
    return _CLIENT


def state_table() -> str:
    return os.environ.get("GHOST_SUPABASE_STATE_TABLE", "ghost_app_state")


def state_id() -> str:
    return os.environ.get("GHOST_STATE_ID", "main")


def storage_bucket() -> str:
    return os.environ.get("GHOST_STORAGE_BUCKET", "ghost-client-files")


def load_state() -> Optional[dict[str, Any]]:
    """Load the JSON state from Supabase. Returns None when Supabase is disabled.

    In non-strict mode, connection errors fall back to local JSON so development
    is not blocked. In strict mode, errors are raised to make deployment failures
    visible immediately.
    """
    global _STATE_CACHE, _STATE_CACHE_AT
    if not supabase_configured():
        return None
    cache_ttl = max(0, float(os.environ.get("GHOST_STATE_CACHE_SECONDS", "3")))
    if _STATE_CACHE is not None and time.monotonic() - _STATE_CACHE_AT < cache_ttl:
        return copy.deepcopy(_STATE_CACHE)
    try:
        client = get_supabase_client()
        res = client.table(state_table()).select("data").eq("id", state_id()).limit(1).execute()
        rows = getattr(res, "data", None) or []
        if not rows:
            return None
        data = rows[0].get("data") or {}
        if isinstance(data, str):
            data = json.loads(data)
        _STATE_CACHE = copy.deepcopy(data)
        _STATE_CACHE_AT = time.monotonic()
        return data
    except Exception as exc:
        print(f"[GHOST/Supabase] Lecture impossible : {exc}")
        if strict_mode():
            raise
        return None


def save_state(data: dict[str, Any]) -> bool:
    """Save the JSON state to Supabase. Returns True when saved remotely."""
    global _STATE_CACHE, _STATE_CACHE_AT
    if not supabase_configured():
        return False
    try:
        client = get_supabase_client()
        payload = {
            "id": state_id(),
            "data": data,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        client.table(state_table()).upsert(payload).execute()
        _STATE_CACHE = copy.deepcopy(data)
        _STATE_CACHE_AT = time.monotonic()
        return True
    except Exception as exc:
        print(f"[GHOST/Supabase] Sauvegarde impossible : {exc}")
        if strict_mode():
            raise
        return False


def bootstrap_from_local_json(local_path: str | os.PathLike[str]) -> Optional[dict[str, Any]]:
    """Optionally migrate a local JSON file into Supabase when the remote row is empty."""
    if not supabase_configured() or not _env_bool("GHOST_AUTO_MIGRATE_LOCAL_JSON", False):
        return None
    path = Path(local_path)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if save_state(data):
            print(f"[GHOST/Supabase] Migration locale effectuée depuis {path}")
            return data
    except Exception as exc:
        print(f"[GHOST/Supabase] Migration locale échouée : {exc}")
        if strict_mode():
            raise
    return None


def storage_configured() -> bool:
    return supabase_configured() and bool(os.environ.get("GHOST_STORAGE_BUCKET", "ghost-client-files"))


def upload_bytes(filename: str, content: bytes, prefix: str = "client") -> Optional[str]:
    """Upload bytes to Supabase Storage and return a public URL.

    The bucket must exist. For this app, use a public bucket during the first
    deployment step so browser links can open attachments directly.
    """
    if not storage_configured():
        return None
    try:
        client = get_supabase_client()
        safe_name = filename.replace("\\", "_").replace("/", "_")
        path = f"{prefix}/{datetime.now(timezone.utc).strftime('%Y/%m')}/{safe_name}"
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        bucket = storage_bucket()
        try:
            client.storage.from_(bucket).upload(
                path,
                content,
                {"content-type": content_type, "upsert": "true"},
            )
        except TypeError:
            client.storage.from_(bucket).upload(path=path, file=content, file_options={"content-type": content_type, "upsert": "true"})
        url = client.storage.from_(bucket).get_public_url(path)
        if isinstance(url, dict):
            return url.get("publicURL") or url.get("publicUrl") or url.get("public_url")
        return str(url)
    except Exception as exc:
        print(f"[GHOST/Supabase] Upload Storage impossible : {exc}")
        if strict_mode():
            raise
        return None
