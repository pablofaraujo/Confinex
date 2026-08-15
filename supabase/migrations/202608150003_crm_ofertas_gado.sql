-- CRM de fornecedores, ofertas recebidas e histórico das negociações de gado.
-- Migração exclusivamente estrutural: não envia mensagens, não cria ofertas
-- reais e não promove registros para compras, vendas ou operações.

BEGIN;

CREATE TABLE IF NOT EXISTS public.ofertas_gado (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  fornecedor_id uuid REFERENCES public.contatos(id),
  corretor_id uuid REFERENCES public.contatos(id),
  recebida_em timestamptz NOT NULL DEFAULT now(),
  validade_ate date,
  sexo text NOT NULL DEFAULT 'nao_informado' CHECK (
    sexo IN ('macho', 'femea', 'misto', 'nao_informado')
  ),
  categoria text,
  quantidade integer CHECK (quantidade IS NULL OR quantidade > 0),
  peso_medio_kg numeric CHECK (peso_medio_kg IS NULL OR peso_medio_kg > 0),
  preco_arroba numeric CHECK (preco_arroba IS NULL OR preco_arroba > 0),
  modalidade_preco text CHECK (
    modalidade_preco IS NULL OR modalidade_preco IN (
      'arroba', 'cabeca', 'kg', 'lote', 'a_combinar'
    )
  ),
  municipio text,
  uf text CHECK (uf IS NULL OR uf ~ '^[A-Z]{2}$'),
  status text NOT NULL DEFAULT 'nova' CHECK (
    status IN ('nova', 'em_analise', 'incompleta', 'descartada', 'convertida', 'expirada')
  ),
  origem_canal text NOT NULL DEFAULT 'manual' CHECK (
    origem_canal IN ('manual', 'whatsapp', 'telegram', 'telefone', 'presencial', 'outro')
  ),
  origem_conversa_id text,
  origem_mensagem_id text,
  campos_faltantes text[] NOT NULL DEFAULT '{}',
  observacoes text,
  metadados jsonb NOT NULL DEFAULT '{}'::jsonb,
  criado_por uuid REFERENCES auth.users(id),
  criado_em timestamptz NOT NULL DEFAULT now(),
  atualizado_em timestamptz NOT NULL DEFAULT now(),
  CHECK (validade_ate IS NULL OR validade_ate >= recebida_em::date),
  CHECK (
    origem_mensagem_id IS NULL
    OR (origem_canal <> 'manual' AND origem_conversa_id IS NOT NULL)
  )
);

