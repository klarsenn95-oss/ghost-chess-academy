"""Finance ledger — dedicated Supabase table (ghost_transactions), separate
from the app's big ghost_app_state JSON blob for the same reason as
puzzles/openings: money records shouldn't live inside a single mutable
document that everything else in the app also rewrites on every save.

This module deliberately does NOT touch student registration/plan payments
(payments_log inside ghost_app_state) — that system already works and
re-routing it through here would risk a working payment flow for no real
benefit. Instead, build_summary() reads payments_log read-only and merges
it into the picture alongside this table's own rows, so the coach gets one
unified balance without the two systems being mixed in storage.

See supabase_finance_schema.sql for the table definition.

Falls back to nothing gracefully when Supabase isn't configured — callers
should check backend_ready() first.
"""
from __future__ import annotations

import re
from typing import Optional

from postgrest.exceptions import APIError

from supabase_backend import supabase_configured, get_supabase_client

TABLE = "ghost_transactions"


class TableNotReady(Exception):
    """Raised when ghost_transactions doesn't exist yet (schema not applied
    to this Supabase project) — distinct from "Supabase not configured" so
    callers can show a clear, specific message instead of a 500."""

KINDS = ("income", "expense")
CATEGORIES = (
    "member_contribution",
    "associate_contribution",
    "tournament_prize_payout",
    "tournament_entry_income",
    "coach_payment",
    "gs_payout",
    "other",
)
CATEGORY_LABELS = {
    "member_contribution": "Cotisation membre",
    "associate_contribution": "Apport associé",
    "tournament_prize_payout": "Gains versés (tournoi)",
    "tournament_entry_income": "Droits d'entrée (tournoi)",
    "coach_payment": "Rémunération coach",
    "gs_payout": "Conversion Gs → FCFA",
    "other": "Autre",
    # Catégories virtuelles, lecture seule (déjà suivies ailleurs dans
    # ghost_app_state) : jamais fusionnées silencieusement dans une seule
    # ligne, pour ne pas masquer un éventuel double-comptage entre les deux
    # systèmes existants (le coach peut recouper lui-même).
    "registration_online": "Inscriptions & options (paiement en ligne)",
    "registration_manual": "Paiements enregistrés sur les fiches élèves",
}


def backend_ready() -> bool:
    return supabase_configured()


def _paginate_select(apply_filters=None, page_size: int = 500) -> list[dict]:
    client = get_supabase_client()
    rows: list[dict] = []
    offset = 0
    while True:
        q = client.table(TABLE).select("*")
        if apply_filters:
            q = apply_filters(q)
        q = q.order("occurred_on", desc=True).range(offset, offset + page_size - 1)
        try:
            batch = q.execute().data or []
        except APIError as e:
            # La table n'existe pas encore (schéma pas encore appliqué sur ce
            # projet Supabase) — un 500 brut ici casserait tout l'onglet
            # Finances au lieu de juste dire "pas encore prêt".
            if (e.code or "") == "PGRST205" or "does not exist" in (e.message or ""):
                raise TableNotReady() from e
            raise
        rows.extend(batch)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


def list_transactions(category: Optional[str] = None, kind: Optional[str] = None, limit: int = 200) -> list[dict]:
    def _filter(q):
        if category:
            q = q.eq("category", category)
        if kind:
            q = q.eq("kind", kind)
        return q
    rows = _paginate_select(_filter)
    return rows[:limit]


def add_transaction(kind: str, category: str, amount: int, occurred_on: str,
                     note: str = "", student_index: Optional[int] = None,
                     tournament_id: Optional[str] = None, coach_user_id: Optional[str] = None,
                     created_by: str = "coach") -> dict:
    if kind not in KINDS:
        raise ValueError("Type de mouvement invalide.")
    if category not in CATEGORIES:
        raise ValueError("Catégorie invalide.")
    amount = int(amount)
    if amount <= 0:
        raise ValueError("Le montant doit être positif.")
    client = get_supabase_client()
    optional = {
        "occurred_on": occurred_on or None, "note": (note or "").strip() or None,
        "student_index": student_index, "tournament_id": tournament_id,
        "coach_user_id": coach_user_id,
    }
    payload = {"kind": kind, "category": category, "amount": amount, "created_by": created_by}
    payload.update({k: v for k, v in optional.items() if v is not None})
    try:
        res = client.table(TABLE).insert(payload).execute()
    except APIError as e:
        if (e.code or "") == "PGRST205" or "does not exist" in (e.message or ""):
            raise TableNotReady() from e
        raise
    return (res.data or [payload])[0]


