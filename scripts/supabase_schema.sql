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

alter table public.google_tokens enable row level security;
alter table public.digest_subscribers enable row level security;
alter table public.app_state enable row level security;

revoke all on table public.google_tokens from anon, authenticated;
revoke all on table public.digest_subscribers from anon, authenticated;
revoke all on table public.app_state from anon, authenticated;
