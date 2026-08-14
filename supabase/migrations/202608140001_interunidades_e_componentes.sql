-- Estrutura auditável para compras compostas, negócios da fazenda e
-- transferências econômicas entre Fazenda e Confinamento.
-- Migração exclusivamente estrutural: não cria nem altera dados operacionais.

BEGIN;

CREATE TABLE IF NOT EXISTS public.compras_componentes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  compra_agregada_id uuid NOT NULL REFERENCES public.compras(id),
  vendedor_id uuid REFERENCES public.contatos(id),
  corretor_id uuid REFERENCES public.contatos(id),
  data_negociacao date,
  quantidade integer NOT NULL CHECK (quantidade > 0),
  peso_total_kg numeric CHECK (peso_total_kg IS NULL OR peso_total_kg > 0),
  preco_arroba numeric CHECK (preco_arroba IS NULL OR preco_arroba > 0),
  valor_total numeric CHECK (valor_total IS NULL OR valor_total >= 0),
  chave_rastreio text NOT NULL CHECK (btrim(chave_rastreio) <> ''),
  fonte text,
  dados_origem jsonb NOT NULL DEFAULT '{}'::jsonb,
  observacoes text,
  criado_em timestamptz NOT NULL DEFAULT now(),
  atualizado_em timestamptz NOT NULL DEFAULT now(),
  UNIQUE (chave_rastreio)
);

CREATE TABLE IF NOT EXISTS public.negocios_fazenda (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tipo text NOT NULL CHECK (tipo IN ('compra', 'venda')),
  data_negociacao date NOT NULL,
  contraparte_id uuid REFERENCES public.contatos(id),
  corretor_id uuid REFERENCES public.contatos(id),
  operacao_destino_id uuid REFERENCES public.operacoes(id),
  categoria text,
  sexo text CHECK (sexo IS NULL OR sexo IN ('macho', 'femea', 'misto')),
  quantidade integer NOT NULL CHECK (quantidade > 0),
  peso_total_kg numeric CHECK (peso_total_kg IS NULL OR peso_total_kg > 0),
  rendimento_carne_pct numeric NOT NULL DEFAULT 50
    CHECK (rendimento_carne_pct > 0 AND rendimento_carne_pct <= 100),
  preco_arroba numeric CHECK (preco_arroba IS NULL OR preco_arroba > 0),
  valor_total numeric CHECK (valor_total IS NULL OR valor_total >= 0),
  custo_total numeric CHECK (custo_total IS NULL OR custo_total >= 0),
  estado text NOT NULL DEFAULT 'rascunho'
    CHECK (estado IN ('rascunho', 'confirmado', 'cancelado')),
  idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
  fonte text,
  dados_origem jsonb NOT NULL DEFAULT '{}'::jsonb,
  observacoes text,
  criado_em timestamptz NOT NULL DEFAULT now(),
  atualizado_em timestamptz NOT NULL DEFAULT now(),
  UNIQUE (idempotency_key),
  CHECK (
    estado <> 'confirmado'
    OR (preco_arroba IS NOT NULL AND valor_total IS NOT NULL AND peso_total_kg IS NOT NULL)
  )
);

ALTER TABLE public.fazenda_ametista
  ADD COLUMN IF NOT EXISTS negocio_fazenda_id uuid
    REFERENCES public.negocios_fazenda(id),
  ADD COLUMN IF NOT EXISTS idempotency_key text;

CREATE UNIQUE INDEX IF NOT EXISTS fazenda_ametista_idempotency_key_unique
  ON public.fazenda_ametista (idempotency_key)
  WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS public.movimentacoes_interunidades (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  data_movimento date NOT NULL,
  venda_fazenda_id uuid NOT NULL UNIQUE REFERENCES public.negocios_fazenda(id),
  lancamento_fazenda_id uuid NOT NULL UNIQUE REFERENCES public.fazenda_ametista(id),
  compra_confinamento_id uuid NOT NULL UNIQUE REFERENCES public.compras(id),
  operacao_destino_id uuid NOT NULL REFERENCES public.operacoes(id),
  quantidade integer NOT NULL CHECK (quantidade > 0),
  peso_total_kg numeric NOT NULL CHECK (peso_total_kg > 0),
  preco_arroba numeric NOT NULL CHECK (preco_arroba > 0),
  valor_total numeric NOT NULL CHECK (valor_total >= 0),
  estado text NOT NULL DEFAULT 'rascunho'
    CHECK (estado IN ('rascunho', 'confirmado', 'cancelado')),
  idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
  dados_origem jsonb NOT NULL DEFAULT '{}'::jsonb,
  observacoes text,
  criado_em timestamptz NOT NULL DEFAULT now(),
  atualizado_em timestamptz NOT NULL DEFAULT now(),
  UNIQUE (idempotency_key)
);

