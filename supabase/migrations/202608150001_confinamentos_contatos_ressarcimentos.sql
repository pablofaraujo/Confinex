-- Contatos por confinamento e valores operacionais pagos por uma parte
-- que ainda precisam ser ressarcidos ou descontados no acerto.

BEGIN;

CREATE TABLE IF NOT EXISTS public.confinamento_contatos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  confinamento_id uuid NOT NULL REFERENCES public.confinamentos(id),
  contato_id uuid NOT NULL REFERENCES public.contatos(id),
  papel text NOT NULL CHECK (papel IN ('confinamento', 'administrativo', 'intermediario', 'finpec', 'outro')),
  principal boolean NOT NULL DEFAULT false,
  obs text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (confinamento_id, contato_id, papel)
);

CREATE TABLE IF NOT EXISTS public.ressarcimentos_operacionais (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operacao_id uuid NOT NULL REFERENCES public.operacoes(id),
  contato_id uuid REFERENCES public.contatos(id),
  tipo text NOT NULL CHECK (tipo IN ('gta', 'vacina', 'transporte', 'outro')),
  descricao text NOT NULL CHECK (btrim(descricao) <> ''),
  valor numeric NOT NULL CHECK (valor >= 0),
  valor_ressarcido numeric NOT NULL DEFAULT 0 CHECK (valor_ressarcido >= 0),
  data_pagamento date,
  status text NOT NULL DEFAULT 'a_ressarcir' CHECK (status IN ('a_ressarcir', 'parcialmente_ressarcido', 'ressarcido', 'descontado', 'dispensado')),
  fonte text,
  obs text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CHECK (valor_ressarcido <= valor),
  UNIQUE (operacao_id, contato_id, tipo, descricao)
);

CREATE TABLE IF NOT EXISTS public.inventarios_confinamento (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  confinamento_id uuid NOT NULL REFERENCES public.confinamentos(id),
  data_referencia date NOT NULL,
  cabecas_total integer NOT NULL CHECK (cabecas_total >= 0),
  machos integer CHECK (machos IS NULL OR machos >= 0),
  femeas integer CHECK (femeas IS NULL OR femeas >= 0),
  fonte text NOT NULL CHECK (btrim(fonte) <> ''),
  obs text,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (confinamento_id, data_referencia),
  CHECK (coalesce(machos, 0) + coalesce(femeas, 0) <= cabecas_total)
);

CREATE INDEX IF NOT EXISTS confinamento_contatos_confinamento_idx
  ON public.confinamento_contatos (confinamento_id, principal DESC);
CREATE INDEX IF NOT EXISTS ressarcimentos_operacionais_abertos_idx
  ON public.ressarcimentos_operacionais (status, operacao_id);
CREATE INDEX IF NOT EXISTS inventarios_confinamento_referencia_idx
  ON public.inventarios_confinamento (confinamento_id, data_referencia DESC);

ALTER TABLE public.confinamento_contatos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.ressarcimentos_operacionais ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inventarios_confinamento ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS confinamento_contatos_authenticated_select ON public.confinamento_contatos;
CREATE POLICY confinamento_contatos_authenticated_select
  ON public.confinamento_contatos FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS ressarcimentos_operacionais_authenticated_select ON public.ressarcimentos_operacionais;
CREATE POLICY ressarcimentos_operacionais_authenticated_select
  ON public.ressarcimentos_operacionais FOR SELECT TO authenticated USING (true);

DROP POLICY IF EXISTS inventarios_confinamento_authenticated_select ON public.inventarios_confinamento;
CREATE POLICY inventarios_confinamento_authenticated_select
  ON public.inventarios_confinamento FOR SELECT TO authenticated USING (true);

REVOKE INSERT, UPDATE, DELETE ON public.confinamento_contatos FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE ON public.ressarcimentos_operacionais FROM anon, authenticated;
REVOKE INSERT, UPDATE, DELETE ON public.inventarios_confinamento FROM anon, authenticated;
GRANT SELECT ON public.confinamento_contatos TO authenticated;
GRANT SELECT ON public.ressarcimentos_operacionais TO authenticated;
GRANT SELECT ON public.inventarios_confinamento TO authenticated;

COMMENT ON TABLE public.confinamento_contatos IS
  'Contatos operacionais e administrativos associados a cada confinamento.';
COMMENT ON TABLE public.ressarcimentos_operacionais IS
  'Despesas adiantadas a recuperar da contraparte ou descontar no acerto.';
COMMENT ON TABLE public.inventarios_confinamento IS
  'Contagens físicas datadas para reconciliar o saldo por entradas, compras e abates.';

COMMIT;
