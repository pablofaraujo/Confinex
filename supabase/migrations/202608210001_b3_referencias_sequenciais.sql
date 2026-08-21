-- Referência humana e estável para cada negócio de bolsa.
-- Formato: B3-AA-NNN (ex.: B3-26-001).
-- A migração altera somente metadados das posições e não cria operações,
-- compras, vendas, abates ou movimentações financeiras.

begin;

alter table public.posicoes_hedge
  add column if not exists referencia_bolsa text;

comment on column public.posicoes_hedge.referencia_bolsa is
  'Código humano imutável do negócio de bolsa, usado na mesa e no cruzamento de mensagens: B3-AA-NNN.';

create table if not exists public.referencias_bolsa_contadores (
  ano smallint primary key,
  ultimo_numero integer not null check (ultimo_numero >= 0),
  atualizado_em timestamptz not null default now()
);

comment on table public.referencias_bolsa_contadores is
  'Contador interno anual das referências B3. Não contém dados operacionais.';

alter table public.referencias_bolsa_contadores enable row level security;
revoke all on table public.referencias_bolsa_contadores from public, anon, authenticated;

do $$
begin
  if not exists (
    select 1
      from pg_constraint
     where conrelid = 'public.posicoes_hedge'::regclass
       and conname = 'posicoes_hedge_referencia_bolsa_formato_ck'
  ) then
    alter table public.posicoes_hedge
      add constraint posicoes_hedge_referencia_bolsa_formato_ck
      check (
        referencia_bolsa is null
        or referencia_bolsa ~ '^B3-[0-9]{2}-[0-9]{3,}$'
      );
  end if;
end $$;

create unique index if not exists posicoes_hedge_referencia_bolsa_uidx
  on public.posicoes_hedge (referencia_bolsa)
  where referencia_bolsa is not null;

-- Numera o legado de modo reprodutível: primeiro pela data de entrada e,
-- na falta dela, pela criação. O contador existente do ano é respeitado para
-- que uma reaplicação nunca renumere nem colida com referências anteriores.
with existentes as (
  select
    substring(referencia_bolsa from '^B3-([0-9]{2})-')::integer as ano_curto,
    max(substring(referencia_bolsa from '-([0-9]+)$')::integer) as ultimo
  from public.posicoes_hedge
  where referencia_bolsa is not null
  group by 1
), numeradas as (
  select
    p.id,
    extract(year from coalesce(p.data_entrada, p.created_at::date, current_date))::integer % 100 as ano_curto,
    row_number() over (
      partition by extract(year from coalesce(p.data_entrada, p.created_at::date, current_date))::integer % 100
      order by coalesce(p.data_entrada, p.created_at::date, current_date), p.created_at, p.id
    ) as numero
  from public.posicoes_hedge p
  where p.referencia_bolsa is null
), finais as (
  select
    n.id,
    n.ano_curto,
    n.numero + coalesce(e.ultimo, 0) as numero
  from numeradas n
  left join existentes e using (ano_curto)
)
update public.posicoes_hedge p
   set referencia_bolsa = format(
     'B3-%s-%s',
     lpad(f.ano_curto::text, 2, '0'),
     lpad(f.numero::text, 3, '0')
   )
  from finais f
 where p.id = f.id;

insert into public.referencias_bolsa_contadores (ano, ultimo_numero)
select
  substring(referencia_bolsa from '^B3-([0-9]{2})-')::smallint,
  max(substring(referencia_bolsa from '-([0-9]+)$')::integer)
from public.posicoes_hedge
where referencia_bolsa is not null
group by 1
on conflict (ano) do update
set ultimo_numero = greatest(
      public.referencias_bolsa_contadores.ultimo_numero,
      excluded.ultimo_numero
    ),
    atualizado_em = now();

create or replace function public.preencher_referencia_bolsa()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_ano smallint;
  v_numero integer;
begin
  if tg_op = 'UPDATE' then
    if old.referencia_bolsa is distinct from new.referencia_bolsa then
      raise exception 'A referência do negócio de bolsa não pode ser alterada.';
    end if;
    return new;
  end if;

  if new.referencia_bolsa is not null then
    raise exception 'A referência do negócio de bolsa é criada automaticamente.';
  end if;

  v_ano := (extract(year from coalesce(new.data_entrada, current_date))::integer % 100)::smallint;

  insert into public.referencias_bolsa_contadores (ano, ultimo_numero)
  values (v_ano, 1)
  on conflict (ano) do update
  set ultimo_numero = public.referencias_bolsa_contadores.ultimo_numero + 1,
      atualizado_em = now()
  returning ultimo_numero into v_numero;

  new.referencia_bolsa := format(
    'B3-%s-%s',
    lpad(v_ano::text, 2, '0'),
    lpad(v_numero::text, 3, '0')
  );
  return new;
end;
$$;

revoke all on function public.preencher_referencia_bolsa() from public;

drop trigger if exists preencher_referencia_bolsa_tg on public.posicoes_hedge;
create trigger preencher_referencia_bolsa_tg
before insert or update of referencia_bolsa on public.posicoes_hedge
for each row execute function public.preencher_referencia_bolsa();

commit;
