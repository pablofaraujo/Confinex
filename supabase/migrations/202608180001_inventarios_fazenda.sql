-- Fotografias físicas do rebanho da Fazenda, separadas do ledger de entradas
-- e saídas. A migração é exclusivamente estrutural e não importa inventários.

BEGIN;

CREATE TABLE IF NOT EXISTS public.inventarios_fazenda (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  unidade_codigo text NOT NULL CHECK (btrim(unidade_codigo) <> ''),
  fazenda_id uuid REFERENCES public.fazendas(id),
  data_referencia date NOT NULL,
  local_nome text NOT NULL CHECK (btrim(local_nome) <> ''),
  categoria text NOT NULL CHECK (btrim(categoria) <> ''),
  sexo text CHECK (sexo IS NULL OR sexo IN ('macho', 'femea', 'misto')),
  cabecas integer NOT NULL CHECK (cabecas > 0),
  peso_medio_kg numeric(12,3) NOT NULL CHECK (peso_medio_kg > 0),
  peso_total_kg numeric(14,3)
    GENERATED ALWAYS AS (cabecas * peso_medio_kg) STORED,
  fonte text NOT NULL CHECK (btrim(fonte) <> ''),
  observacoes text,
  idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
  criado_em timestamptz NOT NULL DEFAULT now(),
  criado_por text NOT NULL CHECK (btrim(criado_por) <> ''),
  UNIQUE (idempotency_key)
);

CREATE UNIQUE INDEX IF NOT EXISTS inventarios_fazenda_item_unico_idx
  ON public.inventarios_fazenda (
    unidade_codigo,
    data_referencia,
    lower(btrim(local_nome)),
    lower(btrim(categoria)),
    coalesce(sexo, 'nao_informado')
  );

CREATE INDEX IF NOT EXISTS inventarios_fazenda_referencia_idx
  ON public.inventarios_fazenda (unidade_codigo, data_referencia DESC);

CREATE OR REPLACE VIEW public.v_inventarios_fazenda_resumo
WITH (security_invoker = true)
AS
SELECT
  unidade_codigo,
  fazenda_id,
  data_referencia,
  sum(cabecas)::bigint AS cabecas_total,
  sum(peso_total_kg) AS peso_total_kg,
  CASE
    WHEN sum(cabecas) > 0 THEN sum(peso_total_kg) / sum(cabecas)
    ELSE NULL
  END AS peso_medio_kg,
  count(*)::bigint AS itens,
  min(criado_em) AS registrado_em
FROM public.inventarios_fazenda
GROUP BY unidade_codigo, fazenda_id, data_referencia;

ALTER TABLE public.inventarios_fazenda ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS inventarios_fazenda_authenticated_select
  ON public.inventarios_fazenda;
CREATE POLICY inventarios_fazenda_authenticated_select
  ON public.inventarios_fazenda
  FOR SELECT TO authenticated
  USING (true);

REVOKE ALL ON public.inventarios_fazenda FROM anon, authenticated;
REVOKE ALL ON public.v_inventarios_fazenda_resumo FROM anon, authenticated;
REVOKE ALL ON public.inventarios_fazenda FROM service_role;
REVOKE ALL ON public.v_inventarios_fazenda_resumo FROM service_role;

GRANT SELECT ON public.inventarios_fazenda TO authenticated;
GRANT SELECT ON public.v_inventarios_fazenda_resumo TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.inventarios_fazenda TO service_role;
GRANT SELECT ON public.v_inventarios_fazenda_resumo TO service_role;

COMMENT ON TABLE public.inventarios_fazenda IS
  'Fotografia física por local e categoria; não cria entrada, saída nem negócio.';
COMMENT ON COLUMN public.inventarios_fazenda.peso_medio_kg IS
  'Peso médio vivo por cabeça informado na contagem física.';
COMMENT ON COLUMN public.inventarios_fazenda.peso_total_kg IS
  'Peso vivo estimado, calculado por cabeças multiplicadas pelo peso médio.';
COMMENT ON COLUMN public.inventarios_fazenda.idempotency_key IS
  'Impede duplicar o mesmo item de um inventário em reexecuções.';
COMMENT ON VIEW public.v_inventarios_fazenda_resumo IS
  'Totais por unidade e data, sem alterar o saldo do ledger da Fazenda.';

COMMIT;
