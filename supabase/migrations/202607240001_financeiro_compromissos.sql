-- RASCUNHO NÃO APLICADO — modelo aditivo para homologação do Financeiro.
-- Não importa, altera ou apaga registros das tabelas operacionais existentes.

create extension if not exists pgcrypto;

create table if not exists public.financeiro_compromissos (
  id uuid primary key default gen_random_uuid(),
  natureza text not null check (natureza in ('pagar', 'receber')),
  descricao text not null check (length(trim(descricao)) > 0),
  contraparte_nome text,
  valor_original numeric(16,2) not null check (valor_original > 0),
  data_emissao date,
  vencimento date not null,
  status text not null default 'previsto'
    check (status in ('previsto', 'parcial', 'realizado', 'renegociado', 'cancelado')),
  origem_tipo text not null
    check (origem_tipo in ('compra', 'venda', 'confinamento', 'emprestimo', 'promissoria', 'outro')),
  origem_referencia text,
  origem_id text,
  operacao_id uuid,
  observacao text,
  criado_por uuid references auth.users(id),
  criado_em timestamptz not null default now(),
  atualizado_em timestamptz not null default now()
);

create table if not exists public.financeiro_parcelas (
  id uuid primary key default gen_random_uuid(),
  compromisso_id uuid not null references public.financeiro_compromissos(id),
  numero integer not null check (numero > 0),
  valor numeric(16,2) not null check (valor > 0),
  vencimento date not null,
  status text not null default 'prevista'
    check (status in ('prevista', 'parcial', 'paga', 'renegociada', 'cancelada')),
  criado_em timestamptz not null default now(),
  unique (compromisso_id, numero)
);

create table if not exists public.financeiro_pagamentos (
  id uuid primary key default gen_random_uuid(),
  parcela_id uuid not null references public.financeiro_parcelas(id),
  valor numeric(16,2) not null check (valor > 0),
  data_pagamento date not null,
  transacao_banco_ref text,
  observacao text,
  registrado_por uuid references auth.users(id),
  registrado_em timestamptz not null default now()
);

create table if not exists public.financeiro_renegociacoes (
  id uuid primary key default gen_random_uuid(),
  compromisso_id uuid not null references public.financeiro_compromissos(id),
  vencimento_anterior date,
  vencimento_novo date not null,
  valor_anterior numeric(16,2) check (valor_anterior is null or valor_anterior > 0),
  valor_novo numeric(16,2) not null check (valor_novo > 0),
  motivo text not null check (length(trim(motivo)) > 0),
  registrado_por uuid references auth.users(id),
  registrado_em timestamptz not null default now()
);

create table if not exists public.financeiro_lembretes (
  id uuid primary key default gen_random_uuid(),
  compromisso_id uuid not null references public.financeiro_compromissos(id),
  antecedencia_dias integer not null default 7 check (antecedencia_dias >= 0),
  canal text not null default 'painel' check (canal in ('painel', 'telegram', 'email')),
  status text not null default 'ativo'
    check (status in ('ativo', 'enviado', 'dispensado')),
  ultimo_envio_em timestamptz,
  criado_por uuid references auth.users(id),
  criado_em timestamptz not null default now(),
  unique (compromisso_id, antecedencia_dias, canal)
);

create index if not exists financeiro_compromissos_vencimento_idx
  on public.financeiro_compromissos (status, vencimento);
create index if not exists financeiro_compromissos_origem_idx
  on public.financeiro_compromissos (origem_tipo, origem_referencia);
create index if not exists financeiro_parcelas_vencimento_idx
  on public.financeiro_parcelas (status, vencimento);
create index if not exists financeiro_pagamentos_parcela_idx
  on public.financeiro_pagamentos (parcela_id, data_pagamento);

create or replace view public.v_financeiro_compromissos
with (security_invoker = true)
as
select
  compromisso.*,
  coalesce(parcelas.total_parcelado, 0) as total_parcelado,
  coalesce(pagamentos.total_pago, 0) as total_pago,
  greatest(compromisso.valor_original - coalesce(pagamentos.total_pago, 0), 0) as saldo_aberto,
  parcelas.proximo_vencimento,
  coalesce(parcelas.quantidade, 0) as quantidade_parcelas,
  coalesce(parcelas.pagas, 0) as parcelas_pagas
from public.financeiro_compromissos compromisso
left join lateral (
  select
    sum(parcela.valor) as total_parcelado,
    min(parcela.vencimento) filter (where parcela.status in ('prevista', 'parcial')) as proximo_vencimento,
    count(*) as quantidade,
    count(*) filter (where parcela.status = 'paga') as pagas
  from public.financeiro_parcelas parcela
  where parcela.compromisso_id = compromisso.id
) parcelas on true
left join lateral (
  select sum(pagamento.valor) as total_pago
  from public.financeiro_parcelas parcela
  join public.financeiro_pagamentos pagamento on pagamento.parcela_id = parcela.id
  where parcela.compromisso_id = compromisso.id
) pagamentos on true;

alter table public.financeiro_compromissos enable row level security;
alter table public.financeiro_parcelas enable row level security;
alter table public.financeiro_pagamentos enable row level security;
alter table public.financeiro_renegociacoes enable row level security;
alter table public.financeiro_lembretes enable row level security;

drop policy if exists financeiro_compromissos_leitura on public.financeiro_compromissos;
create policy financeiro_compromissos_leitura
  on public.financeiro_compromissos for select to authenticated using (true);
drop policy if exists financeiro_parcelas_leitura on public.financeiro_parcelas;
create policy financeiro_parcelas_leitura
  on public.financeiro_parcelas for select to authenticated using (true);
drop policy if exists financeiro_pagamentos_leitura on public.financeiro_pagamentos;
create policy financeiro_pagamentos_leitura
  on public.financeiro_pagamentos for select to authenticated using (true);
drop policy if exists financeiro_renegociacoes_leitura on public.financeiro_renegociacoes;
create policy financeiro_renegociacoes_leitura
  on public.financeiro_renegociacoes for select to authenticated using (true);
drop policy if exists financeiro_lembretes_leitura on public.financeiro_lembretes;
create policy financeiro_lembretes_leitura
  on public.financeiro_lembretes for select to authenticated using (true);

grant select on public.financeiro_compromissos to authenticated;
grant select on public.financeiro_parcelas to authenticated;
grant select on public.financeiro_pagamentos to authenticated;
grant select on public.financeiro_renegociacoes to authenticated;
grant select on public.financeiro_lembretes to authenticated;
grant select on public.v_financeiro_compromissos to authenticated;
