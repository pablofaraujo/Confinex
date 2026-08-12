-- Catálogo pessoal de bases de confinamento acessível em qualquer aparelho.
-- Migração aditiva: não lê, move, altera ou apaga bases mantidas no navegador.

create table if not exists public.confinex_bases (
  criado_por uuid not null default auth.uid() references auth.users(id) on delete cascade,
  chave text not null,
  nome text not null,
  dados jsonb not null,
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now(),
  primary key (criado_por, chave),
  constraint confinex_bases_chave_valida check (length(trim(chave)) > 0),
  constraint confinex_bases_nome_valido check (length(trim(nome)) > 0),
  constraint confinex_bases_dados_objeto check (jsonb_typeof(dados) = 'object')
);

alter table public.confinex_bases enable row level security;

drop policy if exists confinex_bases_do_usuario on public.confinex_bases;
create policy confinex_bases_do_usuario
  on public.confinex_bases
  for all
  to authenticated
  using (criado_por = auth.uid())
  with check (criado_por = auth.uid());

grant select, insert, update, delete on public.confinex_bases to authenticated;

create or replace function public.salvar_base_confinex(
  p_chave text,
  p_nome text,
  p_dados jsonb,
  p_atualizado_em timestamptz
)
returns table (chave text, nome text, dados jsonb, atualizado_em timestamptz)
language plpgsql
security invoker
set search_path = public
as $$
begin
  if auth.uid() is null then
    raise exception 'Acesso autenticado obrigatório';
  end if;
  if nullif(trim(p_chave), '') is null or nullif(trim(p_nome), '') is null then
    raise exception 'Identificação e nome da base são obrigatórios';
  end if;
  if p_dados is null or jsonb_typeof(p_dados) <> 'object' then
    raise exception 'Dados da base inválidos';
  end if;

  insert into public.confinex_bases as atual
    (criado_por, chave, nome, dados, atualizado_em)
  values
    (auth.uid(), trim(p_chave), trim(p_nome), p_dados, coalesce(p_atualizado_em, now()))
  on conflict (criado_por, chave) do update
    set nome = excluded.nome,
        dados = excluded.dados,
        atualizado_em = excluded.atualizado_em
    where excluded.atualizado_em >= atual.atualizado_em;

  return query
  select b.chave, b.nome, b.dados, b.atualizado_em
    from public.confinex_bases b
   where b.criado_por = auth.uid()
     and b.chave = trim(p_chave);
end;
$$;

grant execute on function public.salvar_base_confinex(text, text, jsonb, timestamptz)
  to authenticated;

comment on table public.confinex_bases is
  'Bases pessoais do simulador Confinex sincronizadas entre aparelhos; não são negócios nem dados operacionais.';
comment on column public.confinex_bases.dados is
  'Premissas reutilizáveis de um confinamento. Não contém resultado operacional nem promove negócio.';
comment on function public.salvar_base_confinex(text, text, jsonb, timestamptz) is
  'Salva a versão mais recente de uma base e preserva a versão online quando um aparelho envia cópia mais antiga.';
