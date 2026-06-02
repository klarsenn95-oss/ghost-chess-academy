-- GHOST Chess Platform v9 — Supabase/PostgreSQL bootstrap
-- À coller dans Supabase → SQL Editor → New Query → Run.

create table if not exists public.ghost_app_state (
  id text primary key,
  data jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

insert into public.ghost_app_state (id, data)
values ('main', '{
  "students": [],
  "group_notes": [],
  "sessions": [],
  "pairs": [],
  "exam_bank": {},
  "price_grid": {},
  "units": [],
  "users": [],
  "registration_codes": [],
  "client_notifications": [],
  "client_price_plans": []
}'::jsonb)
on conflict (id) do nothing;

create or replace function public.touch_ghost_app_state_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists trg_touch_ghost_app_state_updated_at on public.ghost_app_state;
create trigger trg_touch_ghost_app_state_updated_at
before update on public.ghost_app_state
for each row execute function public.touch_ghost_app_state_updated_at();

-- Option simple pour la V9 : le backend Flask utilise la SERVICE_ROLE_KEY côté serveur.
-- Le client navigateur ne lit pas cette table directement.
alter table public.ghost_app_state enable row level security;

-- Bucket Storage à créer dans le dashboard Supabase :
-- Storage → New bucket → name: ghost-client-files → Public bucket: ON
-- Les fichiers seront uploadés par le backend Flask avec la service role key.
