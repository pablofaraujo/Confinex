-- Diagnóstico de proveniência: não importa nem concilia pagamentos.
-- A saída contém identificadores privados; não publicar o resultado.
BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '1s';
SET LOCAL idle_in_transaction_session_timeout = '20s';

WITH tabelas AS (
  SELECT 'fontes_importacao' AS nome,
         coalesce(jsonb_agg(to_jsonb(t) ORDER BY id), '[]'::jsonb) AS linhas
  FROM (SELECT id, tipo, hash_sha256, periodo_inicio, periodo_fim,
               quantidade_registros::text
        FROM public.fontes_importacao) t
  UNION ALL
  SELECT 'transacoes_banco_staging',
         coalesce(jsonb_agg(to_jsonb(t) ORDER BY id), '[]'::jsonb)
  FROM (SELECT id, fonte_importacao_id, conta, fitid, data, valor::text,
               transacao_banco_id,
               CASE WHEN jsonb_typeof(dados_origem->'arquivo_sha256') = 'string'
                          AND dados_origem->>'arquivo_sha256' ~ '^[a-fA-F0-9]{64}$'
                    THEN dados_origem->>'arquivo_sha256' END AS arquivo_sha256,
               (dados_origem ? 'arquivo_sha256') AS hash_origem_presente
        FROM public.transacoes_banco_staging) t
)
SELECT jsonb_build_object(
  'versao', 1,
  'modo', 'somente_leitura',
  'papel_sql', current_user,
  'tabelas', jsonb_object_agg(nome, linhas ORDER BY nome),
  'contagens', jsonb_object_agg(nome, jsonb_array_length(linhas) ORDER BY nome)
) AS proveniencia
FROM tabelas;

ROLLBACK;
