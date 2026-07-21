-- Confinex: separa a submissão externa da aprovação operacional no aplicativo.

alter table public.confinex_avaliacoes
  add column if not exists aprovado_por uuid references auth.users(id),
  add column if not exists aprovado_em timestamptz;

create or replace function public.submeter_negocio_confinex(
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
  if nullif(trim(p_nome), '') is null then
    raise exception 'Nome do negócio é obrigatório';
  end if;
  if nullif(trim(p_grupo_origem_nome), '') is null then
    raise exception 'Grupo de origem é obrigatório';
  end if;

  select id into v_operacao_id
    from public.operacoes
   where upper(codigo) = upper(trim(p_codigo))
   limit 1;

  insert into public.confinex_avaliacoes (
    codigo, nome, grupo_origem_id, grupo_origem_nome, status,
    operacao_id, criado_por
  ) values (
    upper(trim(p_codigo)), trim(p_nome), nullif(trim(p_grupo_origem_id), ''),
    trim(p_grupo_origem_nome), 'rascunho', v_operacao_id, auth.uid()
  ) returning id into v_avaliacao_id;

  insert into public.confinex_estimativas (
    avaliacao_id, versao, tipo, premissas, resultado, criado_por
  ) values (
    v_avaliacao_id, 1, 'original', p_premissas, p_resultado, auth.uid()
  );

  return v_avaliacao_id;
end;
$$;

create or replace function public.aprovar_negocio_confinex(
  p_avaliacao_id uuid
)
returns uuid
language plpgsql
security invoker
set search_path = public
as $$
declare
  v_avaliacao_id uuid;
begin
  update public.confinex_avaliacoes
     set status = 'iniciado',
         aprovado_por = auth.uid(),
         aprovado_em = now()
   where id = p_avaliacao_id
     and status = 'rascunho'
  returning id into v_avaliacao_id;

  if v_avaliacao_id is null then
    raise exception 'Avaliação inexistente ou fora da fila de aprovação';
  end if;

  return v_avaliacao_id;
end;
$$;

grant execute on function public.submeter_negocio_confinex(text, text, text, text, jsonb, jsonb)
  to authenticated;
grant execute on function public.aprovar_negocio_confinex(uuid)
  to authenticated;

comment on function public.submeter_negocio_confinex(text, text, text, text, jsonb, jsonb) is
  'Recebe uma avaliação externa como rascunho para aprovação posterior no Confinex.';
comment on function public.aprovar_negocio_confinex(uuid) is
  'Aprova uma avaliação em rascunho e inicia o negócio no Confinex.';