def remove_transaction(transaction_id: str) -> bool:
    client = get_supabase_client()
    try:
        client.table(TABLE).delete().eq("id", transaction_id).execute()
    except APIError as e:
        if (e.code or "") == "PGRST205" or "does not exist" in (e.message or ""):
            raise TableNotReady() from e
        raise
    return True


_GS_NOTE_RE = re.compile(r"^GS_CONVERT:(\d+)\b")


def add_gs_payout(student_index: int, gs_amount: int, fcfa_amount: int, student_name: str = "") -> dict:
    """Records a Gs→FCFA conversion as a normal expense transaction — no
    schema change needed: the Gs count is encoded as a parseable prefix in
    `note` (GS_CONVERT:<n>) so gs_converted_total() can add it back up,
    while the human-readable text after it still shows in the ledger."""
    note = f"GS_CONVERT:{gs_amount} — {gs_amount} Gs convertis" + (f" pour {student_name}" if student_name else "")
    return add_transaction("expense", "gs_payout", fcfa_amount, occurred_on="", note=note, student_index=student_index)


def gs_converted_total(student_index: int) -> int:
    """Sum of Gs already converted to FCFA for this student, all-time —
    the coach's conversion cap logic subtracts this from gs earned so a
    Ghost can't re-convert the same Gs twice."""
    rows = list_transactions(category="gs_payout", limit=100000)
    total = 0
    for r in rows:
        if r.get("student_index") != student_index:
            continue
        m = _GS_NOTE_RE.match(r.get("note") or "")
        if m:
            total += int(m.group(1))
    return total


def gs_converted_this_month(student_index: int) -> int:
    from datetime import date
    ym = date.today().strftime("%Y-%m")
    rows = list_transactions(category="gs_payout", limit=100000)
    total = 0
    for r in rows:
        if r.get("student_index") != student_index:
            continue
        if not (r.get("occurred_on") or "").startswith(ym):
            continue
        m = _GS_NOTE_RE.match(r.get("note") or "")
        if m:
            total += int(m.group(1))
    return total


def build_summary(payments_log: list[dict], students: list[dict]) -> dict:
    """Solde unifié à partir de TROIS sources qui restent séparées en
    stockage (rien n'est déplacé ni fusionné) :
      1. ghost_transactions (cette table) — cotisations, apports, tournois, coachs
      2. payments_log dans ghost_app_state — paiements en ligne (élève -> demande -> coach confirme)
      3. student.payments sur chaque fiche — paiements que le coach enregistre lui-même
    Les sources 2 et 3 sont deux systèmes de suivi de paiement déjà
    existants et potentiellement en partie redondants (un même paiement
    pourrait, en théorie, être noté dans les deux) — cette fonction ne
    tente pas de deviner les doublons, elle les affiche comme deux lignes
    distinctes plutôt que de les additionner en aveugle dans un seul total
    qui masquerait le problème."""
    rows = _paginate_select(page_size=1000)

    registration_online = sum(int(p.get("amount") or 0) for p in (payments_log or []) if p.get("status") == "paid")
    registration_online_count = len([p for p in (payments_log or []) if p.get("status") == "paid"])

    registration_manual = 0
    registration_manual_count = 0
    for s in (students or []):
        for p in (s.get("payments") or []):
            if p.get("status") == "paid":
                registration_manual += int(p.get("amount") or 0)
                registration_manual_count += 1

    by_category: dict[str, dict] = {}
    if registration_online:
        by_category["registration_online"] = {"kind": "income", "total": registration_online, "count": registration_online_count}
    if registration_manual:
        by_category["registration_manual"] = {"kind": "income", "total": registration_manual, "count": registration_manual_count}

    total_income = registration_online + registration_manual
    total_expense = 0
    for r in rows:
        cat = r["category"]
        entry = by_category.setdefault(cat, {"kind": r["kind"], "total": 0, "count": 0})
        entry["total"] += r["amount"]
        entry["count"] += 1
        if r["kind"] == "income":
            total_income += r["amount"]
        else:
            total_expense += r["amount"]

    breakdown = [
        {"category": cat, "label": CATEGORY_LABELS.get(cat, cat), **entry}
        for cat, entry in sorted(by_category.items(), key=lambda x: -x[1]["total"])
    ]
    return {
        "balance": total_income - total_expense,
        "total_income": total_income,
        "total_expense": total_expense,
        "breakdown": breakdown,
        "transaction_count": len(rows),
    }
