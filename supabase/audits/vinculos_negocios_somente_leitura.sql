-- Diagnóstico P1: projeção privada, sem documentos, mensagens ou nomes.
-- Não aplica migração, não corrige vínculos e não executa operações.
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '1s';
SET LOCAL idle_in_transaction_session_timeout = '20s';

WITH tabelas AS (
  SELECT 'operacoes' AS nome, coalesce(jsonb_agg(to_jsonb(t) ORDER BY id), '[]'::jsonb) AS linhas
  FROM (SELECT id, codigo, sexo, tipo_negocio, status, confinamento_id FROM public.operacoes) t
  UNION ALL
  SELECT 'compras', coalesce(jsonb_agg(to_jsonb(t) ORDER BY id), '[]'::jsonb)
  FROM (SELECT id, operacao_id, quantidade::text, peso_total_kg::text,
               valor_total::text, data, idempotency_key FROM public.compras) t
  UNION ALL
  SELECT 'compras_componentes', coalesce(jsonb_agg(to_jsonb(t) ORDER BY id), '[]'::jsonb)
  FROM (SELECT id, compra_agregada_id, quantidade::text, peso_total_kg::text,
               valor_total::text, chave_rastreio,
               jsonb_build_object(
                 'sexo', CASE WHEN jsonb_typeof(dados_origem->'sexo') = 'string'
                               AND length(dados_origem->>'sexo') <= 80
                              THEN dados_origem->>'sexo' END,
                 'categoria', CASE WHEN jsonb_typeof(dados_origem->'categoria') = 'string'
                                    AND length(dados_origem->>'categoria') <= 80
                                   THEN dados_origem->>'categoria' END,
                 'destino', CASE WHEN jsonb_typeof(dados_origem->'destino') = 'string'
                                  AND length(dados_origem->>'destino') <= 80
                                 THEN dados_origem->>'destino' END
               ) AS dimensoes_origem,
               (jsonb_typeof(dados_origem) IS DISTINCT FROM 'object'
                OR EXISTS (SELECT 1 FROM jsonb_each(
                             CASE WHEN jsonb_typeof(dados_origem) = 'object'
                                  THEN dados_origem ELSE '{}'::jsonb END) d
                       WHERE d.key IN ('sexo', 'categoria', 'destino')
                         AND (jsonb_typeof(d.value) NOT IN ('string', 'null')
                              OR (jsonb_typeof(d.value) = 'string'
                                  AND length(d.value #>> '{}') > 80)))) AS dimensoes_formato_inesperado
        FROM public.compras_componentes) t
  UNION ALL
  SELECT 'confinex_avaliacoes', coalesce(jsonb_agg(to_jsonb(t) ORDER BY id), '[]'::jsonb)
  FROM (SELECT id, codigo, operacao_id, status FROM public.confinex_avaliacoes) t
  UNION ALL
  SELECT 'confinex_estimativas', coalesce(jsonb_agg(to_jsonb(t) ORDER BY id), '[]'::jsonb)
  FROM (SELECT id, avaliacao_id, versao::text, tipo FROM public.confinex_estimativas) t
  UNION ALL
  SELECT 'negocios_candidatos', coalesce(jsonb_agg(to_jsonb(t) ORDER BY id), '[]'::jsonb)
  FROM (SELECT id, codigo_fonte, fonte_importacao_id, sexo, categoria, destino,
               estado, operacao_id, incorporado_no_candidato_id, quantidade::text
        FROM public.negocios_candidatos) t
  UNION ALL
  SELECT 'transacoes_banco_staging', coalesce(jsonb_agg(to_jsonb(t) ORDER BY id), '[]'::jsonb)
  FROM (SELECT id, conta, fitid, data, valor::text, transacao_banco_id
        FROM public.transacoes_banco_staging) t
  UNION ALL
  SELECT 'transacoes_banco', coalesce(jsonb_agg(to_jsonb(t) ORDER BY id), '[]'::jsonb)
  FROM (SELECT id, conta, id_externo, data, valor::text, fluxo_caixa_id
        FROM public.transacoes_banco) t
)
SELECT jsonb_build_object(
  'versao', 1,
  'modo', 'somente_leitura',
  'tabelas', jsonb_object_agg(nome, linhas ORDER BY nome),
  'contagens', jsonb_object_agg(nome, jsonb_array_length(linhas) ORDER BY nome)
) AS diagnostico
FROM tabelas;

ROLLBACK;
