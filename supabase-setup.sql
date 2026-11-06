-- مشكاة: إعداد قاعدة البيانات. الصقه كامل في SQL Editor واضغط Run مرة وحدة.
create table public.midad_admins (
  user_id uuid primary key references auth.users on delete cascade
);
create table public.midad_books (
  id text primary key,
  title text not null,
  format text not null check (format in ('pdf', 'epub')),
  storage_path text not null,
  packed text,
  size bigint,
  added_at timestamptz not null default now(),
  added_by uuid default auth.uid() references auth.users on delete set null
);
create table public.midad_user_books (
  user_id uuid not null default auth.uid() references auth.users on delete cascade,
  book_id text not null,
  state jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (user_id, book_id)
);
create table public.midad_ai_usage (
  id bigserial primary key,
  user_id uuid not null default auth.uid() references auth.users on delete cascade,
  created_at timestamptz not null default now()
);
create index midad_ai_usage_user_time on public.midad_ai_usage (user_id, created_at);
create index midad_books_added_by on public.midad_books (added_by);

create function public.midad_is_admin() returns boolean
language sql stable security definer set search_path = ''
as $$ select exists (select 1 from public.midad_admins where user_id = (select auth.uid())) $$;
revoke execute on function public.midad_is_admin() from anon, public;
grant execute on function public.midad_is_admin() to authenticated;

alter table public.midad_admins enable row level security;
alter table public.midad_books enable row level security;
alter table public.midad_user_books enable row level security;
alter table public.midad_ai_usage enable row level security;

create policy "admins see themselves" on public.midad_admins for select to authenticated using (user_id = (select auth.uid()));
create policy "signed-in users read the library" on public.midad_books for select to authenticated using (true);
create policy "admins add books" on public.midad_books for insert to authenticated with check ((select public.midad_is_admin()));
create policy "admins edit books" on public.midad_books for update to authenticated using ((select public.midad_is_admin())) with check ((select public.midad_is_admin()));
create policy "admins delete books" on public.midad_books for delete to authenticated using ((select public.midad_is_admin()));
create policy "own reading state" on public.midad_user_books for all to authenticated using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy "own usage read" on public.midad_ai_usage for select to authenticated using (user_id = (select auth.uid()));
create policy "own usage insert" on public.midad_ai_usage for insert to authenticated with check (user_id = (select auth.uid()));

-- the owner's accounts become admins when they sign up
create function public.midad_grant_owner() returns trigger
language plpgsql security definer set search_path = ''
as $$
begin
  if lower(new.email) in ('ali.alqattan.aa@hotmail.com', 'ali.alqattan.aa@gmail.com') then
    insert into public.midad_admins (user_id) values (new.id) on conflict do nothing;
  end if;
  return new;
end $$;
revoke execute on function public.midad_grant_owner() from anon, authenticated, public;
create trigger midad_grant_owner after insert on auth.users for each row execute function public.midad_grant_owner();

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('midad-books', 'midad-books', false, 52428800, array['application/pdf', 'application/json']);
create policy "midad: signed-in users read books" on storage.objects for select to authenticated using (bucket_id = 'midad-books');
create policy "midad: admins upload books" on storage.objects for insert to authenticated with check (bucket_id = 'midad-books' and (select public.midad_is_admin()));
create policy "midad: admins update books" on storage.objects for update to authenticated using (bucket_id = 'midad-books' and (select public.midad_is_admin()));
create policy "midad: admins delete books" on storage.objects for delete to authenticated using (bucket_id = 'midad-books' and (select public.midad_is_admin()));

alter publication supabase_realtime add table public.midad_books;

select 'تم إعداد مشكاة' as result;
