-- Run once in Supabase Dashboard > SQL Editor.
create table if not exists public.careerlens_users (
    user_id text primary key,
    username text not null,
    username_lookup text not null unique,
    display_name text not null,
    password_salt text not null,
    password_hash text not null,
    password_iterations integer not null,
    created_at timestamptz not null default now(),
    is_active boolean not null default true
);

alter table public.careerlens_users enable row level security;

comment on table public.careerlens_users is
    'CareerLens server-side accounts. Access only with a Supabase secret key.';
