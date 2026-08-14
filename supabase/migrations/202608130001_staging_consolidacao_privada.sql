-- Camada auditável entre fontes privadas e tabelas operacionais do Confinex.
-- Esta migração é exclusivamente estrutural: não importa dados, não concilia
-- pagamentos e não promove candidatos para compras, vendas ou fluxo de caixa.

BEGIN;

CREATE TABLE IF NOT EXISTS public.fontes_importacao (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tipo text NOT NULL CHECK (tipo IN (
    'planilha_consolidacao', 'ofx', 'telegram', 'ima', 'agronotas', 'wey', 'outro'
  )),
  nome_arquivo text NOT NULL,
  hash_sha256 text NOT NULL CHECK (hash_sha256 ~ '^[0-9a-f]{64}$'),
  periodo_inicio date,
  periodo_fim date,
  quantidade_registros integer NOT NULL DEFAULT 0 CHECK (quantidade_registros >= 0),
  origem_canal text,
  origem_referencia text,
  estado text NOT NULL DEFAULT 'recebida' CHECK (estado IN (
    'recebida', 'validada', 'importada_staging', 'rejeitada'
  )),
  metadados jsonb NOT NULL DEFAULT '{}'::jsonb,
  criado_por text NOT NULL DEFAULT 'sistema',
  criado_em timestamptz NOT NULL DEFAULT now(),
  validado_em timestamptz,
  CHECK (periodo_fim IS NULL OR periodo_inicio IS NULL OR periodo_fim >= periodo_inicio),
  UNIQUE (tipo, hash_sha256)
);

CREATE TABLE IF NOT EXISTS public.negocios_candidatos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  fonte_importacao_id uuid NOT NULL REFERENCES public.fontes_importacao(id),
  codigo_fonte text NOT NULL,
  chave_rastreio text NOT NULL,
  nome text NOT NULL,
  contexto text NOT NULL,
  sexo text,
  categoria text,
  destino text,
  data_base date,
  situacao_origem text,
  prioridade text NOT NULL DEFAULT 'media' CHECK (prioridade IN ('baixa', 'media', 'alta')),
  quantidade integer CHECK (quantidade IS NULL OR quantidade > 0),
  peso_total_kg numeric CHECK (peso_total_kg IS NULL OR peso_total_kg > 0),
  preco_arroba numeric CHECK (preco_arroba IS NULL OR preco_arroba > 0),
  valor_total numeric CHECK (valor_total IS NULL OR valor_total >= 0),
  pagamento_descricao text,
  campos_faltantes text[] NOT NULL DEFAULT '{}',
  divergencias text[] NOT NULL DEFAULT '{}',
  acao_recomendada text,
  estado text NOT NULL DEFAULT 'rascunho' CHECK (estado IN (
    'rascunho', 'em_revisao', 'confirmado', 'rejeitado', 'incorporado'
  )),
  confirmado_por text,
  confirmado_em timestamptz,
  incorporado_no_candidato_id uuid REFERENCES public.negocios_candidatos(id),
  operacao_id uuid REFERENCES public.operacoes(id),
  dados_origem jsonb NOT NULL DEFAULT '{}'::jsonb,
  criado_em timestamptz NOT NULL DEFAULT now(),
  atualizado_em timestamptz NOT NULL DEFAULT now(),
  CHECK (
    (estado = 'confirmado' AND confirmado_por IS NOT NULL AND confirmado_em IS NOT NULL)
    OR estado <> 'confirmado'
  ),
  CHECK (
    (estado = 'incorporado' AND incorporado_no_candidato_id IS NOT NULL)
    OR estado <> 'incorporado'
  ),
  UNIQUE (chave_rastreio),
  UNIQUE (fonte_importacao_id, codigo_fonte)
);

CREATE TABLE IF NOT EXISTS public.negocio_versoes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  negocio_candidato_id uuid NOT NULL REFERENCES public.negocios_candidatos(id),
  fonte_importacao_id uuid NOT NULL REFERENCES public.fontes_importacao(id),
  versao_referencia text NOT NULL,
  mensagem_em timestamptz,
  quantidade integer CHECK (quantidade IS NULL OR quantidade > 0),
  peso_total_kg numeric CHECK (peso_total_kg IS NULL OR peso_total_kg > 0),
  preco_arroba numeric CHECK (preco_arroba IS NULL OR preco_arroba > 0),
  valor_total numeric CHECK (valor_total IS NULL OR valor_total >= 0),
  data_negociacao date,
  data_pesagem date,
  pagamento_descricao text,
  correcao_explicita boolean NOT NULL DEFAULT false,
  ocorrencias integer NOT NULL DEFAULT 1 CHECK (ocorrencias > 0),
  conteudo_origem jsonb NOT NULL DEFAULT '{}'::jsonb,
  criado_em timestamptz NOT NULL DEFAULT now(),
  UNIQUE (negocio_candidato_id, versao_referencia)
);

