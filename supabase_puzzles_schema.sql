-- GHOST Puzzles — table dédiée (séparée du blob ghost_app_state).
-- À coller dans Supabase → SQL Editor → New Query → Run.
--
-- Les puzzles sont potentiellement nombreux (dizaines de milliers) : ils ne
-- vivent PAS dans ghost_app_state (le gros blob JSON de l'appli) pour éviter
-- de recharger/dé-copier des dizaines de Mo à chaque requête, comme c'était
-- le cas avant l'optimisation de performance faite plus tôt sur ce projet.

create extension if not exists pgcrypto;

create table if not exists public.ghost_puzzles (
  id uuid primary key default gen_random_uuid(),
  source text not null default 'lichess',
  source_id text,
  title text,
  fen text not null,
  solution_moves jsonb not null,
  rating integer,
  difficulty text not null,
  themes text[] not null default '{}',
  metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);

-- Empêche les doublons si l'import Lichess est relancé. Une VRAIE contrainte
-- unique (pas un index partiel) est nécessaire pour que ON CONFLICT
-- fonctionne ; Postgres traite les NULL comme distincts, donc les puzzles
-- créés par le coach (source_id = null) ne se bloquent pas entre eux.
alter table public.ghost_puzzles drop constraint if exists ghost_puzzles_source_source_id_key;
alter table public.ghost_puzzles add constraint ghost_puzzles_source_source_id_key unique (source, source_id);

create index if not exists ghost_puzzles_difficulty_idx on public.ghost_puzzles (difficulty);
create index if not exists ghost_puzzles_themes_idx on public.ghost_puzzles using gin (themes);

create table if not exists public.ghost_puzzle_attempts (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  puzzle_id uuid not null references public.ghost_puzzles(id) on delete cascade,
  success boolean not null,
  xp_awarded integer not null default 0,
  duration_seconds integer,
  created_at timestamptz not null default now()
);

-- Ajouté après coup pour le chronométrage : sûr à relancer même si la table
-- existe déjà (add column if not exists).
alter table public.ghost_puzzle_attempts add column if not exists duration_seconds integer;

-- Table d'envoi de puzzle coach → élève, pour "Puzzles" dans le Centre de
-- commandement : parcourir la banque et envoyer un puzzle précis à un élève,
-- avec un mot du coach. Les échanges/commentaires réutilisent le système de
-- feedback existant (client_feedback dans le blob JSON), pas une nouvelle
-- table de messagerie.
create table if not exists public.ghost_puzzle_assignments (
  id uuid primary key default gen_random_uuid(),
  puzzle_id uuid not null references public.ghost_puzzles(id) on delete cascade,
  student_index integer not null,
  coach_note text,
  created_at timestamptz not null default now()
);
create index if not exists ghost_puzzle_assignments_student_idx on public.ghost_puzzle_assignments (student_index);

create index if not exists ghost_puzzle_attempts_user_idx on public.ghost_puzzle_attempts (user_id);

-- Une seule tentative "réussie avec XP" comptée par élève et par puzzle : la
-- table elle-même empêche de refarmer l'XP en rejouant le même puzzle,
-- plutôt que de se reposer uniquement sur la logique applicative.
create unique index if not exists ghost_puzzle_attempts_one_reward_idx
  on public.ghost_puzzle_attempts (user_id, puzzle_id)
  where success = true and xp_awarded > 0;

alter table public.ghost_puzzles enable row level security;
alter table public.ghost_puzzle_attempts enable row level security;
alter table public.ghost_puzzle_assignments enable row level security;
-- Le backend Flask utilise la SERVICE ROLE KEY côté serveur (bypass RLS).
-- Le navigateur ne touche jamais directement ces tables.
