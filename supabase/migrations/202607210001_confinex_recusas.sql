-- Confinex: permite recusar uma avaliação pendente sem apagar seu histórico.

alter table public.confinex_avaliacoes
  add column if not exists recusado_por uuid references auth.users(id),
  add column if not exists motivo_recusa text;

create or replace function public.recusar_negocio_confinex(
  p_avaliacao_id uuid,
  p_motivo text default null
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
     set status = 'cancelado',
         cancelado_em = now(),
         recusado_por = auth.uid(),
         motivo_recusa = nullif(trim(p_motivo), '')
   where id = p_avaliacao_id
     and status = 'rascunho'
  returning id into v_avaliacao_id;

  if v_avaliacao_id is null then
    raise exception 'Avaliação inexistente ou fora da fila de aprovação';
  end if;

  return v_avaliacao_id;
end;
$$;

grant execute on function public.recusar_negocio_confinex(uuid, text)
  to authenticated;

comment on function public.recusar_negocio_confinex(uuid, text) is
  'Recusa uma avaliação em rascunho, preservando-a como cancelada para auditoria.';
