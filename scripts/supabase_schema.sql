create table if not exists public.google_tokens (
    user_id bigint primary key,
    token_json jsonb not null,
    updated_at timestamptz not null default now()
);

create table if not exists public.digest_subscribers (
    user_id bigint primary key,
    chat_id bigint not null,
    updated_at timestamptz not null default now()
);

create table if not exists public.app_state (
    key text primary key,
    value jsonb not null,
    updated_at timestamptz not null default now()
);

create table if not exists public.user_profiles (
    user_id bigint primary key,
    chat_id bigint not null,
    timezone text not null,
    digest_morning_hour smallint not null check (digest_morning_hour between 0 and 23),
    digest_evening_hour smallint not null check (digest_evening_hour between 0 and 23),
    updated_at timestamptz not null default now()
);

create table if not exists public.google_auth_sessions (
    user_id bigint primary key,
    state text not null unique,
    redirect_uri text not null,
    code_verifier text not null,
    created_at timestamptz not null default now()
);

alter table public.google_tokens enable row level security;
alter table public.digest_subscribers enable row level security;
alter table public.app_state enable row level security;
alter table public.user_profiles enable row level security;
alter table public.google_auth_sessions enable row level security;

revoke all on table public.google_tokens from anon, authenticated;
revoke all on table public.digest_subscribers from anon, authenticated;
revoke all on table public.app_state from anon, authenticated;
revoke all on table public.user_profiles from anon, authenticated;
revoke all on table public.google_auth_sessions from anon, authenticated;