CREATE TABLE IF NOT EXISTS public.operacao_participantes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operacao_id uuid NOT NULL REFERENCES public.operacoes(id),
  contato_id uuid NOT NULL REFERENCES public.contatos(id),
  papel text NOT NULL CHECK (papel IN ('proprietario', 'parceiro', 'corretor', 'gestor')),
  participacao_pct numeric CHECK (
    participacao_pct IS NULL OR (participacao_pct > 0 AND participacao_pct <= 100)
  ),
  idempotency_key text NOT NULL CHECK (btrim(idempotency_key) <> ''),
  observacoes text,
  criado_em timestamptz NOT NULL DEFAULT now(),
  atualizado_em timestamptz NOT NULL DEFAULT now(),
  UNIQUE (operacao_id, contato_id, papel),
  UNIQUE (idempotency_key),
  CHECK (
    (papel IN ('proprietario', 'parceiro') AND participacao_pct IS NOT NULL)
    OR (papel IN ('corretor', 'gestor') AND participacao_pct IS NULL)
  )
);

CREATE INDEX IF NOT EXISTS compras_componentes_compra_idx
  ON public.compras_componentes (compra_agregada_id);
CREATE INDEX IF NOT EXISTS negocios_fazenda_operacao_idx
  ON public.negocios_fazenda (operacao_destino_id, estado);
CREATE INDEX IF NOT EXISTS movimentacoes_interunidades_operacao_idx
  ON public.movimentacoes_interunidades (operacao_destino_id, estado);
CREATE INDEX IF NOT EXISTS operacao_participantes_operacao_idx
  ON public.operacao_participantes (operacao_id, papel);

CREATE OR REPLACE FUNCTION public.atualizar_timestamp_interunidades()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
BEGIN
  NEW.atualizado_em := now();
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.validar_participacao_operacao()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  total_economico numeric;
BEGIN
  IF NEW.papel NOT IN ('proprietario', 'parceiro') THEN
    RETURN NEW;
  END IF;

  PERFORM pg_advisory_xact_lock(hashtextextended(NEW.operacao_id::text, 0));

  SELECT coalesce(sum(participacao_pct), 0)
    INTO total_economico
    FROM public.operacao_participantes
   WHERE operacao_id = NEW.operacao_id
     AND papel IN ('proprietario', 'parceiro')
     AND id IS DISTINCT FROM NEW.id;

  IF total_economico + NEW.participacao_pct > 100 THEN
    RAISE EXCEPTION 'participação econômica da operação excede 100%%';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.validar_valor_negocio_fazenda()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  valor_calculado numeric;
BEGIN
  IF NEW.estado <> 'confirmado' THEN
    RETURN NEW;
  END IF;
  valor_calculado := NEW.peso_total_kg * (NEW.rendimento_carne_pct / 100) / 15 * NEW.preco_arroba;
  IF abs(NEW.valor_total - valor_calculado) > 0.01 THEN
    RAISE EXCEPTION 'valor do negócio da fazenda diverge do peso, rendimento e preço';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.validar_movimentacao_interunidades()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  venda public.negocios_fazenda%ROWTYPE;
  lancamento public.fazenda_ametista%ROWTYPE;
  compra public.compras%ROWTYPE;
