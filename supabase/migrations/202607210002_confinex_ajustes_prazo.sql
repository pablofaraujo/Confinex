-- Confinex: histórico auditável de ajustes do prazo operacional.
-- A estimativa original permanece imutável; cada alteração registra antes/depois e motivo.

create table if not exists public.confinex_ajustes_prazo (
  id uuid primary key default gen_random_uuid(),
  avaliacao_id uuid not null references public.confinex_avaliacoes(id) on delete cascade,
  dias_anterior integer not null check (dias_anterior > 0),
  dias_novo integer not null check (dias_novo > 0),
  data_saida_anterior date,
  data_saida_nova date,
  motivo text not null check (length(trim(motivo)) > 0),
  ajustado_por uuid references auth.users(id),
  ajustado_em timestamptz not null default now()
);

create index if not exists confinex_ajustes_prazo_avaliacao_idx
  on public.confinex_ajustes_prazo (avaliacao_id, ajustado_em desc);

alter table public.confinex_ajustes_prazo enable row level security;

drop policy if exists confinex_autenticado on public.confinex_ajustes_prazo;
create policy confinex_autenticado
  on public.confinex_ajustes_prazo
  for all to authenticated
  using (true)
  with check (true);

create or replace function public.ajustar_prazo_confinex(
  p_avaliacao_id uuid,
  p_dias_novo integer,
  p_motivo text
)
returns uuid
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_status text;
  v_versao integer;
  v_premissas jsonb;
  v_resultado jsonb;
  v_data_entrada date;
  v_dias_original integer;
  v_dias_anterior integer;
  v_saida_anterior date;
  v_saida_nova date;
  v_ajuste_id uuid;
begin
  if p_dias_novo is null or p_dias_novo <= 0 then
    raise exception 'O novo prazo deve ser maior que zero';
  end if;
  if nullif(trim(p_motivo), '') is null then
    raise exception 'O motivo do ajuste é obrigatório';
  end if;

  select status, estimativa_versao_atual
    into v_status, v_versao
    from public.confinex_avaliacoes
   where id = p_avaliacao_id
   for update;

  if v_status is null then
    raise exception 'Avaliação não encontrada';
  end if;
  if v_status <> 'iniciado' then
    raise exception 'Somente negócios iniciados podem ter o prazo ajustado';
  end if;

  select premissas, resultado
    into v_premissas, v_resultado
    from public.confinex_estimativas
   where avaliacao_id = p_avaliacao_id
     and versao = v_versao;

  v_data_entrada := coalesce(
    nullif(v_premissas #>> '{cenario,dataEntrada}', '')::date,
    nullif(v_premissas ->> 'dataEntrada', '')::date
  );
  v_dias_original := coalesce(
    nullif(v_premissas #>> '{cenario,diasCiclo}', '')::numeric::integer,
    nullif(v_premissas ->> 'diasCiclo', '')::numeric::integer,
    greatest(
      coalesce(nullif(v_resultado ->> 'diasTotal', '')::numeric::integer, 0)
      - coalesce(nullif(v_resultado ->> 'diasPag', '')::numeric::integer, 0),
      1
    )
  );

  select dias_novo, data_saida_nova
    into v_dias_anterior, v_saida_anterior
    from public.confinex_ajustes_prazo
   where avaliacao_id = p_avaliacao_id
   order by ajustado_em desc, id desc
   limit 1;

  v_dias_anterior := coalesce(v_dias_anterior, v_dias_original);
  v_saida_anterior := coalesce(v_saida_anterior, v_data_entrada + v_dias_anterior);
  v_saida_nova := case when v_data_entrada is null then null else v_data_entrada + p_dias_novo end;

  if p_dias_novo = v_dias_anterior then
    raise exception 'O novo prazo é igual ao prazo operacional atual';
  end if;

  insert into public.confinex_ajustes_prazo (
    avaliacao_id, dias_anterior, dias_novo,
    data_saida_anterior, data_saida_nova, motivo, ajustado_por
  ) values (
    p_avaliacao_id, v_dias_anterior, p_dias_novo,
    v_saida_anterior, v_saida_nova, trim(p_motivo), auth.uid()
  ) returning id into v_ajuste_id;

  return v_ajuste_id;
end;
$$;

grant execute on function public.ajustar_prazo_confinex(uuid, integer, text)
  to authenticated;

comment on table public.confinex_ajustes_prazo is
  'Histórico dos prazos operacionais do negócio, sem sobrescrever a estimativa original ou suas revisões.';
comment on function public.ajustar_prazo_confinex(uuid, integer, text) is
  'Altera o prazo operacional corrente de um negócio iniciado e preserva o histórico completo da mudança.';