CREATE TABLE IF NOT EXISTS public.negociacoes_gado (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  oferta_id uuid REFERENCES public.ofertas_gado(id),
  contato_id uuid NOT NULL REFERENCES public.contatos(id),
  responsavel_id uuid REFERENCES auth.users(id),
  status text NOT NULL DEFAULT 'contato_pendente' CHECK (
    status IN (
      'contato_pendente', 'em_negociacao', 'aguardando_resposta',
      'fechada_ganha', 'fechada_perdida', 'cancelada'
    )
  ),
  iniciada_em timestamptz NOT NULL DEFAULT now(),
  encerrada_em timestamptz,
  motivo_perda text,
  quantidade_acordada integer CHECK (quantidade_acordada IS NULL OR quantidade_acordada > 0),
  preco_acordado numeric CHECK (preco_acordado IS NULL OR preco_acordado > 0),
  operacao_id uuid REFERENCES public.operacoes(id),
  observacoes text,
  criado_em timestamptz NOT NULL DEFAULT now(),
  atualizado_em timestamptz NOT NULL DEFAULT now(),
  CHECK (
    (status IN ('fechada_ganha', 'fechada_perdida', 'cancelada') AND encerrada_em IS NOT NULL)
    OR status NOT IN ('fechada_ganha', 'fechada_perdida', 'cancelada')
  ),
  CHECK (status <> 'fechada_perdida' OR nullif(btrim(motivo_perda), '') IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS public.interacoes_crm (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  contato_id uuid NOT NULL REFERENCES public.contatos(id),
  oferta_id uuid REFERENCES public.ofertas_gado(id),
  negociacao_id uuid REFERENCES public.negociacoes_gado(id),
  canal text NOT NULL CHECK (
    canal IN ('whatsapp', 'telegram', 'telefone', 'presencial', 'email', 'sistema', 'outro')
  ),
  direcao text NOT NULL CHECK (direcao IN ('recebida', 'enviada', 'interna')),
  ocorrida_em timestamptz NOT NULL DEFAULT now(),
  resumo text NOT NULL CHECK (nullif(btrim(resumo), '') IS NOT NULL),
  conversa_id text,
  mensagem_id text,
  metadados jsonb NOT NULL DEFAULT '{}'::jsonb,
  criado_por uuid REFERENCES auth.users(id),
  criado_em timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS public.crm_followups (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  contato_id uuid NOT NULL REFERENCES public.contatos(id),
  oferta_id uuid REFERENCES public.ofertas_gado(id),
  negociacao_id uuid REFERENCES public.negociacoes_gado(id),
  descricao text NOT NULL CHECK (nullif(btrim(descricao), '') IS NOT NULL),
  previsto_para timestamptz NOT NULL,
  status text NOT NULL DEFAULT 'pendente' CHECK (
    status IN ('pendente', 'concluido', 'cancelado')
  ),
  concluido_em timestamptz,
  criado_por uuid REFERENCES auth.users(id),
  criado_em timestamptz NOT NULL DEFAULT now(),
  atualizado_em timestamptz NOT NULL DEFAULT now(),
  CHECK ((status = 'concluido' AND concluido_em IS NOT NULL) OR status <> 'concluido')
);

CREATE UNIQUE INDEX IF NOT EXISTS ofertas_gado_origem_mensagem_unique
  ON public.ofertas_gado (origem_canal, origem_conversa_id, origem_mensagem_id)
  WHERE origem_mensagem_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS interacoes_crm_origem_mensagem_unique
  ON public.interacoes_crm (canal, conversa_id, mensagem_id)
  WHERE mensagem_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ofertas_gado_status_recebida_idx
  ON public.ofertas_gado (status, recebida_em DESC);
CREATE INDEX IF NOT EXISTS ofertas_gado_fornecedor_idx
  ON public.ofertas_gado (fornecedor_id, recebida_em DESC);
CREATE INDEX IF NOT EXISTS negociacoes_gado_status_idx
  ON public.negociacoes_gado (status, iniciada_em DESC);
CREATE INDEX IF NOT EXISTS interacoes_crm_contato_idx
  ON public.interacoes_crm (contato_id, ocorrida_em DESC);
CREATE INDEX IF NOT EXISTS crm_followups_pendentes_idx
  ON public.crm_followups (previsto_para)
  WHERE status = 'pendente';

CREATE OR REPLACE FUNCTION public.atualizar_timestamp_crm_gado()
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

DROP TRIGGER IF EXISTS ofertas_gado_atualizado_em ON public.ofertas_gado;
CREATE TRIGGER ofertas_gado_atualizado_em
BEFORE UPDATE ON public.ofertas_gado
FOR EACH ROW EXECUTE FUNCTION public.atualizar_timestamp_crm_gado();

DROP TRIGGER IF EXISTS negociacoes_gado_atualizado_em ON public.negociacoes_gado;
CREATE TRIGGER negociacoes_gado_atualizado_em
BEFORE UPDATE ON public.negociacoes_gado
FOR EACH ROW EXECUTE FUNCTION public.atualizar_timestamp_crm_gado();

DROP TRIGGER IF EXISTS crm_followups_atualizado_em ON public.crm_followups;
CREATE TRIGGER crm_followups_atualizado_em
BEFORE UPDATE ON public.crm_followups
FOR EACH ROW EXECUTE FUNCTION public.atualizar_timestamp_crm_gado();

ALTER TABLE public.ofertas_gado ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.negociacoes_gado ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.interacoes_crm ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.crm_followups ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS ofertas_gado_authenticated_select ON public.ofertas_gado;
CREATE POLICY ofertas_gado_authenticated_select ON public.ofertas_gado
  FOR SELECT TO authenticated USING (true);
DROP POLICY IF EXISTS ofertas_gado_authenticated_insert ON public.ofertas_gado;
CREATE POLICY ofertas_gado_authenticated_insert ON public.ofertas_gado
  FOR INSERT TO authenticated WITH CHECK (true);
DROP POLICY IF EXISTS ofertas_gado_authenticated_update ON public.ofertas_gado;
CREATE POLICY ofertas_gado_authenticated_update ON public.ofertas_gado
  FOR UPDATE TO authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS negociacoes_gado_authenticated_select ON public.negociacoes_gado;
CREATE POLICY negociacoes_gado_authenticated_select ON public.negociacoes_gado
  FOR SELECT TO authenticated USING (true);
DROP POLICY IF EXISTS negociacoes_gado_authenticated_insert ON public.negociacoes_gado;
CREATE POLICY negociacoes_gado_authenticated_insert ON public.negociacoes_gado
  FOR INSERT TO authenticated WITH CHECK (true);
DROP POLICY IF EXISTS negociacoes_gado_authenticated_update ON public.negociacoes_gado;
CREATE POLICY negociacoes_gado_authenticated_update ON public.negociacoes_gado
  FOR UPDATE TO authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS interacoes_crm_authenticated_select ON public.interacoes_crm;
CREATE POLICY interacoes_crm_authenticated_select ON public.interacoes_crm
  FOR SELECT TO authenticated USING (true);
DROP POLICY IF EXISTS interacoes_crm_authenticated_insert ON public.interacoes_crm;
CREATE POLICY interacoes_crm_authenticated_insert ON public.interacoes_crm
  FOR INSERT TO authenticated WITH CHECK (true);
DROP POLICY IF EXISTS interacoes_crm_authenticated_update ON public.interacoes_crm;
CREATE POLICY interacoes_crm_authenticated_update ON public.interacoes_crm
  FOR UPDATE TO authenticated USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS crm_followups_authenticated_select ON public.crm_followups;
CREATE POLICY crm_followups_authenticated_select ON public.crm_followups
  FOR SELECT TO authenticated USING (true);
DROP POLICY IF EXISTS crm_followups_authenticated_insert ON public.crm_followups;
CREATE POLICY crm_followups_authenticated_insert ON public.crm_followups
  FOR INSERT TO authenticated WITH CHECK (true);
DROP POLICY IF EXISTS crm_followups_authenticated_update ON public.crm_followups;
CREATE POLICY crm_followups_authenticated_update ON public.crm_followups
  FOR UPDATE TO authenticated USING (true) WITH CHECK (true);

REVOKE ALL ON public.ofertas_gado FROM anon;
REVOKE ALL ON public.negociacoes_gado FROM anon;
REVOKE ALL ON public.interacoes_crm FROM anon;
REVOKE ALL ON public.crm_followups FROM anon;
REVOKE DELETE, TRUNCATE ON public.ofertas_gado FROM authenticated;
REVOKE DELETE, TRUNCATE ON public.negociacoes_gado FROM authenticated;
REVOKE DELETE, TRUNCATE ON public.interacoes_crm FROM authenticated;
REVOKE DELETE, TRUNCATE ON public.crm_followups FROM authenticated;
GRANT SELECT, INSERT, UPDATE ON public.ofertas_gado TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.negociacoes_gado TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.interacoes_crm TO authenticated;
GRANT SELECT, INSERT, UPDATE ON public.crm_followups TO authenticated;
REVOKE DELETE, TRUNCATE ON public.ofertas_gado FROM service_role;
REVOKE DELETE, TRUNCATE ON public.negociacoes_gado FROM service_role;
REVOKE DELETE, TRUNCATE ON public.interacoes_crm FROM service_role;
REVOKE DELETE, TRUNCATE ON public.crm_followups FROM service_role;
GRANT SELECT, INSERT, UPDATE ON public.ofertas_gado TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.negociacoes_gado TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.interacoes_crm TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.crm_followups TO service_role;

COMMENT ON TABLE public.ofertas_gado IS
  'Ofertas de gado recebidas, inclusive incompletas, sem efeito operacional automático.';
COMMENT ON TABLE public.negociacoes_gado IS
  'Funil e resultado das negociações, com eventual vínculo posterior à operação.';
COMMENT ON TABLE public.interacoes_crm IS
  'Histórico auditável de interações com fornecedores, corretores e frigoríficos.';
COMMENT ON TABLE public.crm_followups IS
  'Próximas ações internas do CRM; não executa contato automático.';

COMMIT;