BEGIN
  IF NEW.estado <> 'confirmado' THEN
    RETURN NEW;
  END IF;

  SELECT * INTO STRICT venda
    FROM public.negocios_fazenda WHERE id = NEW.venda_fazenda_id;
  SELECT * INTO STRICT lancamento
    FROM public.fazenda_ametista WHERE id = NEW.lancamento_fazenda_id;
  SELECT * INTO STRICT compra
    FROM public.compras WHERE id = NEW.compra_confinamento_id;

  IF venda.tipo <> 'venda' OR venda.estado <> 'confirmado' THEN
    RAISE EXCEPTION 'negócio da fazenda deve ser uma venda confirmada';
  END IF;
  IF lancamento.tipo <> 'saida' OR lancamento.negocio_fazenda_id IS DISTINCT FROM venda.id THEN
    RAISE EXCEPTION 'lançamento da fazenda deve ser uma saída vinculada à venda';
  END IF;
  IF venda.operacao_destino_id IS DISTINCT FROM NEW.operacao_destino_id
     OR compra.operacao_id IS DISTINCT FROM NEW.operacao_destino_id THEN
    RAISE EXCEPTION 'venda, compra e movimento devem apontar para a mesma operação';
  END IF;
  IF venda.quantidade IS DISTINCT FROM NEW.quantidade
     OR lancamento.cabecas IS DISTINCT FROM NEW.quantidade
     OR compra.quantidade IS DISTINCT FROM NEW.quantidade THEN
    RAISE EXCEPTION 'quantidades divergentes na movimentação interunidades';
  END IF;
  IF venda.peso_total_kg IS DISTINCT FROM NEW.peso_total_kg
     OR compra.peso_total_kg IS DISTINCT FROM NEW.peso_total_kg
     OR venda.preco_arroba IS DISTINCT FROM NEW.preco_arroba
     OR compra.preco_arroba IS DISTINCT FROM NEW.preco_arroba
     OR venda.valor_total IS DISTINCT FROM NEW.valor_total
     OR compra.valor_total IS DISTINCT FROM NEW.valor_total THEN
    RAISE EXCEPTION 'peso ou valor divergente na movimentação interunidades';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS compras_componentes_atualizado_em ON public.compras_componentes;
CREATE TRIGGER compras_componentes_atualizado_em BEFORE UPDATE ON public.compras_componentes
FOR EACH ROW EXECUTE FUNCTION public.atualizar_timestamp_interunidades();
DROP TRIGGER IF EXISTS negocios_fazenda_atualizado_em ON public.negocios_fazenda;
CREATE TRIGGER negocios_fazenda_atualizado_em BEFORE UPDATE ON public.negocios_fazenda
FOR EACH ROW EXECUTE FUNCTION public.atualizar_timestamp_interunidades();
DROP TRIGGER IF EXISTS negocios_fazenda_valor_consistente ON public.negocios_fazenda;
CREATE TRIGGER negocios_fazenda_valor_consistente BEFORE INSERT OR UPDATE ON public.negocios_fazenda
FOR EACH ROW EXECUTE FUNCTION public.validar_valor_negocio_fazenda();
DROP TRIGGER IF EXISTS movimentacoes_interunidades_atualizado_em ON public.movimentacoes_interunidades;
CREATE TRIGGER movimentacoes_interunidades_atualizado_em BEFORE UPDATE ON public.movimentacoes_interunidades
FOR EACH ROW EXECUTE FUNCTION public.atualizar_timestamp_interunidades();
DROP TRIGGER IF EXISTS operacao_participantes_atualizado_em ON public.operacao_participantes;
CREATE TRIGGER operacao_participantes_atualizado_em BEFORE UPDATE ON public.operacao_participantes
FOR EACH ROW EXECUTE FUNCTION public.atualizar_timestamp_interunidades();

DROP TRIGGER IF EXISTS operacao_participantes_limite ON public.operacao_participantes;
CREATE TRIGGER operacao_participantes_limite BEFORE INSERT OR UPDATE ON public.operacao_participantes
FOR EACH ROW EXECUTE FUNCTION public.validar_participacao_operacao();
DROP TRIGGER IF EXISTS movimentacoes_interunidades_consistencia ON public.movimentacoes_interunidades;
CREATE TRIGGER movimentacoes_interunidades_consistencia BEFORE INSERT OR UPDATE ON public.movimentacoes_interunidades
FOR EACH ROW EXECUTE FUNCTION public.validar_movimentacao_interunidades();

CREATE OR REPLACE VIEW public.v_compras_componentes_resumo
WITH (security_invoker = true)
AS
SELECT
  c.id AS compra_agregada_id,
  c.operacao_id,
  c.quantidade AS quantidade_compra,
  c.peso_total_kg AS peso_compra_kg,
  c.valor_total AS valor_compra,
  count(cc.id) AS componentes,
  coalesce(sum(cc.quantidade), 0) AS quantidade_componentes,
  coalesce(sum(cc.peso_total_kg), 0) AS peso_componentes_kg,
  coalesce(sum(cc.valor_total), 0) AS valor_componentes
