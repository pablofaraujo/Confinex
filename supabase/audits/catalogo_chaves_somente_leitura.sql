BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;
SET LOCAL statement_timeout = '15s';
SET LOCAL lock_timeout = '2s';

WITH objetos AS MATERIALIZED (
  SELECT
    rel.oid,
    rel.relname,
    rel.relkind,
    rel.relrowsecurity,
    rel.relforcerowsecurity
  FROM pg_catalog.pg_class AS rel
  JOIN pg_catalog.pg_namespace AS ns
    ON ns.oid = rel.relnamespace
  WHERE ns.nspname = 'public'
    AND rel.relkind IN ('r', 'p', 'v', 'm')
)
SELECT pg_catalog.jsonb_build_object(
  'versao', 1,
  'fonte', 'pg_catalog',
  'esquema', 'public',
  'somente_leitura', true,
  'objetos', coalesce((
    SELECT pg_catalog.jsonb_agg(
      pg_catalog.jsonb_build_object(
        'nome', objeto.relname,
        'tipo', objeto.relkind,
        'rls', objeto.relrowsecurity,
        'rls_forcada', objeto.relforcerowsecurity,
        'colunas', coalesce((
          SELECT pg_catalog.jsonb_agg(
            pg_catalog.jsonb_build_object(
              'nome', atributo.attname,
              'posicao', atributo.attnum,
              'tipo', pg_catalog.format_type(atributo.atttypid, atributo.atttypmod),
              'nao_nulo', atributo.attnotnull
            )
            ORDER BY atributo.attnum
          )
          FROM pg_catalog.pg_attribute AS atributo
          WHERE atributo.attrelid = objeto.oid
            AND atributo.attnum > 0
            AND NOT atributo.attisdropped
        ), '[]'::pg_catalog.jsonb)
      )
      ORDER BY objeto.relname, objeto.relkind
    )
    FROM objetos AS objeto
  ), '[]'::pg_catalog.jsonb),
  'restricoes', coalesce((
    SELECT pg_catalog.jsonb_agg(
      pg_catalog.jsonb_build_object(
        'tabela', tabela.relname,
        'nome', restricao.conname,
        'tipo', CASE restricao.contype
          WHEN 'p' THEN 'p'
          WHEN 'u' THEN 'u'
          WHEN 'f' THEN 'f'
          WHEN 'x' THEN 'x'
        END,
        'colunas', coalesce((
          SELECT pg_catalog.jsonb_agg(
            pg_catalog.to_jsonb(coluna.attname)
            ORDER BY ordinal.ordinalidade
          )
          FROM pg_catalog.generate_subscripts(restricao.conkey, 1)
            AS ordinal(ordinalidade)
          LEFT JOIN pg_catalog.pg_attribute AS coluna
            ON coluna.attrelid = restricao.conrelid
           AND coluna.attnum = restricao.conkey[ordinal.ordinalidade]
           AND NOT coluna.attisdropped
        ), '[]'::pg_catalog.jsonb),
        'referencia', CASE WHEN restricao.contype = 'f' THEN
          pg_catalog.jsonb_build_object(
            'esquema', referencia_ns.nspname,
            'tabela', referencia_tabela.relname,
            'colunas', coalesce((
              SELECT pg_catalog.jsonb_agg(
                pg_catalog.to_jsonb(referencia_coluna.attname)
                ORDER BY referencia_ordinal.ordinalidade
              )
              FROM pg_catalog.generate_subscripts(restricao.confkey, 1)
                AS referencia_ordinal(ordinalidade)
              LEFT JOIN pg_catalog.pg_attribute AS referencia_coluna
                ON referencia_coluna.attrelid = restricao.confrelid
               AND referencia_coluna.attnum = restricao.confkey[referencia_ordinal.ordinalidade]
               AND NOT referencia_coluna.attisdropped
            ), '[]'::pg_catalog.jsonb)
          )
          ELSE NULL::pg_catalog.jsonb
        END,
        'validada', restricao.convalidated,
        'herdada', (NOT restricao.conislocal OR restricao.coninhcount > 0),
        'indice', CASE WHEN restricao.contype IN ('p', 'u', 'x') THEN
          indice_suporte.relname
          ELSE NULL
        END
      )
      ORDER BY tabela.relname, restricao.conname, restricao.contype
    )
    FROM pg_catalog.pg_constraint AS restricao
    JOIN objetos AS tabela
      ON tabela.oid = restricao.conrelid
    LEFT JOIN pg_catalog.pg_class AS referencia_tabela
      ON referencia_tabela.oid = restricao.confrelid
    LEFT JOIN pg_catalog.pg_namespace AS referencia_ns
      ON referencia_ns.oid = referencia_tabela.relnamespace
    LEFT JOIN pg_catalog.pg_class AS indice_suporte
      ON indice_suporte.oid = restricao.conindid
    WHERE restricao.contype IN ('p', 'u', 'f', 'x')
  ), '[]'::pg_catalog.jsonb),
  'indices', coalesce((
    SELECT pg_catalog.jsonb_agg(
      pg_catalog.jsonb_build_object(
        'tabela', tabela.relname,
        'nome', indice.relname,
        'unico', pg_indice.indisunique,
        'primario', pg_indice.indisprimary,
        'valido', pg_indice.indisvalid,
        'pronto', pg_indice.indisready,
        'vivo', pg_indice.indislive,
        'parcial', (pg_indice.indpred IS NOT NULL),
        'expressao', (
          pg_indice.indexprs IS NOT NULL
          OR EXISTS (
            SELECT 1
            FROM pg_catalog.unnest(pg_indice.indkey) WITH ORDINALITY
              AS expressao_ordinal(indice_attnum, ordinalidade)
            WHERE expressao_ordinal.indice_attnum = 0
          )
        ),
        'colunas', coalesce((
          SELECT pg_catalog.jsonb_agg(
            pg_catalog.to_jsonb(coluna.attname)
            ORDER BY ordinal.ordinalidade
          )
          FROM pg_catalog.unnest(pg_indice.indkey) WITH ORDINALITY
            AS ordinal(indice_attnum, ordinalidade)
          LEFT JOIN pg_catalog.pg_attribute AS coluna
            ON coluna.attrelid = pg_indice.indrelid
           AND coluna.attnum = ordinal.indice_attnum
           AND NOT coluna.attisdropped
          WHERE ordinal.ordinalidade <= pg_indice.indnkeyatts
        ), '[]'::pg_catalog.jsonb),
        'incluidas', coalesce((
          SELECT pg_catalog.jsonb_agg(
            pg_catalog.to_jsonb(coluna_incluida.attname)
            ORDER BY inclusao_ordinal.ordinalidade
          )
          FROM pg_catalog.unnest(pg_indice.indkey) WITH ORDINALITY
            AS inclusao_ordinal(indice_attnum, ordinalidade)
          JOIN pg_catalog.pg_attribute AS coluna_incluida
            ON coluna_incluida.attrelid = pg_indice.indrelid
           AND coluna_incluida.attnum = inclusao_ordinal.indice_attnum
           AND NOT coluna_incluida.attisdropped
          WHERE inclusao_ordinal.ordinalidade > pg_indice.indnkeyatts
            AND inclusao_ordinal.ordinalidade <= pg_indice.indnatts
        ), '[]'::pg_catalog.jsonb),
        'restricao_propria', (
          SELECT restricao.conname
          FROM pg_catalog.pg_constraint AS restricao
          WHERE restricao.conrelid = pg_indice.indrelid
            AND restricao.conindid = pg_indice.indexrelid
            AND restricao.contype IN ('p', 'u', 'x')
          ORDER BY restricao.conname
          LIMIT 1
        ),
        'nulos_nao_distintos', pg_indice.indnullsnotdistinct
      )
      ORDER BY tabela.relname, indice.relname
    )
    FROM pg_catalog.pg_index AS pg_indice
    JOIN objetos AS tabela
      ON tabela.oid = pg_indice.indrelid
    JOIN pg_catalog.pg_class AS indice
      ON indice.oid = pg_indice.indexrelid
  ), '[]'::pg_catalog.jsonb)
) AS catalogo;

ROLLBACK;
