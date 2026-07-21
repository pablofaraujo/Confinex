-- Confinex: avaliações, estimativas versionadas e consolidação previsto x realizado.
-- Aplicar primeiro no projeto de testes; promover para produção após homologação.

create extension if not exists pgcrypto;

create table if not exists public.confinex_avaliacoes (
  id uuid primary key default gen_random_uuid(),
  codigo text not null unique,
  nome text not null,
  grupo_origem_id text,
  grupo_origem_nome text not null,
  status text not null default 'iniciado'
    check (status in ('rascunho', 'iniciado', 'consolidado', 'cancelado')),
  operacao_id uuid,
  estimativa_versao_atual integer not null default 1,
  criado_por uuid references auth.users(id),
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now(),
  consolidado_em timestamptz,
  cancelado_em timestamptz,
  constraint confinex_avaliacoes_grupo_origem check (length(trim(grupo_origem_nome)) > 0)
);

create table if not exists public.confinex_estimativas (
  id uuid primary key default gen_random_uuid(),
  avaliacao_id uuid not null references public.confinex_avaliacoes(id) on delete cascade,
  versao integer not null,
  tipo text not null default 'original' check (tipo in ('original', 'revisao')),
  premissas jsonb not null,
  resultado jsonb not null,
  motivo_revisao text,
  criado_por uuid references auth.users(id),
  criado_em timestamptz not null default now(),
  unique (avaliacao_id, versao)
);

create table if not exists public.confinex_testes (
  id uuid primary key default gen_random_uuid(),
  nome text not null,
  dispositivo text,
  estado jsonb not null,
  criado_por uuid references auth.users(id),
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now()
);

create table if not exists public.confinex_consolidacoes (
  id uuid primary key default gen_random_uuid(),
  avaliacao_id uuid not null unique references public.confinex_avaliacoes(id) on delete cascade,
  estimativa_versao integer not null,
  realizado jsonb not null,
  resultado_final jsonb not null,
  comentario_geral text,
  consolidado_por uuid references auth.users(id),
  consolidado_em timestamptz not null default now()
);

create table if not exists public.confinex_desvios (
  id uuid primary key default gen_random_uuid(),
  consolidacao_id uuid not null references public.confinex_consolidacoes(id) on delete cascade,
  indicador text not null,
  natureza text not null check (natureza in ('custo', 'receita', 'resultado', 'prazo', 'zootecnico')),
  estimado numeric,
  realizado numeric,
  desvio numeric generated always as (realizado - estimado) stored,
  desvio_percentual numeric generated always as (
    case when estimado is null or estimado = 0 then null
         else ((realizado - estimado) / abs(estimado)) * 100 end
  ) stored,
  classificacao text check (classificacao in ('favoravel', 'neutro', 'desfavoravel')),
  comentario_automatico text,
  comentario_manual text,
  material boolean not null default false,
  unique (consolidacao_id, indicador)
);

create index if not exists confinex_avaliacoes_status_idx
  on public.confinex_avaliacoes (status, atualizado_em desc);
create index if not exists confinex_avaliacoes_grupo_idx
  on public.confinex_avaliacoes (grupo_origem_id, grupo_origem_nome);
create index if not exists confinex_estimativas_avaliacao_idx
  on public.confinex_estimativas (avaliacao_id, versao desc);

create or replace function public.confinex_atualizar_timestamp()
returns trigger
language plpgsql
as $$
begin
  new.atualizado_em = now();
  return new;
end;
$$;

drop trigger if exists confinex_avaliacoes_atualizado_em on public.confinex_avaliacoes;
create trigger confinex_avaliacoes_atualizado_em
before update on public.confinex_avaliacoes
for each row execute function public.confinex_atualizar_timestamp();

drop trigger if exists confinex_testes_atualizado_em on public.confinex_testes;
create trigger confinex_testes_atualizado_em
before update on public.confinex_testes
for each row execute function public.confinex_atualizar_timestamp();

