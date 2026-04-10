-- Extensions
create extension if not exists "pgcrypto";

-- Enums
create type public.user_role as enum ('employe', 'chef_service', 'direction');
create type public.demande_statut as enum (
  'en_attente',
  'valide_chef',
  'refuse_chef',
  'valide_direction',
  'refuse_direction'
);
create type public.comment_type as enum ('public', 'prive');

-- Tables
create table if not exists public.services (
  id uuid primary key default gen_random_uuid(),
  nom text not null unique,
  created_at timestamptz not null default now()
);

create table if not exists public.users (
  id uuid primary key references auth.users(id) on delete cascade,
  nom text not null,
  email text not null unique,
  role public.user_role not null default 'employe',
  service_id uuid references public.services(id),
  date_embauche date not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint user_service_for_employes check (
    role = 'direction' or service_id is not null
  )
);

create table if not exists public.demandes_conges (
  id uuid primary key default gen_random_uuid(),
  user_id uuid not null references public.users(id) on delete cascade,
  date_debut date not null,
  date_fin date not null,
  nb_jours numeric(6,2) not null check (nb_jours > 0),
  statut public.demande_statut not null default 'en_attente',
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint dates_valides check (date_fin >= date_debut)
);

create table if not exists public.commentaires_demandes (
  id uuid primary key default gen_random_uuid(),
  demande_id uuid not null references public.demandes_conges(id) on delete cascade,
  auteur_id uuid not null references public.users(id) on delete cascade,
  type_commentaire public.comment_type not null default 'public',
  contenu text not null,
  created_at timestamptz not null default now()
);

create table if not exists public.historique_actions (
  id bigint generated always as identity primary key,
  user_id uuid references public.users(id) on delete set null,
  action text not null,
  date timestamptz not null default now(),
  commentaire text,
  created_at timestamptz not null default now()
);

-- Indexes
create index if not exists idx_users_role on public.users(role);
create index if not exists idx_users_service on public.users(service_id);
create index if not exists idx_demandes_user on public.demandes_conges(user_id);
create index if not exists idx_demandes_statut on public.demandes_conges(statut);
create index if not exists idx_demandes_dates on public.demandes_conges(date_debut, date_fin);
create index if not exists idx_commentaires_demande on public.commentaires_demandes(demande_id);
create index if not exists idx_historique_user_date on public.historique_actions(user_id, date desc);

-- updated_at trigger function
create or replace function public.set_updated_at()
returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

-- Triggers
create trigger tr_users_updated_at
before update on public.users
for each row execute procedure public.set_updated_at();

create trigger tr_demandes_updated_at
before update on public.demandes_conges
for each row execute procedure public.set_updated_at();

-- Role helper functions
create or replace function public.current_user_role()
returns public.user_role
language sql
stable
as $$
  select role from public.users where id = auth.uid();
$$;

create or replace function public.current_user_service_id()
returns uuid
language sql
stable
as $$
  select service_id from public.users where id = auth.uid();
$$;

-- Enable RLS
alter table public.users enable row level security;
alter table public.demandes_conges enable row level security;
alter table public.commentaires_demandes enable row level security;
alter table public.historique_actions enable row level security;
alter table public.services enable row level security;

-- users policies
create policy "users_select_self_or_direction"
on public.users for select
using (
  id = auth.uid()
  or public.current_user_role() = 'direction'
  or (
    public.current_user_role() = 'chef_service'
    and service_id = public.current_user_service_id()
  )
);

create policy "users_update_self"
on public.users for update
using (id = auth.uid())
with check (id = auth.uid());

-- demandes policies
create policy "demandes_select_scope"
on public.demandes_conges for select
using (
  user_id = auth.uid()
  or public.current_user_role() = 'direction'
  or (
    public.current_user_role() = 'chef_service'
    and exists (
      select 1 from public.users u
      where u.id = demandes_conges.user_id
      and u.service_id = public.current_user_service_id()
    )
  )
);

create policy "demandes_insert_self"
on public.demandes_conges for insert
with check (user_id = auth.uid());

create policy "demandes_update_management"
on public.demandes_conges for update
using (
  public.current_user_role() = 'direction'
  or (
    public.current_user_role() = 'chef_service'
    and exists (
      select 1 from public.users u
      where u.id = demandes_conges.user_id
      and u.service_id = public.current_user_service_id()
    )
  )
)
with check (
  public.current_user_role() = 'direction'
  or (
    public.current_user_role() = 'chef_service'
    and exists (
      select 1 from public.users u
      where u.id = demandes_conges.user_id
      and u.service_id = public.current_user_service_id()
    )
  )
);

-- commentaires policies
create policy "commentaires_select_scope"
on public.commentaires_demandes for select
using (
  exists (
    select 1 from public.demandes_conges d
    join public.users u on u.id = d.user_id
    where d.id = commentaires_demandes.demande_id
      and (
        d.user_id = auth.uid()
        or public.current_user_role() = 'direction'
        or (
          public.current_user_role() = 'chef_service'
          and u.service_id = public.current_user_service_id()
        )
      )
  )
  and (
    type_commentaire = 'public'
    or public.current_user_role() in ('chef_service', 'direction')
  )
);

create policy "commentaires_insert_scope"
on public.commentaires_demandes for insert
with check (
  auteur_id = auth.uid()
  and (
    type_commentaire = 'public'
    or public.current_user_role() in ('chef_service', 'direction')
  )
);

-- historique policies
create policy "historique_select_management_or_self"
on public.historique_actions for select
using (
  user_id = auth.uid()
  or public.current_user_role() in ('chef_service', 'direction')
);

create policy "historique_insert_self"
on public.historique_actions for insert
with check (user_id = auth.uid());

-- services policies
create policy "services_read_authenticated"
on public.services for select
using (auth.role() = 'authenticated');