CREATE TABLE IF NOT EXISTS public.evidencias_negocio (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  negocio_candidato_id uuid NOT NULL REFERENCES public.negocios_candidatos(id),
  fonte_importacao_id uuid NOT NULL REFERENCES public.fontes_importacao(id),
  tipo text NOT NULL CHECK (tipo IN (
    'telegram', 'wey', 'gta', 'nf', 'ima', 'ofx', 'planilha', 'decisao_usuario', 'outro'
  )),
  referencia text NOT NULL,
  hash_conteudo text CHECK (hash_conteudo IS NULL OR hash_conteudo ~ '^[0-9a-f]{64}$'),
  evidenciado_em timestamptz,
  confianca numeric CHECK (confianca IS NULL OR (confianca >= 0 AND confianca <= 1)),
  classificacao text,
  dados jsonb NOT NULL DEFAULT '{}'::jsonb,
  criado_em timestamptz NOT NULL DEFAULT now(),
  UNIQUE (negocio_candidato_id, tipo, referencia)
);

CREATE TABLE IF NOT EXISTS public.transacoes_banco_staging (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  fonte_importacao_id uuid NOT NULL REFERENCES public.fontes_importacao(id),
  fitid text NOT NULL CHECK (btrim(fitid) <> ''),
  conta text NOT NULL CHECK (btrim(conta) <> ''),
  banco text,
  data date NOT NULL,
  tipo text,
  valor numeric NOT NULL CHECK (valor <> 0),
  descricao text,
  memo text,
  estado text NOT NULL DEFAULT 'nao_revisada' CHECK (estado IN (
    'nao_revisada', 'em_revisao', 'confirmada', 'rejeitada', 'promovida'
  )),
  transacao_banco_id uuid REFERENCES public.transacoes_banco(id),
  dados_origem jsonb NOT NULL DEFAULT '{}'::jsonb,
  criado_em timestamptz NOT NULL DEFAULT now(),
  revisado_em timestamptz,
  UNIQUE (conta, fitid)
);