FROM public.compras c
LEFT JOIN public.compras_componentes cc ON cc.compra_agregada_id = c.id
GROUP BY c.id, c.operacao_id, c.quantidade, c.peso_total_kg, c.valor_total;

CREATE OR REPLACE VIEW public.v_movimentacoes_interunidades
WITH (security_invoker = true)
AS
SELECT
  m.id,
  m.data_movimento,
  m.operacao_destino_id,
  o.codigo AS operacao_codigo,
  m.quantidade,
  m.peso_total_kg,
  m.preco_arroba,
  m.valor_total,
  m.estado,
  m.venda_fazenda_id,
  m.lancamento_fazenda_id,
  m.compra_confinamento_id,
  m.criado_em,
  m.atualizado_em
FROM public.movimentacoes_interunidades m
JOIN public.operacoes o ON o.id = m.operacao_destino_id;

ALTER TABLE public.compras_componentes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.negocios_fazenda ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.movimentacoes_interunidades ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.operacao_participantes ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS compras_componentes_authenticated_select ON public.compras_componentes;
DROP POLICY IF EXISTS negocios_fazenda_authenticated_select ON public.negocios_fazenda;
DROP POLICY IF EXISTS movimentacoes_interunidades_authenticated_select ON public.movimentacoes_interunidades;
DROP POLICY IF EXISTS operacao_participantes_authenticated_select ON public.operacao_participantes;

CREATE POLICY compras_componentes_authenticated_select ON public.compras_componentes
  FOR SELECT TO authenticated USING (true);
CREATE POLICY negocios_fazenda_authenticated_select ON public.negocios_fazenda
  FOR SELECT TO authenticated USING (true);
CREATE POLICY movimentacoes_interunidades_authenticated_select ON public.movimentacoes_interunidades
  FOR SELECT TO authenticated USING (true);
CREATE POLICY operacao_participantes_authenticated_select ON public.operacao_participantes
  FOR SELECT TO authenticated USING (true);

REVOKE ALL ON public.compras_componentes FROM anon, authenticated;
REVOKE ALL ON public.negocios_fazenda FROM anon, authenticated;
REVOKE ALL ON public.movimentacoes_interunidades FROM anon, authenticated;
REVOKE ALL ON public.operacao_participantes FROM anon, authenticated;
REVOKE ALL ON public.v_compras_componentes_resumo FROM anon, authenticated;
REVOKE ALL ON public.v_movimentacoes_interunidades FROM anon, authenticated;
REVOKE ALL ON public.compras_componentes FROM service_role;
REVOKE ALL ON public.negocios_fazenda FROM service_role;
REVOKE ALL ON public.movimentacoes_interunidades FROM service_role;
REVOKE ALL ON public.operacao_participantes FROM service_role;
REVOKE ALL ON public.v_compras_componentes_resumo FROM service_role;
REVOKE ALL ON public.v_movimentacoes_interunidades FROM service_role;

GRANT SELECT ON public.compras_componentes TO authenticated;
GRANT SELECT ON public.negocios_fazenda TO authenticated;
GRANT SELECT ON public.movimentacoes_interunidades TO authenticated;
GRANT SELECT ON public.operacao_participantes TO authenticated;
GRANT SELECT ON public.v_compras_componentes_resumo TO authenticated;
GRANT SELECT ON public.v_movimentacoes_interunidades TO authenticated;

GRANT SELECT, INSERT, UPDATE ON public.compras_componentes TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.negocios_fazenda TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.movimentacoes_interunidades TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.operacao_participantes TO service_role;
GRANT SELECT ON public.v_compras_componentes_resumo TO service_role;
GRANT SELECT ON public.v_movimentacoes_interunidades TO service_role;

COMMENT ON TABLE public.compras_componentes IS
  'Composição informativa de uma compra agregada; componentes nunca são somados novamente ao total da operação.';
COMMENT ON TABLE public.negocios_fazenda IS
  'Compra ou venda econômica da Fazenda, separada do lançamento físico do rebanho.';
COMMENT ON TABLE public.movimentacoes_interunidades IS
  'Vínculo reconciliado entre venda da Fazenda, saída física e compra do Confinamento.';
COMMENT ON TABLE public.operacao_participantes IS
  'Papéis e participações por operação sem inferência automática de propriedade.';

COMMIT;
