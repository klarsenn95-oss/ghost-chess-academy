-- GHOST Openings V2 : le coach attribue une FAMILLE entiere (ex. "Sicilian
-- Defense"), pas une variante precise. Le systeme construit un parcours
-- (course) de variantes principales de cette famille, les enchaine, et ne
-- debloque la suivante qu'apres 3 reussites propres (sans erreur) d'affilee.
-- A coller dans Supabase -> SQL Editor -> New Query -> Run.

alter table public.ghost_opening_repertoire add column if not exists family text;
alter table public.ghost_opening_repertoire add column if not exists course jsonb not null default '[]'::jsonb;
alter table public.ghost_opening_repertoire add column if not exists course_position integer not null default 0;
alter table public.ghost_opening_repertoire add column if not exists clean_streak integer not null default 0;

alter table public.ghost_opening_repertoire drop constraint if exists ghost_opening_repertoire_student_index_opening_id_key;
alter table public.ghost_opening_repertoire drop constraint if exists ghost_opening_repertoire_student_family_key;
alter table public.ghost_opening_repertoire add constraint ghost_opening_repertoire_student_family_key unique (student_index, family);