CREATE TABLE IF NOT EXISTS public.conciliacoes_candidatas (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  transacao_staging_id uuid NOT NULL REFERENCES public.transacoes_banco_staging(id),
  negocio_candidato_id uuid REFERENCES public.negocios_candidatos(id),
  operacao_id uuid REFERENCES public.operacoes(id),
  fluxo_caixa_id uuid REFERENCES public.fluxo_caixa(id),
  valor_alocado numeric NOT NULL CHECK (valor_alocado > 0),
  classificacao text NOT NULL CHECK (classificacao IN ('possivel', 'provavel', 'forte', 'confirmada')),
  confianca numeric NOT NULL CHECK (confianca >= 0 AND confianca <= 1),
  justificativa text NOT NULL,
  estado text NOT NULL DEFAULT 'pendente' CHECK (estado IN (
    'pendente', 'confirmada', 'rejeitada', 'promovida'
  )),
  confirmado_por text,
  confirmado_em timestamptz,
  criado_em timestamptz NOT NULL DEFAULT now(),
  CHECK (num_nonnulls(negocio_candidato_id, operacao_id, fluxo_caixa_id) = 1),
  CHECK (
    (estado = 'confirmada' AND confirmado_por IS NOT NULL AND confirmado_em IS NOT NULL)
    OR estado <> 'confirmada'
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS conciliacoes_candidatas_alvo_unique
ON public.conciliacoes_candidatas (
  transacao_staging_id,
  coalesce(negocio_candidato_id::text, ''),
  coalesce(operacao_id::text, ''),
  coalesce(fluxo_caixa_id::text, '')
);

CREATE TABLE IF NOT EXISTS public.vinculos_documentais_candidatos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  fonte_importacao_id uuid NOT NULL REFERENCES public.fontes_importacao(id),
  negocio_candidato_id uuid REFERENCES public.negocios_candidatos(id),
  gta_numero text,
  gta_id uuid REFERENCES public.gtas(id),
  nf_referencia text,
  nota_fiscal_raw_id uuid REFERENCES public.notas_fiscais_xml_raw(id),
  classificacao text NOT NULL CHECK (classificacao IN ('possivel', 'provavel', 'forte', 'confirmado')),
  confianca numeric CHECK (confianca IS NULL OR (confianca >= 0 AND confianca <= 1)),
  estado text NOT NULL DEFAULT 'pendente' CHECK (estado IN ('pendente', 'confirmado', 'rejeitado', 'promovido')),
  justificativa text,
  confirmado_por text,
  confirmado_em timestamptz,
  criado_em timestamptz NOT NULL DEFAULT now(),
  CHECK (gta_numero IS NOT NULL OR gta_id IS NOT NULL),
  CHECK (nf_referencia IS NOT NULL OR nota_fiscal_raw_id IS NOT NULL),
  CHECK (
    (estado = 'confirmado' AND confirmado_por IS NOT NULL AND confirmado_em IS NOT NULL)
    OR estado <> 'confirmado'
  )
);

CREATE TABLE IF NOT EXISTS public.decisoes_consolidacao (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entidade_tipo text NOT NULL CHECK (entidade_tipo IN (
    'negocio_candidato', 'conciliacao_candidata', 'vinculo_documental_candidato',
    'transacao_banco_staging'
  )),
  entidade_id uuid NOT NULL,
  estado_anterior text NOT NULL,
  estado_novo text NOT NULL,
  decisao text NOT NULL CHECK (decisao IN ('confirmar', 'rejeitar', 'incorporar', 'promover', 'reabrir')),
  decidido_por text NOT NULL,
  motivo text NOT NULL CHECK (btrim(motivo) <> ''),
  evidencias_ids uuid[] NOT NULL DEFAULT '{}',
  origem_canal text,
  origem_conversa_id text,
  origem_mensagem_id text,
  criado_em timestamptz NOT NULL DEFAULT now(),
  CHECK (estado_anterior <> estado_novo)
);

CREATE UNIQUE INDEX IF NOT EXISTS vinculos_documentais_candidatos_unique
ON public.vinculos_documentais_candidatos (
  fonte_importacao_id,
  coalesce(negocio_candidato_id::text, ''),
  coalesce(gta_numero, ''),
  coalesce(gta_id::text, ''),
  coalesce(nf_referencia, ''),
  coalesce(nota_fiscal_raw_id::text, '')
);

CREATE INDEX IF NOT EXISTS negocios_candidatos_estado_idx
  ON public.negocios_candidatos (estado, prioridade);
CREATE INDEX IF NOT EXISTS negocio_versoes_negocio_idx
  ON public.negocio_versoes (negocio_candidato_id, mensagem_em);
CREATE INDEX IF NOT EXISTS evidencias_negocio_negocio_idx
  ON public.evidencias_negocio (negocio_candidato_id, tipo);
CREATE INDEX IF NOT EXISTS transacoes_banco_staging_data_idx
  ON public.transacoes_banco_staging (data, estado);
CREATE INDEX IF NOT EXISTS conciliacoes_candidatas_estado_idx
  ON public.conciliacoes_candidatas (estado, classificacao);
CREATE INDEX IF NOT EXISTS vinculos_documentais_candidatos_estado_idx
  ON public.vinculos_documentais_candidatos (estado, classificacao);
CREATE INDEX IF NOT EXISTS decisoes_consolidacao_entidade_idx
  ON public.decisoes_consolidacao (entidade_tipo, entidade_id, criado_em);

CREATE OR REPLACE FUNCTION public.atualizar_timestamp_staging_consolidacao()
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

DROP TRIGGER IF EXISTS negocios_candidatos_atualizado_em ON public.negocios_candidatos;
CREATE TRIGGER negocios_candidatos_atualizado_em
BEFORE UPDATE ON public.negocios_candidatos
FOR EACH ROW EXECUTE FUNCTION public.atualizar_timestamp_staging_consolidacao();

ALTER TABLE public.fontes_importacao ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.negocios_candidatos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.negocio_versoes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.evidencias_negocio ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.transacoes_banco_staging ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.conciliacoes_candidatas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.vinculos_documentais_candidatos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.decisoes_consolidacao ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS fontes_importacao_authenticated_select ON public.fontes_importacao;
DROP POLICY IF EXISTS negocios_candidatos_authenticated_select ON public.negocios_candidatos;
DROP POLICY IF EXISTS negocio_versoes_authenticated_select ON public.negocio_versoes;
DROP POLICY IF EXISTS evidencias_negocio_authenticated_select ON public.evidencias_negocio;
DROP POLICY IF EXISTS transacoes_banco_staging_authenticated_select ON public.transacoes_banco_staging;
DROP POLICY IF EXISTS conciliacoes_candidatas_authenticated_select ON public.conciliacoes_candidatas;
DROP POLICY IF EXISTS vinculos_documentais_candidatos_authenticated_select ON public.vinculos_documentais_candidatos;
DROP POLICY IF EXISTS decisoes_consolidacao_authenticated_select ON public.decisoes_consolidacao;

CREATE POLICY fontes_importacao_authenticated_select ON public.fontes_importacao
  FOR SELECT TO authenticated USING (true);
CREATE POLICY negocios_candidatos_authenticated_select ON public.negocios_candidatos
  FOR SELECT TO authenticated USING (true);
CREATE POLICY negocio_versoes_authenticated_select ON public.negocio_versoes
  FOR SELECT TO authenticated USING (true);
CREATE POLICY evidencias_negocio_authenticated_select ON public.evidencias_negocio
  FOR SELECT TO authenticated USING (true);
CREATE POLICY transacoes_banco_staging_authenticated_select ON public.transacoes_banco_staging
  FOR SELECT TO authenticated USING (true);
CREATE POLICY conciliacoes_candidatas_authenticated_select ON public.conciliacoes_candidatas
  FOR SELECT TO authenticated USING (true);
CREATE POLICY vinculos_documentais_candidatos_authenticated_select ON public.vinculos_documentais_candidatos
  FOR SELECT TO authenticated USING (true);
CREATE POLICY decisoes_consolidacao_authenticated_select ON public.decisoes_consolidacao
  FOR SELECT TO authenticated USING (true);

REVOKE ALL ON public.fontes_importacao FROM anon, authenticated;
REVOKE ALL ON public.negocios_candidatos FROM anon, authenticated;
REVOKE ALL ON public.negocio_versoes FROM anon, authenticated;
REVOKE ALL ON public.evidencias_negocio FROM anon, authenticated;
REVOKE ALL ON public.transacoes_banco_staging FROM anon, authenticated;
REVOKE ALL ON public.conciliacoes_candidatas FROM anon, authenticated;
REVOKE ALL ON public.vinculos_documentais_candidatos FROM anon, authenticated;
REVOKE ALL ON public.decisoes_consolidacao FROM anon, authenticated;

GRANT SELECT ON public.fontes_importacao TO authenticated;
GRANT SELECT ON public.negocios_candidatos TO authenticated;
GRANT SELECT ON public.negocio_versoes TO authenticated;
GRANT SELECT ON public.evidencias_negocio TO authenticated;
GRANT SELECT ON public.transacoes_banco_staging TO authenticated;
GRANT SELECT ON public.conciliacoes_candidatas TO authenticated;
GRANT SELECT ON public.vinculos_documentais_candidatos TO authenticated;
GRANT SELECT ON public.decisoes_consolidacao TO authenticated;

GRANT SELECT, INSERT, UPDATE ON public.fontes_importacao TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.negocios_candidatos TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.negocio_versoes TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.evidencias_negocio TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.transacoes_banco_staging TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.conciliacoes_candidatas TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.vinculos_documentais_candidatos TO service_role;
GRANT SELECT, INSERT ON public.decisoes_consolidacao TO service_role;

COMMENT ON TABLE public.fontes_importacao IS
  'Registro idempotente das fontes privadas; não representa promoção operacional.';
COMMENT ON TABLE public.negocios_candidatos IS
  'Negócios consolidados ainda sujeitos a revisão; não são operações reais.';
COMMENT ON TABLE public.transacoes_banco_staging IS
  'Movimentos OFX deduplicados por conta e FITID antes de qualquer conciliação.';
COMMENT ON TABLE public.conciliacoes_candidatas IS
  'Alocações muitos-para-muitos propostas; confirmação não altera fluxo_caixa.';
COMMENT ON TABLE public.vinculos_documentais_candidatos IS
  'Candidatos GTA-NF-negócio preservados sem associação operacional automática.';
COMMENT ON TABLE public.decisoes_consolidacao IS
  'Histórico imutável de revisão; registra antes/depois, autor, motivo e evidências.';

COMMIT;
