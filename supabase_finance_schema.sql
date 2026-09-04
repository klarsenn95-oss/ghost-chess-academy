-- GHOST Finances — registre des mouvements d'argent, table dédiée
-- (séparée de ghost_app_state, le gros blob JSON de l'appli).
-- À coller dans Supabase → SQL Editor → New Query → Run.
--
-- Les inscriptions/options élèves restent suivies là où elles le sont
-- déjà (payments_log dans ghost_app_state) — ce registre ne les duplique
-- PAS, pour ne rien risquer sur un système de paiement existant qui
-- fonctionne. Il sert aux mouvements qui n'avaient nulle part où vivre :
-- cotisations membres, apports associés, gains/paiements de tournois,
-- rémunération coach. Le solde affiché au coach additionne les deux
-- sources en lecture seule (voir finance_service.py), sans les mélanger
-- dans le même stockage.

create extension if not exists pgcrypto;

create table if not exists public.ghost_transactions (
  id uuid primary key default gen_random_uuid(),
  kind text not null check (kind in ('income','expense')),
  category text not null check (category in (
    'member_contribution', 'associate_contribution',
    'tournament_prize_payout', 'tournament_entry_income',
    'coach_payment', 'other'
  )),
  amount integer not null check (amount > 0),
  currency text not null default 'FCFA',
  occurred_on date not null default current_date,
  note text,
  student_index integer,
  tournament_id text,
  coach_user_id text,
  created_by text not null default 'coach',
  created_at timestamptz not null default now()
);

create index if not exists ghost_transactions_occurred_on_idx on public.ghost_transactions (occurred_on desc);
create index if not exists ghost_transactions_category_idx on public.ghost_transactions (category);

alter table public.ghost_transactions enable row level security;
-- Le backend Flask utilise la SERVICE ROLE KEY côté serveur (bypass RLS).
-- Le navigateur ne touche jamais directement cette table.
