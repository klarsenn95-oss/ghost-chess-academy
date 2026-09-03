-- GHOST Openings - bibliotheque d'ouvertures (arbre de positions), separee
-- du blob ghost_app_state pour les memes raisons que ghost_puzzles : des
-- milliers de lignes ne doivent pas etre re-chargees a chaque requete.
-- A coller dans Supabase -> SQL Editor -> New Query -> Run.
--
-- Source des donnees : lichess-org/chess-openings (CC0-1.0, verifie via
-- l'API GitHub avant import). Chaque ligne source (eco, name, pgn) est
-- rejouee coup par coup ; les prefixes communs a plusieurs variantes ne
-- sont stockes qu'une fois grace a parent_id, formant un vrai arbre.

create extension if not exists pgcrypto;

create table if not exists public.ghost_openings (
  id uuid primary key default gen_random_uuid(),
  parent_id uuid references public.ghost_openings(id) on delete cascade,
  fen text not null,
  move_san text,
  ply integer not null default 0,
  eco text,
  name text,
  family text,
  variation text,
  subvariation text,
  source text not null default 'lichess-chess-openings',
  created_at timestamptz not null default now()
);

create index if not exists ghost_openings_parent_idx on public.ghost_openings (parent_id);
create index if not exists ghost_openings_eco_idx on public.ghost_openings (eco);
create index if not exists ghost_openings_family_idx on public.ghost_openings (family);
create index if not exists ghost_openings_name_idx on public.ghost_openings using gin (to_tsvector('simple', coalesce(name, '')));

alter table public.ghost_openings enable row level security;

-- Repertoire : les ouvertures qu'un coach a assignees a un Ghost.
-- Une ligne = une variante (un noeud nomme de ghost_openings) assignee.
-- `side` : le coach choisit explicitement si le Ghost pratique cette ligne
-- avec les Blancs ou les Noirs (ex. Najdorf est une defense noire meme si le
-- noeud assigne est a un ply impair) - pas deductible de la parite du ply.
-- `line` : sequence complete racine -> noeud, precalculee une fois a
-- l'assignation (comme ghost_program_days.puzzle_ids pour les puzzles) pour
-- ne pas remonter l'arbre a chaque tentative d'entrainement.
create table if not exists public.ghost_opening_repertoire (
  id uuid primary key default gen_random_uuid(),
  student_index integer not null,
  opening_id uuid not null references public.ghost_openings(id) on delete cascade,
  side text not null default 'white',
  line jsonb not null default '[]'::jsonb,
  status text not null default 'active',
  created_at timestamptz not null default now(),
  unique (student_index, opening_id)
);

create index if not exists ghost_opening_repertoire_student_idx on public.ghost_opening_repertoire (student_index);

alter table public.ghost_opening_repertoire enable row level security;

-- Progression par position et par Ghost (mode entrainement : a-t-il trouve
-- le bon coup a cette position). Pas encore de repetition espacee en V1 -
-- ces compteurs suffisent pour "points faibles" cote coach ; l'ajout d'un
-- calendrier de revision (ease_factor/next_review_date) est prevu en V2
-- sans migration cassante (colonnes nullables ajoutees plus tard).
create table if not exists public.ghost_opening_progress (
  id uuid primary key default gen_random_uuid(),
  user_id text not null,
  opening_id uuid not null references public.ghost_openings(id) on delete cascade,
  correct_count integer not null default 0,
  wrong_count integer not null default 0,
  last_seen_at timestamptz not null default now(),
  unique (user_id, opening_id)
);

create index if not exists ghost_opening_progress_user_idx on public.ghost_opening_progress (user_id);

alter table public.ghost_opening_progress enable row level security;