create or replace function public.iniciar_negocio_confinex(
  p_codigo text,
  p_nome text,
  p_grupo_origem_id text,
  p_grupo_origem_nome text,
  p_premissas jsonb,
  p_resultado jsonb
)
returns uuid
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_avaliacao_id uuid;
  v_operacao_id uuid;
begin
  if nullif(trim(p_codigo), '') is null then
    raise exception 'Código do negócio é obrigatório';
  end if;
  if nullif(trim(p_grupo_origem_nome), '') is null then
    raise exception 'Grupo de origem é obrigatório';
  end if;

  select id into v_operacao_id
    from public.operacoes
   where upper(codigo) = upper(trim(p_codigo))
   limit 1;

  insert into public.confinex_avaliacoes (
    codigo, nome, grupo_origem_id, grupo_origem_nome, operacao_id, criado_por
  ) values (
    upper(trim(p_codigo)), trim(p_nome), nullif(trim(p_grupo_origem_id), ''),
    trim(p_grupo_origem_nome), v_operacao_id, auth.uid()
  ) returning id into v_avaliacao_id;

  insert into public.confinex_estimativas (
    avaliacao_id, versao, tipo, premissas, resultado, criado_por
  ) values (
    v_avaliacao_id, 1, 'original', p_premissas, p_resultado, auth.uid()
  );

  return v_avaliacao_id;
end;
$$;

create or replace function public.consolidar_negocio_confinex(
  p_avaliacao_id uuid,
  p_realizado jsonb,
  p_comentario_geral text default null
)
returns uuid
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_versao integer;
  v_estimado jsonb;
  v_consolidacao_id uuid;
begin
  select estimativa_versao_atual
    into v_versao
    from public.confinex_avaliacoes
   where id = p_avaliacao_id and status <> 'cancelado'
   for update;

  if v_versao is null then
    raise exception 'Avaliação inexistente ou cancelada';
  end if;

  select resultado into v_estimado
    from public.confinex_estimativas
   where avaliacao_id = p_avaliacao_id and versao = v_versao;

  insert into public.confinex_consolidacoes (
    avaliacao_id, estimativa_versao, realizado, resultado_final,
    comentario_geral, consolidado_por
  ) values (
    p_avaliacao_id, v_versao, p_realizado, p_realizado,
    nullif(trim(p_comentario_geral), ''), auth.uid()
  )
  on conflict (avaliacao_id) do update set
    estimativa_versao = excluded.estimativa_versao,
    realizado = excluded.realizado,
    resultado_final = excluded.resultado_final,
    comentario_geral = excluded.comentario_geral,
    consolidado_por = excluded.consolidado_por,
    consolidado_em = now()
  returning id into v_consolidacao_id;

  delete from public.confinex_desvios where consolidacao_id = v_consolidacao_id;

  insert into public.confinex_desvios (
    consolidacao_id, indicador, natureza, estimado, realizado,
    classificacao, comentario_automatico, material
  )
  select
    v_consolidacao_id,
    item.indicador,
    item.natureza,
    item.estimado,
    item.realizado,
    case
      when item.estimado is null or item.realizado is null or item.realizado = item.estimado then 'neutro'
      when item.natureza in ('custo', 'prazo') and item.realizado < item.estimado then 'favoravel'
      when item.natureza in ('custo', 'prazo') then 'desfavoravel'
      when item.realizado > item.estimado then 'favoravel'
      else 'desfavoravel'
    end,
    case
      when item.estimado is null or item.realizado is null then 'Sem base suficiente para comparar.'
      when item.realizado = item.estimado then 'Realizado em linha com o estimado.'
      else format(
        '%s %s do previsto em %s%%.',
        item.indicador,
        case when item.realizado > item.estimado then 'acima' else 'abaixo' end,
        round(abs((item.realizado - item.estimado) / nullif(abs(item.estimado), 0) * 100), 1)
      )
    end,
    case
      when item.estimado is null or item.estimado = 0 or item.realizado is null then false
      else abs((item.realizado - item.estimado) / abs(item.estimado) * 100) >= 5
    end
  from (
    values
      ('Compra dos animais', 'custo', (v_estimado->>'custoCompra')::numeric, (p_realizado->>'custoCompra')::numeric),
      ('Frete', 'custo', (v_estimado->>'freteTotal')::numeric, (p_realizado->>'freteTotal')::numeric),
      ('Confinamento', 'custo', (v_estimado->>'custoCont')::numeric, (p_realizado->>'custoCont')::numeric),
      ('Custo do dinheiro', 'custo', (v_estimado->>'custoDinheiroTotal')::numeric, (p_realizado->>'custoDinheiroTotal')::numeric),
      ('Receita', 'receita', (v_estimado->>'receita')::numeric, (p_realizado->>'receita')::numeric),
      ('Lucro líquido', 'resultado', (v_estimado->>'lucroLiquido')::numeric, (p_realizado->>'lucroLiquido')::numeric),
      ('Rentabilidade total (%)', 'resultado', (v_estimado->>'rTliq')::numeric, (p_realizado->>'rTliq')::numeric),
      ('Prazo total (dias)', 'prazo', (v_estimado->>'diasTotal')::numeric, (p_realizado->>'diasTotal')::numeric),
      ('Peso de abate (kg/cab)', 'zootecnico', (v_estimado->>'pesoAbate')::numeric, (p_realizado->>'pesoAbate')::numeric)
  ) as item(indicador, natureza, estimado, realizado);

  update public.confinex_avaliacoes
     set status = 'consolidado', consolidado_em = now()
   where id = p_avaliacao_id;

  return v_consolidacao_id;
end;
$$;

create or replace function public.revisar_estimativa_confinex(
  p_avaliacao_id uuid,
  p_premissas jsonb,
  p_resultado jsonb,
  p_motivo text
)
returns integer
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_versao integer;
begin
  if nullif(trim(p_motivo), '') is null then
    raise exception 'Motivo da revisão é obrigatório';
  end if;

  update public.confinex_avaliacoes
     set estimativa_versao_atual = estimativa_versao_atual + 1
   where id = p_avaliacao_id and status = 'iniciado'
   returning estimativa_versao_atual into v_versao;

  if v_versao is null then
    raise exception 'Somente negócios iniciados podem receber revisão';
  end if;

  insert into public.confinex_estimativas (
    avaliacao_id, versao, tipo, premissas, resultado, motivo_revisao, criado_por
  ) values (
    p_avaliacao_id, v_versao, 'revisao', p_premissas, p_resultado, trim(p_motivo), auth.uid()
  );

  return v_versao;
end;
$$;

alter table public.confinex_avaliacoes enable row level security;
alter table public.confinex_estimativas enable row level security;
alter table public.confinex_testes enable row level security;
alter table public.confinex_consolidacoes enable row level security;
alter table public.confinex_desvios enable row level security;

do $$
declare
  tabela text;
begin
  foreach tabela in array array[
    'confinex_avaliacoes', 'confinex_estimativas', 'confinex_testes',
    'confinex_consolidacoes', 'confinex_desvios'
  ] loop
    execute format('drop policy if exists confinex_autenticado on public.%I', tabela);
    execute format(
      'create policy confinex_autenticado on public.%I for all to authenticated using (true) with check (true)',
      tabela
    );
  end loop;
end;
$$;

grant execute on function public.iniciar_negocio_confinex(text, text, text, text, jsonb, jsonb)
  to authenticated;
grant execute on function public.consolidar_negocio_confinex(uuid, jsonb, text)
  to authenticated;
grant execute on function public.revisar_estimativa_confinex(uuid, jsonb, jsonb, text)
  to authenticated;

comment on table public.confinex_avaliacoes is
  'Negócios iniciados no Confinex. Cancelados permanecem para auditoria e são ocultados das listas operacionais.';
comment on table public.confinex_estimativas is
  'Fotografias imutáveis das premissas e resultados estimados de cada negócio.';
comment on table public.confinex_testes is
  'Simulações livres do Confinex; usar projeto separado para homologação.';
comment on table public.confinex_desvios is
  'Comparação normalizada entre estimado e realizado, com comentários automáticos e manuais.';
