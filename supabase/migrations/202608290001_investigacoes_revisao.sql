-- Plano de controle auditável para investigar pendências antes da Revisão.
-- A migração é aditiva e não cria consumidores, timers, triggers de origem ou
-- dados. Conteúdo bruto continua nas fontes privadas e no staging canônico.

BEGIN;

-- O Supabase instala pgcrypto no schema `extensions`. Falhar antes de qualquer
-- DDL se o ambiente divergir evita criar funções cuja resolução dependa do
-- extra_search_path da API.
DO $$
DECLARE
  v_extensao oid;
  v_extensao_owner oid;
  v_funcao regprocedure;
  v_papel oid;
BEGIN
  SELECT extensao.oid, extensao.extowner
    INTO v_extensao, v_extensao_owner
    FROM pg_extension extensao
    JOIN pg_namespace esquema ON esquema.oid = extensao.extnamespace
   WHERE extensao.extname = 'pgcrypto'
     AND esquema.nspname = 'extensions';
  IF v_extensao IS NULL
     OR to_regprocedure('extensions.digest(bytea,text)') IS NULL
     OR to_regprocedure('extensions.hmac(bytea,bytea,text)') IS NULL
     OR to_regprocedure('extensions.gen_random_bytes(integer)') IS NULL THEN
    RAISE EXCEPTION 'Pré-requisito ausente: primitivas pgcrypto devem existir em extensions';
  END IF;
  FOREACH v_funcao IN ARRAY ARRAY[
    'extensions.digest(bytea,text)'::regprocedure,
    'extensions.hmac(bytea,bytea,text)'::regprocedure,
    'extensions.gen_random_bytes(integer)'::regprocedure
  ] LOOP
    IF NOT EXISTS (
      SELECT 1
        FROM pg_proc funcao
        JOIN pg_depend dependencia
          ON dependencia.classid = 'pg_proc'::regclass
         AND dependencia.objid = funcao.oid
         AND dependencia.refclassid = 'pg_extension'::regclass
         AND dependencia.refobjid = v_extensao
         AND dependencia.deptype = 'e'
       WHERE funcao.oid = v_funcao::oid
         AND funcao.proowner = v_extensao_owner
    ) THEN
      RAISE EXCEPTION 'Primitiva criptográfica não pertence à extensão pgcrypto confiável: %', v_funcao;
    END IF;
  END LOOP;
  IF EXISTS (
    SELECT 1
      FROM pg_namespace esquema
      CROSS JOIN LATERAL aclexplode(
        coalesce(esquema.nspacl, acldefault('n', esquema.nspowner))
      ) privilegio
     WHERE esquema.nspname = 'extensions'
       AND privilegio.grantee = 0
       AND privilegio.privilege_type = 'CREATE'
  ) THEN
    RAISE EXCEPTION 'PUBLIC não pode criar objetos no schema extensions';
  END IF;
  FOR v_papel IN
    SELECT oid FROM pg_roles
     WHERE rolname IN ('anon', 'authenticated', 'service_role')
  LOOP
    IF has_schema_privilege(v_papel, 'extensions', 'CREATE')
       OR pg_has_role(v_papel, v_extensao_owner, 'MEMBER')
       OR EXISTS (
         SELECT 1 FROM pg_roles
          WHERE oid = v_papel AND rolsuper
       ) THEN
      RAISE EXCEPTION 'Papel da aplicação pode substituir/remover a primitiva pgcrypto';
    END IF;
  END LOOP;
END;
$$;

-- Serialização canônica independente da ordem original do JSON. O mesmo
-- contrato é usado pelo adaptador ao assinar o bundle inteiro; a prova de
-- cobertura nunca assina apenas o rótulo informado pelo chamador.
CREATE OR REPLACE FUNCTION public.investigacao_json_canonico(p_payload jsonb)
RETURNS text
LANGUAGE plpgsql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_resultado text;
BEGIN
  CASE jsonb_typeof(p_payload)
    WHEN 'object' THEN
      SELECT '{' || coalesce(string_agg(
        to_jsonb(item.key)::text || ':' ||
          public.investigacao_json_canonico(item.value),
        ',' ORDER BY item.key COLLATE "C"
      ), '') || '}'
        INTO v_resultado
        FROM jsonb_each(p_payload) item;
    WHEN 'array' THEN
      SELECT '[' || coalesce(string_agg(
        public.investigacao_json_canonico(item.value),
        ',' ORDER BY item.ordinalidade
      ), '') || ']'
        INTO v_resultado
        FROM jsonb_array_elements(p_payload)
             WITH ORDINALITY AS item(value, ordinalidade);
    ELSE
      v_resultado := p_payload::text;
  END CASE;
  RETURN v_resultado;
END;
$$;

CREATE OR REPLACE FUNCTION public.investigacao_hex_igual_constante(
  p_a text,
  p_b text
)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
  SELECT p_a ~ '^[0-9a-f]{64}$'
    AND p_b ~ '^[0-9a-f]{64}$'
    AND (
      SELECT bit_or(get_byte(decode(p_a, 'hex'), indice)
                    # get_byte(decode(p_b, 'hex'), indice)) = 0
        FROM generate_series(0, 31) AS indice
    );
$$;

CREATE OR REPLACE FUNCTION public.investigacao_json_sanitizado(p_payload jsonb)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_chave text;
  v_chave_normalizada text;
  v_valor jsonb;
  v_texto text;
BEGIN
  IF jsonb_typeof(p_payload) = 'object' THEN
    FOR v_chave, v_valor IN SELECT * FROM pg_catalog.jsonb_each(p_payload) LOOP
      -- Contratos persistidos usam nomes de campos técnicos ASCII. Rejeitar
      -- qualquer chave fora desse alfabeto evita variantes visuais como
      -- "sênha" contornarem a política de segredos.
      IF v_chave !~ '^[A-Za-z0-9_. -]+$' THEN
        RETURN false;
      END IF;
      v_chave_normalizada := trim(both '_' FROM pg_catalog.regexp_replace(
        lower(v_chave), '[^a-z0-9]+', '_', 'g'
      ));
      IF v_chave_normalizada = ANY (ARRAY[
        'token', 'access_token', 'refresh_token', 'authorization', 'senha',
        'password', 'secret', 'service_role', 'service_role_key', 'apikey',
        'api_key', 'headers', 'cookie', 'cookies', 'json_bruto',
        'conteudo_bruto', 'mensagem_bruta', 'payload_bruto', 'xml_bruto',
        'ofx_bruto', 'documento_bruto', 'conversa_integral',
        'jid', 'telefone', 'email'
      ])
         OR v_chave_normalizada ~ '(authorization|token|password|senha|secret|cookie|service_role|api_key|apikey|headers)' THEN
        RETURN false;
      END IF;
      IF v_chave_normalizada LIKE '%\_hash' ESCAPE '\'
         AND jsonb_typeof(v_valor) = 'string'
         AND v_valor #>> '{}' ~ '^[0-9a-f]{64}$' THEN
        CONTINUE;
      END IF;
      IF (v_chave_normalizada LIKE '%\_ref' ESCAPE '\'
           OR v_chave_normalizada = 'chave_idempotencia')
         AND jsonb_typeof(v_valor) = 'string'
         AND v_valor #>> '{}' ~ '^[a-z][a-z0-9-]{1,30}_[0-9a-f]{32}$' THEN
        CONTINUE;
      END IF;
      IF (v_chave_normalizada = 'id'
           OR v_chave_normalizada LIKE '%\_id' ESCAPE '\')
         AND jsonb_typeof(v_valor) = 'string'
         AND v_valor #>> '{}' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$' THEN
        CONTINUE;
      END IF;
      IF v_chave_normalizada = 'linhagem'
         AND jsonb_typeof(v_valor) = 'string'
         AND v_valor #>> '{}' ~ '^lin_[0-9a-f]{32}$' THEN
        CONTINUE;
      END IF;
      IF NOT public.investigacao_json_sanitizado(v_valor) THEN
        RETURN false;
      END IF;
    END LOOP;
  ELSIF jsonb_typeof(p_payload) = 'array' THEN
    FOR v_valor IN SELECT * FROM pg_catalog.jsonb_array_elements(p_payload) LOOP
      IF NOT public.investigacao_json_sanitizado(v_valor) THEN
        RETURN false;
      END IF;
    END LOOP;
  ELSIF jsonb_typeof(p_payload) = 'string' THEN
    v_texto := p_payload #>> '{}';
    IF v_texto ~* '\mBearer[[:space:]]+[[:graph:]]+'
       OR v_texto ~* '\m(sk|sbp|eyj)[[:alnum:]_.-]{12,}\M'
       OR v_texto ~* '\m(x[-_ ]?api[-_ ]?key|api[-_ ]?key|authorization|access[-_ ]?token|refresh[-_ ]?token|password|senha|secret)[[:space:]]*[:=][[:space:]]*[^[:space:]]+'
       OR v_texto ~* '[[:alnum:]._%+-]+@[[:alnum:].-]+\.[[:alpha:]]{2,}'
       OR v_texto ~ '(^|[^[:alnum:]-])[0-9]{3}\.?[0-9]{3}\.?[0-9]{3}-?[0-9]{2}([^[:alnum:]-]|$)'
       OR v_texto ~ '(^|[^[:alnum:]-])[0-9]{2}\.?[0-9]{3}\.?[0-9]{3}/?[0-9]{4}-?[0-9]{2}([^[:alnum:]-]|$)'
       OR v_texto ~ '(^|[^[:alnum:]-])(\+?55[ .-]?)?(\(?[0-9]{2}\)?[ .-]?)(9[0-9]{4}|[0-9]{4})[ .-]?[0-9]{4}([^[:alnum:]-]|$)'
       OR v_texto ~* '\m(cpf|cnpj)[^0-9]{0,12}[0-9]{11,14}([^0-9]|$)'
       OR v_texto ~ '(^|[^0-9])[0-9]{44}([^0-9]|$)'
       OR v_texto ~ '(^|[^[:alnum:]-])[0-9]{11,14}([^[:alnum:]-]|$)' THEN
      RETURN false;
    END IF;
  ELSIF jsonb_typeof(p_payload) = 'number' THEN
    -- Identificadores fiscais também chegam como número em OCR/planilhas. No
    -- plano de controle, valores inteiros com 11 a 14 dígitos devem ser
    -- referenciados apenas por hash opaco; grandezas de negócio usam escala
    -- compatível com o domínio pecuário e não dependem dessa faixa inteira.
    v_texto := p_payload::text;
    IF v_texto ~ '^-?(?:[0-9]{11,14}|[0-9]{44})$' THEN
      RETURN false;
    END IF;
  END IF;
  RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION public.investigacao_json_publico_sanitizado(
  p_payload jsonb
)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_chave text;
  v_chave_normalizada text;
  v_valor jsonb;
  v_texto text;
BEGIN
  IF NOT public.investigacao_json_sanitizado(p_payload) THEN
    RETURN false;
  END IF;
  IF jsonb_typeof(p_payload) = 'object' THEN
    FOR v_chave, v_valor IN SELECT * FROM pg_catalog.jsonb_each(p_payload) LOOP
      v_chave_normalizada := trim(both '_' FROM pg_catalog.regexp_replace(
        lower(v_chave), '[^a-z0-9]+', '_', 'g'
      ));
      IF v_chave_normalizada LIKE 'origem\_%' ESCAPE '\'
         OR right(v_chave_normalizada, 3) = '_id'
         OR v_chave_normalizada = ANY (ARRAY[
           'jid', 'telefone', 'email', 'grupo_telegram_id',
           'chave_nfe', 'gta_qualificada', 'fitid_qualificado',
           'hash_anexo', 'identidade_registro', 'chave_natural'
         ]) THEN
        RETURN false;
      END IF;
      IF NOT public.investigacao_json_publico_sanitizado(v_valor) THEN
        RETURN false;
      END IF;
    END LOOP;
  ELSIF jsonb_typeof(p_payload) = 'array' THEN
    FOR v_valor IN SELECT * FROM pg_catalog.jsonb_array_elements(p_payload) LOOP
      IF NOT public.investigacao_json_publico_sanitizado(v_valor) THEN
        RETURN false;
      END IF;
    END LOOP;
  ELSIF jsonb_typeof(p_payload) = 'string' THEN
    v_texto := p_payload #>> '{}';
    IF v_texto ~* '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
       OR v_texto ~* '\m(nfe|chave[ _-]?(nfe|fiscal))[^0-9]{0,16}[0-9]{44}([^0-9]|$)'
       OR v_texto ~ '(^|[^0-9])[0-9]{44}([^0-9]|$)'
       OR v_texto ~ '(^|[^0-9])[0-9]{11,14}([^0-9]|$)' THEN
      RETURN false;
    END IF;
  ELSIF jsonb_typeof(p_payload) = 'number' THEN
    v_texto := p_payload::text;
    IF v_texto ~ '^-?(?:[0-9]{11,14}|[0-9]{44})$' THEN
      RETURN false;
    END IF;
  END IF;
  RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION public.investigacao_texto_sanitizado(p_texto text)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$ SELECT public.investigacao_json_sanitizado(pg_catalog.to_jsonb(p_texto)); $$;

-- Textos projetados para o frontend seguem o contrato público mais restritivo:
-- além de segredos e contatos, nenhum UUID técnico pode atravessar a view.
CREATE OR REPLACE FUNCTION public.investigacao_texto_publico_sanitizado(
  p_texto text
)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
  SELECT public.investigacao_json_publico_sanitizado(
    pg_catalog.to_jsonb(p_texto)
  );
$$;

CREATE OR REPLACE FUNCTION public.investigacao_uuid_texto_seguro(p_texto text)
RETURNS uuid
LANGUAGE plpgsql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
BEGIN
  RETURN p_texto::uuid;
EXCEPTION WHEN invalid_text_representation THEN
  RETURN NULL;
END;
$$;

-- operation_drafts e registros históricos não possuem CHECK de forma para os
-- vínculos de staging. JSON null, escalar ou objeto nunca deve abortar o gate,
-- tampouco mascarar valores válidos presentes na outra fonte. Esta função é o
-- único parser desses vínculos: une e conserva apenas UUIDs válidos e ordenados.
CREATE OR REPLACE FUNCTION public.investigacao_ids_candidatos_rascunho(
  p_inferencias jsonb,
  p_dados_extraidos jsonb
)
RETURNS uuid[]
LANGUAGE plpgsql
IMMUTABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_ids uuid[];
BEGIN
  SELECT coalesce(array_agg(DISTINCT id ORDER BY id), '{}'::uuid[])
    INTO v_ids
    FROM (
      SELECT public.investigacao_uuid_texto_seguro(item #>> '{}') AS id
        FROM jsonb_array_elements(CASE
          WHEN jsonb_typeof(p_inferencias -> 'staging_candidato_ids') = 'array'
            THEN p_inferencias -> 'staging_candidato_ids'
          ELSE '[]'::jsonb
        END) AS item
      UNION ALL
      SELECT public.investigacao_uuid_texto_seguro(item #>> '{}')
        FROM jsonb_array_elements(CASE
          WHEN jsonb_typeof(p_dados_extraidos -> 'staging_candidato_ids') = 'array'
            THEN p_dados_extraidos -> 'staging_candidato_ids'
          ELSE '[]'::jsonb
        END) AS item
      UNION ALL
      SELECT public.investigacao_uuid_texto_seguro(
        p_inferencias ->> 'staging_candidato_id'
      )
      UNION ALL
      SELECT public.investigacao_uuid_texto_seguro(
        p_dados_extraidos ->> 'staging_candidato_id'
      )
    ) AS candidatos
   WHERE id IS NOT NULL;
  RETURN v_ids;
END;
$$;

CREATE OR REPLACE FUNCTION public.investigacao_jsonb_primeiro_valor(
  p_dados jsonb,
  p_caminhos text[]
)
RETURNS jsonb
LANGUAGE plpgsql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
  v_caminho text;
  v_valor jsonb;
BEGIN
  FOREACH v_caminho IN ARRAY p_caminhos LOOP
    v_valor := p_dados #> string_to_array(v_caminho, '.');
    IF v_valor IS NOT NULL
       AND v_valor <> 'null'::jsonb
       AND NOT (
         jsonb_typeof(v_valor) = 'string'
         AND btrim(v_valor #>> '{}') = ''
       ) THEN
      RETURN v_valor;
    END IF;
  END LOOP;
  RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION public.investigacao_instante_operacional(
  p_instante timestamptz
)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
  SELECT isfinite(p_instante)
    AND p_instante >= timestamptz '2000-01-01 00:00:00+00'
    AND p_instante < timestamptz '2200-01-01 00:00:00+00';
$$;

CREATE OR REPLACE FUNCTION public.investigacao_instante_texto_seguro(
  p_texto text
)
RETURNS timestamptz
LANGUAGE plpgsql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
DECLARE
  v_instante timestamptz;
BEGIN
  -- Exige fuso explícito para a conversão não depender da sessão do executor.
  IF p_texto !~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}[T ][0-9]{2}:[0-9]{2}:[0-9]{2}([.][0-9]{1,6})?(Z|[+-][0-9]{2}:[0-9]{2})$' THEN
    RETURN NULL;
  END IF;
  v_instante := p_texto::timestamptz;
  RETURN CASE
    WHEN public.investigacao_instante_operacional(v_instante) THEN v_instante
    ELSE NULL
  END;
EXCEPTION WHEN datetime_field_overflow OR invalid_datetime_format THEN
  RETURN NULL;
END;
$$;

CREATE OR REPLACE FUNCTION public.investigacao_uuid_array_unico(p_ids uuid[])
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
  SELECT cardinality(p_ids) = (
    SELECT count(DISTINCT item) FROM unnest(p_ids) AS item
  );
$$;

CREATE OR REPLACE FUNCTION public.investigacao_text_array_unico(p_itens text[])
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
  SELECT cardinality(p_itens) = (
    SELECT count(DISTINCT item) FROM unnest(p_itens) AS item
  ) AND NOT EXISTS (
    SELECT 1 FROM unnest(p_itens) AS item
     WHERE item IS NULL OR item IS DISTINCT FROM btrim(item) OR item = ''
  );
$$;

CREATE OR REPLACE FUNCTION public.investigacao_campos_obrigatorios_validos(
  p_campos text[]
)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
  SELECT NOT EXISTS (
      SELECT 1 FROM unnest(p_campos) AS campo
       WHERE campo IS NULL
          OR campo IS DISTINCT FROM btrim(campo)
          OR campo !~ '^[a-z][a-z0-9_]{0,62}$'
    )
    AND cardinality(p_campos) = (
      SELECT count(DISTINCT campo) FROM unnest(p_campos) AS campo
    );
$$;

CREATE OR REPLACE FUNCTION public.investigacao_uuid_array_corresponde_objeto(
  p_ids uuid[],
  p_objeto jsonb
)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
  SELECT jsonb_typeof(p_objeto) = 'object'
    AND NOT EXISTS (
      SELECT 1 FROM jsonb_object_keys(p_objeto) AS chave
       WHERE public.investigacao_uuid_texto_seguro(chave) IS NULL
          OR chave IS DISTINCT FROM
               public.investigacao_uuid_texto_seguro(chave)::text
    )
    AND (
      SELECT coalesce(array_agg(
        public.investigacao_uuid_texto_seguro(chave)
        ORDER BY public.investigacao_uuid_texto_seguro(chave)
      ), '{}'::uuid[])
        FROM jsonb_object_keys(p_objeto) AS chave
    ) = (
      SELECT coalesce(array_agg(item ORDER BY item), '{}'::uuid[])
        FROM (SELECT DISTINCT unnest(p_ids) AS item) AS ids
    );
$$;

CREATE OR REPLACE FUNCTION public.investigacao_snapshots_candidatos_validos(
  p_ids uuid[],
  p_objeto jsonb,
  p_principal uuid,
  p_principal_atualizado_em timestamptz
)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_item record;
BEGIN
  IF NOT public.investigacao_uuid_array_corresponde_objeto(p_ids, p_objeto) THEN
    RETURN false;
  END IF;
  FOR v_item IN SELECT chave, valor FROM jsonb_each(p_objeto) AS item(chave, valor)
  LOOP
    IF jsonb_typeof(v_item.valor) IS DISTINCT FROM 'string'
       OR public.investigacao_instante_texto_seguro(v_item.valor #>> '{}') IS NULL THEN
      RETURN false;
    END IF;
  END LOOP;
  IF cardinality(p_ids) = 0 THEN
    RETURN p_principal IS NULL AND p_principal_atualizado_em IS NULL;
  END IF;
  RETURN p_principal IS NOT NULL
    AND p_principal_atualizado_em IS NOT NULL
    AND (p_principal = ANY (p_ids)) IS TRUE
    AND p_objeto ? p_principal::text
    AND public.investigacao_instante_texto_seguro(
      p_objeto ->> p_principal::text
    ) IS NOT DISTINCT FROM p_principal_atualizado_em;
END;
$$;

CREATE OR REPLACE FUNCTION public.investigacao_jsonb_objeto_tamanho(p_objeto jsonb)
RETURNS integer
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
  SELECT CASE
    WHEN jsonb_typeof(p_objeto) = 'object'
    THEN (SELECT count(*)::integer FROM jsonb_object_keys(p_objeto))
    ELSE -1
  END;
$$;

CREATE OR REPLACE FUNCTION public.investigacao_consulta_spec_valida(p_spec jsonb)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
  SELECT jsonb_typeof(p_spec) = 'object'
    AND p_spec - ARRAY[
      'tipo', 'pergunta', 'termos', 'campos', 'janela_inicio', 'janela_fim',
      'limite', 'paginacao', 'cobertura_esperada'
    ] = '{}'::jsonb
    AND (SELECT count(*) FROM jsonb_object_keys(p_spec)) = 9
    AND jsonb_typeof(p_spec -> 'tipo') = 'string'
    AND jsonb_typeof(p_spec -> 'pergunta') = 'string'
    AND jsonb_typeof(p_spec -> 'termos') = 'array'
    AND jsonb_typeof(p_spec -> 'campos') = 'array'
    AND jsonb_typeof(p_spec -> 'janela_inicio') = 'string'
    AND jsonb_typeof(p_spec -> 'janela_fim') = 'string'
    AND jsonb_typeof(p_spec -> 'limite') = 'number'
    AND (p_spec ->> 'limite')::integer BETWEEN 1 AND 1000
    AND jsonb_typeof(p_spec -> 'paginacao') = 'string'
    AND jsonb_typeof(p_spec -> 'cobertura_esperada') = 'string'
    AND NOT EXISTS (
      SELECT 1 FROM jsonb_array_elements(p_spec -> 'termos') item
       WHERE jsonb_typeof(item) <> 'string'
    )
    AND NOT EXISTS (
      SELECT 1 FROM jsonb_array_elements(p_spec -> 'campos') item
       WHERE jsonb_typeof(item) <> 'string'
    )
    AND public.investigacao_json_sanitizado(p_spec);
$$;

CREATE OR REPLACE FUNCTION public.investigacao_confianca_campos_valida(
  p_campos jsonb
)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_campo text;
  v_avaliacao jsonb;
  v_contexto jsonb;
  v_base jsonb;
  v_entrada jsonb;
  v_confianca numeric;
  v_limite numeric;
  v_limite_base numeric;
  v_classificacao text;
  v_canonico text;
  v_caps_requeridos text[];
  v_penalidades_requeridas text[];
BEGIN
  IF jsonb_typeof(p_campos) <> 'object' OR p_campos = '{}'::jsonb
     OR NOT public.investigacao_json_sanitizado(p_campos) THEN
    RETURN false;
  END IF;
  FOR v_campo, v_avaliacao IN SELECT * FROM jsonb_each(p_campos) LOOP
    IF btrim(v_campo) = '' OR jsonb_typeof(v_avaliacao) <> 'object'
       OR v_avaliacao - ARRAY[
         'classificacao', 'confianca', 'regra_id', 'regra_version',
         'policy_version', 'avaliador', 'ruleset_hash', 'inputs_hash',
         'inputs_contexto', 'inputs_canonico', 'linhagens', 'penalidades', 'caps'
       ] <> '{}'::jsonb
       OR (SELECT count(*) FROM jsonb_object_keys(v_avaliacao)) <> 13
       OR jsonb_typeof(v_avaliacao -> 'classificacao') <> 'string'
       OR (v_avaliacao ->> 'classificacao' IN (
         'inconclusivo', 'possivel', 'provavel', 'forte'
       )) IS NOT TRUE
       OR jsonb_typeof(v_avaliacao -> 'confianca') <> 'number'
       OR jsonb_typeof(v_avaliacao -> 'regra_id') <> 'string'
       OR jsonb_typeof(v_avaliacao -> 'regra_version') <> 'string'
       OR jsonb_typeof(v_avaliacao -> 'policy_version') <> 'string'
       OR jsonb_typeof(v_avaliacao -> 'avaliador') <> 'string'
       OR jsonb_typeof(v_avaliacao -> 'ruleset_hash') <> 'string'
       OR jsonb_typeof(v_avaliacao -> 'inputs_hash') <> 'string'
       OR jsonb_typeof(v_avaliacao -> 'inputs_contexto') <> 'object'
       OR jsonb_typeof(v_avaliacao -> 'inputs_canonico') <> 'string'
       OR btrim(v_avaliacao ->> 'policy_version') = ''
       OR v_avaliacao ->> 'regra_id' <> 'correspondencia_deterministica'
       OR v_avaliacao ->> 'regra_version' <> 'confianca-deterministica-v2'
       OR v_avaliacao ->> 'avaliador' <> 'correlator'
       OR v_avaliacao ->> 'ruleset_hash' <>
          '24982e6a934449e0881331ad16b126382fbbf62cf91295673a147a56c59107b7'
       OR v_avaliacao ->> 'inputs_hash' !~ '^[0-9a-f]{64}$'
       OR jsonb_typeof(v_avaliacao -> 'linhagens') <> 'array'
       OR jsonb_typeof(v_avaliacao -> 'penalidades') <> 'array'
       OR jsonb_typeof(v_avaliacao -> 'caps') <> 'array' THEN
      RETURN false;
    END IF;
    IF EXISTS (
         SELECT 1 FROM jsonb_array_elements(v_avaliacao -> 'linhagens') item
          WHERE jsonb_typeof(item) <> 'string'
             OR item #>> '{}' !~ '^lin_[0-9a-f]{32}$'
       )
       OR jsonb_array_length(v_avaliacao -> 'linhagens') <> (
         SELECT count(DISTINCT item #>> '{}')
           FROM jsonb_array_elements(v_avaliacao -> 'linhagens') item
       )
       OR EXISTS (
         SELECT 1 FROM jsonb_array_elements(v_avaliacao -> 'caps') item
          WHERE jsonb_typeof(item) <> 'string'
             OR item #>> '{}' NOT IN (
               'universo_nao_comprovado', 'unicidade_nao_comprovada',
               'coerencia_nao_comprovada', 'extracao_nao_confirmada',
               'llm_somente_pista', 'ambiguidade_no_campo',
               'grupo_correlacao_nao_verificado'
             )
       )
       OR EXISTS (
         SELECT 1 FROM jsonb_array_elements(v_avaliacao -> 'penalidades') item
          WHERE jsonb_typeof(item) <> 'string'
             OR item #>> '{}' NOT IN (
               'incoerencia_verificada', 'divergencia_central'
             )
       ) THEN
      RETURN false;
    END IF;

    v_classificacao := v_avaliacao ->> 'classificacao';
    v_confianca := (v_avaliacao ->> 'confianca')::numeric;
    IF v_confianca IS DISTINCT FROM (CASE v_classificacao
         WHEN 'inconclusivo' THEN 0::numeric
         WHEN 'possivel' THEN 0.35::numeric
         WHEN 'provavel' THEN 0.7::numeric
         WHEN 'forte' THEN 0.95::numeric
       END) THEN
      RETURN false;
    END IF;
    v_contexto := v_avaliacao -> 'inputs_contexto';
    v_base := v_contexto -> 'base';
    IF v_base - ARRAY[
         'avaliacoes', 'ambiguidade_no_campo', 'grupo_correlacao_verificado'
       ] <> '{}'::jsonb
       OR public.investigacao_jsonb_objeto_tamanho(v_base) <> 3
       OR jsonb_typeof(v_base -> 'avaliacoes') <> 'array'
       OR jsonb_array_length(v_base -> 'avaliacoes') = 0
       OR jsonb_typeof(v_base -> 'ambiguidade_no_campo') <> 'boolean'
       OR jsonb_typeof(v_base -> 'grupo_correlacao_verificado') <> 'boolean' THEN
      RETURN false;
    END IF;
    v_limite_base := 0;
    v_caps_requeridos := '{}'::text[];
    v_penalidades_requeridas := '{}'::text[];
    FOR v_entrada IN SELECT value FROM jsonb_array_elements(v_base -> 'avaliacoes') LOOP
      IF jsonb_typeof(v_entrada) <> 'object'
         OR v_entrada - ARRAY[
           'tipo_correspondencia', 'valor_hash', 'linhagem',
           'identidade_tipo', 'identidade_namespace_hash',
           'identidade_valor_hash',
           'universo_coberto', 'quantidade_correspondencias',
           'coerencia_verificada', 'extracao_confirmada',
           'aritmetica_consistente', 'divergencia_central'
         ] <> '{}'::jsonb
         OR public.investigacao_jsonb_objeto_tamanho(v_entrada) <> 12
         OR jsonb_typeof(v_entrada -> 'tipo_correspondencia') <> 'string'
         OR (v_entrada ->> 'tipo_correspondencia' IN (
           'identificador_exato', 'valor_data_contraparte', 'valor_data',
           'documento_referenciado', 'nome', 'valor', 'extracao_llm', 'ocr',
           'desconhecido'
         )) IS NOT TRUE
         OR jsonb_typeof(v_entrada -> 'valor_hash') <> 'string'
         OR v_entrada ->> 'valor_hash' !~ '^[0-9a-f]{64}$'
         OR jsonb_typeof(v_entrada -> 'identidade_tipo') NOT IN (
           'string', 'null'
         )
         OR (
           v_entrada -> 'identidade_tipo' = 'null'::jsonb
           AND (
             v_entrada -> 'identidade_namespace_hash'
               IS DISTINCT FROM 'null'::jsonb
             OR v_entrada -> 'identidade_valor_hash'
               IS DISTINCT FROM 'null'::jsonb
           )
         )
         OR (
           jsonb_typeof(v_entrada -> 'identidade_tipo') = 'string'
           AND (
             (v_entrada ->> 'identidade_tipo' IN (
               'chave_nfe', 'gta_qualificada', 'fitid_qualificado', 'hash_anexo'
             )) IS NOT TRUE
             OR jsonb_typeof(v_entrada -> 'identidade_namespace_hash')
                  <> 'string'
             OR jsonb_typeof(v_entrada -> 'identidade_valor_hash')
                  <> 'string'
             OR v_entrada ->> 'identidade_namespace_hash' !~ '^[0-9a-f]{64}$'
             OR v_entrada ->> 'identidade_valor_hash' !~ '^[0-9a-f]{64}$'
           )
         )
         OR (
           v_entrada ->> 'tipo_correspondencia' = 'identificador_exato'
           AND v_entrada -> 'identidade_tipo' = 'null'::jsonb
         )
         OR jsonb_typeof(v_entrada -> 'linhagem') <> 'string'
         OR v_entrada ->> 'linhagem' !~ '^lin_[0-9a-f]{32}$'
         OR jsonb_typeof(v_entrada -> 'universo_coberto') <> 'boolean'
         OR jsonb_typeof(v_entrada -> 'divergencia_central') <> 'boolean'
         OR jsonb_typeof(v_entrada -> 'quantidade_correspondencias')
              NOT IN ('number', 'null')
         OR jsonb_typeof(v_entrada -> 'coerencia_verificada')
              NOT IN ('boolean', 'null')
         OR jsonb_typeof(v_entrada -> 'extracao_confirmada')
              IS DISTINCT FROM 'boolean'
         OR v_entrada -> 'aritmetica_consistente'
              IS DISTINCT FROM 'null'::jsonb THEN
        RETURN false;
      END IF;
      v_limite_base := greatest(v_limite_base, CASE v_entrada ->> 'tipo_correspondencia'
        WHEN 'identificador_exato' THEN 0.95
        WHEN 'valor_data_contraparte' THEN 0.7
        WHEN 'valor_data' THEN 0.7
        WHEN 'documento_referenciado' THEN 0.7
        WHEN 'nome' THEN 0.35
        WHEN 'valor' THEN 0.35
        WHEN 'extracao_llm' THEN 0.35
        WHEN 'ocr' THEN 0.35
        ELSE 0
      END);
      IF v_entrada ->> 'tipo_correspondencia' = 'identificador_exato' THEN
        IF coalesce((v_entrada ->> 'universo_coberto')::boolean, false) IS NOT TRUE THEN
          v_caps_requeridos := array_append(v_caps_requeridos, 'universo_nao_comprovado');
        END IF;
        IF nullif(v_entrada ->> 'quantidade_correspondencias', '')::numeric IS DISTINCT FROM 1 THEN
          v_caps_requeridos := array_append(v_caps_requeridos, 'unicidade_nao_comprovada');
        END IF;
        IF coalesce((v_entrada ->> 'coerencia_verificada')::boolean, false) IS NOT TRUE THEN
          v_caps_requeridos := array_append(v_caps_requeridos, 'coerencia_nao_comprovada');
        END IF;
        IF (v_entrada ->> 'coerencia_verificada')::boolean IS FALSE THEN
          v_penalidades_requeridas := array_append(
            v_penalidades_requeridas, 'incoerencia_verificada'
          );
        END IF;
      END IF;
      IF v_entrada ->> 'tipo_correspondencia' = 'extracao_llm' THEN
        v_caps_requeridos := array_append(v_caps_requeridos, 'llm_somente_pista');
      END IF;
      IF (v_entrada ->> 'extracao_confirmada')::boolean IS FALSE
         AND v_entrada ->> 'tipo_correspondencia' IN (
           'identificador_exato', 'valor_data_contraparte', 'valor_data',
           'documento_referenciado'
         )
         AND (v_entrada ->> 'coerencia_verificada')::boolean IS NOT FALSE
         AND (v_entrada ->> 'aritmetica_consistente')::boolean IS NOT FALSE
         AND NOT (v_entrada ->> 'divergencia_central')::boolean THEN
        v_caps_requeridos := array_append(v_caps_requeridos, 'extracao_nao_confirmada');
      END IF;
      IF (v_entrada ->> 'aritmetica_consistente')::boolean IS FALSE
         OR (v_entrada ->> 'divergencia_central')::boolean THEN
        v_penalidades_requeridas := array_append(
          v_penalidades_requeridas, 'divergencia_central'
        );
      END IF;
    END LOOP;
    IF EXISTS (
         SELECT 1
           FROM jsonb_array_elements(v_base -> 'avaliacoes') entrada
          WHERE NOT (v_avaliacao -> 'linhagens' ? (entrada ->> 'linhagem'))
       )
       OR EXISTS (
         SELECT 1
           FROM jsonb_array_elements(v_avaliacao -> 'linhagens') linhagem
          WHERE NOT EXISTS (
            SELECT 1
              FROM jsonb_array_elements(v_base -> 'avaliacoes') entrada
             WHERE entrada ->> 'linhagem' = linhagem #>> '{}'
          )
       ) THEN
      RETURN false;
    END IF;
    IF (v_base ->> 'ambiguidade_no_campo')::boolean THEN
      v_caps_requeridos := array_append(v_caps_requeridos, 'ambiguidade_no_campo');
    END IF;
    IF NOT (v_base ->> 'grupo_correlacao_verificado')::boolean THEN
      v_caps_requeridos := array_append(
        v_caps_requeridos, 'grupo_correlacao_nao_verificado'
      );
    END IF;
    IF EXISTS (
         SELECT 1 FROM unnest(v_caps_requeridos) requerido
          WHERE NOT (v_avaliacao -> 'caps' ? requerido)
       )
       OR EXISTS (
         SELECT 1 FROM unnest(v_penalidades_requeridas) requerido
          WHERE NOT (v_avaliacao -> 'penalidades' ? requerido)
       ) THEN
      RETURN false;
    END IF;

    v_limite := v_limite_base;
    IF (v_avaliacao -> 'caps') ?| ARRAY[
         'universo_nao_comprovado', 'unicidade_nao_comprovada',
         'coerencia_nao_comprovada', 'ambiguidade_no_campo'
       ] THEN
      v_limite := least(v_limite, 0.7);
    END IF;
    IF (v_avaliacao -> 'caps') ?| ARRAY[
         'extracao_nao_confirmada', 'llm_somente_pista',
         'grupo_correlacao_nao_verificado'
       ] THEN
      v_limite := least(v_limite, 0.35);
    END IF;
    IF jsonb_array_length(v_avaliacao -> 'penalidades') > 0 THEN
      v_limite := 0;
    END IF;
    IF v_confianca > v_limite THEN
      RETURN false;
    END IF;

    v_canonico := v_avaliacao ->> 'inputs_canonico';
    IF v_contexto - ARRAY[
         'base', 'classificacao', 'confianca', 'caps', 'penalidades'
       ] <> '{}'::jsonb
       OR public.investigacao_jsonb_objeto_tamanho(v_contexto) <> 5
       OR jsonb_typeof(v_contexto -> 'base') <> 'object'
       OR jsonb_typeof(v_contexto -> 'caps') <> 'array'
       OR jsonb_typeof(v_contexto -> 'penalidades') <> 'array'
       OR v_contexto ->> 'classificacao' IS DISTINCT FROM v_classificacao
       OR (v_contexto ->> 'confianca')::numeric IS DISTINCT FROM v_confianca
       OR v_contexto -> 'caps' IS DISTINCT FROM v_avaliacao -> 'caps'
       OR v_contexto -> 'penalidades' IS DISTINCT FROM v_avaliacao -> 'penalidades'
       OR v_canonico::jsonb IS DISTINCT FROM v_contexto
       OR encode(extensions.digest(convert_to(v_canonico, 'UTF8'), 'sha256'), 'hex')
          IS DISTINCT FROM v_avaliacao ->> 'inputs_hash'
       OR NOT public.investigacao_json_sanitizado(v_contexto) THEN
      RETURN false;
    END IF;
  END LOOP;
  RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION public.investigacao_provas_campos_validas(
  p_fatos jsonb,
  p_provas jsonb,
  p_canonico text,
  p_hash text
)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
  SELECT coalesce((
    jsonb_typeof(p_fatos) = 'object'
    AND p_fatos <> '{}'::jsonb
    AND jsonb_typeof(p_provas) = 'object'
    AND p_provas - ARRAY['versao', 'campos'] = '{}'::jsonb
    AND public.investigacao_jsonb_objeto_tamanho(p_provas) = 2
    AND jsonb_typeof(p_provas -> 'versao') = 'string'
    AND p_provas ->> 'versao' = 'provas-campos-v1'
    AND jsonb_typeof(p_provas -> 'campos') = 'object'
    AND public.investigacao_jsonb_objeto_tamanho(p_provas -> 'campos') =
          public.investigacao_jsonb_objeto_tamanho(p_fatos)
    AND NOT EXISTS (
      SELECT 1 FROM jsonb_object_keys(p_fatos) campo
       WHERE NOT (p_provas -> 'campos' ? campo)
    )
    AND NOT EXISTS (
      SELECT 1 FROM jsonb_each(p_provas -> 'campos') item
       WHERE NOT (p_fatos ? item.key)
          OR jsonb_typeof(item.value) <> 'object'
          OR item.value - ARRAY[
            'criterio', 'identidade_tipo', 'identidade_namespace_hash',
            'identidade_valor_hash'
          ] <> '{}'::jsonb
          OR public.investigacao_jsonb_objeto_tamanho(item.value) <> 4
          OR jsonb_typeof(item.value -> 'criterio') IS DISTINCT FROM 'string'
          OR (item.value ->> 'criterio' IN (
            'identificador_exato', 'valor_data_contraparte', 'valor_data',
            'documento_referenciado', 'nome', 'valor', 'extracao_llm', 'ocr',
            'desconhecido'
          )) IS NOT TRUE
          OR jsonb_typeof(item.value -> 'identidade_tipo') NOT IN (
            'string', 'null'
          )
          OR (
            item.value -> 'identidade_tipo' = 'null'::jsonb
            AND (
              item.value -> 'identidade_namespace_hash'
                IS DISTINCT FROM 'null'::jsonb
              OR item.value -> 'identidade_valor_hash'
                IS DISTINCT FROM 'null'::jsonb
              OR item.value ->> 'criterio' = 'identificador_exato'
            )
          )
          OR (
            jsonb_typeof(item.value -> 'identidade_tipo') = 'string'
            AND (
              (item.value ->> 'identidade_tipo' IN (
                'chave_nfe', 'gta_qualificada', 'fitid_qualificado', 'hash_anexo'
              )) IS NOT TRUE
              OR jsonb_typeof(item.value -> 'identidade_namespace_hash')
                   IS DISTINCT FROM 'string'
              OR jsonb_typeof(item.value -> 'identidade_valor_hash')
                   IS DISTINCT FROM 'string'
              OR item.value ->> 'identidade_namespace_hash' !~ '^[0-9a-f]{64}$'
              OR item.value ->> 'identidade_valor_hash' !~ '^[0-9a-f]{64}$'
            )
          )
    )
    AND p_canonico::jsonb = p_provas
    AND encode(
      extensions.digest(convert_to(p_canonico, 'UTF8'), 'sha256'), 'hex'
    ) = p_hash
  ), false);
$$;

CREATE OR REPLACE FUNCTION public.investigacao_identidade_permitida_adaptador(
  p_adaptador text,
  p_identidade_tipo text
)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
  SELECT CASE
    WHEN p_identidade_tipo IS NULL THEN true
    WHEN p_adaptador = 'agronotas' THEN p_identidade_tipo IN (
      'chave_nfe', 'gta_qualificada', 'hash_anexo'
    )
    WHEN p_adaptador = 'ofx' THEN p_identidade_tipo IN (
      'fitid_qualificado', 'hash_anexo'
    )
    WHEN p_adaptador = 'ima' THEN p_identidade_tipo IN (
      'gta_qualificada', 'hash_anexo'
    )
    WHEN p_adaptador IN ('telegram', 'wey', 'outro')
      THEN p_identidade_tipo = 'hash_anexo'
    ELSE false
  END;
$$;

CREATE OR REPLACE FUNCTION public.investigacao_plano_tarefas_valido(
  p_tarefas jsonb
)
RETURNS boolean
LANGUAGE plpgsql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_item jsonb;
BEGIN
  IF jsonb_typeof(p_tarefas) <> 'array'
     OR jsonb_array_length(p_tarefas) < 2
     OR NOT public.investigacao_json_sanitizado(p_tarefas) THEN
    RETURN false;
  END IF;
  FOR v_item IN SELECT value FROM jsonb_array_elements(p_tarefas) LOOP
    IF jsonb_typeof(v_item) <> 'object'
       OR v_item - ARRAY[
         'plano_item_ref', 'adaptador', 'adaptador_version',
         'consulta_ref', 'consulta_schema_version', 'consulta_spec',
         'consulta_canonico', 'consulta_hash'
       ] <> '{}'::jsonb
       OR public.investigacao_jsonb_objeto_tamanho(v_item) <> 8
       OR jsonb_typeof(v_item -> 'plano_item_ref') IS DISTINCT FROM 'string'
       OR v_item ->> 'plano_item_ref' !~ '^pitem_[0-9a-f]{32}$'
       OR jsonb_typeof(v_item -> 'adaptador') IS DISTINCT FROM 'string'
       OR (v_item ->> 'adaptador' IN (
         'agronotas', 'ofx', 'ima', 'telegram', 'wey', 'outro', 'sintese'
       )) IS NOT TRUE
       OR jsonb_typeof(v_item -> 'adaptador_version') IS DISTINCT FROM 'string'
       OR btrim(v_item ->> 'adaptador_version') = ''
       OR jsonb_typeof(v_item -> 'consulta_ref') IS DISTINCT FROM 'string'
       OR v_item ->> 'consulta_ref' !~ '^qref_[0-9a-f]{32}$'
       OR jsonb_typeof(v_item -> 'consulta_schema_version') IS DISTINCT FROM 'string'
       OR btrim(v_item ->> 'consulta_schema_version') = ''
       OR jsonb_typeof(v_item -> 'consulta_spec') IS DISTINCT FROM 'object'
       OR NOT public.investigacao_consulta_spec_valida(v_item -> 'consulta_spec')
       OR jsonb_typeof(v_item -> 'consulta_canonico') IS DISTINCT FROM 'string'
       OR jsonb_typeof(v_item -> 'consulta_hash') IS DISTINCT FROM 'string'
       OR v_item ->> 'consulta_hash' !~ '^[0-9a-f]{64}$'
       OR (v_item ->> 'consulta_canonico')::jsonb
            IS DISTINCT FROM v_item -> 'consulta_spec'
       OR encode(extensions.digest(
            convert_to(v_item ->> 'consulta_canonico', 'UTF8'), 'sha256'
          ), 'hex') IS DISTINCT FROM v_item ->> 'consulta_hash'
       OR v_item ->> 'consulta_ref'
            IS DISTINCT FROM 'qref_' || left(v_item ->> 'consulta_hash', 32) THEN
      RETURN false;
    END IF;
  END LOOP;
  IF (SELECT count(*) FROM jsonb_array_elements(p_tarefas) item
       WHERE item ->> 'adaptador' = 'sintese') <> 1
     OR (SELECT count(DISTINCT item ->> 'plano_item_ref')
           FROM jsonb_array_elements(p_tarefas) item)
        <> jsonb_array_length(p_tarefas) THEN
    RETURN false;
  END IF;
  RETURN true;
END;
$$;

-- Registro fechado da política de completude. Adaptadores e modelos nunca
-- escolhem os campos que tornam uma investigação suficiente.
CREATE OR REPLACE FUNCTION public.investigacao_politica_campos(
  p_assunto_tipo text,
  p_policy_version text
)
RETURNS text[]
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
  SELECT CASE
    WHEN p_policy_version <> 'investigacao-v1' THEN NULL
    WHEN p_assunto_tipo = 'compra'
      THEN ARRAY['data', 'negocio', 'quantidade', 'valor_total']::text[]
    WHEN p_assunto_tipo = 'venda'
      THEN ARRAY[
        'cabecas', 'data_abate', 'peso_carcaca_total',
        'prazo_recebimento', 'valor_bruto'
      ]::text[]
    WHEN p_assunto_tipo = 'pesagem'
      THEN ARRAY['contexto', 'data_folha', 'peso_kg']::text[]
    WHEN p_assunto_tipo = 'abate'
      THEN ARRAY['cabecas', 'data_abate', 'lote', 'peso_liquido_kg']::text[]
    WHEN p_assunto_tipo = 'documento_fiscal'
      THEN ARRAY[
        'data_emissao', 'numero_nf', 'quantidade',
        'relacao_negocio', 'valor_total'
      ]::text[]
    WHEN p_assunto_tipo = 'conciliacao_financeira'
      THEN ARRAY['contraparte', 'data', 'valor']::text[]
    WHEN p_assunto_tipo = 'acerto_confinamento'
      THEN ARRAY['data', 'negocio', 'valor']::text[]
    WHEN p_assunto_tipo = 'revisao'
      THEN ARRAY['decisao_humana']::text[]
    ELSE NULL
  END;
$$;

CREATE OR REPLACE FUNCTION public.investigacao_politica_schema_hash(
  p_policy_version text
)
RETURNS text
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
  SELECT CASE WHEN p_policy_version = 'investigacao-v1'
    THEN '67cbdc991384e6cb6f7c65ce120e59b481aca204b0b8b316fb723365aa08220e'
    ELSE NULL
  END;
$$;

-- Controle concorrente das promoções novas. Linhas históricas ficam com
-- versão NULL; o gate 0002 exige que promoções ativas tenham sido drenadas e
-- toda nova preparação nasce em lease-v1. Nenhuma coluna altera dado
-- operacional por si só.
ALTER TABLE public.pending_actions
  ADD COLUMN IF NOT EXISTS promocao_controle_version text,
  ADD COLUMN IF NOT EXISTS promocao_lease_executor text,
  ADD COLUMN IF NOT EXISTS promocao_lease_token uuid,
  ADD COLUMN IF NOT EXISTS promocao_lease_expira_em timestamptz,
  ADD COLUMN IF NOT EXISTS promocao_confirmacao_origem_conversa_id text,
  ADD COLUMN IF NOT EXISTS promocao_confirmacao_origem_mensagem_id text,
  ADD COLUMN IF NOT EXISTS promocao_preparacao_chave text,
  ADD COLUMN IF NOT EXISTS promocao_preparacao_hash text,
  ADD COLUMN IF NOT EXISTS promocao_fencing_token bigint NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS promocao_resultado_lease_token uuid,
  ADD COLUMN IF NOT EXISTS promocao_resultado_fencing_token bigint,
  ADD COLUMN IF NOT EXISTS promocao_resultado_pedido_hash text;

-- Vínculo durável entre a promoção e a linha operacional. Compras já usam a
-- chave idempotente persistente; os demais destinos recebem uma referência
-- nullable e única, sem tocar nos registros históricos. Isso impede que um
-- UUID de outra linha semanticamente igual seja aceito como resultado.
ALTER TABLE public.vendas
  ADD COLUMN IF NOT EXISTS promocao_origem_id uuid
    REFERENCES public.pending_actions(id);
ALTER TABLE public.pesagens_caderno
  ADD COLUMN IF NOT EXISTS promocao_origem_id uuid
    REFERENCES public.pending_actions(id);
ALTER TABLE public.abates
  ADD COLUMN IF NOT EXISTS promocao_origem_id uuid
    REFERENCES public.pending_actions(id);

CREATE UNIQUE INDEX IF NOT EXISTS vendas_promocao_origem_id_unica
  ON public.vendas (promocao_origem_id)
  WHERE promocao_origem_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS pesagens_promocao_origem_id_unica
  ON public.pesagens_caderno (promocao_origem_id)
  WHERE promocao_origem_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS abates_promocao_origem_id_unica
  ON public.abates (promocao_origem_id)
  WHERE promocao_origem_id IS NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'public.pending_actions'::regclass
       AND conname = 'pending_actions_promocao_lease_v1_valido'
  ) THEN
    ALTER TABLE public.pending_actions
      ADD CONSTRAINT pending_actions_promocao_lease_v1_valido CHECK (
        (promocao_controle_version IS NULL AND promocao_fencing_token = 0
          AND num_nonnulls(
            promocao_lease_executor, promocao_lease_token,
            promocao_lease_expira_em,
            promocao_confirmacao_origem_conversa_id,
            promocao_confirmacao_origem_mensagem_id,
            promocao_resultado_lease_token,
            promocao_resultado_fencing_token, promocao_resultado_pedido_hash
          ) = 0)
        OR
        (promocao_controle_version = 'lease-v1'
          AND promocao_fencing_token >= 0
          AND (
            (status = 'em_execucao'
              AND num_nonnulls(
                promocao_lease_executor, promocao_lease_token,
                promocao_lease_expira_em,
                promocao_confirmacao_origem_conversa_id,
                promocao_confirmacao_origem_mensagem_id
              ) = 5
              AND num_nonnulls(
                promocao_resultado_lease_token,
                promocao_resultado_fencing_token,
                promocao_resultado_pedido_hash
              ) = 0)
            OR
            (status IN ('executado', 'erro_pos_gravacao', 'erro')
              AND num_nonnulls(
                promocao_lease_executor, promocao_lease_token,
                promocao_lease_expira_em
              ) = 0
              AND num_nonnulls(
                promocao_confirmacao_origem_conversa_id,
                promocao_confirmacao_origem_mensagem_id
              ) = 2
              AND num_nonnulls(
                promocao_resultado_lease_token,
                promocao_resultado_fencing_token,
                promocao_resultado_pedido_hash
              ) = 3)
            OR
            (status NOT IN (
                'em_execucao', 'executado', 'erro_pos_gravacao', 'erro'
              )
              AND num_nonnulls(
                promocao_lease_executor, promocao_lease_token,
                promocao_lease_expira_em,
                promocao_confirmacao_origem_conversa_id,
                promocao_confirmacao_origem_mensagem_id,
                promocao_resultado_lease_token,
                promocao_resultado_fencing_token,
                promocao_resultado_pedido_hash
              ) = 0)
          )
        )
      );
  END IF;
END;
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'public.pending_actions'::regclass
       AND conname = 'pending_actions_promocao_preparacao_interna_valida'
  ) THEN
    ALTER TABLE public.pending_actions
      ADD CONSTRAINT pending_actions_promocao_preparacao_interna_valida CHECK (
        (promocao_controle_version IS NULL
          AND promocao_preparacao_chave IS NULL
          AND promocao_preparacao_hash IS NULL)
        OR
        (promocao_controle_version = 'lease-v1'
          AND btrim(promocao_preparacao_chave) <> ''
          AND promocao_preparacao_hash ~ '^[0-9a-f]{64}$')
      );
  END IF;
END;
$$;

-- Capacidade transacional consumida pelo trigger de pending_actions. O papel
-- service_role pode chamar as RPCs, mas não consegue forjar uma autorização
-- de INSERT/UPDATE direto na promoção.
CREATE TABLE IF NOT EXISTS public.investigacao_autorizacoes_promocao (
  txid bigint NOT NULL,
  backend_pid integer NOT NULL,
  pending_action_id uuid NOT NULL,
  operacao text NOT NULL CHECK (operacao IN ('INSERT', 'UPDATE')),
  status_anterior text,
  status_novo text NOT NULL,
  criado_em timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (txid, backend_pid, pending_action_id, operacao),
  CHECK (public.investigacao_instante_operacional(criado_em))
);

-- Capacidades efêmeras consumidas pelos gatilhos corretivos. O chamador não
-- controla GUCs nem consegue pré-semear estas linhas: somente as RPCs
-- SECURITY DEFINER as criam e cada gatilho apaga sua capacidade exata na
-- mesma transação.
CREATE TABLE IF NOT EXISTS public.investigacao_autorizacoes_corretiva (
  txid bigint NOT NULL,
  backend_pid integer NOT NULL,
  recurso text NOT NULL CHECK (recurso IN (
    'selar_investigacao', 'inserir_acao', 'inserir_draft',
    'vincular_draft', 'anexar_corretiva', 'anexar_draft_corretivo',
    'criar_sucessora', 'obsoletar_predecessora',
    'reativar_complementar', 'obsoletar_investigacao',
    'atestar_decisao', 'criar_sucessora_complementar',
    'consumir_complementar',
    'decidir_acao', 'decidir_draft'
  )),
  investigacao_id uuid NOT NULL,
  operation_draft_id uuid NOT NULL,
  pending_action_id uuid NOT NULL,
  pedido_hash text NOT NULL CHECK (pedido_hash ~ '^[0-9a-f]{64}$'),
  criado_em timestamptz NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (
    txid, backend_pid, recurso, investigacao_id,
    operation_draft_id, pending_action_id
  ),
  CHECK (public.investigacao_instante_operacional(criado_em))
);

CREATE TABLE IF NOT EXISTS public.investigacoes_revisao (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  raiz_investigacao_id uuid REFERENCES public.investigacoes_revisao(id),
  sucessora_de_id uuid UNIQUE REFERENCES public.investigacoes_revisao(id),
  geracao integer NOT NULL DEFAULT 0 CHECK (geracao >= 0),
  sucessao_pedido_hash text CHECK (
    sucessao_pedido_hash IS NULL
    OR sucessao_pedido_hash ~ '^[0-9a-f]{64}$'
  ),
  referencia_publica text NOT NULL DEFAULT (
    'inv_' || encode(extensions.gen_random_bytes(16), 'hex')
  ) UNIQUE CHECK (referencia_publica ~ '^inv_[0-9a-f]{32}$'),
  chave_idempotencia text NOT NULL UNIQUE CHECK (btrim(chave_idempotencia) <> ''),
  assunto_tipo text NOT NULL CHECK (btrim(assunto_tipo) <> ''),
  assunto_referencia text,
  titulo text NOT NULL CHECK (btrim(titulo) <> '') CHECK (public.investigacao_texto_publico_sanitizado(titulo)),
  fluxo_tipo text NOT NULL DEFAULT 'pre_revisao' CHECK (
    fluxo_tipo IN ('pre_revisao', 'corretiva_pos_gravacao')
  ),
  promocao_origem_id uuid REFERENCES public.pending_actions(id),
  draft_operacional_origem_id uuid REFERENCES public.operation_drafts(id),
  destino_operacional_origem text CHECK (
    destino_operacional_origem IS NULL OR destino_operacional_origem IN (
      'compras', 'vendas', 'pesagens_caderno', 'abates'
    )
  ),
  registro_operacional_origem_id uuid,
  registro_operacional_origem_snapshot_ref text CHECK (
    registro_operacional_origem_snapshot_ref IS NULL
    OR registro_operacional_origem_snapshot_ref ~ '^snp_[0-9a-f]{32}$'
  ),
  vinculo_operacional_estado text CHECK (
    vinculo_operacional_estado IS NULL
    OR vinculo_operacional_estado = 'confirmado'
  ),
  source_draft_id uuid REFERENCES public.operation_drafts(id),
  source_draft_atualizado_em timestamptz,
  CHECK (num_nonnulls(source_draft_id, source_draft_atualizado_em) IN (0, 2)),
  CHECK (
    source_draft_atualizado_em IS NULL
    OR public.investigacao_instante_operacional(source_draft_atualizado_em)
  ),
  negocio_candidato_id uuid REFERENCES public.negocios_candidatos(id),
  source_candidato_atualizado_em timestamptz,
  CHECK (
    source_candidato_atualizado_em IS NULL
    OR public.investigacao_instante_operacional(source_candidato_atualizado_em)
  ),
  negocio_candidato_ids uuid[] NOT NULL DEFAULT '{}'::uuid[]
    CHECK (public.investigacao_uuid_array_unico(negocio_candidato_ids)),
  source_candidatos_atualizados_em jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (
      jsonb_typeof(source_candidatos_atualizados_em) = 'object'
      AND public.investigacao_json_sanitizado(source_candidatos_atualizados_em)
    ),
  fingerprint_base text NOT NULL CHECK (fingerprint_base ~ '^[0-9a-f]{64}$'),
  plano_hash text NOT NULL CHECK (plano_hash ~ '^[0-9a-f]{64}$'),
  plano_canonico text NOT NULL CHECK (btrim(plano_canonico) <> ''),
  plano_tarefas jsonb NOT NULL
    CHECK (public.investigacao_plano_tarefas_valido(plano_tarefas)),
  policy_version text NOT NULL CHECK (btrim(policy_version) <> ''),
  policy_schema_hash text NOT NULL CHECK (policy_schema_hash ~ '^[0-9a-f]{64}$'),
  campos_obrigatorios text[] NOT NULL DEFAULT '{}'::text[]
    CHECK (public.investigacao_campos_obrigatorios_validos(campos_obrigatorios)),
  CHECK (
    public.investigacao_politica_campos(assunto_tipo, policy_version) IS NOT NULL
    AND campos_obrigatorios IS NOT DISTINCT FROM
      public.investigacao_politica_campos(assunto_tipo, policy_version)
    AND public.investigacao_politica_schema_hash(policy_version) IS NOT NULL
    AND policy_schema_hash IS NOT DISTINCT FROM
      public.investigacao_politica_schema_hash(policy_version)
  ),
  gatilho_tipo text NOT NULL DEFAULT 'timer' CHECK (gatilho_tipo IN (
    'outbox', 'timer', 'manual', 'backfill'
  )),
  prioridade text NOT NULL DEFAULT 'media' CHECK (prioridade IN ('baixa', 'media', 'alta')),
  contexto_canonico text CHECK (public.investigacao_texto_sanitizado(contexto_canonico)),
  contexto_nome text CHECK (public.investigacao_texto_publico_sanitizado(contexto_nome)),
  origem_canal text,
  origem_conversa_id text,
  origem_mensagem_id text,
  escopo text NOT NULL DEFAULT 'investigacao_operacional',
  estado_execucao text NOT NULL DEFAULT 'pendente' CHECK (estado_execucao IN (
    'pendente', 'em_execucao', 'aguardando_retentativa', 'concluida',
    'cancelada', 'obsoleta'
  )),
  estado_resultado text CHECK (estado_resultado IS NULL OR estado_resultado IN (
    'alternativa_unica', 'alternativas_multiplas', 'divergente',
    'evidencia_insuficiente', 'cobertura_incompleta'
  )),
  resumo_sanitizado text CHECK (public.investigacao_texto_publico_sanitizado(resumo_sanitizado)),
  anexo_chave text UNIQUE,
  anexado_draft_id uuid REFERENCES public.operation_drafts(id),
  anexado_evento_id uuid REFERENCES public.eventos(id),
  anexado_em timestamptz,
  anexado_draft_atualizado_em timestamptz,
  materializacao_pedido_hash text CHECK (
    materializacao_pedido_hash IS NULL
    OR materializacao_pedido_hash ~ '^[0-9a-f]{64}$'
  ),
  decisao_draft_atualizado_em timestamptz,
  decisao_preparacao_hash text CHECK (
    decisao_preparacao_hash IS NULL
    OR decisao_preparacao_hash ~ '^[0-9a-f]{64}$'
  ),
  obsolescencia_motivo text CHECK (
    obsolescencia_motivo IS NULL
    OR obsolescencia_motivo IN (
      'pre_revisao_stale', 'complementar_promocao_ativa',
      'complementar_consumida', 'registro_operacional_stale'
    )
  ),
  promocao_ativa_id uuid REFERENCES public.pending_actions(id),
  criado_por text NOT NULL DEFAULT 'sistema',
  criado_em timestamptz NOT NULL DEFAULT now(),
  atualizado_em timestamptz NOT NULL DEFAULT now(),
  concluida_em timestamptz,
  CHECK (
    (sucessora_de_id IS NULL
      AND raiz_investigacao_id = id AND geracao = 0
      AND sucessao_pedido_hash IS NULL)
    OR
    (sucessora_de_id IS NOT NULL
      AND raiz_investigacao_id IS NOT NULL
      AND raiz_investigacao_id <> id AND geracao > 0
      AND sucessao_pedido_hash IS NOT NULL)
  ),
  CHECK (
    (fluxo_tipo = 'pre_revisao'
      AND num_nonnulls(
        promocao_origem_id, draft_operacional_origem_id,
        destino_operacional_origem, registro_operacional_origem_id,
        registro_operacional_origem_snapshot_ref,
        vinculo_operacional_estado
      ) = 0)
    OR
    (fluxo_tipo = 'corretiva_pos_gravacao'
      AND num_nonnulls(
        promocao_origem_id, draft_operacional_origem_id,
        destino_operacional_origem, registro_operacional_origem_id,
        registro_operacional_origem_snapshot_ref,
        vinculo_operacional_estado
      ) = 6
      AND vinculo_operacional_estado = 'confirmado')
  ),
  CHECK (
    public.investigacao_instante_operacional(criado_em)
    AND public.investigacao_instante_operacional(atualizado_em)
    AND (
      concluida_em IS NULL
      OR public.investigacao_instante_operacional(concluida_em)
    )
    AND (
      anexado_em IS NULL
      OR public.investigacao_instante_operacional(anexado_em)
    )
    AND (
      anexado_draft_atualizado_em IS NULL
      OR public.investigacao_instante_operacional(anexado_draft_atualizado_em)
    )
    AND (
      decisao_draft_atualizado_em IS NULL
      OR public.investigacao_instante_operacional(decisao_draft_atualizado_em)
    )
  ),
  CHECK (
    estado_execucao <> 'concluida'
    OR (estado_resultado IS NOT NULL AND concluida_em IS NOT NULL)
  ),
  CHECK (
    (anexado_em IS NULL AND anexado_draft_atualizado_em IS NULL)
    OR num_nonnulls(
      anexado_draft_id, anexado_evento_id, anexado_draft_atualizado_em
    ) = 3
  ),
  CHECK (
    materializacao_pedido_hash IS NULL
    OR (anexado_em IS NOT NULL AND anexado_draft_id IS NOT NULL)
  ),
  CHECK (
    num_nonnulls(decisao_draft_atualizado_em, decisao_preparacao_hash)
      IN (0, 2)
  ),
  CHECK (
    (estado_execucao <> 'obsoleta'
      AND obsolescencia_motivo IS NULL
      AND promocao_ativa_id IS NULL)
    OR
    (estado_execucao = 'obsoleta'
      AND obsolescencia_motivo = 'pre_revisao_stale'
      AND promocao_ativa_id IS NULL)
    OR
    (estado_execucao = 'obsoleta'
      AND obsolescencia_motivo = 'complementar_promocao_ativa'
      AND promocao_ativa_id IS NOT NULL)
    OR
    (estado_execucao = 'obsoleta'
      AND obsolescencia_motivo = 'complementar_consumida'
      AND promocao_ativa_id IS NULL)
    OR
    (estado_execucao = 'obsoleta'
      AND obsolescencia_motivo = 'registro_operacional_stale'
      AND promocao_ativa_id IS NULL)
  ),
  CHECK (
    cardinality(negocio_candidato_ids) =
      public.investigacao_jsonb_objeto_tamanho(source_candidatos_atualizados_em)
  ),
  CHECK (public.investigacao_uuid_array_corresponde_objeto(
    negocio_candidato_ids,
    source_candidatos_atualizados_em
  )),
  CHECK (public.investigacao_snapshots_candidatos_validos(
    negocio_candidato_ids,
    source_candidatos_atualizados_em,
    negocio_candidato_id,
    source_candidato_atualizado_em
  )),
  CHECK (
    (cardinality(negocio_candidato_ids) = 0
      AND negocio_candidato_id IS NULL
      AND source_candidato_atualizado_em IS NULL)
    OR
    (cardinality(negocio_candidato_ids) > 0
      AND negocio_candidato_id IS NOT NULL
      AND (negocio_candidato_id = ANY (negocio_candidato_ids)) IS TRUE
      AND source_candidato_atualizado_em IS NOT NULL)
  ),
  CHECK (
    negocio_candidato_id IS NULL
    OR source_candidatos_atualizados_em ? negocio_candidato_id::text
  ),
  CHECK (
    plano_canonico::jsonb = jsonb_build_object(
      'tarefas', plano_tarefas,
      'campos_obrigatorios', to_jsonb(campos_obrigatorios),
      'policy_schema_hash', policy_schema_hash
    )
    AND encode(extensions.digest(convert_to(plano_canonico, 'UTF8'), 'sha256'), 'hex')
      = plano_hash
  )
);

-- Outbox terminal independente do planner. A conclusão da promoção grava
-- somente este intent simples; o heartbeat cria sucessoras depois, sem poder
-- desfazer o resultado operacional caso o planner/configuração esteja ruim.
CREATE TABLE IF NOT EXISTS public.investigacao_sucessoes_pendentes (
  id uuid PRIMARY KEY,
  promocao_id uuid NOT NULL UNIQUE REFERENCES public.pending_actions(id),
  status_terminal text NOT NULL CHECK (status_terminal IN (
    'cancelado', 'rejeitado', 'expirado', 'erro',
    'executado', 'erro_pos_gravacao'
  )),
  resultado_terminal_hash text NOT NULL CHECK (
    resultado_terminal_hash ~ '^[0-9a-f]{64}$'
  ),
  pedido_hash text NOT NULL UNIQUE CHECK (pedido_hash ~ '^[0-9a-f]{64}$'),
  classe_desfecho_terminal text NOT NULL CHECK (classe_desfecho_terminal IN (
    'sem_gravacao', 'com_gravacao', 'incerto'
  )),
  estado text NOT NULL DEFAULT 'pendente' CHECK (estado IN (
    'pendente', 'aguardando_reconciliacao', 'aguardando_planejamento',
    'concluida', 'falha_permanente'
  )),
  classe_resolvida text CHECK (
    classe_resolvida IS NULL
    OR classe_resolvida IN ('sem_gravacao', 'com_gravacao')
  ),
  registro_reconciliado_id uuid,
  registro_reconciliado_snapshot_ref text CHECK (
    registro_reconciliado_snapshot_ref IS NULL
    OR registro_reconciliado_snapshot_ref ~ '^snp_[0-9a-f]{32}$'
  ),
  resolucao_hash text CHECK (
    resolucao_hash IS NULL OR resolucao_hash ~ '^[0-9a-f]{64}$'
  ),
  resolucao_versao text CHECK (
    resolucao_versao IS NULL OR resolucao_versao ~ '^[a-z0-9][a-z0-9._-]{0,79}$'
  ),
  resolvida_em timestamptz,
  resolvida_por text CHECK (
    resolvida_por IS NULL OR (
      btrim(resolvida_por) <> ''
      AND octet_length(resolvida_por) <= 160
      AND public.investigacao_texto_sanitizado(resolvida_por)
    )
  ),
  filhas_quantidade integer CHECK (
    filhas_quantidade IS NULL OR filhas_quantidade >= 0
  ),
  filhas_mapa_hash text CHECK (
    filhas_mapa_hash IS NULL OR filhas_mapa_hash ~ '^[0-9a-f]{64}$'
  ),
  -- Hash do pedido de materialização. Para replanejamento ele é o hash do
  -- payload fechado; para o reuso pré-revisão é derivado pelo consumidor.
  -- Assim um retry concluído nunca aceita outro plano só porque o outbox é o
  -- mesmo.
  replanejamento_pedido_hash text CHECK (
    replanejamento_pedido_hash IS NULL
    OR replanejamento_pedido_hash ~ '^[0-9a-f]{64}$'
  ),
  concluida_em timestamptz,
  ultimo_erro_codigo text CHECK (
    ultimo_erro_codigo IS NULL
    OR ultimo_erro_codigo ~ '^[A-Z0-9_]{3,80}$'
  ),
  ultima_varredura_em timestamptz,
  criado_em timestamptz NOT NULL DEFAULT clock_timestamp(),
  atualizado_em timestamptz NOT NULL DEFAULT clock_timestamp(),
  CHECK (
    (classe_resolvida IS NULL
      AND num_nonnulls(
        registro_reconciliado_id, registro_reconciliado_snapshot_ref,
        resolucao_hash, resolucao_versao, resolvida_em, resolvida_por
      ) = 0)
    OR
    (classe_resolvida = 'sem_gravacao'
      AND registro_reconciliado_id IS NULL
      AND registro_reconciliado_snapshot_ref IS NULL
      AND num_nonnulls(
        resolucao_hash, resolucao_versao, resolvida_em, resolvida_por
      ) = 4)
    OR
    (classe_resolvida = 'com_gravacao'
      AND num_nonnulls(
        registro_reconciliado_id, registro_reconciliado_snapshot_ref,
        resolucao_hash, resolucao_versao, resolvida_em, resolvida_por
      ) = 6)
  ),
  CHECK (
    public.investigacao_instante_operacional(criado_em)
    AND public.investigacao_instante_operacional(atualizado_em)
    AND (
      ultima_varredura_em IS NULL
      OR public.investigacao_instante_operacional(ultima_varredura_em)
    )
    AND (
      resolvida_em IS NULL
      OR public.investigacao_instante_operacional(resolvida_em)
    )
    AND (
      concluida_em IS NULL
      OR public.investigacao_instante_operacional(concluida_em)
    )
  ),
  CHECK (
    (estado = 'concluida'
      AND classe_resolvida IS NOT NULL
      AND num_nonnulls(
        filhas_quantidade, filhas_mapa_hash, concluida_em
      ) = 3)
    OR
    (estado <> 'concluida'
      AND num_nonnulls(
        filhas_quantidade, filhas_mapa_hash, concluida_em
      ) = 0)
  )
);

ALTER TABLE public.investigacao_sucessoes_pendentes
  ADD COLUMN IF NOT EXISTS replanejamento_pedido_hash text CHECK (
    replanejamento_pedido_hash IS NULL
    OR replanejamento_pedido_hash ~ '^[0-9a-f]{64}$'
  );

ALTER TABLE public.investigacoes_revisao
  ADD COLUMN IF NOT EXISTS sucessao_outbox_id uuid
    REFERENCES public.investigacao_sucessoes_pendentes(id);

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'public.investigacoes_revisao'::regclass
       AND conname = 'investigacoes_sucessao_outbox_somente_filha'
  ) THEN
    ALTER TABLE public.investigacoes_revisao
      ADD CONSTRAINT investigacoes_sucessao_outbox_somente_filha CHECK (
        sucessao_outbox_id IS NULL OR sucessora_de_id IS NOT NULL
      );
  END IF;
END;
$$;

CREATE UNIQUE INDEX IF NOT EXISTS investigacoes_sucessao_outbox_pai_unica
  ON public.investigacoes_revisao (sucessao_outbox_id, sucessora_de_id)
  WHERE sucessao_outbox_id IS NOT NULL;

CREATE OR REPLACE FUNCTION public.proteger_sucessao_promocao_terminal()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'O histórico do outbox terminal não pode ser apagado';
  END IF;
  IF NEW.id IS DISTINCT FROM OLD.id
     OR NEW.promocao_id IS DISTINCT FROM OLD.promocao_id
     OR NEW.status_terminal IS DISTINCT FROM OLD.status_terminal
     OR NEW.resultado_terminal_hash
          IS DISTINCT FROM OLD.resultado_terminal_hash
     OR NEW.pedido_hash IS DISTINCT FROM OLD.pedido_hash
     OR NEW.classe_desfecho_terminal
          IS DISTINCT FROM OLD.classe_desfecho_terminal
     OR NEW.criado_em IS DISTINCT FROM OLD.criado_em THEN
    RAISE EXCEPTION 'O desfecho terminal do outbox é imutável';
  END IF;
  IF OLD.classe_resolvida IS NOT NULL
     AND (
       NEW.classe_resolvida IS DISTINCT FROM OLD.classe_resolvida
       OR NEW.registro_reconciliado_id
            IS DISTINCT FROM OLD.registro_reconciliado_id
       OR NEW.registro_reconciliado_snapshot_ref
            IS DISTINCT FROM OLD.registro_reconciliado_snapshot_ref
       OR NEW.resolucao_hash IS DISTINCT FROM OLD.resolucao_hash
       OR NEW.resolucao_versao IS DISTINCT FROM OLD.resolucao_versao
       OR NEW.resolvida_em IS DISTINCT FROM OLD.resolvida_em
       OR NEW.resolvida_por IS DISTINCT FROM OLD.resolvida_por
     ) THEN
    RAISE EXCEPTION 'A resolução persistida do outbox é imutável';
  END IF;
  IF NEW.estado = 'concluida' AND NEW.classe_resolvida IS NULL THEN
    RAISE EXCEPTION 'Outbox não resolvido não pode ser concluído';
  END IF;
  IF NEW.estado = 'concluida' AND NEW.replanejamento_pedido_hash IS NULL THEN
    RAISE EXCEPTION 'Outbox concluído exige hash do pedido de materialização';
  END IF;
  IF OLD.replanejamento_pedido_hash IS NOT NULL
     AND NEW.replanejamento_pedido_hash
           IS DISTINCT FROM OLD.replanejamento_pedido_hash
     AND NOT (
       OLD.estado = 'concluida'
       AND NEW.estado <> 'concluida'
       AND NEW.replanejamento_pedido_hash IS NULL
       AND NEW.filhas_quantidade IS NULL
       AND NEW.filhas_mapa_hash IS NULL
       AND NEW.concluida_em IS NULL
     ) THEN
    RAISE EXCEPTION 'O pedido de materialização do outbox é imutável';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS investigacao_sucessoes_pendentes_imutavel
  ON public.investigacao_sucessoes_pendentes;
CREATE TRIGGER investigacao_sucessoes_pendentes_imutavel
BEFORE UPDATE OR DELETE ON public.investigacao_sucessoes_pendentes
FOR EACH ROW EXECUTE FUNCTION public.proteger_sucessao_promocao_terminal();

-- Um rascunho corretivo é um objeto humano não executável. Os vínculos são
-- tipados e persistentes para que nenhuma cópia de JSON possa convertê-lo em
-- promoção ou apagar a origem depois de uma gravação.
ALTER TABLE public.operation_drafts
  ADD COLUMN IF NOT EXISTS revisao_tipo text NOT NULL DEFAULT 'pre_revisao',
  ADD COLUMN IF NOT EXISTS investigacao_origem_id uuid
    REFERENCES public.investigacoes_revisao(id),
  ADD COLUMN IF NOT EXISTS promocao_origem_id uuid
    REFERENCES public.pending_actions(id);

ALTER TABLE public.pending_actions
  ADD COLUMN IF NOT EXISTS executavel boolean NOT NULL DEFAULT true;

CREATE UNIQUE INDEX IF NOT EXISTS operation_drafts_corretiva_investigacao_unica
  ON public.operation_drafts (investigacao_origem_id)
  WHERE revisao_tipo = 'corretiva_pos_gravacao';

CREATE OR REPLACE FUNCTION public.investigacao_json_possui_chave(
  p_valor jsonb,
  p_chaves text[]
)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
  WITH RECURSIVE percurso(valor) AS (
    SELECT p_valor
    UNION ALL
    SELECT filho.valor
      FROM percurso atual
      CROSS JOIN LATERAL (
        SELECT item.value AS valor
          FROM jsonb_each(CASE
            WHEN jsonb_typeof(atual.valor) = 'object' THEN atual.valor
            ELSE '{}'::jsonb
          END) item
        UNION ALL
        SELECT item.value AS valor
          FROM jsonb_array_elements(CASE
            WHEN jsonb_typeof(atual.valor) = 'array' THEN atual.valor
            ELSE '[]'::jsonb
          END) item
      ) filho
  )
  SELECT EXISTS (
    SELECT 1 FROM percurso
     WHERE jsonb_typeof(valor) = 'object'
       AND valor ?| p_chaves
  );
$$;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'public.operation_drafts'::regclass
       AND conname = 'operation_drafts_revisao_tipo_valido'
  ) THEN
    ALTER TABLE public.operation_drafts
      ADD CONSTRAINT operation_drafts_revisao_tipo_valido CHECK (
        (revisao_tipo = 'pre_revisao'
          AND investigacao_origem_id IS NULL
          AND promocao_origem_id IS NULL)
        OR
        (revisao_tipo = 'corretiva_pos_gravacao'
          AND investigacao_origem_id IS NOT NULL
          AND promocao_origem_id IS NOT NULL
          AND tipo_operacao IS NOT DISTINCT FROM 'correcao_pos_gravacao'
          AND entidade_final_tipo IS NOT DISTINCT FROM 'correcao_pos_gravacao'
          AND entidade_final_id IS NULL)
      );
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
     WHERE conrelid = 'public.pending_actions'::regclass
       AND conname = 'pending_actions_corretiva_nao_executavel'
  ) THEN
    ALTER TABLE public.pending_actions
      ADD CONSTRAINT pending_actions_corretiva_nao_executavel CHECK (
        (acao_tipo = 'revisar_correcao_pos_gravacao'
          AND NOT executavel
          AND entidade_tipo IS NOT DISTINCT FROM 'operation_draft'
          AND (status IN (
            'aguardando_confirmacao', 'em_revisao', 'rejeitado', 'cancelado'
          )) IS TRUE
          AND NOT (
            jsonb_path_exists(payload, '$.**.target_table')
            OR jsonb_path_exists(payload, '$.**.proposed_record')
            OR jsonb_path_exists(payload, '$.**.idempotency')
            OR jsonb_path_exists(payload, '$.**.idempotency_key')
            OR jsonb_path_exists(payload, '$.**.promocao_controle_version')
          ))
        OR acao_tipo IS DISTINCT FROM 'revisar_correcao_pos_gravacao'
      );
  END IF;
END;
$$;

-- Manifesto fechado que impede uma versão mal configurada de se declarar como
-- família independente ou adquirir capacidade de envio. O hash do artefato
-- continua individual por implantação, mas a semântica adapter→fonte é
-- versionada aqui e não pode ser escolhida pelo worker ou pelo modelo.
CREATE OR REPLACE FUNCTION public.investigacao_manifesto_adaptador_valido(
  p_adaptador text,
  p_adaptador_version text,
  p_familia_fonte text,
  p_autoridade_fonte text,
  p_fontes_tipo text[],
  p_tabelas text[],
  p_tabelas_nativas text[],
  p_identidades text[],
  p_capacidades text[]
)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog
AS $$
  SELECT CASE p_adaptador
    WHEN 'agronotas' THEN
      p_adaptador_version = 'v1'
      AND p_familia_fonte = 'fiscal_estruturada'
      AND p_autoridade_fonte = 'agronotas_fiscal'
      AND p_fontes_tipo = ARRAY['nf']::text[]
      AND p_tabelas = ARRAY[
        'evidencias_negocio', 'fontes_importacao',
        'negocios_candidatos', 'notas_fiscais_xml_raw'
      ]::text[]
      AND p_tabelas_nativas = ARRAY['notas_fiscais_xml_raw']::text[]
      AND p_identidades = ARRAY[
        'chave_nfe', 'gta_qualificada', 'hash_anexo'
      ]::text[]
      AND p_capacidades = ARRAY['history', 'read', 'search']::text[]
    WHEN 'ofx' THEN
      p_adaptador_version = 'v1'
      AND p_familia_fonte = 'financeira_estruturada'
      AND p_autoridade_fonte = 'instituicao_ofx'
      AND p_fontes_tipo = ARRAY['ofx']::text[]
      AND p_tabelas = ARRAY[
        'evidencias_negocio', 'fontes_importacao',
        'negocios_candidatos', 'transacoes_banco_staging'
      ]::text[]
      AND p_tabelas_nativas = ARRAY['transacoes_banco_staging']::text[]
      AND p_identidades = ARRAY[
        'fitid_qualificado', 'hash_anexo'
      ]::text[]
      AND p_capacidades = ARRAY['history', 'read', 'search']::text[]
    WHEN 'ima' THEN
      p_adaptador_version = 'v1'
      AND p_familia_fonte = 'sanitaria_estruturada'
      AND p_autoridade_fonte = 'ima_oficial'
      AND p_fontes_tipo = ARRAY['gta', 'ima']::text[]
      AND p_tabelas = ARRAY[
        'evidencias_negocio', 'fontes_importacao', 'negocios_candidatos'
      ]::text[]
      AND p_tabelas_nativas = ARRAY['fontes_importacao']::text[]
      AND p_identidades = ARRAY[
        'gta_qualificada', 'hash_anexo'
      ]::text[]
      AND p_capacidades = ARRAY['history', 'read', 'search']::text[]
    WHEN 'telegram' THEN
      p_adaptador_version = 'v1'
      AND p_familia_fonte = 'conversa'
      AND p_autoridade_fonte = 'telegram'
      AND p_fontes_tipo = ARRAY['telegram']::text[]
      AND p_tabelas = ARRAY[
        'evidencias_negocio', 'fontes_importacao', 'negocios_candidatos'
      ]::text[]
      AND p_tabelas_nativas = ARRAY['fontes_importacao']::text[]
      AND p_identidades = ARRAY['hash_anexo']::text[]
      AND p_capacidades = ARRAY['history', 'read', 'search']::text[]
    WHEN 'wey' THEN
      p_adaptador_version = 'v1'
      AND p_familia_fonte = 'conversa'
      AND p_autoridade_fonte = 'whatsapp_wey'
      AND p_fontes_tipo = ARRAY['wey']::text[]
      AND p_tabelas = ARRAY[
        'evidencias_negocio', 'fontes_importacao', 'negocios_candidatos'
      ]::text[]
      AND p_tabelas_nativas = ARRAY['fontes_importacao']::text[]
      AND p_identidades = ARRAY['hash_anexo']::text[]
      AND p_capacidades = ARRAY['history', 'read', 'search']::text[]
    WHEN 'outro' THEN
      p_adaptador_version = 'v1'
      AND p_familia_fonte = 'auxiliar'
      AND p_autoridade_fonte = 'auxiliar'
      AND p_fontes_tipo = ARRAY['b3', 'outro', 'planilha']::text[]
      AND p_tabelas = ARRAY[
        'evidencias_negocio', 'fontes_importacao', 'negocios_candidatos'
      ]::text[]
      AND p_tabelas_nativas = '{}'::text[]
      AND p_identidades = ARRAY['hash_anexo']::text[]
      AND p_capacidades = ARRAY['history', 'read', 'search']::text[]
    ELSE false
  END;
$$;

-- Registro imutável, não secreto, dos executores autorizados. Nenhuma versão
-- antiga herda a capacidade de uma versão nova; fonte e tabelas públicas são
-- derivadas deste contrato, nunca do texto produzido pelo modelo.
CREATE TABLE IF NOT EXISTS public.investigacao_adaptadores_config (
  adaptador text NOT NULL CHECK (adaptador IN (
    'agronotas', 'ofx', 'ima', 'telegram', 'wey', 'outro'
  )),
  adaptador_version text NOT NULL CHECK (btrim(adaptador_version) <> ''),
  artefato_hash text NOT NULL CHECK (artefato_hash ~ '^[0-9a-f]{64}$'),
  familia_fonte text NOT NULL CHECK (familia_fonte ~ '^[a-z][a-z0-9_-]{1,63}$'),
  autoridade_fonte text NOT NULL CHECK (
    autoridade_fonte ~ '^[a-z][a-z0-9_-]{1,63}$'
  ),
  fontes_tipo_permitidas text[] NOT NULL,
  tabelas_permitidas text[] NOT NULL DEFAULT '{}'::text[],
  tabelas_nativas text[] NOT NULL DEFAULT '{}'::text[],
  identidades_permitidas text[] NOT NULL DEFAULT '{}'::text[],
  capacidades text[] NOT NULL DEFAULT '{}'::text[],
  habilitado boolean NOT NULL DEFAULT false,
  desabilitado_em timestamptz,
  desabilitacao_motivo text,
  criado_em timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (adaptador, adaptador_version),
  CHECK (public.investigacao_instante_operacional(criado_em)),
  CHECK (public.investigacao_text_array_unico(tabelas_permitidas)),
  CHECK (public.investigacao_text_array_unico(tabelas_nativas)),
  CHECK (tabelas_nativas <@ tabelas_permitidas),
  CHECK (public.investigacao_text_array_unico(fontes_tipo_permitidas)),
  CHECK (public.investigacao_text_array_unico(identidades_permitidas)),
  CHECK (public.investigacao_text_array_unico(capacidades)),
  CHECK (public.investigacao_manifesto_adaptador_valido(
    adaptador, adaptador_version, familia_fonte, autoridade_fonte,
    fontes_tipo_permitidas, tabelas_permitidas, tabelas_nativas,
    identidades_permitidas, capacidades
  )),
  CHECK ((
    (habilitado AND desabilitado_em IS NULL AND desabilitacao_motivo IS NULL)
    OR (
      NOT habilitado
      AND public.investigacao_instante_operacional(desabilitado_em)
      AND btrim(desabilitacao_motivo) <> ''
    )
  ) IS TRUE)
);
CREATE UNIQUE INDEX IF NOT EXISTS investigacao_adaptador_config_habilitado_uidx
  ON public.investigacao_adaptadores_config (adaptador)
  WHERE habilitado;

-- Segredos rotacionáveis ficam fora dos bundles, views, logs e eventos. Cada
-- adaptador recebe somente a própria chave; o broker com service_role não a
-- lê e apenas entrega o envelope assinado à RPC transacional. Linhas são
-- imutáveis: rotação insere outra chave e preserva a anterior até drenar leases.
CREATE TABLE IF NOT EXISTS public.investigacao_adaptador_credenciais (
  adaptador text NOT NULL CHECK (adaptador IN (
    'agronotas', 'ofx', 'ima', 'telegram', 'wey', 'outro'
  )),
  adaptador_version text NOT NULL CHECK (btrim(adaptador_version) <> ''),
  chave_id text NOT NULL CHECK (chave_id ~ '^key_[0-9a-z._-]{8,80}$'),
  chave_hmac bytea NOT NULL CHECK (octet_length(chave_hmac) >= 32),
  valida_desde timestamptz NOT NULL,
  emite_ate timestamptz NOT NULL,
  aceita_ate timestamptz NOT NULL,
  criado_em timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (adaptador, adaptador_version, chave_id),
  FOREIGN KEY (adaptador, adaptador_version)
    REFERENCES public.investigacao_adaptadores_config(adaptador, adaptador_version),
  CHECK (
    public.investigacao_instante_operacional(criado_em)
    AND public.investigacao_instante_operacional(valida_desde)
    AND public.investigacao_instante_operacional(emite_ate)
    AND public.investigacao_instante_operacional(aceita_ate)
    AND valida_desde < emite_ate
    AND aceita_ate >= emite_ate + interval '900 seconds'
  )
);

CREATE TABLE IF NOT EXISTS public.investigacao_credenciais_revogadas (
  adaptador text NOT NULL,
  adaptador_version text NOT NULL,
  chave_id text NOT NULL,
  revogada_em timestamptz NOT NULL,
  motivo_codigo text NOT NULL CHECK (motivo_codigo ~ '^[a-z][a-z0-9_]{2,63}$'),
  PRIMARY KEY (adaptador, adaptador_version, chave_id),
  FOREIGN KEY (adaptador, adaptador_version, chave_id)
    REFERENCES public.investigacao_adaptador_credenciais(
      adaptador, adaptador_version, chave_id
    ),
  CHECK (public.investigacao_instante_operacional(revogada_em))
);

CREATE OR REPLACE FUNCTION public.proteger_config_adaptador()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF TG_OP = 'UPDATE'
     AND OLD.habilitado
     AND NOT NEW.habilitado
     AND NEW.desabilitado_em IS NOT NULL
     AND btrim(coalesce(NEW.desabilitacao_motivo, '')) <> ''
     AND (to_jsonb(NEW) - ARRAY[
       'habilitado', 'desabilitado_em', 'desabilitacao_motivo'
     ]) = (to_jsonb(OLD) - ARRAY[
       'habilitado', 'desabilitado_em', 'desabilitacao_motivo'
     ]) THEN
    IF NOT pg_try_advisory_xact_lock(hashtextextended(
      'investigacao-config:' || OLD.adaptador || ':' || OLD.adaptador_version,
      0
    )) THEN
      RAISE EXCEPTION 'Configuração em uso; tente desabilitar novamente';
    END IF;
    IF EXISTS (
      SELECT 1
        FROM public.investigacao_tarefas tarefa
        JOIN public.investigacoes_revisao investigacao
          ON investigacao.id = tarefa.investigacao_id
       WHERE tarefa.adaptador = OLD.adaptador
         AND tarefa.adaptador_version = OLD.adaptador_version
         AND (
           tarefa.estado_execucao IN (
             'pendente', 'em_execucao', 'aguardando_retentativa'
           )
           OR (
             tarefa.estado_execucao = 'obsoleta'
             AND investigacao.estado_execucao = 'obsoleta'
             AND investigacao.obsolescencia_motivo =
                   'complementar_promocao_ativa'
           )
         )
    ) THEN
      RAISE EXCEPTION 'Versão com tarefas não terminais precisa ser drenada antes de desabilitar';
    END IF;
    RETURN NEW;
  END IF;
  RAISE EXCEPTION 'Versão de adaptador só pode passar uma vez de habilitada para desabilitada';
END;
$$;

CREATE OR REPLACE FUNCTION public.proteger_registro_adaptador_imutavel()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_pai_estado text;
  v_pai_motivo text;
BEGIN
  RAISE EXCEPTION 'Configuração e chave de adaptador são append-only';
END;
$$;
DROP TRIGGER IF EXISTS investigacao_adaptadores_config_append_only
  ON public.investigacao_adaptadores_config;
CREATE TRIGGER investigacao_adaptadores_config_append_only
BEFORE UPDATE OR DELETE ON public.investigacao_adaptadores_config
FOR EACH ROW EXECUTE FUNCTION public.proteger_config_adaptador();

CREATE OR REPLACE FUNCTION public.validar_janela_emissao_credencial()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'investigacao-chave:' || NEW.adaptador || ':' || NEW.adaptador_version, 0
  ));
  IF EXISTS (
    SELECT 1 FROM public.investigacao_adaptador_credenciais existente
     WHERE existente.adaptador = NEW.adaptador
       AND existente.adaptador_version = NEW.adaptador_version
       AND tstzrange(existente.valida_desde, existente.emite_ate, '[)')
             && tstzrange(NEW.valida_desde, NEW.emite_ate, '[)')
  ) THEN
    RAISE EXCEPTION 'Janelas de emissão de credenciais não podem se sobrepor';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS investigacao_adaptador_credenciais_janela
  ON public.investigacao_adaptador_credenciais;
CREATE TRIGGER investigacao_adaptador_credenciais_janela
BEFORE INSERT ON public.investigacao_adaptador_credenciais
FOR EACH ROW EXECUTE FUNCTION public.validar_janela_emissao_credencial();
DROP TRIGGER IF EXISTS investigacao_adaptador_credenciais_append_only
  ON public.investigacao_adaptador_credenciais;
CREATE TRIGGER investigacao_adaptador_credenciais_append_only
BEFORE UPDATE OR DELETE ON public.investigacao_adaptador_credenciais
FOR EACH ROW EXECUTE FUNCTION public.proteger_registro_adaptador_imutavel();
DROP TRIGGER IF EXISTS investigacao_credenciais_revogadas_append_only
  ON public.investigacao_credenciais_revogadas;
CREATE TRIGGER investigacao_credenciais_revogadas_append_only
BEFORE UPDATE OR DELETE ON public.investigacao_credenciais_revogadas
FOR EACH ROW EXECUTE FUNCTION public.proteger_registro_adaptador_imutavel();

CREATE OR REPLACE FUNCTION public.validar_revogacao_credencial()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'investigacao-chave:' || NEW.adaptador || ':' || NEW.adaptador_version, 0
  ));
  IF NEW.revogada_em > clock_timestamp() THEN
    RAISE EXCEPTION 'Revogação não pode ser datada no futuro';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS investigacao_credenciais_revogadas_valida
  ON public.investigacao_credenciais_revogadas;
CREATE TRIGGER investigacao_credenciais_revogadas_valida
BEFORE INSERT ON public.investigacao_credenciais_revogadas
FOR EACH ROW EXECUTE FUNCTION public.validar_revogacao_credencial();

-- A ativação exige um atestado explícito da implantação do broker. A fundação
-- não insere esta linha: ela só pode ser criada numa janela autorizada depois
-- de provar que workers não possuem JWT service_role nem contexto de síntese.
CREATE TABLE IF NOT EXISTS public.investigacao_configuracao_ativacao (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  broker_version text NOT NULL CHECK (
    broker_version ~ '^[a-z0-9][a-z0-9._-]{2,80}$'
  ),
  broker_artefato_hash text NOT NULL CHECK (
    broker_artefato_hash ~ '^[0-9a-f]{64}$'
  ),
  teste_capacidades_hash text NOT NULL CHECK (
    teste_capacidades_hash ~ '^[0-9a-f]{64}$'
  ),
  atestado_por text NOT NULL CHECK (
    atestado_por ~ '^[a-z_][a-z0-9_-]{1,62}$'
  ),
  adaptadores_isolados boolean NOT NULL CHECK (adaptadores_isolados),
  workers_sem_service_role boolean NOT NULL CHECK (workers_sem_service_role),
  atestado_em timestamptz NOT NULL,
  CHECK (public.investigacao_instante_operacional(atestado_em))
);

CREATE TABLE IF NOT EXISTS public.investigacao_tarefas (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  investigacao_id uuid NOT NULL REFERENCES public.investigacoes_revisao(id),
  chave_idempotencia text NOT NULL CHECK (btrim(chave_idempotencia) <> ''),
  plano_item_ref text NOT NULL CHECK (plano_item_ref ~ '^pitem_[0-9a-f]{32}$'),
  adaptador text NOT NULL CHECK (adaptador IN (
    'agronotas', 'ofx', 'ima', 'telegram', 'wey', 'outro', 'sintese'
  )),
  consulta_ref text NOT NULL CHECK (consulta_ref ~ '^qref_[0-9a-f]{32}$'),
  consulta_schema_version text NOT NULL CHECK (btrim(consulta_schema_version) <> ''),
  consulta_spec jsonb NOT NULL CHECK (
    public.investigacao_consulta_spec_valida(consulta_spec)
  ),
  consulta_canonico text NOT NULL CHECK (btrim(consulta_canonico) <> ''),
  consulta_hash text NOT NULL CHECK (consulta_hash ~ '^[0-9a-f]{64}$'),
  adaptador_version text NOT NULL CHECK (btrim(adaptador_version) <> ''),
  estado_execucao text NOT NULL DEFAULT 'pendente' CHECK (estado_execucao IN (
    'pendente', 'em_execucao', 'aguardando_retentativa', 'concluida',
    'cancelada', 'obsoleta'
  )),
  estado_resultado text CHECK (estado_resultado IS NULL OR estado_resultado IN (
    'alternativa_unica', 'alternativas_multiplas', 'divergente',
    'evidencia_insuficiente', 'cobertura_incompleta'
  )),
  estado_cobertura text CHECK (estado_cobertura IS NULL OR estado_cobertura IN (
    'completa', 'vazio_com_cobertura', 'cobertura_incompleta', 'indisponivel',
    'reautenticacao_necessaria', 'erro_permanente'
  )),
  prova_cobertura jsonb CHECK (
    prova_cobertura IS NULL OR jsonb_typeof(prova_cobertura) = 'object'
  ),
  tentativas integer NOT NULL DEFAULT 0 CHECK (tentativas >= 0),
  proxima_execucao_em timestamptz NOT NULL DEFAULT now(),
  lease_executor text,
  lease_token uuid,
  lease_expira_em timestamptz,
  lease_chave_id text CHECK (
    lease_chave_id IS NULL OR lease_chave_id ~ '^key_[0-9a-z._-]{8,80}$'
  ),
  fencing_token bigint NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
  resultado_lease_token uuid,
  resultado_fencing_token bigint CHECK (resultado_fencing_token IS NULL OR resultado_fencing_token > 0),
  resultado_pedido_hash text CHECK (
    resultado_pedido_hash IS NULL OR resultado_pedido_hash ~ '^[0-9a-f]{64}$'
  ),
  retentativa_lease_token uuid,
  retentativa_fencing_token bigint CHECK (
    retentativa_fencing_token IS NULL OR retentativa_fencing_token > 0
  ),
  retentativa_executor text,
  retentativa_pedido_hash text CHECK (
    retentativa_pedido_hash IS NULL
    OR retentativa_pedido_hash ~ '^[0-9a-f]{64}$'
  ),
  erro_codigo text,
  erro_sanitizado text CHECK (public.investigacao_texto_sanitizado(erro_sanitizado)),
  resumo_sanitizado text CHECK (public.investigacao_texto_sanitizado(resumo_sanitizado)),
  criado_em timestamptz NOT NULL DEFAULT now(),
  iniciado_em timestamptz,
  concluido_em timestamptz,
  UNIQUE (investigacao_id, chave_idempotencia),
  UNIQUE (investigacao_id, plano_item_ref),
  UNIQUE (investigacao_id, id),
  CHECK (
    public.investigacao_instante_operacional(proxima_execucao_em)
    AND public.investigacao_instante_operacional(criado_em)
    AND (
      lease_expira_em IS NULL
      OR public.investigacao_instante_operacional(lease_expira_em)
    )
    AND (
      iniciado_em IS NULL
      OR public.investigacao_instante_operacional(iniciado_em)
    )
    AND (
      concluido_em IS NULL
      OR public.investigacao_instante_operacional(concluido_em)
    )
  ),
  CHECK (
    consulta_canonico::jsonb = consulta_spec
    AND encode(extensions.digest(convert_to(consulta_canonico, 'UTF8'), 'sha256'), 'hex')
      = consulta_hash
    AND consulta_ref = 'qref_' || left(consulta_hash, 32)
  ),
  CHECK (
    (
      estado_execucao = 'em_execucao'
      AND
      num_nonnulls(lease_executor, lease_token, lease_expira_em) = 3
      AND (
        (adaptador = 'sintese' AND lease_chave_id IS NULL)
        OR (adaptador <> 'sintese' AND lease_chave_id IS NOT NULL)
      )
    )
    OR (
      estado_execucao <> 'em_execucao'
      AND num_nonnulls(
        lease_executor, lease_token, lease_expira_em, lease_chave_id
      ) = 0
    )
  ),
  CHECK (
    num_nonnulls(
      resultado_lease_token, resultado_fencing_token, resultado_pedido_hash
    ) IN (0, 3)
  ),
  CHECK (
    num_nonnulls(
      retentativa_lease_token, retentativa_fencing_token,
      retentativa_executor, retentativa_pedido_hash
    ) IN (0, 4)
  )
);

CREATE OR REPLACE FUNCTION public.investigacao_prova_cobertura_valida(
  p_tarefa_id uuid,
  p_investigacao_id uuid,
  p_lease_token uuid,
  p_fencing_token bigint,
  p_estado_cobertura text,
  p_estado_resultado text,
  p_atestado jsonb,
  p_bundle jsonb,
  p_resumo_sanitizado text,
  p_erro_codigo text,
  p_erro_sanitizado text
)
RETURNS boolean
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_tarefa public.investigacao_tarefas%ROWTYPE;
  v_prova jsonb := p_atestado;
  v_chave bytea;
  v_artefato_hash text;
  v_familia_fonte text;
  v_pedido_hash text;
  v_metadados jsonb;
  v_hmac_esperado text;
  v_agora timestamptz;
  v_quantidade_evidencias integer;
BEGIN
  IF jsonb_typeof(p_bundle) IS DISTINCT FROM 'object'
     OR jsonb_typeof(coalesce(p_bundle -> 'evidencias', '[]'::jsonb))
          IS DISTINCT FROM 'array' THEN
    RETURN false;
  END IF;
  v_quantidade_evidencias := jsonb_array_length(
    coalesce(p_bundle -> 'evidencias', '[]'::jsonb)
  );
  IF jsonb_typeof(v_prova) IS DISTINCT FROM 'object'
     OR public.investigacao_jsonb_objeto_tamanho(v_prova) <> 24
     OR v_prova - ARRAY[
       'schema_version', 'chave_id', 'adaptador', 'adaptador_version',
       'artefato_hash', 'familia_fonte', 'consulta_hash', 'consulta_ref',
       'tarefa_id', 'investigacao_id', 'lease_token',
       'fencing_token', 'estado_cobertura', 'inicio_confirmado',
       'fim_confirmado', 'paginas_confirmadas', 'registros_confirmados',
       'paginacao_modo', 'artefato_cobertura_tipo',
       'cursor_final_hash', 'snapshot_fonte_hash',
       'estado_resultado', 'pedido_hash', 'hmac'
     ] <> '{}'::jsonb
     OR v_prova ->> 'schema_version' IS DISTINCT FROM 'cobertura-hmac-v1'
     OR jsonb_typeof(v_prova -> 'chave_id') IS DISTINCT FROM 'string'
     OR jsonb_typeof(v_prova -> 'adaptador') IS DISTINCT FROM 'string'
     OR jsonb_typeof(v_prova -> 'adaptador_version') IS DISTINCT FROM 'string'
     OR jsonb_typeof(v_prova -> 'artefato_hash') IS DISTINCT FROM 'string'
     OR jsonb_typeof(v_prova -> 'familia_fonte') IS DISTINCT FROM 'string'
     OR jsonb_typeof(v_prova -> 'consulta_hash') IS DISTINCT FROM 'string'
     OR jsonb_typeof(v_prova -> 'consulta_ref') IS DISTINCT FROM 'string'
     OR jsonb_typeof(v_prova -> 'tarefa_id') IS DISTINCT FROM 'string'
     OR jsonb_typeof(v_prova -> 'investigacao_id') IS DISTINCT FROM 'string'
     OR jsonb_typeof(v_prova -> 'lease_token') IS DISTINCT FROM 'string'
     OR jsonb_typeof(v_prova -> 'estado_cobertura') IS DISTINCT FROM 'string'
     OR jsonb_typeof(v_prova -> 'estado_resultado') IS DISTINCT FROM 'string'
     OR jsonb_typeof(v_prova -> 'paginacao_modo') IS DISTINCT FROM 'string'
     OR jsonb_typeof(v_prova -> 'artefato_cobertura_tipo')
          IS DISTINCT FROM 'string'
     OR jsonb_typeof(v_prova -> 'pedido_hash') IS DISTINCT FROM 'string'
     OR jsonb_typeof(v_prova -> 'hmac') IS DISTINCT FROM 'string'
     OR jsonb_typeof(v_prova -> 'fencing_token') IS DISTINCT FROM 'string'
     OR v_prova ->> 'fencing_token' !~ '^[1-9][0-9]*$'
     OR jsonb_typeof(v_prova -> 'inicio_confirmado') IS DISTINCT FROM 'boolean'
     OR jsonb_typeof(v_prova -> 'fim_confirmado') IS DISTINCT FROM 'boolean'
     OR jsonb_typeof(v_prova -> 'paginas_confirmadas') IS DISTINCT FROM 'number'
     OR v_prova ->> 'paginas_confirmadas' !~ '^[0-9]+$'
     OR jsonb_typeof(v_prova -> 'registros_confirmados') IS DISTINCT FROM 'number'
     OR v_prova ->> 'registros_confirmados' !~ '^[0-9]+$'
     OR v_prova ->> 'paginacao_modo' NOT IN (
       'cursor_final', 'nao_paginado', 'nao_iniciada', 'parcial'
     )
     OR v_prova ->> 'artefato_cobertura_tipo' NOT IN (
       'snapshot_fonte', 'erro_pre_resposta',
       'snapshot_parcial', 'erro_pos_cobertura'
     )
     OR (
       v_prova -> 'snapshot_fonte_hash' IS DISTINCT FROM 'null'::jsonb
       AND (
         jsonb_typeof(v_prova -> 'snapshot_fonte_hash')
           IS DISTINCT FROM 'string'
         OR v_prova ->> 'snapshot_fonte_hash' !~ '^[0-9a-f]{64}$'
       )
     )
     OR (
       v_prova -> 'cursor_final_hash' IS DISTINCT FROM 'null'::jsonb
       AND (
         jsonb_typeof(v_prova -> 'cursor_final_hash')
           IS DISTINCT FROM 'string'
         OR v_prova ->> 'cursor_final_hash' !~ '^[0-9a-f]{64}$'
       )
     )
     OR (
       v_prova ->> 'paginacao_modo' = 'cursor_final'
       AND (
         jsonb_typeof(v_prova -> 'cursor_final_hash')
           IS DISTINCT FROM 'string'
         OR v_prova ->> 'cursor_final_hash' !~ '^[0-9a-f]{64}$'
       )
     )
     OR v_prova ->> 'pedido_hash' !~ '^[0-9a-f]{64}$'
     OR v_prova ->> 'hmac' !~ '^[0-9a-f]{64}$' THEN
    RETURN false;
  END IF;

  SELECT * INTO v_tarefa
    FROM public.investigacao_tarefas
   WHERE id = p_tarefa_id
     AND investigacao_id = p_investigacao_id;
  IF NOT FOUND OR v_tarefa.adaptador = 'sintese' THEN
    RETURN false;
  END IF;
  IF v_prova ->> 'adaptador' IS DISTINCT FROM v_tarefa.adaptador
     OR v_prova ->> 'adaptador_version' IS DISTINCT FROM v_tarefa.adaptador_version
     OR v_prova ->> 'chave_id' IS DISTINCT FROM v_tarefa.lease_chave_id
     OR v_prova ->> 'consulta_ref' IS DISTINCT FROM v_tarefa.consulta_ref
     OR v_prova ->> 'consulta_hash' IS DISTINCT FROM v_tarefa.consulta_hash
     OR public.investigacao_uuid_texto_seguro(v_prova ->> 'tarefa_id')
          IS DISTINCT FROM p_tarefa_id
     OR public.investigacao_uuid_texto_seguro(v_prova ->> 'investigacao_id')
          IS DISTINCT FROM p_investigacao_id
     OR public.investigacao_uuid_texto_seguro(v_prova ->> 'lease_token')
          IS DISTINCT FROM p_lease_token
     OR (v_prova ->> 'fencing_token')::bigint IS DISTINCT FROM p_fencing_token
     OR v_prova ->> 'estado_cobertura' IS DISTINCT FROM p_estado_cobertura
     OR v_prova ->> 'estado_resultado' IS DISTINCT FROM p_estado_resultado
     OR (v_prova ->> 'registros_confirmados')::bigint
          IS DISTINCT FROM v_quantidade_evidencias::bigint
     OR (
       p_estado_cobertura = 'vazio_com_cobertura'
       AND (v_prova ->> 'registros_confirmados')::bigint <> 0
     )
     OR (
       p_estado_cobertura = 'completa'
       AND (v_prova ->> 'registros_confirmados')::bigint = 0
     )
     OR (
       p_estado_cobertura IN ('completa', 'vazio_com_cobertura')
       AND NOT coalesce((
         (v_prova ->> 'paginas_confirmadas')::bigint >= 1
         AND (v_prova ->> 'inicio_confirmado')::boolean IS TRUE
         AND (v_prova ->> 'fim_confirmado')::boolean IS TRUE
         AND v_prova ->> 'artefato_cobertura_tipo' = 'snapshot_fonte'
         AND v_prova ->> 'snapshot_fonte_hash' ~ '^[0-9a-f]{64}$'
         AND (
           (v_prova ->> 'paginacao_modo' = 'cursor_final'
             AND v_prova ->> 'cursor_final_hash' ~ '^[0-9a-f]{64}$')
           OR (v_prova ->> 'paginacao_modo' = 'nao_paginado'
             AND v_prova -> 'cursor_final_hash'
                   IS NOT DISTINCT FROM 'null'::jsonb)
         )
       ), false)
     )
     OR (
       p_estado_cobertura IN (
         'cobertura_incompleta', 'indisponivel',
         'reautenticacao_necessaria', 'erro_permanente'
       )
       AND NOT coalesce((
         (
           (v_prova ->> 'paginas_confirmadas')::bigint = 0
           AND (v_prova ->> 'registros_confirmados')::bigint = 0
           AND (v_prova ->> 'inicio_confirmado')::boolean IS FALSE
           AND (v_prova ->> 'fim_confirmado')::boolean IS FALSE
           AND v_prova ->> 'paginacao_modo' = 'nao_iniciada'
           AND v_prova ->> 'artefato_cobertura_tipo' = 'erro_pre_resposta'
           AND v_quantidade_evidencias = 0
           AND v_prova -> 'cursor_final_hash' IS NOT DISTINCT FROM 'null'::jsonb
           AND v_prova -> 'snapshot_fonte_hash' IS NOT DISTINCT FROM 'null'::jsonb
         )
         OR (
           (v_prova ->> 'paginas_confirmadas')::bigint >= 1
           AND (v_prova ->> 'inicio_confirmado')::boolean IS TRUE
           AND (
             (
               (v_prova ->> 'fim_confirmado')::boolean IS FALSE
               AND v_prova ->> 'paginacao_modo' = 'parcial'
               AND v_prova ->> 'artefato_cobertura_tipo' = 'snapshot_parcial'
               AND v_prova ->> 'cursor_final_hash' ~ '^[0-9a-f]{64}$'
             )
             OR (
               (v_prova ->> 'fim_confirmado')::boolean IS TRUE
               AND v_prova ->> 'paginacao_modo' IN (
                 'cursor_final', 'nao_paginado'
               )
               AND v_prova ->> 'artefato_cobertura_tipo' = 'erro_pos_cobertura'
               AND (
                 (v_prova ->> 'paginacao_modo' = 'cursor_final'
                   AND v_prova ->> 'cursor_final_hash' ~ '^[0-9a-f]{64}$')
                 OR (v_prova ->> 'paginacao_modo' = 'nao_paginado'
                   AND v_prova -> 'cursor_final_hash'
                         IS NOT DISTINCT FROM 'null'::jsonb)
               )
             )
           )
           AND v_prova ->> 'snapshot_fonte_hash' ~ '^[0-9a-f]{64}$'
         )
       ), false)
     ) THEN
    RETURN false;
  END IF;

  v_pedido_hash := encode(extensions.digest(convert_to(
    public.investigacao_json_canonico(jsonb_build_object(
      'estado_cobertura', p_estado_cobertura,
      'estado_resultado', p_estado_resultado,
      'bundle', p_bundle,
      'resumo_sanitizado', p_resumo_sanitizado,
      'erro_codigo', p_erro_codigo,
      'erro_sanitizado', p_erro_sanitizado
    )),
    'UTF8'
  ), 'sha256'), 'hex');
  IF v_prova ->> 'pedido_hash' IS DISTINCT FROM v_pedido_hash THEN
    RETURN false;
  END IF;
  PERFORM pg_advisory_xact_lock_shared(hashtextextended(
    'investigacao-chave:' || v_tarefa.adaptador || ':'
      || v_tarefa.adaptador_version,
    0
  ));
  v_agora := clock_timestamp();
  SELECT * INTO v_tarefa
    FROM public.investigacao_tarefas
   WHERE id = p_tarefa_id
     AND investigacao_id = p_investigacao_id;
  IF NOT FOUND
     OR v_tarefa.estado_execucao <> 'em_execucao'
     OR v_tarefa.lease_token IS DISTINCT FROM p_lease_token
     OR v_tarefa.fencing_token IS DISTINCT FROM p_fencing_token
     OR v_tarefa.lease_expira_em <= v_agora THEN
    RETURN false;
  END IF;
  SELECT credencial.chave_hmac, config.artefato_hash, config.familia_fonte
    INTO v_chave, v_artefato_hash, v_familia_fonte
    FROM public.investigacao_adaptador_credenciais credencial
    JOIN public.investigacao_adaptadores_config config
      ON config.adaptador = credencial.adaptador
     AND config.adaptador_version = credencial.adaptador_version
   WHERE credencial.adaptador = v_tarefa.adaptador
     AND credencial.adaptador_version = v_tarefa.adaptador_version
     AND credencial.chave_id = v_prova ->> 'chave_id'
     AND config.habilitado
     AND v_agora >= credencial.valida_desde
     AND v_agora < credencial.aceita_ate
     AND NOT EXISTS (
       SELECT 1 FROM public.investigacao_credenciais_revogadas revogada
        WHERE revogada.adaptador = credencial.adaptador
          AND revogada.adaptador_version = credencial.adaptador_version
          AND revogada.chave_id = credencial.chave_id
     );
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  IF v_prova ->> 'artefato_hash' IS DISTINCT FROM v_artefato_hash
     OR v_prova ->> 'familia_fonte' IS DISTINCT FROM v_familia_fonte THEN
    RETURN false;
  END IF;
  v_metadados := jsonb_build_object(
    'schema_version', v_prova ->> 'schema_version',
    'chave_id', v_prova ->> 'chave_id',
    'adaptador', v_tarefa.adaptador,
    'adaptador_version', v_tarefa.adaptador_version,
    'artefato_hash', v_artefato_hash,
    'familia_fonte', v_familia_fonte,
    'consulta_hash', v_tarefa.consulta_hash,
    'consulta_ref', v_tarefa.consulta_ref,
    'tarefa_id', p_tarefa_id::text,
    'investigacao_id', p_investigacao_id::text,
    'lease_token', p_lease_token::text,
    'fencing_token', p_fencing_token::text,
    'estado_cobertura', p_estado_cobertura,
    'estado_resultado', p_estado_resultado,
    'inicio_confirmado', (v_prova ->> 'inicio_confirmado')::boolean,
    'fim_confirmado', (v_prova ->> 'fim_confirmado')::boolean,
    'paginas_confirmadas', (v_prova ->> 'paginas_confirmadas')::bigint,
    'registros_confirmados', (v_prova ->> 'registros_confirmados')::bigint,
    'paginacao_modo', v_prova ->> 'paginacao_modo',
    'artefato_cobertura_tipo', v_prova ->> 'artefato_cobertura_tipo',
    'cursor_final_hash', v_prova -> 'cursor_final_hash',
    'snapshot_fonte_hash', v_prova ->> 'snapshot_fonte_hash',
    'pedido_hash', v_pedido_hash
  );
  v_hmac_esperado := encode(extensions.hmac(
    convert_to(public.investigacao_json_canonico(v_metadados), 'UTF8'),
    v_chave, 'sha256'
  ), 'hex');
  RETURN public.investigacao_hex_igual_constante(
    v_hmac_esperado, v_prova ->> 'hmac'
  );
EXCEPTION
  WHEN invalid_text_representation OR numeric_value_out_of_range THEN
    RETURN false;
END;
$$;

-- Resolve a referência de origem no próprio banco. A tabela vem de lista
-- fechada e o snapshot usa o xmin corrente da linha: UUID inventado, linha
-- removida ou versão anterior nunca adquire proveniência nativa. O conteúdo
-- da linha não sai desta função e nenhum dado bruto é projetado.
CREATE OR REPLACE FUNCTION public.investigacao_proveniencia_registro(
  p_adaptador text,
  p_adaptador_version text,
  p_tabela text,
  p_registro_id uuid
)
RETURNS jsonb
LANGUAGE plpgsql
STABLE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_xmin text;
  v_relacao regclass;
BEGIN
  IF p_adaptador_version <> 'v1'
     OR p_adaptador NOT IN (
       'agronotas', 'ofx', 'ima', 'telegram', 'wey', 'outro'
     ) THEN
    RETURN NULL;
  END IF;
  IF p_tabela NOT IN (
    'notas_fiscais_xml_raw', 'transacoes_banco_staging',
    'fontes_importacao', 'evidencias_negocio', 'negocios_candidatos',
    'operation_drafts', 'pending_actions', 'eventos', 'compras', 'vendas',
    'abates', 'pesagens_caderno', 'memorias_agentes', 'contexto_handoff'
  ) THEN
    RETURN NULL;
  END IF;
  v_relacao := to_regclass('public.' || p_tabela);
  IF v_relacao IS NULL THEN
    RETURN NULL;
  END IF;
  IF p_adaptador = 'ofx'
     AND p_tabela = 'transacoes_banco_staging' THEN
    SELECT transacao.xmin::text INTO v_xmin
      FROM public.transacoes_banco_staging transacao
      JOIN public.fontes_importacao fonte
        ON fonte.id = transacao.fonte_importacao_id
     WHERE transacao.id = p_registro_id
       AND fonte.tipo = 'ofx'
       AND fonte.estado IN ('validada', 'importada_staging');
  ELSIF p_adaptador IN ('ima', 'telegram', 'wey')
     AND p_tabela = 'fontes_importacao' THEN
    SELECT fonte.xmin::text INTO v_xmin
      FROM public.fontes_importacao fonte
     WHERE fonte.id = p_registro_id
       AND fonte.tipo = CASE p_adaptador
         WHEN 'ima' THEN 'ima'
         WHEN 'telegram' THEN 'telegram'
         ELSE 'wey'
       END
       AND fonte.estado IN ('validada', 'importada_staging');
  ELSIF p_adaptador = 'agronotas'
     AND p_tabela = 'notas_fiscais_xml_raw' THEN
    EXECUTE format(
      'SELECT xmin::text FROM %s WHERE id = $1', v_relacao
    ) INTO v_xmin USING p_registro_id;
  ELSIF p_tabela NOT IN (
    'notas_fiscais_xml_raw', 'transacoes_banco_staging', 'fontes_importacao'
  ) THEN
    -- Tabelas correlacionadas são aceitas somente como derivadas. Ainda
    -- provamos que a linha existia no snapshot lido.
    EXECUTE format(
      'SELECT xmin::text FROM %s WHERE id = $1', v_relacao
    ) INTO v_xmin USING p_registro_id;
  ELSE
    RETURN NULL;
  END IF;
  IF v_xmin IS NULL THEN
    RETURN NULL;
  END IF;
  RETURN jsonb_build_object(
    'registro_ref', 'src_' || substr(encode(extensions.digest(convert_to(
      p_tabela || ':' || p_registro_id::text, 'UTF8'
    ), 'sha256'), 'hex'), 1, 32),
    'snapshot_ref', 'snp_' || substr(encode(extensions.digest(convert_to(
      p_tabela || ':' || p_registro_id::text || ':' || v_xmin, 'UTF8'
    ), 'sha256'), 'hex'), 1, 32),
    'ancestral_ref', 'anc_' || substr(encode(extensions.digest(convert_to(
      p_tabela || ':' || p_registro_id::text, 'UTF8'
    ), 'sha256'), 'hex'), 1, 32)
  );
END;
$$;

-- Lê, trava e atesta a linha operacional em um único statement. O lock
-- impede que snapshot e conteúdo pertençam a versões diferentes durante a
-- conclusão/materialização da revisão corretiva.
CREATE OR REPLACE FUNCTION public.investigacao_snapshot_registro_promocao(
  p_tabela text,
  p_registro_id uuid,
  p_promocao_id uuid,
  p_proposto jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_relacao regclass;
  v_registro jsonb;
  v_retrato jsonb;
  v_xmin text;
  v_identidade_valida boolean := false;
  v_corresponde boolean := false;
BEGIN
  IF p_tabela NOT IN ('compras', 'vendas', 'pesagens_caderno', 'abates')
     OR jsonb_typeof(p_proposto) IS DISTINCT FROM 'object'
     OR p_proposto = '{}'::jsonb THEN
    RETURN NULL;
  END IF;
  v_relacao := to_regclass('public.' || p_tabela);
  IF v_relacao IS NULL THEN
    RETURN NULL;
  END IF;
  EXECUTE format(
    'SELECT to_jsonb(registro), registro.xmin::text '
      || 'FROM %s AS registro WHERE registro.id = $1 FOR SHARE',
    v_relacao
  ) INTO v_registro, v_xmin USING p_registro_id;
  IF v_registro IS NULL THEN
    RETURN NULL;
  END IF;
  v_identidade_valida := (
    (p_tabela = 'compras'
      AND v_registro ->> 'idempotency_key'
        IS NOT DISTINCT FROM
          'promocao_operacional:' || p_promocao_id::text)
    OR
    (p_tabela <> 'compras'
      AND v_registro ->> 'promocao_origem_id'
        IS NOT DISTINCT FROM p_promocao_id::text)
  );
  IF NOT EXISTS (
    SELECT 1 FROM jsonb_object_keys(p_proposto) AS chave
     WHERE NOT (v_registro ? chave)
  ) THEN
    SELECT coalesce(
             jsonb_object_agg(chave, v_registro -> chave ORDER BY chave),
             '{}'::jsonb
           )
      INTO v_retrato
      FROM jsonb_object_keys(p_proposto) AS chave;
    v_corresponde := v_identidade_valida
      AND v_retrato IS NOT DISTINCT FROM p_proposto;
  END IF;
  RETURN jsonb_build_object(
    'identidade_valida', v_identidade_valida,
    'corresponde', v_corresponde,
    'registro_ref', 'src_' || substr(encode(extensions.digest(convert_to(
      p_tabela || ':' || p_registro_id::text, 'UTF8'
    ), 'sha256'), 'hex'), 1, 32),
    'snapshot_ref', 'snp_' || substr(encode(extensions.digest(convert_to(
      p_tabela || ':' || p_registro_id::text || ':' || v_xmin, 'UTF8'
    ), 'sha256'), 'hex'), 1, 32),
    'ancestral_ref', 'anc_' || substr(encode(extensions.digest(convert_to(
      p_tabela || ':' || p_registro_id::text, 'UTF8'
    ), 'sha256'), 'hex'), 1, 32)
  );
END;
$$;

-- Uma linha existente não prova, por si só, que foi criada pela promoção.
-- Compara no servidor o retrato operacional com a prévia canônica selada na
-- ação e, para compras, exige também a chave idempotente daquela promoção.
-- A lista de destinos é fechada antes do SQL dinâmico e nenhum conteúdo da
-- linha é retornado ao chamador.
CREATE OR REPLACE FUNCTION public.investigacao_registro_corresponde_promocao(
  p_tabela text,
  p_registro_id uuid,
  p_promocao_id uuid,
  p_proposto jsonb
)
RETURNS boolean
LANGUAGE sql
VOLATILE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
  SELECT coalesce(
    (public.investigacao_snapshot_registro_promocao(
      p_tabela, p_registro_id, p_promocao_id, p_proposto
    ) ->> 'corresponde')::boolean,
    false
  );
$$;

-- Protege o vínculo durável no momento do INSERT operacional. Registros
-- históricos sem vínculo continuam aceitos; uma referência de promoção só
-- pode nascer pelo service_role, durante o lease ativo, no destino e com o
-- retrato exato já aprovado. Depois de gravada, a referência é imutável.
CREATE OR REPLACE FUNCTION public.proteger_vinculo_promocao_operacional()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_registro jsonb;
  v_antigo jsonb;
  v_promocao_id uuid;
  v_promocao public.pending_actions%ROWTYPE;
  v_proposto jsonb;
  v_retrato jsonb;
  v_chave text;
BEGIN
  IF TG_TABLE_SCHEMA IS DISTINCT FROM 'public'
     OR TG_TABLE_NAME NOT IN (
       'compras', 'vendas', 'pesagens_caderno', 'abates'
     ) THEN
    RAISE EXCEPTION 'Guardião de vínculo chamado fora do destino autorizado';
  END IF;
  IF TG_OP = 'DELETE' THEN
    v_antigo := to_jsonb(OLD);
    IF (TG_TABLE_NAME = 'compras'
        AND coalesce(v_antigo ->> 'idempotency_key', '')
              LIKE 'promocao_operacional:%')
       OR (TG_TABLE_NAME <> 'compras'
           AND v_antigo -> 'promocao_origem_id' <> 'null'::jsonb) THEN
      RAISE EXCEPTION 'Registro promovido não pode ser apagado; use correção auditada';
    END IF;
    RETURN OLD;
  END IF;
  v_registro := to_jsonb(NEW);
  IF TG_OP = 'UPDATE' THEN
    v_antigo := to_jsonb(OLD);
    IF TG_TABLE_NAME = 'compras' THEN
      IF v_registro -> 'idempotency_key'
           IS DISTINCT FROM v_antigo -> 'idempotency_key' THEN
        RAISE EXCEPTION 'A chave de origem da compra é imutável';
      END IF;
      IF coalesce(v_antigo ->> 'idempotency_key', '')
           LIKE 'promocao_operacional:%' THEN
        v_promocao_id := public.investigacao_uuid_texto_seguro(substr(
          v_antigo ->> 'idempotency_key',
          length('promocao_operacional:') + 1
        ));
      END IF;
    ELSIF v_registro -> 'promocao_origem_id'
             IS DISTINCT FROM v_antigo -> 'promocao_origem_id' THEN
      RAISE EXCEPTION 'O vínculo da promoção operacional é imutável';
    ELSE
      v_promocao_id := public.investigacao_uuid_texto_seguro(
        nullif(v_antigo ->> 'promocao_origem_id', '')
      );
    END IF;
    IF v_promocao_id IS NULL THEN
      RETURN NEW;
    END IF;
    -- UPDATE/DELETE já chegam ao trigger com a linha operacional travada.
    -- Nunca esperamos pelo advisory que o finalizador pode estar segurando:
    -- falhamos fechado e soltamos a linha, evitando o ciclo operacional→ação.
    IF NOT pg_catalog.pg_try_advisory_xact_lock(
      pg_catalog.hashtextextended(
        'investigacao-promocao:' || v_promocao_id::text, 0
      )
    ) THEN
      RAISE EXCEPTION 'A promoção está sendo finalizada; tente novamente depois';
    END IF;
    SELECT * INTO v_promocao
      FROM public.pending_actions acao
     WHERE acao.id = v_promocao_id;
    IF NOT FOUND
       OR v_promocao.acao_tipo
            IS DISTINCT FROM 'promover_revisao_operacional'
       OR jsonb_typeof(v_promocao.payload -> 'proposed_record')
            IS DISTINCT FROM 'object' THEN
      RAISE EXCEPTION 'Registro promovido perdeu sua origem auditável';
    END IF;
    v_proposto := v_promocao.payload -> 'proposed_record';
    IF EXISTS (
      SELECT 1
        FROM jsonb_object_keys(v_proposto) AS chave
       WHERE (v_registro -> chave) IS DISTINCT FROM (v_antigo -> chave)
    ) THEN
      RAISE EXCEPTION 'Campo aprovado da promoção é imutável; use correção auditada';
    END IF;
    IF EXISTS (
      SELECT 1
        FROM public.investigacoes_revisao investigacao
        JOIN public.operation_drafts draft
          ON draft.investigacao_origem_id = investigacao.id
        JOIN public.pending_actions acao_corretiva
          ON acao_corretiva.id = draft.pending_action_id
       WHERE investigacao.promocao_origem_id = v_promocao_id
         AND investigacao.fluxo_tipo = 'corretiva_pos_gravacao'
         AND acao_corretiva.acao_tipo = 'revisar_correcao_pos_gravacao'
         AND acao_corretiva.status IN (
           'aguardando_confirmacao', 'em_revisao'
         )
    ) THEN
      RAISE EXCEPTION 'Há uma conferência corretiva aberta para este registro';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP IS DISTINCT FROM 'INSERT' THEN
    RETURN NEW;
  END IF;
  IF TG_TABLE_NAME = 'compras' THEN
    v_chave := nullif(v_registro ->> 'idempotency_key', '');
    IF v_chave IS NULL OR v_chave NOT LIKE 'promocao_operacional:%' THEN
      RETURN NEW;
    END IF;
    v_promocao_id := public.investigacao_uuid_texto_seguro(
      substr(v_chave, length('promocao_operacional:') + 1)
    );
  ELSE
    v_promocao_id := public.investigacao_uuid_texto_seguro(
      nullif(v_registro ->> 'promocao_origem_id', '')
    );
    IF v_promocao_id IS NULL
       AND v_registro -> 'promocao_origem_id' = 'null'::jsonb THEN
      RETURN NEW;
    END IF;
  END IF;
  IF coalesce(
       nullif(current_setting('role', true), 'none'), session_user
     ) IS DISTINCT FROM 'service_role'
     OR v_promocao_id IS NULL THEN
    RAISE EXCEPTION 'Vínculo operacional protegido exige o mediador autorizado';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'investigacao-promocao:' || v_promocao_id::text, 0
    )
  );
  SELECT * INTO v_promocao
    FROM public.pending_actions acao
   WHERE acao.id = v_promocao_id
   FOR UPDATE;
  IF NOT FOUND
     OR v_promocao.acao_tipo IS DISTINCT FROM 'promover_revisao_operacional'
     OR v_promocao.executavel IS NOT TRUE
     OR v_promocao.promocao_controle_version IS DISTINCT FROM 'lease-v1'
     OR v_promocao.status IS DISTINCT FROM 'em_execucao'
     OR v_promocao.promocao_lease_token IS NULL
     OR v_promocao.promocao_lease_expira_em <= clock_timestamp()
     OR v_promocao.payload ->> 'target_table'
          IS DISTINCT FROM TG_TABLE_NAME
     OR EXISTS (
       SELECT 1 FROM public.operation_drafts draft
        WHERE draft.id = public.investigacao_uuid_texto_seguro(
          v_promocao.payload ->> 'source_draft_id'
        )
          AND draft.revisao_tipo = 'corretiva_pos_gravacao'
     ) THEN
    RAISE EXCEPTION 'Promoção não possui lease ativo e origem executável para este destino';
  END IF;
  v_proposto := v_promocao.payload -> 'proposed_record';
  IF jsonb_typeof(v_proposto) IS DISTINCT FROM 'object'
     OR EXISTS (
       SELECT 1 FROM jsonb_object_keys(v_proposto) AS chave
        WHERE NOT (v_registro ? chave)
     ) THEN
    RAISE EXCEPTION 'Prévia operacional ausente ou incompatível com o destino';
  END IF;
  SELECT coalesce(
           jsonb_object_agg(chave, v_registro -> chave ORDER BY chave),
           '{}'::jsonb
         )
    INTO v_retrato
    FROM jsonb_object_keys(v_proposto) AS chave;
  IF v_retrato IS DISTINCT FROM v_proposto THEN
    RAISE EXCEPTION 'Registro operacional diverge da prévia aprovada';
  END IF;
  RETURN NEW;
END;
$$;

CREATE TABLE IF NOT EXISTS public.investigacao_evidencias (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  id_logico text NOT NULL CHECK (btrim(id_logico) <> ''),
  investigacao_id uuid NOT NULL REFERENCES public.investigacoes_revisao(id),
  tarefa_id uuid NOT NULL,
  tarefa_lease_token uuid NOT NULL,
  tarefa_fencing_token bigint NOT NULL CHECK (tarefa_fencing_token > 0),
  fonte_tipo text NOT NULL CHECK (fonte_tipo IN (
    'nf', 'gta', 'ofx', 'ima', 'telegram', 'wey', 'b3', 'planilha', 'outro'
  )),
  fonte_tabela text CHECK (fonte_tabela IS NULL OR fonte_tabela IN (
    'notas_fiscais_xml_raw', 'transacoes_banco_staging',
    'fontes_importacao', 'evidencias_negocio', 'negocios_candidatos',
    'operation_drafts', 'pending_actions', 'eventos', 'compras', 'vendas',
    'abates', 'pesagens_caderno', 'memorias_agentes', 'contexto_handoff'
  )),
  fonte_registro_id uuid,
  -- Proveniência não é aceita do bundle. A RPC a deriva do manifesto privado
  -- do adaptador, da tabela permitida e da referência de registro assinada.
  origem_classe text NOT NULL CHECK (origem_classe IN ('nativa', 'derivada')),
  autoridade_fonte text NOT NULL CHECK (
    autoridade_fonte ~ '^[a-z][a-z0-9_-]{1,63}$'
  ),
  dataset_ref text NOT NULL CHECK (dataset_ref ~ '^dst_[0-9a-f]{32}$'),
  registro_origem_ref text NOT NULL CHECK (
    registro_origem_ref ~ '^src_[0-9a-f]{32}$'
  ),
  snapshot_fonte_ref text NOT NULL CHECK (
    snapshot_fonte_ref ~ '^snp_[0-9a-f]{32}$'
  ),
  ancestral_ref text NOT NULL CHECK (ancestral_ref ~ '^anc_[0-9a-f]{32}$'),
  linhagem text NOT NULL CHECK (linhagem ~ '^lin_[0-9a-f]{32}$'),
  chave_natural_hash text NOT NULL CHECK (chave_natural_hash ~ '^[0-9a-f]{64}$'),
  referencia_opaca text CHECK (
    referencia_opaca IS NULL OR referencia_opaca ~ '^ref_[0-9a-f]{32}$'
  ),
  fatos_normalizados jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(fatos_normalizados) = 'object' AND fatos_normalizados <> '{}'::jsonb AND public.investigacao_json_publico_sanitizado(fatos_normalizados)),
  classificacao text NOT NULL CHECK (classificacao IN (
    'possivel', 'provavel', 'forte', 'ambiguo', 'inconclusivo'
  )),
  confianca numeric CHECK (confianca IS NULL OR (confianca >= 0 AND confianca <= 1)),
  provas_campos jsonb NOT NULL,
  provas_campos_canonico text NOT NULL CHECK (btrim(provas_campos_canonico) <> ''),
  provas_campos_hash text NOT NULL CHECK (provas_campos_hash ~ '^[0-9a-f]{64}$'),
  regra_confianca_version text NOT NULL CHECK (btrim(regra_confianca_version) <> ''),
  resumo_sanitizado text NOT NULL
    CHECK (btrim(resumo_sanitizado) <> '')
    CHECK (public.investigacao_texto_publico_sanitizado(resumo_sanitizado)),
  evidenciado_em timestamptz,
  criado_em timestamptz NOT NULL DEFAULT now(),
  -- A mesma evidência pode ser reemitida por uma nova tentativa depois de um
  -- lease vencido. A tentativa antiga permanece auditável, mas deixa de ser
  -- projetada e anexada.
  UNIQUE (
    investigacao_id, tarefa_id, tarefa_fencing_token, id_logico
  ),
  UNIQUE (
    investigacao_id, tarefa_id, tarefa_fencing_token, linhagem,
    chave_natural_hash
  ),
  UNIQUE (investigacao_id, id),
  CHECK (
    public.investigacao_instante_operacional(criado_em)
    AND (
      evidenciado_em IS NULL
      OR public.investigacao_instante_operacional(evidenciado_em)
    )
  ),
  CHECK (public.investigacao_provas_campos_validas(
    fatos_normalizados, provas_campos, provas_campos_canonico,
    provas_campos_hash
  )),
  FOREIGN KEY (investigacao_id, tarefa_id)
    REFERENCES public.investigacao_tarefas(investigacao_id, id)
);

CREATE TABLE IF NOT EXISTS public.investigacao_alternativas (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  referencia_publica text NOT NULL DEFAULT (
    'alt_' || encode(extensions.gen_random_bytes(16), 'hex')
  ) UNIQUE CHECK (referencia_publica ~ '^alt_[0-9a-f]{32}$'),
  id_logico text NOT NULL CHECK (btrim(id_logico) <> ''),
  investigacao_id uuid NOT NULL REFERENCES public.investigacoes_revisao(id),
  tarefa_id uuid NOT NULL,
  tarefa_lease_token uuid NOT NULL,
  tarefa_fencing_token bigint NOT NULL CHECK (tarefa_fencing_token > 0),
  chave_idempotencia text NOT NULL CHECK (btrim(chave_idempotencia) <> ''),
  titulo text NOT NULL CHECK (btrim(titulo) <> '') CHECK (public.investigacao_texto_publico_sanitizado(titulo)),
  campos_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(campos_snapshot) = 'object' AND campos_snapshot <> '{}'::jsonb AND public.investigacao_json_publico_sanitizado(campos_snapshot)),
  confianca_campos jsonb NOT NULL DEFAULT '{}'::jsonb
    CHECK (public.investigacao_confianca_campos_valida(confianca_campos)),
  confianca_geral numeric CHECK (
    confianca_geral IS NULL OR (confianca_geral >= 0 AND confianca_geral <= 1)
  ),
  classificacao text NOT NULL CHECK (classificacao IN (
    'possivel', 'provavel', 'forte', 'ambiguo'
  )),
  regra_confianca_version text NOT NULL CHECK (btrim(regra_confianca_version) <> ''),
  justificativa_sanitizada text NOT NULL CHECK (btrim(justificativa_sanitizada) <> '') CHECK (public.investigacao_texto_publico_sanitizado(justificativa_sanitizada)),
  origem_modelo boolean NOT NULL DEFAULT false,
  criado_em timestamptz NOT NULL DEFAULT now(),
  UNIQUE (
    investigacao_id, tarefa_id, tarefa_fencing_token, id_logico
  ),
  UNIQUE (
    investigacao_id, tarefa_id, tarefa_fencing_token, chave_idempotencia
  ),
  UNIQUE (investigacao_id, id),
  CHECK (public.investigacao_instante_operacional(criado_em)),
  FOREIGN KEY (investigacao_id, tarefa_id)
    REFERENCES public.investigacao_tarefas(investigacao_id, id)
);

CREATE OR REPLACE FUNCTION public.investigacao_campos_escopo_validos(
  p_campos text[]
)
RETURNS boolean
LANGUAGE sql
IMMUTABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
  SELECT coalesce(
    cardinality(p_campos) = (
      SELECT count(DISTINCT campo) FROM unnest(p_campos) campo
    )
    AND NOT EXISTS (
      SELECT 1 FROM unnest(p_campos) campo
       WHERE campo IS NULL
          OR btrim(campo) = ''
          OR NOT public.investigacao_texto_publico_sanitizado(campo)
    ),
    false
  );
$$;

CREATE TABLE IF NOT EXISTS public.investigacao_alternativa_evidencias (
  investigacao_id uuid NOT NULL REFERENCES public.investigacoes_revisao(id),
  alternativa_id uuid NOT NULL,
  evidencia_id uuid NOT NULL,
  papel text NOT NULL CHECK (papel IN ('favoravel', 'contraria')),
  campos_suportados text[] NOT NULL DEFAULT '{}'::text[],
  campos_contestados text[] NOT NULL DEFAULT '{}'::text[],
  criado_em timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (alternativa_id, evidencia_id),
  FOREIGN KEY (investigacao_id, alternativa_id)
    REFERENCES public.investigacao_alternativas(investigacao_id, id),
  FOREIGN KEY (investigacao_id, evidencia_id)
    REFERENCES public.investigacao_evidencias(investigacao_id, id),
  CHECK (
    (papel = 'favoravel'
      AND cardinality(campos_suportados) > 0
      AND cardinality(campos_contestados) = 0)
    OR
    (papel = 'contraria'
      AND cardinality(campos_suportados) = 0
      AND cardinality(campos_contestados) > 0)
  ),
  CHECK (
    public.investigacao_campos_escopo_validos(campos_suportados)
    AND public.investigacao_campos_escopo_validos(campos_contestados)
  ),
  CHECK (public.investigacao_instante_operacional(criado_em))
);

CREATE TABLE IF NOT EXISTS public.investigacao_pendencias (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  id_logico text NOT NULL CHECK (btrim(id_logico) <> ''),
  investigacao_id uuid NOT NULL REFERENCES public.investigacoes_revisao(id),
  tarefa_id uuid NOT NULL,
  tarefa_lease_token uuid NOT NULL,
  tarefa_fencing_token bigint NOT NULL CHECK (tarefa_fencing_token > 0),
  chave_idempotencia text NOT NULL CHECK (btrim(chave_idempotencia) <> ''),
  tipo text NOT NULL CHECK (tipo IN (
    'dado_ausente', 'divergencia', 'cobertura_incompleta',
    'fonte_indisponivel', 'reautenticacao', 'decisao_humana'
  )),
  campo text CHECK (public.investigacao_texto_publico_sanitizado(campo)),
  fonte_tipo text,
  descricao_sanitizada text NOT NULL CHECK (btrim(descricao_sanitizada) <> '') CHECK (public.investigacao_texto_publico_sanitizado(descricao_sanitizada)),
  estado text NOT NULL DEFAULT 'aberta' CHECK (estado IN ('aberta', 'resolvida', 'dispensada')),
  criado_em timestamptz NOT NULL DEFAULT now(),
  resolvida_em timestamptz,
  decidida_por text,
  decisao_motivo_sanitizado text,
  decisao_draft_atualizado_em timestamptz,
  UNIQUE (
    investigacao_id, tarefa_id, tarefa_fencing_token, id_logico
  ),
  UNIQUE (
    investigacao_id, tarefa_id, tarefa_fencing_token, chave_idempotencia
  ),
  FOREIGN KEY (investigacao_id, tarefa_id)
    REFERENCES public.investigacao_tarefas(investigacao_id, id),
  CHECK (
    public.investigacao_instante_operacional(criado_em)
    AND (
      resolvida_em IS NULL
      OR public.investigacao_instante_operacional(resolvida_em)
    )
  ),
  CHECK (
    (estado = 'aberta'
      AND num_nonnulls(
        resolvida_em, decidida_por, decisao_motivo_sanitizado,
        decisao_draft_atualizado_em
      ) = 0)
    OR (estado IN ('resolvida', 'dispensada')
      AND num_nonnulls(
        resolvida_em, decidida_por, decisao_motivo_sanitizado,
        decisao_draft_atualizado_em
      ) = 4
      AND public.investigacao_texto_sanitizado(decidida_por)
      AND public.investigacao_texto_publico_sanitizado(
        decisao_motivo_sanitizado
      )
      AND octet_length(decisao_motivo_sanitizado) <= 1000
      AND public.investigacao_instante_operacional(
        decisao_draft_atualizado_em
      ))
  )
);

-- Revalida a versão física de cada fonte usada pela tentativa aceita. O xmin
-- é apenas um marcador de versão da linha, não um hash de conteúdo. Qualquer
-- mudança exige nova consulta antes de síntese, anexo ou promoção.
CREATE OR REPLACE FUNCTION public.investigacao_evidencias_fontes_atuais(
  p_investigacao_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
  SELECT NOT EXISTS (
    SELECT 1
      FROM public.investigacao_evidencias evidencia
      JOIN public.investigacao_tarefas tarefa
        ON tarefa.id = evidencia.tarefa_id
       AND tarefa.investigacao_id = evidencia.investigacao_id
      LEFT JOIN LATERAL public.investigacao_proveniencia_registro(
        tarefa.adaptador, tarefa.adaptador_version,
        evidencia.fonte_tabela, evidencia.fonte_registro_id
      ) proveniencia ON true
     WHERE evidencia.investigacao_id = p_investigacao_id
       AND tarefa.estado_execucao = 'concluida'
       AND tarefa.resultado_lease_token = evidencia.tarefa_lease_token
       AND tarefa.resultado_fencing_token = evidencia.tarefa_fencing_token
       AND evidencia.fonte_registro_id IS NOT NULL
       AND (
         proveniencia IS NULL
         OR proveniencia ->> 'registro_ref'
              IS DISTINCT FROM evidencia.registro_origem_ref
         OR proveniencia ->> 'snapshot_ref'
              IS DISTINCT FROM evidencia.snapshot_fonte_ref
         OR proveniencia ->> 'ancestral_ref'
              IS DISTINCT FROM evidencia.ancestral_ref
       )
  );
$$;

-- Evento técnico append-only. A tentativa de entrega fica separada abaixo.
CREATE TABLE IF NOT EXISTS public.investigacao_eventos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  investigacao_id uuid NOT NULL REFERENCES public.investigacoes_revisao(id),
  chave_idempotencia text NOT NULL UNIQUE CHECK (btrim(chave_idempotencia) <> ''),
  tipo text NOT NULL CHECK (tipo IN (
    'investigacao_criada', 'tarefa_assumida', 'tarefa_retentativa',
    'tarefa_concluida',
    'resultado_atualizado', 'evidencia_anexada', 'investigacao_obsoleta',
    'investigacao_reativada', 'decisao_revisao_atestada',
    'investigacao_sucessora_criada', 'investigacao_sucessora_replanejada',
    'sucessao_enfileirada',
    'pendencia_decidida'
  )),
  referencia_entidade text CHECK (public.investigacao_texto_sanitizado(referencia_entidade)),
  resumo_sanitizado text CHECK (public.investigacao_texto_sanitizado(resumo_sanitizado)),
  criado_em timestamptz NOT NULL DEFAULT now()
    CHECK (public.investigacao_instante_operacional(criado_em))
);

CREATE TABLE IF NOT EXISTS public.investigacao_entregas (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  evento_id uuid NOT NULL UNIQUE REFERENCES public.investigacao_eventos(id),
  estado text NOT NULL DEFAULT 'pendente' CHECK (estado IN (
    'pendente', 'em_execucao', 'aguardando_retentativa', 'entregue', 'falha_permanente'
  )),
  tentativas integer NOT NULL DEFAULT 0 CHECK (tentativas >= 0),
  proxima_execucao_em timestamptz NOT NULL DEFAULT now(),
  lease_executor text,
  lease_token uuid,
  lease_expira_em timestamptz,
  erro_codigo text CHECK (
    erro_codigo IS NULL OR erro_codigo ~ '^[a-z0-9_.-]{1,80}$'
  ),
  erro_sanitizado text CHECK (
    erro_sanitizado IS NULL
    OR public.investigacao_texto_sanitizado(erro_sanitizado)
  ),
  entregue_em timestamptz,
  criado_em timestamptz NOT NULL DEFAULT now(),
  atualizado_em timestamptz NOT NULL DEFAULT now(),
  CHECK (
    public.investigacao_instante_operacional(proxima_execucao_em)
    AND public.investigacao_instante_operacional(criado_em)
    AND public.investigacao_instante_operacional(atualizado_em)
    AND (
      lease_expira_em IS NULL
      OR public.investigacao_instante_operacional(lease_expira_em)
    )
    AND (
      entregue_em IS NULL
      OR public.investigacao_instante_operacional(entregue_em)
    )
  ),
  CHECK (
    estado <> 'em_execucao'
    OR num_nonnulls(lease_executor, lease_token, lease_expira_em) = 3
  )
);

CREATE OR REPLACE FUNCTION public.criar_entrega_evento_investigacao()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
  INSERT INTO public.investigacao_entregas (evento_id)
  VALUES (NEW.id);
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS investigacao_eventos_cria_entrega
  ON public.investigacao_eventos;
CREATE TRIGGER investigacao_eventos_cria_entrega
AFTER INSERT ON public.investigacao_eventos
FOR EACH ROW EXECUTE FUNCTION public.criar_entrega_evento_investigacao();

-- Prova no banco que cada valor apresentado por uma alternativa está apoiado
-- por evidência favorável aceita da mesma linhagem e com o mesmo valor. O hash
-- usa a representação canônica de escalares do jsonb, compatível com o
-- correlator Python. Objetos e listas não são aceitos como valor de campo.
CREATE OR REPLACE FUNCTION public.investigacao_alternativas_suportadas(
  p_investigacao_id uuid,
  p_tarefa_id uuid,
  p_lease_token uuid,
  p_fencing_token bigint
)
RETURNS boolean
LANGUAGE sql
STABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
  SELECT NOT EXISTS (
    SELECT 1
      FROM public.investigacao_alternativas alternativa
      CROSS JOIN LATERAL jsonb_each(alternativa.campos_snapshot) campo
     WHERE alternativa.investigacao_id = p_investigacao_id
       AND alternativa.tarefa_id = p_tarefa_id
       AND alternativa.tarefa_lease_token = p_lease_token
       AND alternativa.tarefa_fencing_token = p_fencing_token
       AND (
         -- Uma versão é atômica: uma única evidência favorável aceita precisa
         -- conter todo o snapshot. Evidências adicionais corroboram confiança,
         -- mas nunca montam campos avulsos de registros incompatíveis.
         NOT EXISTS (
           SELECT 1
             FROM public.investigacao_alternativa_evidencias ligacao_atomica
             JOIN public.investigacao_evidencias evidencia_atomica
               ON evidencia_atomica.id = ligacao_atomica.evidencia_id
              AND evidencia_atomica.investigacao_id = ligacao_atomica.investigacao_id
             JOIN public.investigacao_tarefas tarefa_atomica
               ON tarefa_atomica.id = evidencia_atomica.tarefa_id
              AND tarefa_atomica.investigacao_id = evidencia_atomica.investigacao_id
            WHERE ligacao_atomica.investigacao_id = alternativa.investigacao_id
              AND ligacao_atomica.alternativa_id = alternativa.id
              AND ligacao_atomica.papel = 'favoravel'
              AND evidencia_atomica.fatos_normalizados @> alternativa.campos_snapshot
              AND NOT EXISTS (
                SELECT 1
                  FROM jsonb_object_keys(alternativa.campos_snapshot) campo_atomico
                 WHERE NOT (campo_atomico = ANY(
                   ligacao_atomica.campos_suportados
                 ))
              )
              AND tarefa_atomica.estado_execucao = 'concluida'
              AND tarefa_atomica.resultado_lease_token =
                    evidencia_atomica.tarefa_lease_token
              AND tarefa_atomica.resultado_fencing_token =
                    evidencia_atomica.tarefa_fencing_token
         )
         OR (jsonb_typeof(campo.value) IN ('string', 'number', 'boolean')) IS NOT TRUE
         OR NOT (alternativa.confianca_campos ? campo.key)
         -- O correlator informa o hash usado na avaliação, mas a aceitação
         -- deriva novamente esse valor do snapshot persistido. Assim, trocar o
         -- valor e reaproveitar uma explicação assinada nunca passa.
         OR EXISTS (
           SELECT 1
             FROM jsonb_array_elements(
               alternativa.confianca_campos -> campo.key
                 -> 'inputs_contexto' -> 'base' -> 'avaliacoes'
             ) avaliacao_hash
            WHERE avaliacao_hash ->> 'valor_hash' IS DISTINCT FROM encode(
              extensions.digest(
                convert_to(
                  public.investigacao_json_canonico(campo.value), 'UTF8'
                ),
                'sha256'
              ),
              'hex'
            )
         )
         -- A ambiguidade também é fato do conjunto persistido, não uma
         -- declaração do worker. Duas versões com valores distintos obrigam
         -- o cap correspondente em todas as alternativas daquele campo.
         OR (
           (alternativa.confianca_campos -> campo.key
              -> 'inputs_contexto' -> 'base'
              ->> 'ambiguidade_no_campo')::boolean
           IS DISTINCT FROM (
             SELECT count(DISTINCT outra.campos_snapshot -> campo.key) > 1
               FROM public.investigacao_alternativas outra
              WHERE outra.investigacao_id = alternativa.investigacao_id
                AND outra.tarefa_id = alternativa.tarefa_id
                AND outra.tarefa_lease_token = alternativa.tarefa_lease_token
                AND outra.tarefa_fencing_token =
                      alternativa.tarefa_fencing_token
                AND outra.campos_snapshot ? campo.key
           )
         )
         -- O grupo atômico é derivado da evidência persistida que contém a
         -- versão inteira. O sintetizador não pode autodeclarar que campos de
         -- linhas distintas pertencem ao mesmo negócio/documento.
         OR (
           (alternativa.confianca_campos -> campo.key
              -> 'inputs_contexto' -> 'base'
              ->> 'grupo_correlacao_verificado')::boolean
           IS DISTINCT FROM EXISTS (
             SELECT 1
               FROM public.investigacao_alternativa_evidencias ligacao_grupo
               JOIN public.investigacao_evidencias evidencia_grupo
                 ON evidencia_grupo.id = ligacao_grupo.evidencia_id
                AND evidencia_grupo.investigacao_id =
                      ligacao_grupo.investigacao_id
               JOIN public.investigacao_tarefas tarefa_grupo
                 ON tarefa_grupo.id = evidencia_grupo.tarefa_id
                AND tarefa_grupo.investigacao_id =
                      evidencia_grupo.investigacao_id
              WHERE ligacao_grupo.investigacao_id = alternativa.investigacao_id
                AND ligacao_grupo.alternativa_id = alternativa.id
                AND ligacao_grupo.papel = 'favoravel'
                AND evidencia_grupo.fatos_normalizados @>
                      alternativa.campos_snapshot
                AND NOT EXISTS (
                  SELECT 1
                    FROM jsonb_object_keys(
                      alternativa.campos_snapshot
                    ) campo_grupo
                   WHERE NOT (
                     campo_grupo = ANY(ligacao_grupo.campos_suportados)
                   )
                )
                AND tarefa_grupo.estado_execucao = 'concluida'
                AND tarefa_grupo.resultado_lease_token =
                      evidencia_grupo.tarefa_lease_token
                AND tarefa_grupo.resultado_fencing_token =
                      evidencia_grupo.tarefa_fencing_token
           )
         )
         OR NOT EXISTS (
           SELECT 1
             FROM public.investigacao_alternativa_evidencias ligacao
             JOIN public.investigacao_evidencias evidencia
               ON evidencia.id = ligacao.evidencia_id
              AND evidencia.investigacao_id = ligacao.investigacao_id
             JOIN public.investigacao_tarefas tarefa_fonte
               ON tarefa_fonte.id = evidencia.tarefa_id
              AND tarefa_fonte.investigacao_id = evidencia.investigacao_id
            WHERE ligacao.investigacao_id = alternativa.investigacao_id
              AND ligacao.alternativa_id = alternativa.id
              AND ligacao.papel = 'favoravel'
              AND campo.key = ANY(ligacao.campos_suportados)
              AND evidencia.fatos_normalizados ? campo.key
              AND evidencia.fatos_normalizados -> campo.key = campo.value
              AND tarefa_fonte.estado_execucao = 'concluida'
              AND tarefa_fonte.resultado_lease_token = evidencia.tarefa_lease_token
              AND tarefa_fonte.resultado_fencing_token = evidencia.tarefa_fencing_token
         )
         OR EXISTS (
           SELECT 1
             FROM jsonb_array_elements(
               alternativa.confianca_campos -> campo.key
                 -> 'inputs_contexto' -> 'base' -> 'avaliacoes'
             ) avaliacao
            WHERE NOT EXISTS (
                 SELECT 1
                   FROM public.investigacao_alternativa_evidencias ligacao
                   JOIN public.investigacao_evidencias evidencia
                     ON evidencia.id = ligacao.evidencia_id
                    AND evidencia.investigacao_id = ligacao.investigacao_id
                   JOIN public.investigacao_tarefas tarefa_fonte
                     ON tarefa_fonte.id = evidencia.tarefa_id
                    AND tarefa_fonte.investigacao_id = evidencia.investigacao_id
                  WHERE ligacao.investigacao_id = alternativa.investigacao_id
                    AND ligacao.alternativa_id = alternativa.id
                    AND ligacao.papel = 'favoravel'
                    AND campo.key = ANY(ligacao.campos_suportados)
                    AND evidencia.linhagem = avaliacao ->> 'linhagem'
                    AND evidencia.provas_campos -> 'campos' -> campo.key
                          ->> 'criterio' = avaliacao ->> 'tipo_correspondencia'
                    AND evidencia.provas_campos -> 'campos' -> campo.key
                          ->> 'identidade_tipo'
                          IS NOT DISTINCT FROM avaliacao ->> 'identidade_tipo'
                    AND evidencia.provas_campos -> 'campos' -> campo.key
                          ->> 'identidade_namespace_hash'
                          IS NOT DISTINCT FROM
                            avaliacao ->> 'identidade_namespace_hash'
                    AND evidencia.provas_campos -> 'campos' -> campo.key
                          ->> 'identidade_valor_hash'
                          IS NOT DISTINCT FROM
                            avaliacao ->> 'identidade_valor_hash'
                    AND evidencia.fatos_normalizados ? campo.key
                    AND evidencia.fatos_normalizados -> campo.key = campo.value
                    AND tarefa_fonte.estado_execucao = 'concluida'
                    AND ((avaliacao ->> 'universo_coberto')::boolean)
                          IS NOT DISTINCT FROM
                            (tarefa_fonte.estado_cobertura = 'completa')
                    AND ((avaliacao ->> 'extracao_confirmada')::boolean)
                          IS NOT DISTINCT FROM (
                            tarefa_fonte.adaptador IN ('agronotas', 'ofx', 'ima')
                            AND evidencia.provas_campos -> 'campos' -> campo.key
                                  ->> 'criterio' IN (
                              'identificador_exato', 'documento_referenciado'
                            )
                            AND jsonb_typeof(
                              evidencia.provas_campos -> 'campos' -> campo.key
                                -> 'identidade_tipo'
                            ) = 'string'
                          )
                    AND ((avaliacao ->> 'divergencia_central')::boolean)
                          IS NOT DISTINCT FROM (
                            (avaliacao ->> 'identidade_tipo') IS NOT NULL
                            AND EXISTS (
                              SELECT 1
                                FROM public.investigacao_evidencias evidencia_div
                                JOIN public.investigacao_tarefas tarefa_div
                                  ON tarefa_div.id = evidencia_div.tarefa_id
                                 AND tarefa_div.investigacao_id =
                                       evidencia_div.investigacao_id
                               WHERE evidencia_div.investigacao_id =
                                       alternativa.investigacao_id
                                 AND tarefa_div.adaptador IN (
                                   'agronotas', 'ofx', 'ima'
                                 )
                                 AND tarefa_div.estado_execucao = 'concluida'
                                 AND tarefa_div.estado_cobertura = 'completa'
                                 AND tarefa_div.resultado_lease_token =
                                       evidencia_div.tarefa_lease_token
                                 AND tarefa_div.resultado_fencing_token =
                                       evidencia_div.tarefa_fencing_token
                                 AND evidencia_div.fatos_normalizados ? campo.key
                                 AND evidencia_div.provas_campos -> 'campos'
                                       -> campo.key ->> 'criterio' IN (
                                   'identificador_exato',
                                   'documento_referenciado'
                                 )
                                 AND evidencia_div.provas_campos -> 'campos'
                                       -> campo.key ->> 'identidade_tipo'
                                       IS NOT DISTINCT FROM
                                         avaliacao ->> 'identidade_tipo'
                                 AND evidencia_div.provas_campos -> 'campos'
                                       -> campo.key ->> 'identidade_namespace_hash'
                                       IS NOT DISTINCT FROM
                                         avaliacao ->> 'identidade_namespace_hash'
                                 AND evidencia_div.provas_campos -> 'campos'
                                       -> campo.key ->> 'identidade_valor_hash'
                                       IS NOT DISTINCT FROM
                                         avaliacao ->> 'identidade_valor_hash'
                               HAVING count(DISTINCT evidencia_div.tarefa_id) > 1
                                  AND count(DISTINCT (
                                    evidencia_div.fatos_normalizados -> campo.key
                                  )) > 1
                            )
                          )
                    AND nullif(
                          avaliacao ->> 'quantidade_correspondencias', ''
                        )::numeric IS NOT DISTINCT FROM (
                          SELECT count(*)::numeric
                            FROM public.investigacao_evidencias mesma_fonte
                           WHERE mesma_fonte.investigacao_id = evidencia.investigacao_id
                             AND mesma_fonte.tarefa_id = evidencia.tarefa_id
                             AND mesma_fonte.tarefa_lease_token =
                                   evidencia.tarefa_lease_token
                             AND mesma_fonte.tarefa_fencing_token =
                                   evidencia.tarefa_fencing_token
                             AND mesma_fonte.provas_campos -> 'campos' -> campo.key
                                   ->> 'criterio' = 'identificador_exato'
                             AND mesma_fonte.provas_campos -> 'campos' -> campo.key
                                   ->> 'identidade_tipo'
                                   IS NOT DISTINCT FROM
                                     avaliacao ->> 'identidade_tipo'
                             AND mesma_fonte.provas_campos -> 'campos' -> campo.key
                                   ->> 'identidade_namespace_hash'
                                   IS NOT DISTINCT FROM
                                     avaliacao ->> 'identidade_namespace_hash'
                             AND mesma_fonte.provas_campos -> 'campos' -> campo.key
                                   ->> 'identidade_valor_hash'
                                   IS NOT DISTINCT FROM
                                     avaliacao ->> 'identidade_valor_hash'
                             AND mesma_fonte.fatos_normalizados ? campo.key
                             AND mesma_fonte.fatos_normalizados -> campo.key = campo.value
                        )
                    AND tarefa_fonte.resultado_lease_token = evidencia.tarefa_lease_token
                    AND tarefa_fonte.resultado_fencing_token = evidencia.tarefa_fencing_token
               )
               OR (
                 (avaliacao ->> 'coerencia_verificada')::boolean IS TRUE
                 AND NOT EXISTS (
                   SELECT 1
                     FROM public.investigacao_alternativa_evidencias ligacao_a
                     JOIN public.investigacao_evidencias evidencia_a
                       ON evidencia_a.id = ligacao_a.evidencia_id
                      AND evidencia_a.investigacao_id = ligacao_a.investigacao_id
                     JOIN public.investigacao_tarefas tarefa_a
                       ON tarefa_a.id = evidencia_a.tarefa_id
                      AND tarefa_a.investigacao_id = evidencia_a.investigacao_id
                     JOIN public.investigacao_alternativa_evidencias ligacao_b
                       ON ligacao_b.alternativa_id = ligacao_a.alternativa_id
                      AND ligacao_b.investigacao_id = ligacao_a.investigacao_id
                      AND ligacao_b.papel = 'favoravel'
                     JOIN public.investigacao_evidencias evidencia_b
                       ON evidencia_b.id = ligacao_b.evidencia_id
                      AND evidencia_b.investigacao_id = ligacao_b.investigacao_id
                     JOIN public.investigacao_tarefas tarefa_b
                       ON tarefa_b.id = evidencia_b.tarefa_id
                      AND tarefa_b.investigacao_id = evidencia_b.investigacao_id
                    WHERE ligacao_a.alternativa_id = alternativa.id
                      AND ligacao_a.investigacao_id = alternativa.investigacao_id
                      AND ligacao_a.papel = 'favoravel'
                      AND campo.key = ANY(ligacao_a.campos_suportados)
                      AND campo.key = ANY(ligacao_b.campos_suportados)
                      AND evidencia_a.linhagem = avaliacao ->> 'linhagem'
                      AND evidencia_a.tarefa_id IS DISTINCT FROM evidencia_b.tarefa_id
                      AND evidencia_a.linhagem IS DISTINCT FROM evidencia_b.linhagem
                      AND evidencia_a.origem_classe = 'nativa'
                      AND evidencia_b.origem_classe = 'nativa'
                      AND evidencia_a.autoridade_fonte IS DISTINCT FROM
                            evidencia_b.autoridade_fonte
                      AND evidencia_a.dataset_ref IS DISTINCT FROM
                            evidencia_b.dataset_ref
                      AND evidencia_a.registro_origem_ref IS DISTINCT FROM
                            evidencia_b.registro_origem_ref
                      AND evidencia_a.snapshot_fonte_ref IS DISTINCT FROM
                            evidencia_b.snapshot_fonte_ref
                      AND evidencia_a.ancestral_ref IS DISTINCT FROM
                            evidencia_b.ancestral_ref
                      AND tarefa_a.adaptador IS DISTINCT FROM tarefa_b.adaptador
                      AND tarefa_a.prova_cobertura ->> 'familia_fonte'
                            IS DISTINCT FROM
                          tarefa_b.prova_cobertura ->> 'familia_fonte'
                      AND evidencia_a.provas_campos -> 'campos' -> campo.key
                            ->> 'criterio' IN (
                        'identificador_exato', 'documento_referenciado'
                      )
                      AND evidencia_b.provas_campos -> 'campos' -> campo.key
                            ->> 'criterio' IN (
                        'identificador_exato', 'documento_referenciado'
                      )
                      AND jsonb_typeof(
                        evidencia_a.provas_campos -> 'campos' -> campo.key
                          -> 'identidade_tipo'
                      ) = 'string'
                      AND jsonb_typeof(
                        evidencia_b.provas_campos -> 'campos' -> campo.key
                          -> 'identidade_tipo'
                      ) = 'string'
                      AND evidencia_a.provas_campos -> 'campos' -> campo.key
                            ->> 'identidade_tipo'
                            IS NOT DISTINCT FROM
                              evidencia_b.provas_campos -> 'campos' -> campo.key
                                ->> 'identidade_tipo'
                      AND evidencia_a.provas_campos -> 'campos' -> campo.key
                            ->> 'identidade_namespace_hash'
                            IS NOT DISTINCT FROM
                              evidencia_b.provas_campos -> 'campos' -> campo.key
                                ->> 'identidade_namespace_hash'
                      AND evidencia_a.provas_campos -> 'campos' -> campo.key
                            ->> 'identidade_valor_hash'
                            IS NOT DISTINCT FROM
                              evidencia_b.provas_campos -> 'campos' -> campo.key
                                ->> 'identidade_valor_hash'
                      AND evidencia_a.fatos_normalizados -> campo.key = campo.value
                      AND evidencia_b.fatos_normalizados -> campo.key = campo.value
                      AND tarefa_a.estado_execucao = 'concluida'
                      AND tarefa_a.resultado_lease_token =
                            evidencia_a.tarefa_lease_token
                      AND tarefa_a.resultado_fencing_token =
                            evidencia_a.tarefa_fencing_token
                      AND tarefa_b.estado_execucao = 'concluida'
                      AND tarefa_b.resultado_lease_token =
                            evidencia_b.tarefa_lease_token
                      AND tarefa_b.resultado_fencing_token =
                            evidencia_b.tarefa_fencing_token
                 )
               )
         )
         OR (
           alternativa.confianca_campos -> campo.key ->> 'classificacao' = 'forte'
           AND NOT EXISTS (
             SELECT 1
               FROM public.investigacao_alternativa_evidencias ligacao
               JOIN public.investigacao_evidencias evidencia
                 ON evidencia.id = ligacao.evidencia_id
                AND evidencia.investigacao_id = ligacao.investigacao_id
               JOIN public.investigacao_tarefas tarefa_fonte
                 ON tarefa_fonte.id = evidencia.tarefa_id
                AND tarefa_fonte.investigacao_id = evidencia.investigacao_id
              WHERE ligacao.investigacao_id = alternativa.investigacao_id
                AND ligacao.alternativa_id = alternativa.id
                AND ligacao.papel = 'favoravel'
                AND campo.key = ANY(ligacao.campos_suportados)
                AND tarefa_fonte.adaptador IN ('agronotas', 'ofx', 'ima')
                AND evidencia.provas_campos -> 'campos' -> campo.key
                      ->> 'criterio' IN (
                  'identificador_exato', 'documento_referenciado'
                )
                AND jsonb_typeof(
                  evidencia.provas_campos -> 'campos' -> campo.key
                    -> 'identidade_tipo'
                ) = 'string'
                AND evidencia.fatos_normalizados ? campo.key
                AND evidencia.fatos_normalizados -> campo.key = campo.value
                AND tarefa_fonte.estado_execucao = 'concluida'
                AND tarefa_fonte.estado_cobertura = 'completa'
                AND tarefa_fonte.resultado_lease_token = evidencia.tarefa_lease_token
                AND tarefa_fonte.resultado_fencing_token = evidencia.tarefa_fencing_token
              GROUP BY
                evidencia.provas_campos -> 'campos' -> campo.key
                  ->> 'identidade_tipo',
                evidencia.provas_campos -> 'campos' -> campo.key
                  ->> 'identidade_namespace_hash',
                evidencia.provas_campos -> 'campos' -> campo.key
                  ->> 'identidade_valor_hash'
             HAVING count(DISTINCT evidencia.linhagem) >= 2
                AND count(DISTINCT evidencia.tarefa_id) >= 2
                AND count(DISTINCT tarefa_fonte.adaptador) >= 2
                AND bool_and(evidencia.origem_classe = 'nativa')
                AND count(DISTINCT evidencia.autoridade_fonte) >= 2
                AND count(DISTINCT evidencia.dataset_ref) >= 2
                AND count(DISTINCT evidencia.registro_origem_ref) >= 2
                AND count(DISTINCT evidencia.snapshot_fonte_ref) >= 2
                AND count(DISTINCT evidencia.ancestral_ref) >= 2
                AND count(DISTINCT (
                  tarefa_fonte.prova_cobertura ->> 'familia_fonte'
                )) >= 2
                AND bool_or(
                  evidencia.provas_campos -> 'campos' -> campo.key
                    ->> 'criterio' = 'identificador_exato'
                )
           )
         )
       )
  );
$$;

CREATE INDEX IF NOT EXISTS investigacoes_revisao_fila_idx
  ON public.investigacoes_revisao (estado_execucao, prioridade, criado_em);
CREATE INDEX IF NOT EXISTS investigacoes_revisao_draft_bloqueio_idx
  ON public.investigacoes_revisao (source_draft_id, estado_execucao)
  WHERE source_draft_id IS NOT NULL AND anexado_em IS NULL;
CREATE INDEX IF NOT EXISTS investigacoes_revisao_candidatos_bloqueio_idx
  ON public.investigacoes_revisao USING gin (negocio_candidato_ids)
  WHERE anexado_em IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS investigacoes_revisao_folha_ativa_unica
  ON public.investigacoes_revisao (raiz_investigacao_id)
  WHERE estado_execucao <> 'obsoleta';
CREATE INDEX IF NOT EXISTS investigacao_tarefas_fila_idx
  ON public.investigacao_tarefas (estado_execucao, proxima_execucao_em, lease_expira_em);
CREATE UNIQUE INDEX IF NOT EXISTS investigacao_tarefas_sintese_unica_idx
  ON public.investigacao_tarefas (investigacao_id)
  WHERE adaptador = 'sintese';
CREATE INDEX IF NOT EXISTS investigacao_evidencias_investigacao_idx
  ON public.investigacao_evidencias (investigacao_id, classificacao, fonte_tipo);
CREATE INDEX IF NOT EXISTS investigacao_alternativas_investigacao_idx
  ON public.investigacao_alternativas (investigacao_id, classificacao);
CREATE INDEX IF NOT EXISTS investigacao_pendencias_abertas_idx
  ON public.investigacao_pendencias (investigacao_id, tipo) WHERE estado = 'aberta';
CREATE INDEX IF NOT EXISTS investigacao_eventos_criados_idx
  ON public.investigacao_eventos (investigacao_id, criado_em);
CREATE INDEX IF NOT EXISTS investigacao_entregas_fila_idx
  ON public.investigacao_entregas (estado, proxima_execucao_em, lease_expira_em);

CREATE OR REPLACE FUNCTION public.validar_tarefa_no_plano_investigacao()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_pai_estado text;
  v_pai_motivo text;
BEGIN
  IF NEW.adaptador IS DISTINCT FROM 'sintese' THEN
    PERFORM pg_advisory_xact_lock(hashtextextended(
      'investigacao-config:' || NEW.adaptador || ':' || NEW.adaptador_version,
      0
    ));
    PERFORM 1
      FROM public.investigacao_adaptadores_config config
     WHERE config.adaptador = NEW.adaptador
       AND config.adaptador_version = NEW.adaptador_version
       AND config.habilitado
     FOR UPDATE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Tarefa-fonte exige versão de adaptador habilitada';
    END IF;
  END IF;
  IF NEW.estado_execucao IS DISTINCT FROM 'pendente'
     OR NEW.estado_resultado IS NOT NULL
     OR NEW.estado_cobertura IS NOT NULL
     OR NEW.tentativas IS DISTINCT FROM 0
     OR num_nonnulls(
       NEW.lease_executor, NEW.lease_token, NEW.lease_expira_em,
       NEW.resultado_lease_token, NEW.resultado_fencing_token,
       NEW.resultado_pedido_hash, NEW.retentativa_lease_token,
       NEW.retentativa_fencing_token, NEW.retentativa_executor,
       NEW.retentativa_pedido_hash,
       NEW.iniciado_em, NEW.concluido_em
     ) <> 0
     OR NEW.fencing_token IS DISTINCT FROM 0 THEN
    RAISE EXCEPTION 'A tarefa deve nascer pendente, sem lease ou resultado';
  END IF;
  IF NOT EXISTS (
    SELECT 1
      FROM public.investigacoes_revisao investigacao,
           jsonb_array_elements(investigacao.plano_tarefas) item
     WHERE investigacao.id = NEW.investigacao_id
       AND item ->> 'plano_item_ref' = NEW.plano_item_ref
       AND item ->> 'adaptador' = NEW.adaptador
       AND item ->> 'adaptador_version' = NEW.adaptador_version
       AND item ->> 'consulta_ref' = NEW.consulta_ref
       AND item ->> 'consulta_schema_version' = NEW.consulta_schema_version
       AND item -> 'consulta_spec' = NEW.consulta_spec
       AND item ->> 'consulta_canonico' = NEW.consulta_canonico
       AND item ->> 'consulta_hash' = NEW.consulta_hash
  ) THEN
    RAISE EXCEPTION 'A tarefa não pertence ao plano imutável da investigação';
  END IF;
  SELECT estado_execucao, obsolescencia_motivo
    INTO v_pai_estado, v_pai_motivo
    FROM public.investigacoes_revisao
   WHERE id = NEW.investigacao_id;
  IF v_pai_estado = 'obsoleta'
     AND v_pai_motivo = 'complementar_promocao_ativa' THEN
    NEW.estado_execucao := 'obsoleta';
  ELSIF v_pai_estado NOT IN (
    'pendente', 'em_execucao', 'aguardando_retentativa'
  ) THEN
    RAISE EXCEPTION 'A tarefa não pode nascer sob investigação terminal';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS investigacao_tarefas_plano_imutavel
  ON public.investigacao_tarefas;
CREATE TRIGGER investigacao_tarefas_plano_imutavel
BEFORE INSERT ON public.investigacao_tarefas
FOR EACH ROW EXECUTE FUNCTION public.validar_tarefa_no_plano_investigacao();

CREATE OR REPLACE FUNCTION public.proteger_consulta_tarefa_investigacao()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF NEW.consulta_ref IS DISTINCT FROM OLD.consulta_ref
     OR NEW.plano_item_ref IS DISTINCT FROM OLD.plano_item_ref
     OR NEW.consulta_schema_version IS DISTINCT FROM OLD.consulta_schema_version
     OR NEW.consulta_spec IS DISTINCT FROM OLD.consulta_spec
     OR NEW.consulta_canonico IS DISTINCT FROM OLD.consulta_canonico
     OR NEW.consulta_hash IS DISTINCT FROM OLD.consulta_hash
     OR NEW.adaptador IS DISTINCT FROM OLD.adaptador
     OR NEW.adaptador_version IS DISTINCT FROM OLD.adaptador_version THEN
    RAISE EXCEPTION 'A consulta assumida por um worker é imutável';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS investigacao_tarefas_consulta_imutavel
  ON public.investigacao_tarefas;
CREATE TRIGGER investigacao_tarefas_consulta_imutavel
BEFORE UPDATE OF plano_item_ref, consulta_ref, consulta_schema_version, consulta_spec,
  consulta_canonico, consulta_hash, adaptador, adaptador_version
ON public.investigacao_tarefas
FOR EACH ROW EXECUTE FUNCTION public.proteger_consulta_tarefa_investigacao();

CREATE OR REPLACE FUNCTION public.atualizar_timestamp_investigacoes_revisao()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF OLD.atualizado_em IS NULL
     OR NOT public.investigacao_instante_operacional(OLD.atualizado_em) THEN
    RAISE EXCEPTION 'Snapshot temporal inválido; saneie a linha antes de atualizar';
  END IF;
  -- `now()` é fixo no início da transação e pode repetir ou até regredir a
  -- versão depois de espera por lock. O snapshot precisa avançar a cada UPDATE.
  NEW.atualizado_em := greatest(
    clock_timestamp(), OLD.atualizado_em + interval '1 microsecond'
  );
  RETURN NEW;
END;
$$;

-- Atualiza o helper legado do staging: `now()` repetia a mesma versão em duas
-- edições na mesma transação e podia regredir depois de espera por lock.
CREATE OR REPLACE FUNCTION public.atualizar_timestamp_staging_consolidacao()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF OLD.atualizado_em IS NULL
     OR NOT public.investigacao_instante_operacional(OLD.atualizado_em) THEN
    RAISE EXCEPTION 'Snapshot temporal inválido; saneie a linha antes de atualizar';
  END IF;
  NEW.atualizado_em := greatest(
    clock_timestamp(), OLD.atualizado_em + interval '1 microsecond'
  );
  RETURN NEW;
END;
$$;

-- Preflight somente leitura: a migração para se qualquer origem já contiver
-- infinito. Não corrigimos silenciosamente um snapshot legado porque isso
-- apagaria a versão que deveria proteger a investigação contra stale data.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM public.operation_drafts
     WHERE atualizado_em IS NULL
        OR NOT public.investigacao_instante_operacional(atualizado_em)
  ) OR EXISTS (
    SELECT 1 FROM public.pending_actions
     WHERE atualizado_em IS NULL
        OR NOT public.investigacao_instante_operacional(atualizado_em)
  ) OR EXISTS (
    SELECT 1 FROM public.negocios_candidatos
     WHERE atualizado_em IS NULL
        OR NOT public.investigacao_instante_operacional(atualizado_em)
  ) THEN
    RAISE EXCEPTION 'Preflight falhou: origem correlacionada possui timestamp fora da janela operacional';
  END IF;
END;
$$;

DO $$
DECLARE
  v_tabela text;
  v_constraint text;
  v_definicao text;
BEGIN
  FOREACH v_tabela IN ARRAY ARRAY[
    'operation_drafts', 'pending_actions', 'negocios_candidatos'
  ] LOOP
    v_constraint := v_tabela || '_atualizado_em_operacional';
    SELECT pg_get_constraintdef(constraint_row.oid)
      INTO v_definicao
      FROM pg_constraint constraint_row
     WHERE constraint_row.conrelid = format('public.%I', v_tabela)::regclass
       AND constraint_row.conname = v_constraint;
    IF v_definicao IS NULL THEN
      EXECUTE format(
        'ALTER TABLE public.%I ADD CONSTRAINT %I CHECK (public.investigacao_instante_operacional(atualizado_em))',
        v_tabela, v_constraint
      );
    ELSIF v_definicao NOT LIKE '%investigacao_instante_operacional(atualizado_em)%' THEN
      RAISE EXCEPTION 'A constraint temporal % já existe com outra definição',
        v_constraint;
    END IF;
  END LOOP;
END;
$$;

DROP TRIGGER IF EXISTS investigacoes_revisao_atualizado_em ON public.investigacoes_revisao;
CREATE TRIGGER investigacoes_revisao_atualizado_em
BEFORE UPDATE ON public.investigacoes_revisao
FOR EACH ROW EXECUTE FUNCTION public.atualizar_timestamp_investigacoes_revisao();

DROP TRIGGER IF EXISTS investigacao_entregas_atualizado_em ON public.investigacao_entregas;
CREATE TRIGGER investigacao_entregas_atualizado_em
BEFORE UPDATE ON public.investigacao_entregas
FOR EACH ROW EXECUTE FUNCTION public.atualizar_timestamp_investigacoes_revisao();

-- O snapshot de um rascunho só é confiável se toda mudança semântica renovar
-- atualizado_em, inclusive escritas de clientes antigos.
DROP TRIGGER IF EXISTS operation_drafts_investigacao_atualizado_em
  ON public.operation_drafts;
CREATE TRIGGER operation_drafts_investigacao_atualizado_em
BEFORE UPDATE ON public.operation_drafts
FOR EACH ROW EXECUTE FUNCTION public.atualizar_timestamp_investigacoes_revisao();

-- A pendência-fonte participa do mesmo snapshot otimista da preparação. Toda
-- edição precisa renovar a versão, mesmo quando um cliente antigo omite o
-- campo atualizado_em.
DROP TRIGGER IF EXISTS pending_actions_investigacao_atualizado_em
  ON public.pending_actions;
CREATE TRIGGER pending_actions_investigacao_atualizado_em
BEFORE UPDATE ON public.pending_actions
FOR EACH ROW EXECUTE FUNCTION public.atualizar_timestamp_investigacoes_revisao();

-- Todo resultado pertence ao lease que efetivamente consultou a fonte. Um
-- worker antigo não consegue gravar depois que a tarefa é retomada por outro.
CREATE OR REPLACE FUNCTION public.validar_fencing_resultado_investigacao()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_policy_version text;
  v_adaptador text;
BEGIN
  IF NOT EXISTS (
    SELECT 1
      FROM public.investigacao_tarefas tarefa
     WHERE tarefa.id = NEW.tarefa_id
       AND tarefa.investigacao_id = NEW.investigacao_id
       AND tarefa.estado_execucao = 'em_execucao'
       AND tarefa.lease_token = NEW.tarefa_lease_token
       AND tarefa.fencing_token = NEW.tarefa_fencing_token
       AND tarefa.lease_expira_em >= clock_timestamp()
  ) THEN
    RAISE EXCEPTION 'Lease vencido, substituído ou incompatível com o resultado';
  END IF;
  IF TG_TABLE_NAME = 'investigacao_alternativas' THEN
    SELECT policy_version INTO v_policy_version
      FROM public.investigacoes_revisao
     WHERE id = NEW.investigacao_id;
    IF v_policy_version IS NULL
       OR to_jsonb(NEW) ->> 'regra_confianca_version'
            <> 'confianca-deterministica-v2'
       OR EXISTS (
         SELECT 1
           FROM jsonb_each(to_jsonb(NEW) -> 'confianca_campos') AS campo
          WHERE campo.value ->> 'policy_version' IS DISTINCT FROM v_policy_version
       ) THEN
      RAISE EXCEPTION 'Política de confiança incompatível com a investigação';
    END IF;
  ELSIF TG_TABLE_NAME = 'investigacao_evidencias' THEN
    SELECT adaptador INTO v_adaptador
      FROM public.investigacao_tarefas
     WHERE id = NEW.tarefa_id
       AND investigacao_id = NEW.investigacao_id;
    IF v_adaptador IS NULL OR EXISTS (
      SELECT 1 FROM jsonb_each(NEW.provas_campos -> 'campos') prova
       WHERE NOT public.investigacao_identidade_permitida_adaptador(
         v_adaptador, nullif(prova.value ->> 'identidade_tipo', '')
       )
    ) THEN
      RAISE EXCEPTION 'Prova de identidade incompatível com o adaptador registrado';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS investigacao_evidencias_fencing ON public.investigacao_evidencias;
CREATE TRIGGER investigacao_evidencias_fencing
BEFORE INSERT ON public.investigacao_evidencias
FOR EACH ROW EXECUTE FUNCTION public.validar_fencing_resultado_investigacao();

DROP TRIGGER IF EXISTS investigacao_alternativas_fencing ON public.investigacao_alternativas;
CREATE TRIGGER investigacao_alternativas_fencing
BEFORE INSERT ON public.investigacao_alternativas
FOR EACH ROW EXECUTE FUNCTION public.validar_fencing_resultado_investigacao();

DROP TRIGGER IF EXISTS investigacao_pendencias_fencing ON public.investigacao_pendencias;
CREATE TRIGGER investigacao_pendencias_fencing
BEFORE INSERT ON public.investigacao_pendencias
FOR EACH ROW EXECUTE FUNCTION public.validar_fencing_resultado_investigacao();

CREATE OR REPLACE FUNCTION public.validar_fencing_ligacao_investigacao()
RETURNS trigger
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
      FROM public.investigacao_alternativas alternativa
      JOIN public.investigacao_evidencias evidencia
        ON evidencia.investigacao_id = alternativa.investigacao_id
      JOIN public.investigacao_tarefas tarefa_sintese
        ON tarefa_sintese.id = alternativa.tarefa_id
       AND tarefa_sintese.investigacao_id = alternativa.investigacao_id
      JOIN public.investigacao_tarefas tarefa_fonte
        ON tarefa_fonte.id = evidencia.tarefa_id
       AND tarefa_fonte.investigacao_id = evidencia.investigacao_id
     WHERE alternativa.id = NEW.alternativa_id
       AND evidencia.id = NEW.evidencia_id
       AND alternativa.investigacao_id = NEW.investigacao_id
       AND tarefa_sintese.estado_execucao = 'em_execucao'
       AND tarefa_sintese.lease_token = alternativa.tarefa_lease_token
       AND tarefa_sintese.fencing_token = alternativa.tarefa_fencing_token
       AND tarefa_sintese.lease_expira_em >= clock_timestamp()
       AND tarefa_fonte.estado_execucao = 'concluida'
       AND tarefa_fonte.resultado_lease_token = evidencia.tarefa_lease_token
       AND tarefa_fonte.resultado_fencing_token = evidencia.tarefa_fencing_token
  ) THEN
    RAISE EXCEPTION 'Vínculo produzido fora do lease ativo da tarefa';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS investigacao_alternativa_evidencias_fencing
  ON public.investigacao_alternativa_evidencias;
CREATE TRIGGER investigacao_alternativa_evidencias_fencing
BEFORE INSERT ON public.investigacao_alternativa_evidencias
FOR EACH ROW EXECUTE FUNCTION public.validar_fencing_ligacao_investigacao();

-- Projeções humanas: nenhuma delas expõe conversa, mensagem, chave natural,
-- referência privada, consulta, lease, erro técnico ou conteúdo bruto.
CREATE OR REPLACE VIEW public.v_investigacoes_revisao
WITH (security_barrier = true)
AS SELECT
  referencia_publica, assunto_tipo, titulo, prioridade, contexto_nome,
  fluxo_tipo, estado_execucao, estado_resultado, resumo_sanitizado, criado_em,
  atualizado_em, concluida_em, anexado_em
FROM public.investigacoes_revisao;

-- View enxuta para o gate da fila. O frontend ainda pagina até EOF; o filtro
-- evita que histórico encerrado consuma as primeiras páginas do PostgREST.
CREATE OR REPLACE VIEW public.v_investigacoes_revisao_bloqueios
WITH (security_barrier = true)
AS SELECT
  referencia_publica, fluxo_tipo, estado_execucao, anexado_em
FROM public.investigacoes_revisao
WHERE estado_execucao IN ('pendente', 'em_execucao', 'aguardando_retentativa')
   OR (estado_execucao = 'concluida' AND anexado_em IS NULL);

-- Projeção técnica mínima e exclusiva do materializador service_role. Não é
-- concedida ao frontend e não contém conversa, mensagem, resumo ou evidência.
CREATE OR REPLACE VIEW public.v_investigacoes_revisao_materializacao
WITH (security_barrier = true)
AS SELECT
  id, referencia_publica, source_draft_id, negocio_candidato_ids,
  fingerprint_base,
  estado_execucao, anexado_em, fluxo_tipo, promocao_origem_id,
  draft_operacional_origem_id, destino_operacional_origem,
  registro_operacional_origem_id,
  registro_operacional_origem_snapshot_ref, vinculo_operacional_estado
FROM public.investigacoes_revisao;

CREATE OR REPLACE VIEW public.v_investigacao_alternativas
WITH (security_barrier = true)
AS SELECT
  alternativa.referencia_publica,
  investigacao.referencia_publica AS investigacao_referencia,
  alternativa.titulo,
  ARRAY(
    SELECT chave
      FROM jsonb_object_keys(alternativa.campos_snapshot) AS chave
     ORDER BY chave
  ) AS campos_presentes,
  alternativa.confianca_geral, alternativa.classificacao,
  alternativa.justificativa_sanitizada, alternativa.criado_em
FROM public.investigacao_alternativas alternativa
JOIN public.investigacoes_revisao investigacao
  ON investigacao.id = alternativa.investigacao_id
JOIN public.investigacao_tarefas tarefa
  ON tarefa.id = alternativa.tarefa_id
 AND tarefa.investigacao_id = alternativa.investigacao_id
WHERE tarefa.estado_execucao = 'concluida'
  AND tarefa.resultado_lease_token = alternativa.tarefa_lease_token
  AND tarefa.resultado_fencing_token = alternativa.tarefa_fencing_token;

CREATE OR REPLACE VIEW public.v_investigacao_evidencias
WITH (security_barrier = true)
AS SELECT
  'evi_' || substr(encode(extensions.digest(convert_to(
    evidencia.id::text, 'UTF8'
  ), 'sha256'), 'hex'), 1, 32) AS referencia_publica,
  investigacao.referencia_publica AS investigacao_referencia,
  evidencia.fonte_tipo,
  evidencia.classificacao, evidencia.confianca, evidencia.resumo_sanitizado,
  evidencia.evidenciado_em, evidencia.criado_em
FROM public.investigacao_evidencias evidencia
JOIN public.investigacoes_revisao investigacao
  ON investigacao.id = evidencia.investigacao_id
JOIN public.investigacao_tarefas tarefa
  ON tarefa.id = evidencia.tarefa_id
 AND tarefa.investigacao_id = evidencia.investigacao_id
WHERE tarefa.estado_execucao = 'concluida'
  AND tarefa.resultado_lease_token = evidencia.tarefa_lease_token
  AND tarefa.resultado_fencing_token = evidencia.tarefa_fencing_token;

CREATE OR REPLACE VIEW public.v_investigacao_pendencias
WITH (security_barrier = true)
AS SELECT
  'pen_' || substr(encode(extensions.digest(convert_to(
    pendencia.id::text, 'UTF8'
  ), 'sha256'), 'hex'), 1, 32) AS referencia_publica,
  investigacao.referencia_publica AS investigacao_referencia,
  pendencia.tipo, pendencia.campo,
  pendencia.descricao_sanitizada, pendencia.estado, pendencia.criado_em,
  pendencia.resolvida_em, pendencia.decidida_por,
  pendencia.decisao_motivo_sanitizado
FROM public.investigacao_pendencias pendencia
JOIN public.investigacoes_revisao investigacao
  ON investigacao.id = pendencia.investigacao_id
JOIN public.investigacao_tarefas tarefa
  ON tarefa.id = pendencia.tarefa_id
 AND tarefa.investigacao_id = pendencia.investigacao_id
WHERE tarefa.estado_execucao = 'concluida'
  AND tarefa.resultado_lease_token = pendencia.tarefa_lease_token
  AND tarefa.resultado_fencing_token = pendencia.tarefa_fencing_token;

-- Uma pendência é evidência imutável até uma decisão humana explícita.
-- A transição CAS registra ator, motivo, snapshot do rascunho e evento na
-- mesma transação. Nenhum preenchimento automático resolve ou dispensa linhas.
CREATE OR REPLACE FUNCTION public.decidir_pendencia_investigacao(
  p_pendencia_id uuid,
  p_estado text,
  p_draft_id uuid,
  p_draft_atualizado_em timestamptz,
  p_ator text,
  p_motivo text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_pendencia public.investigacao_pendencias%ROWTYPE;
  v_investigacao public.investigacoes_revisao%ROWTYPE;
  v_draft public.operation_drafts%ROWTYPE;
  v_evento_chave text;
BEGIN
  IF coalesce(
       nullif(current_setting('role', true), 'none'), session_user
     ) IS DISTINCT FROM 'service_role'
     OR p_pendencia_id IS NULL OR p_draft_id IS NULL
     OR p_estado NOT IN ('resolvida', 'dispensada')
     OR btrim(coalesce(p_ator, '')) = ''
     OR btrim(coalesce(p_motivo, '')) = ''
     OR octet_length(p_motivo) > 1000
     OR NOT public.investigacao_texto_sanitizado(p_ator)
     OR NOT public.investigacao_texto_publico_sanitizado(p_motivo)
     OR NOT public.investigacao_instante_operacional(p_draft_atualizado_em) THEN
    RAISE EXCEPTION 'Decisão da pendência inválida';
  END IF;
  SELECT * INTO v_pendencia
    FROM public.investigacao_pendencias
   WHERE id = p_pendencia_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Pendência não encontrada';
  END IF;
  SELECT * INTO v_investigacao
    FROM public.investigacoes_revisao
   WHERE id = v_pendencia.investigacao_id;
  IF NOT FOUND OR v_investigacao.anexado_draft_id IS DISTINCT FROM p_draft_id
     OR v_investigacao.anexado_em IS NULL THEN
    RAISE EXCEPTION 'Pendência não pertence ao rascunho anexado';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('investigacao-draft:' || p_draft_id::text, 0)
  );
  SELECT * INTO v_draft FROM public.operation_drafts
   WHERE id = p_draft_id FOR UPDATE;
  SELECT * INTO v_investigacao FROM public.investigacoes_revisao
   WHERE id = v_pendencia.investigacao_id FOR UPDATE;
  SELECT * INTO v_pendencia FROM public.investigacao_pendencias
   WHERE id = p_pendencia_id FOR UPDATE;
  IF NOT FOUND OR v_investigacao.anexado_draft_id IS DISTINCT FROM v_draft.id
     OR v_draft.atualizado_em IS DISTINCT FROM p_draft_atualizado_em THEN
    RAISE EXCEPTION 'A revisão mudou; recarregue antes de decidir a pendência';
  END IF;
  IF v_pendencia.estado IN ('resolvida', 'dispensada') THEN
    IF v_pendencia.estado IS DISTINCT FROM p_estado
       OR v_pendencia.decidida_por IS DISTINCT FROM p_ator
       OR v_pendencia.decisao_motivo_sanitizado IS DISTINCT FROM p_motivo
       OR v_pendencia.decisao_draft_atualizado_em
            IS DISTINCT FROM p_draft_atualizado_em THEN
      RAISE EXCEPTION 'A pendência já recebeu outra decisão';
    END IF;
    RETURN jsonb_build_object(
      'decidida', false, 'repeticao_idempotente', true,
      'estado', v_pendencia.estado
    );
  END IF;
  IF v_pendencia.estado IS DISTINCT FROM 'aberta' THEN
    RAISE EXCEPTION 'Estado anterior da pendência não é reconhecido';
  END IF;
  IF p_estado = 'resolvida' AND v_pendencia.campo IS NOT NULL
     AND v_pendencia.campo = ANY(
       coalesce(v_draft.campos_pendentes, '{}'::text[])
     ) THEN
    RAISE EXCEPTION 'Salve o campo preenchido antes de marcá-lo como resolvido';
  END IF;
  UPDATE public.investigacao_pendencias
     SET estado = p_estado,
         resolvida_em = clock_timestamp(),
         decidida_por = p_ator,
         decisao_motivo_sanitizado = p_motivo,
         decisao_draft_atualizado_em = p_draft_atualizado_em
   WHERE id = v_pendencia.id AND estado = 'aberta';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'A pendência mudou durante a decisão';
  END IF;
  v_evento_chave := 'pendencia-decidida:' || v_pendencia.id::text || ':'
    || encode(extensions.digest(convert_to(jsonb_build_object(
      'estado', p_estado, 'draft_id', p_draft_id,
      'draft_atualizado_em', p_draft_atualizado_em,
      'ator', p_ator, 'motivo', p_motivo
    )::text, 'UTF8'), 'sha256'), 'hex');
  INSERT INTO public.investigacao_eventos (
    investigacao_id, chave_idempotencia, tipo, referencia_entidade,
    resumo_sanitizado
  ) VALUES (
    v_investigacao.id, v_evento_chave, 'pendencia_decidida',
    v_pendencia.id::text,
    CASE p_estado WHEN 'resolvida'
      THEN 'Pendência marcada como resolvida por decisão humana.'
      ELSE 'Pendência dispensada com motivo registrado por decisão humana.'
    END
  );
  RETURN jsonb_build_object(
    'decidida', true, 'repeticao_idempotente', false, 'estado', p_estado
  );
END;
$$;

-- Claim único e não retomável automaticamente. Se o executor cair depois de
-- uma escrita operacional incerta, a linha permanece em execução para
-- reconciliação humana; outro worker nunca herda a capacidade de repetir.
CREATE OR REPLACE FUNCTION public.assumir_promocao_operacional(
  p_pending_action_id uuid,
  p_status_esperado text,
  p_executor text,
  p_confirmacao_origem_conversa_id text,
  p_confirmacao_origem_mensagem_id text,
  p_lease_segundos integer DEFAULT 300
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_acao public.pending_actions%ROWTYPE;
  v_token uuid := pg_catalog.gen_random_uuid();
  v_lease_expira_em timestamptz;
  v_origem_conversa_esperada text;
  v_origem_mensagem_original text;
BEGIN
  IF coalesce(
       nullif(current_setting('role', true), 'none'), session_user
     ) IS DISTINCT FROM 'service_role'
     OR p_pending_action_id IS NULL
     OR p_status_esperado NOT IN ('aguardando_confirmacao', 'aprovado_confinex')
     OR btrim(coalesce(p_executor, '')) = ''
     OR octet_length(p_executor) > 160
     OR btrim(coalesce(p_confirmacao_origem_conversa_id, '')) = ''
     OR octet_length(p_confirmacao_origem_conversa_id) > 320
     OR btrim(coalesce(p_confirmacao_origem_mensagem_id, '')) = ''
     OR octet_length(p_confirmacao_origem_mensagem_id) > 320
     OR p_lease_segundos NOT BETWEEN 30 AND 1800
     OR NOT public.investigacao_texto_sanitizado(p_executor)
     OR NOT public.investigacao_texto_sanitizado(
       p_confirmacao_origem_conversa_id
     )
     OR NOT public.investigacao_texto_sanitizado(
       p_confirmacao_origem_mensagem_id
     ) THEN
    RAISE EXCEPTION 'Claim da promoção inválido';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'investigacao-promocao:' || p_pending_action_id::text, 0
    )
  );
  SELECT * INTO v_acao FROM public.pending_actions
   WHERE id = p_pending_action_id FOR UPDATE;
  v_origem_conversa_esperada := coalesce(
    nullif(v_acao.origem_conversa_id, ''),
    nullif(v_acao.payload #>> '{dados_revisados,origem_conversa_id}', ''),
    nullif(v_acao.payload ->> 'origem_conversa_id', '')
  );
  v_origem_mensagem_original := coalesce(
    nullif(v_acao.origem_mensagem_id, ''),
    nullif(v_acao.payload #>> '{dados_revisados,origem_mensagem_id}', ''),
    nullif(v_acao.payload ->> 'origem_mensagem_id', '')
  );
  IF NOT FOUND
     OR v_acao.acao_tipo IS DISTINCT FROM 'promover_revisao_operacional'
     OR v_acao.executavel IS NOT TRUE
     OR v_acao.promocao_controle_version IS DISTINCT FROM 'lease-v1'
     OR v_acao.status IS DISTINCT FROM p_status_esperado
     OR v_acao.promocao_lease_token IS NOT NULL
     OR v_origem_conversa_esperada IS NULL
     OR p_confirmacao_origem_conversa_id
          IS DISTINCT FROM v_origem_conversa_esperada
     OR p_confirmacao_origem_mensagem_id
          IS NOT DISTINCT FROM v_origem_mensagem_original THEN
    RAISE EXCEPTION 'Promoção não está disponível no estado esperado';
  END IF;
  v_lease_expira_em := clock_timestamp()
    + make_interval(secs => p_lease_segundos);
  INSERT INTO public.investigacao_autorizacoes_promocao (
    txid, backend_pid, pending_action_id, operacao,
    status_anterior, status_novo
  ) VALUES (
    txid_current(), pg_backend_pid(), v_acao.id, 'UPDATE',
    v_acao.status, 'em_execucao'
  );
  UPDATE public.pending_actions
     SET status = 'em_execucao',
         promocao_lease_executor = p_executor,
         promocao_lease_token = v_token,
         promocao_lease_expira_em = v_lease_expira_em,
         promocao_confirmacao_origem_conversa_id =
           p_confirmacao_origem_conversa_id,
         promocao_confirmacao_origem_mensagem_id =
           p_confirmacao_origem_mensagem_id,
         promocao_fencing_token = promocao_fencing_token + 1
   WHERE id = v_acao.id AND status = p_status_esperado;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'A promoção mudou durante o claim';
  END IF;
  DELETE FROM public.investigacao_autorizacoes_promocao
   WHERE txid = txid_current() AND backend_pid = pg_backend_pid()
     AND pending_action_id = v_acao.id AND operacao = 'UPDATE';
  RETURN jsonb_build_object(
    'assumida', true, 'pending_action_id', v_acao.id,
    'lease_token', v_token,
    'fencing_token', v_acao.promocao_fencing_token + 1,
    'lease_expira_em', v_lease_expira_em
  );
END;
$$;

CREATE OR REPLACE FUNCTION public.concluir_promocao_operacional(
  p_pending_action_id uuid,
  p_lease_token uuid,
  p_fencing_token bigint,
  p_status text,
  p_resultado jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_acao public.pending_actions%ROWTYPE;
  v_draft_origem public.operation_drafts%ROWTYPE;
  v_pedido_hash text;
  v_resultado jsonb;
  v_payload_novo jsonb;
  v_idempotency_key text;
  v_target text;
  v_resultado_id uuid;
  v_registro_vinculado_id uuid;
  v_draft_origem_id uuid;
  v_evento_id uuid;
  v_evento_tipo text;
  v_executor text;
  v_agora timestamptz := clock_timestamp();
BEGIN
  IF coalesce(
       nullif(current_setting('role', true), 'none'), session_user
     ) IS DISTINCT FROM 'service_role'
     OR p_pending_action_id IS NULL OR p_lease_token IS NULL
     OR p_fencing_token IS NULL OR p_fencing_token <= 0
     OR p_status NOT IN ('executado', 'erro_pos_gravacao', 'erro')
     OR p_resultado IS NULL OR jsonb_typeof(p_resultado) <> 'object'
     OR octet_length(p_resultado::text) > 65536
     OR NOT public.investigacao_json_sanitizado(p_resultado) THEN
    RAISE EXCEPTION 'Conclusão da promoção inválida';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'investigacao-promocao:' || p_pending_action_id::text, 0
    )
  );
  SELECT * INTO v_acao FROM public.pending_actions
   WHERE id = p_pending_action_id FOR UPDATE;
  IF NOT FOUND OR v_acao.acao_tipo IS DISTINCT FROM
      'promover_revisao_operacional'
     OR v_acao.executavel IS NOT TRUE
     OR v_acao.promocao_controle_version IS DISTINCT FROM 'lease-v1' THEN
    RAISE EXCEPTION 'Promoção controlada não encontrada';
  END IF;
  v_target := v_acao.payload ->> 'target_table';
  v_draft_origem_id := public.investigacao_uuid_texto_seguro(
    nullif(v_acao.payload ->> 'source_draft_id', '')
  );
  v_executor := nullif(btrim(v_acao.promocao_lease_executor), '');
  v_resultado_id := public.investigacao_uuid_texto_seguro(
    nullif(p_resultado ->> 'target_record_id', '')
  );
  v_resultado := p_resultado;
  v_payload_novo := v_acao.payload;
  IF p_status = 'erro_pos_gravacao' AND v_resultado_id IS NULL THEN
    v_idempotency_key := CASE
      WHEN v_target = 'compras'
        THEN 'promocao_operacional:' || v_acao.id::text
      ELSE NULL
    END;
    v_resultado := p_resultado || jsonb_build_object(
      'target_table', v_target,
      'promovido_para_operacional', false,
      'requer_reconciliacao', true,
      'estado_idempotencia', 'uncertain',
      'idempotency_key', v_idempotency_key
    );
    v_payload_novo := v_acao.payload || jsonb_build_object(
      'idempotency', jsonb_build_object(
        'state', 'uncertain', 'key', v_idempotency_key
      )
    );
  END IF;
  v_pedido_hash := encode(extensions.digest(convert_to(
    jsonb_build_object(
      'pending_action_id', p_pending_action_id,
      'lease_token', p_lease_token,
      'fencing_token', p_fencing_token,
      'status', p_status,
      'resultado', v_resultado
    )::text, 'UTF8'
  ), 'sha256'), 'hex');
  v_evento_id := md5(
    'conclusao-promocao:' || v_acao.id::text || ':' || v_pedido_hash
  )::uuid;
  -- Retry exato independe do estado atual da linha operacional. O desfecho
  -- terminal já foi selado; reconciliar a linha depois não pode transformar
  -- uma resposta perdida em pedido divergente.
  IF v_acao.status IN ('executado', 'erro_pos_gravacao', 'erro') THEN
    IF v_acao.status IS DISTINCT FROM p_status
       OR v_acao.promocao_resultado_lease_token IS DISTINCT FROM p_lease_token
       OR v_acao.promocao_resultado_fencing_token IS DISTINCT FROM p_fencing_token
       OR v_acao.promocao_resultado_pedido_hash IS DISTINCT FROM v_pedido_hash
       OR v_acao.resultado IS DISTINCT FROM v_resultado
       OR NOT EXISTS (
         SELECT 1 FROM public.eventos evento
          WHERE evento.id = v_evento_id
            AND evento.dados ->> 'pending_action_id' = v_acao.id::text
            AND evento.dados ->> 'pedido_hash' = v_pedido_hash
            AND evento.dados ->> 'status_terminal' = p_status
       ) THEN
      RAISE EXCEPTION 'Promoção já concluída por outro lease ou resultado';
    END IF;
    RETURN jsonb_build_object(
      'concluida', false, 'repeticao_idempotente', true,
      'status', v_acao.status, 'evento_id', v_evento_id
    );
  END IF;
  IF v_acao.status IS DISTINCT FROM 'em_execucao'
     OR v_acao.promocao_lease_token IS DISTINCT FROM p_lease_token
     OR v_acao.promocao_fencing_token IS DISTINCT FROM p_fencing_token
     OR v_executor IS NULL
     OR v_draft_origem_id IS NULL
     OR jsonb_typeof(v_acao.payload -> 'proposed_record')
          IS DISTINCT FROM 'object'
     OR v_acao.payload -> 'proposed_record' = '{}'::jsonb THEN
    RAISE EXCEPTION 'Lease, fencing ou prévia da promoção não correspondem ao claim';
  END IF;
  IF v_target = 'compras' THEN
    SELECT compra.id INTO v_registro_vinculado_id
      FROM public.compras compra
     WHERE compra.idempotency_key =
           'promocao_operacional:' || v_acao.id::text;
  ELSIF v_target = 'vendas' THEN
    SELECT venda.id INTO v_registro_vinculado_id
      FROM public.vendas venda
     WHERE venda.promocao_origem_id = v_acao.id;
  ELSIF v_target = 'pesagens_caderno' THEN
    SELECT pesagem.id INTO v_registro_vinculado_id
      FROM public.pesagens_caderno pesagem
     WHERE pesagem.promocao_origem_id = v_acao.id;
  ELSIF v_target = 'abates' THEN
    SELECT abate.id INTO v_registro_vinculado_id
      FROM public.abates abate
     WHERE abate.promocao_origem_id = v_acao.id;
  ELSE
    RAISE EXCEPTION 'Destino da promoção não pertence à lista operacional';
  END IF;
  IF p_status = 'executado' THEN
    IF p_resultado ->> 'target_table' IS DISTINCT FROM v_target
       OR coalesce(
            (p_resultado ->> 'promovido_para_operacional')::boolean, false
          ) IS NOT TRUE
       OR v_registro_vinculado_id IS NULL
       OR v_resultado_id IS DISTINCT FROM v_registro_vinculado_id
       OR public.investigacao_registro_corresponde_promocao(
         v_target, v_registro_vinculado_id, v_acao.id,
         v_acao.payload -> 'proposed_record'
       ) IS NOT TRUE THEN
      RAISE EXCEPTION 'Conclusão executada não corresponde ao registro vinculado à promoção';
    END IF;
  ELSIF p_status = 'erro_pos_gravacao' THEN
    IF p_resultado ->> 'target_table' IS DISTINCT FROM v_target THEN
      RAISE EXCEPTION 'Erro pós-gravação diverge do destino autorizado';
    ELSIF v_resultado_id IS NOT NULL AND (
         v_registro_vinculado_id IS NULL
         OR v_resultado_id IS DISTINCT FROM v_registro_vinculado_id
         OR public.investigacao_registro_corresponde_promocao(
           v_target, v_registro_vinculado_id, v_acao.id,
           v_acao.payload -> 'proposed_record'
         ) IS NOT TRUE
       ) THEN
      RAISE EXCEPTION 'Erro pós-gravação aponta registro não vinculado à promoção';
    ELSIF v_resultado_id IS NULL
          AND v_registro_vinculado_id IS NOT NULL THEN
      RAISE EXCEPTION 'Existe registro vinculado; o erro pós-gravação deve identificá-lo';
    END IF;
  ELSIF p_status = 'erro' THEN
    IF p_resultado - ARRAY[
         'target_table', 'target_record_id',
         'promovido_para_operacional', 'erro'
       ] <> '{}'::jsonb
       OR p_resultado ->> 'target_table' IS DISTINCT FROM v_target
       OR v_resultado_id IS NOT NULL
       OR coalesce(
            (p_resultado ->> 'promovido_para_operacional')::boolean, true
          ) IS NOT FALSE
       OR v_registro_vinculado_id IS NOT NULL THEN
      RAISE EXCEPTION 'Falha pré-gravação não pode declarar ou ocultar registro operacional';
    END IF;
  END IF;
  SELECT * INTO v_draft_origem
    FROM public.operation_drafts draft
   WHERE draft.id = v_draft_origem_id
   FOR UPDATE;
  IF NOT FOUND
     OR v_acao.entidade_id IS DISTINCT FROM v_draft_origem.id
     OR v_draft_origem.revisao_tipo IS DISTINCT FROM 'pre_revisao'
     OR v_draft_origem.entidade_final_tipo IS DISTINCT FROM v_target
     OR (
       v_registro_vinculado_id IS NOT NULL
       AND v_draft_origem.entidade_final_id IS NOT NULL
       AND v_draft_origem.entidade_final_id
             IS DISTINCT FROM v_registro_vinculado_id
     ) THEN
    RAISE EXCEPTION 'Rascunho de origem não corresponde à promoção terminal';
  END IF;
  IF v_acao.status IS DISTINCT FROM 'em_execucao'
     OR v_acao.promocao_lease_token IS DISTINCT FROM p_lease_token
     OR v_acao.promocao_fencing_token IS DISTINCT FROM p_fencing_token THEN
    RAISE EXCEPTION 'Lease ou fencing da promoção não corresponde ao claim';
  END IF;
  INSERT INTO public.investigacao_autorizacoes_promocao (
    txid, backend_pid, pending_action_id, operacao,
    status_anterior, status_novo
  ) VALUES (
    txid_current(), pg_backend_pid(), v_acao.id, 'UPDATE',
    v_acao.status, p_status
  );
  IF v_registro_vinculado_id IS NOT NULL THEN
    UPDATE public.operation_drafts
       SET status = 'realizado',
           atualizado_em = v_agora,
           entidade_final_tipo = v_target,
           entidade_final_id = v_registro_vinculado_id
     WHERE id = v_draft_origem.id
       AND (entidade_final_id IS NULL
            OR entidade_final_id = v_registro_vinculado_id);
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Rascunho de origem mudou durante a conclusão';
    END IF;
  END IF;
  v_evento_tipo := CASE p_status
    WHEN 'executado' THEN 'promocao_operacional_executada'
    WHEN 'erro_pos_gravacao' THEN 'promocao_operacional_requer_conferencia'
    ELSE 'promocao_operacional_falhou_sem_gravacao'
  END;
  INSERT INTO public.eventos (
    id, tipo, agente, usuario, entidade_tipo, entidade_id, entidade_codigo,
    origem, origem_canal, origem_conversa_id, origem_mensagem_id,
    contexto_canonico, contexto_nome, escopo, status, dados, observacao
  ) VALUES (
    v_evento_id, v_evento_tipo, 'confinex', v_executor,
    CASE WHEN v_registro_vinculado_id IS NULL
      THEN 'pending_action' ELSE v_target END,
    coalesce(v_registro_vinculado_id, v_acao.id), v_acao.entidade_codigo,
    'confinex_promocao_operacional', v_acao.origem_canal,
    v_acao.origem_conversa_id, v_acao.origem_mensagem_id,
    v_acao.contexto_canonico, v_acao.contexto_nome, v_acao.escopo,
    'registrado', jsonb_build_object(
      'pending_action_id', v_acao.id,
      'source_draft_id', v_draft_origem.id,
      'target_table', v_target,
      'target_record_id', v_registro_vinculado_id,
      'promovido_para_operacional', v_registro_vinculado_id IS NOT NULL,
      'status_terminal', p_status,
      'pedido_hash', v_pedido_hash
    ),
    CASE p_status
      WHEN 'executado'
        THEN 'Promoção operacional concluída pela rotina controlada.'
      WHEN 'erro_pos_gravacao'
        THEN 'Promoção encerrada com necessidade de conferência auditada.'
      ELSE 'Promoção encerrada sem criar lançamento operacional.'
    END
  );
  UPDATE public.pending_actions
     SET status = p_status,
         resultado = v_resultado,
         payload = v_payload_novo,
         confirmado_por = v_executor,
         confirmado_em = coalesce(confirmado_em, v_agora),
         promocao_resultado_lease_token = p_lease_token,
         promocao_resultado_fencing_token = p_fencing_token,
         promocao_resultado_pedido_hash = v_pedido_hash,
         promocao_lease_executor = NULL,
         promocao_lease_token = NULL,
         promocao_lease_expira_em = NULL
   WHERE id = v_acao.id AND status = 'em_execucao'
     AND promocao_lease_token = p_lease_token
     AND promocao_fencing_token = p_fencing_token;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'A promoção mudou durante a conclusão';
  END IF;
  DELETE FROM public.investigacao_autorizacoes_promocao
   WHERE txid = txid_current() AND backend_pid = pg_backend_pid()
     AND pending_action_id = v_acao.id AND operacao = 'UPDATE';
  RETURN jsonb_build_object(
    'concluida', true, 'repeticao_idempotente', false, 'status', p_status,
    'evento_id', v_evento_id
  );
END;
$$;

-- Recuperação manual e idempotente de um lease expirado. A rotina nunca
-- repete o INSERT operacional: sob o mesmo advisory lock da promoção, ela
-- observa somente o vínculo durável já existente e encerra o subgrafo na
-- mesma transação do evento de recuperação.
CREATE OR REPLACE FUNCTION public.reconciliar_promocao_em_execucao(
  p_pending_action_id uuid,
  p_fencing_esperado bigint,
  p_ator text,
  p_motivo text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_acao public.pending_actions%ROWTYPE;
  v_target text;
  v_registro_id uuid;
  v_resultado jsonb;
  v_conclusao jsonb;
  v_evento_id uuid;
  v_pedido_hash text;
BEGIN
  IF coalesce(
       nullif(current_setting('role', true), 'none'), session_user
     ) IS DISTINCT FROM 'service_role'
     OR p_pending_action_id IS NULL
     OR p_fencing_esperado IS NULL OR p_fencing_esperado <= 0
     OR btrim(coalesce(p_ator, '')) = ''
     OR octet_length(p_ator) > 160
     OR btrim(coalesce(p_motivo, '')) = ''
     OR octet_length(p_motivo) > 1000
     OR NOT public.investigacao_texto_sanitizado(p_ator)
     OR NOT public.investigacao_texto_publico_sanitizado(p_motivo) THEN
    RAISE EXCEPTION 'Pedido de recuperação da promoção inválido';
  END IF;
  v_pedido_hash := encode(extensions.digest(convert_to(
    jsonb_build_object(
      'pending_action_id', p_pending_action_id,
      'fencing_esperado', p_fencing_esperado,
      'ator', p_ator,
      'motivo', p_motivo
    )::text, 'UTF8'
  ), 'sha256'), 'hex');
  v_evento_id := md5(
    'recuperacao-promocao:' || p_pending_action_id::text || ':'
      || p_fencing_esperado::text
  )::uuid;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'investigacao-promocao:' || p_pending_action_id::text, 0
    )
  );
  SELECT * INTO v_acao
    FROM public.pending_actions
   WHERE id = p_pending_action_id
   FOR UPDATE;
  IF NOT FOUND
     OR v_acao.acao_tipo IS DISTINCT FROM 'promover_revisao_operacional'
     OR v_acao.executavel IS NOT TRUE
     OR v_acao.promocao_controle_version IS DISTINCT FROM 'lease-v1' THEN
    RAISE EXCEPTION 'Promoção controlada não encontrada para recuperação';
  END IF;
  IF v_acao.status IN ('executado', 'erro_pos_gravacao', 'erro') THEN
    IF v_acao.promocao_resultado_fencing_token
         IS DISTINCT FROM p_fencing_esperado
       OR NOT EXISTS (
         SELECT 1 FROM public.eventos evento
          WHERE evento.id = v_evento_id
            AND evento.tipo = 'promocao_operacional_recuperada'
            AND evento.dados ->> 'pedido_hash' = v_pedido_hash
       ) THEN
      RAISE EXCEPTION 'A promoção já terminou fora deste pedido de recuperação';
    END IF;
    RETURN jsonb_build_object(
      'recuperada', false, 'repeticao_idempotente', true,
      'status', v_acao.status, 'evento_id', v_evento_id
    );
  END IF;
  IF v_acao.status IS DISTINCT FROM 'em_execucao'
     OR v_acao.promocao_fencing_token IS DISTINCT FROM p_fencing_esperado
     OR v_acao.promocao_lease_token IS NULL
     OR v_acao.promocao_lease_expira_em IS NULL
     OR v_acao.promocao_lease_expira_em >= clock_timestamp() THEN
    RAISE EXCEPTION 'O lease ainda está ativo ou o fencing mudou';
  END IF;
  v_target := v_acao.payload ->> 'target_table';
  IF v_target = 'compras' THEN
    SELECT compra.id INTO v_registro_id
      FROM public.compras compra
     WHERE compra.idempotency_key =
       'promocao_operacional:' || v_acao.id::text;
  ELSIF v_target = 'vendas' THEN
    SELECT venda.id INTO v_registro_id
      FROM public.vendas venda
     WHERE venda.promocao_origem_id = v_acao.id;
  ELSIF v_target = 'pesagens_caderno' THEN
    SELECT pesagem.id INTO v_registro_id
      FROM public.pesagens_caderno pesagem
     WHERE pesagem.promocao_origem_id = v_acao.id;
  ELSIF v_target = 'abates' THEN
    SELECT abate.id INTO v_registro_id
      FROM public.abates abate
     WHERE abate.promocao_origem_id = v_acao.id;
  ELSE
    RAISE EXCEPTION 'Destino operacional inválido na recuperação';
  END IF;
  IF v_registro_id IS NOT NULL THEN
    IF public.investigacao_registro_corresponde_promocao(
         v_target, v_registro_id, v_acao.id,
         v_acao.payload -> 'proposed_record'
       ) IS NOT TRUE THEN
      RAISE EXCEPTION 'O vínculo operacional divergiu; exige auditoria antes de encerrar';
    END IF;
    v_resultado := jsonb_build_object(
      'target_table', v_target,
      'target_record_id', v_registro_id,
      'promovido_para_operacional', true
    );
    v_conclusao := public.concluir_promocao_operacional(
      v_acao.id, v_acao.promocao_lease_token,
      v_acao.promocao_fencing_token, 'executado', v_resultado
    );
  ELSE
    v_resultado := jsonb_build_object(
      'target_table', v_target,
      'promovido_para_operacional', false
    );
    v_conclusao := public.concluir_promocao_operacional(
      v_acao.id, v_acao.promocao_lease_token,
      v_acao.promocao_fencing_token, 'erro_pos_gravacao', v_resultado
    );
  END IF;
  INSERT INTO public.eventos (
    id, tipo, agente, usuario, entidade_tipo, entidade_id,
    origem, origem_canal, origem_conversa_id, origem_mensagem_id,
    contexto_canonico, contexto_nome, escopo, status, dados, observacao
  ) VALUES (
    v_evento_id, 'promocao_operacional_recuperada', 'confinex', p_ator,
    'pending_action', v_acao.id, 'confinex_recuperacao_promocao',
    v_acao.origem_canal, v_acao.origem_conversa_id,
    v_acao.origem_mensagem_id, v_acao.contexto_canonico,
    v_acao.contexto_nome, v_acao.escopo, 'registrado',
    jsonb_build_object(
      'pending_action_id', v_acao.id,
      'fencing_token', p_fencing_esperado,
      'target_table', v_target,
      'target_record_id', v_registro_id,
      'pedido_hash', v_pedido_hash,
      'resultado_terminal', v_conclusao ->> 'status'
    ),
    'Lease expirado reconciliado sem repetir o lançamento. Motivo: '
      || p_motivo
  );
  RETURN jsonb_build_object(
    'recuperada', true, 'repeticao_idempotente', false,
    'status', v_conclusao ->> 'status', 'evento_id', v_evento_id,
    'target_record_id', v_registro_id
  );
END;
$$;

-- Substitui, sem bifurcar, uma rodada corretiva cujo snapshot operacional
-- mudou antes da materialização. O predecessor e seus resultados continuam
-- imutáveis para auditoria; somente a nova folha ativa volta à fila.
CREATE OR REPLACE FUNCTION public.substituir_investigacao_corretiva_stale(
  p_investigacao_id uuid,
  p_snapshot_anterior_esperado text,
  p_snapshot_novo_esperado text,
  p_ator text,
  p_motivo text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_pre public.investigacoes_revisao%ROWTYPE;
  v_investigacao public.investigacoes_revisao%ROWTYPE;
  v_sucessora public.investigacoes_revisao%ROWTYPE;
  v_promocao public.pending_actions%ROWTYPE;
  v_draft public.operation_drafts%ROWTYPE;
  v_snapshot jsonb;
  v_snapshot_atual text;
  v_sucessora_id uuid;
  v_sucessora_hash text;
  v_pedido_hash text;
  v_ids_lock uuid[];
  v_id uuid;
  v_snapshots_candidatos jsonb := '{}'::jsonb;
  v_source_candidato_atualizado_em timestamptz;
BEGIN
  IF coalesce(
       nullif(current_setting('role', true), 'none'), session_user
     ) IS DISTINCT FROM 'service_role'
     OR p_investigacao_id IS NULL
     OR p_snapshot_anterior_esperado !~ '^snp_[0-9a-f]{32}$'
     OR p_snapshot_novo_esperado !~ '^snp_[0-9a-f]{32}$'
     OR p_snapshot_novo_esperado = p_snapshot_anterior_esperado
     OR btrim(coalesce(p_ator, '')) = ''
     OR octet_length(p_ator) > 160
     OR btrim(coalesce(p_motivo, '')) = ''
     OR octet_length(p_motivo) > 1000
     OR NOT public.investigacao_texto_sanitizado(p_ator)
     OR NOT public.investigacao_texto_publico_sanitizado(p_motivo) THEN
    RAISE EXCEPTION 'Pedido de substituição corretiva inválido';
  END IF;
  -- A substituição corretiva antiga copiava o plano/tarefas da rodada cujo
  -- retrato operacional já havia mudado. Não existe mais caminho seguro para
  -- isso: o mediador deve usar o outbox e replanejar um lote fechado com CAS.
  -- Falhar aqui, antes de qualquer capacidade, UPDATE ou INSERT, preserva a
  -- trilha anterior e impede uma corretiva sinteticamente "nova".
  RAISE EXCEPTION USING
    ERRCODE = 'P0001',
    MESSAGE = 'Replanejamento explícito é necessário para a rodada corretiva',
    DETAIL = jsonb_build_object(
      'codigo', 'PLANEJAMENTO_FONTES_NECESSARIO',
      'acao', 'usar_obter_contexto_replanejamento_corretiva_stale'
    )::text;
  v_pedido_hash := encode(extensions.digest(convert_to(
    jsonb_build_object(
      'investigacao_id', p_investigacao_id,
      'snapshot_anterior', p_snapshot_anterior_esperado,
      'snapshot_novo', p_snapshot_novo_esperado,
      'ator', p_ator,
      'motivo', p_motivo
    )::text, 'UTF8'
  ), 'sha256'), 'hex');
  v_sucessora_id := md5(
    'sucessora-corretiva:' || p_investigacao_id::text || ':' || v_pedido_hash
  )::uuid;
  SELECT * INTO v_pre
    FROM public.investigacoes_revisao
   WHERE id = p_investigacao_id;
  IF NOT FOUND
     OR v_pre.fluxo_tipo IS DISTINCT FROM 'corretiva_pos_gravacao'
     OR v_pre.promocao_origem_id IS NULL
     OR v_pre.draft_operacional_origem_id IS NULL THEN
    RAISE EXCEPTION 'Investigação corretiva não encontrada';
  END IF;
  SELECT coalesce(array_agg(item ORDER BY item), '{}'::uuid[])
    INTO v_ids_lock
    FROM (SELECT DISTINCT unnest(v_pre.negocio_candidato_ids) AS item) ids;
  FOREACH v_id IN ARRAY v_ids_lock LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended('investigacao-candidato:' || v_id::text, 0)
    );
  END LOOP;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'investigacao-promocao:' || v_pre.promocao_origem_id::text, 0
    )
  );
  SELECT * INTO v_investigacao
    FROM public.investigacoes_revisao
   WHERE id = p_investigacao_id
   FOR UPDATE;
  IF NOT FOUND
     OR v_investigacao.promocao_origem_id
          IS DISTINCT FROM v_pre.promocao_origem_id
     OR v_investigacao.draft_operacional_origem_id
          IS DISTINCT FROM v_pre.draft_operacional_origem_id THEN
    RAISE EXCEPTION 'Snapshot ou vínculo corretivo mudou; recarregue o plano';
  END IF;
  -- Retry após perda de resposta consulta primeiro a filha persistida e não
  -- recalcula o pedido a partir de um registro operacional que pode ter mudado.
  IF v_investigacao.estado_execucao = 'obsoleta' THEN
    SELECT * INTO v_sucessora
      FROM public.investigacoes_revisao sucessora
     WHERE sucessora.sucessora_de_id = v_investigacao.id;
    IF v_investigacao.obsolescencia_motivo
         IS DISTINCT FROM 'registro_operacional_stale'
       OR NOT FOUND
       OR v_sucessora.id IS DISTINCT FROM v_sucessora_id
       OR v_sucessora.sucessao_pedido_hash IS DISTINCT FROM v_pedido_hash
       OR v_sucessora.registro_operacional_origem_snapshot_ref
            IS DISTINCT FROM p_snapshot_novo_esperado
       OR v_sucessora.source_draft_id IS NOT NULL
       OR v_sucessora.source_draft_atualizado_em IS NOT NULL
       OR v_sucessora.negocio_candidato_id
            IS DISTINCT FROM v_investigacao.negocio_candidato_id
       OR v_sucessora.source_candidato_atualizado_em
            IS DISTINCT FROM v_investigacao.source_candidato_atualizado_em
       OR v_sucessora.negocio_candidato_ids
            IS DISTINCT FROM v_investigacao.negocio_candidato_ids
       OR v_sucessora.source_candidatos_atualizados_em
            IS DISTINCT FROM v_investigacao.source_candidatos_atualizados_em
       OR v_sucessora.fingerprint_base
            IS DISTINCT FROM v_investigacao.fingerprint_base
       OR v_sucessora.plano_hash IS DISTINCT FROM v_investigacao.plano_hash
       OR v_sucessora.policy_version
            IS DISTINCT FROM v_investigacao.policy_version
       OR v_sucessora.policy_schema_hash
            IS DISTINCT FROM v_investigacao.policy_schema_hash THEN
      RAISE EXCEPTION 'A rodada obsoleta pertence a outro pedido de substituição';
    END IF;
    RETURN jsonb_build_object(
      'substituida', false, 'repeticao_idempotente', true,
      'investigacao_sucessora_id', v_sucessora.id,
      'snapshot_atual', v_sucessora.registro_operacional_origem_snapshot_ref,
      'pedido_hash', v_pedido_hash
    );
  END IF;
  SELECT * INTO v_promocao
    FROM public.pending_actions
   WHERE id = v_investigacao.promocao_origem_id
   FOR SHARE;
  SELECT * INTO v_draft
    FROM public.operation_drafts
   WHERE id = v_investigacao.draft_operacional_origem_id
   FOR SHARE;
  IF v_promocao.id IS NULL OR v_draft.id IS NULL
     OR v_investigacao.vinculo_operacional_estado
          IS DISTINCT FROM 'confirmado'
     OR v_investigacao.registro_operacional_origem_id IS NULL
     OR v_investigacao.registro_operacional_origem_snapshot_ref
          IS DISTINCT FROM p_snapshot_anterior_esperado
     OR v_promocao.status NOT IN ('executado', 'erro_pos_gravacao')
     OR v_investigacao.anexado_em IS NOT NULL THEN
    RAISE EXCEPTION 'Snapshot ou vínculo corretivo mudou; recarregue o plano';
  END IF;
  v_snapshot := public.investigacao_snapshot_registro_promocao(
    v_investigacao.destino_operacional_origem,
    v_investigacao.registro_operacional_origem_id,
    v_investigacao.promocao_origem_id,
    v_promocao.payload -> 'proposed_record'
  );
  IF coalesce(
       (v_snapshot ->> 'identidade_valida')::boolean, false
     ) IS NOT TRUE THEN
    RAISE EXCEPTION 'O vínculo operacional deixou de pertencer à promoção';
  END IF;
  v_snapshot_atual := v_snapshot ->> 'snapshot_ref';
  IF v_snapshot_atual IS NULL
     OR v_snapshot_atual IS DISTINCT FROM p_snapshot_novo_esperado THEN
    RAISE EXCEPTION USING
      ERRCODE = 'P0001',
      MESSAGE = 'O retrato operacional mudou novamente',
      DETAIL = jsonb_build_object(
        'codigo', 'CORRETIVA_SNAPSHOT_DIVERGENTE',
        'snapshot_esperado', p_snapshot_novo_esperado,
        'snapshot_atual', v_snapshot_atual
      )::text;
  END IF;
  IF v_investigacao.estado_execucao NOT IN (
       'pendente', 'em_execucao', 'aguardando_retentativa', 'concluida'
     ) THEN
    RAISE EXCEPTION 'A rodada corretiva não pode ser substituída neste estado';
  END IF;
  PERFORM 1
    FROM public.investigacao_tarefas tarefa
   WHERE tarefa.investigacao_id = v_investigacao.id
   ORDER BY tarefa.id
   FOR UPDATE;
  IF cardinality(v_ids_lock) > 0 THEN
    PERFORM 1
      FROM public.negocios_candidatos
     WHERE id = ANY(v_ids_lock)
     ORDER BY id
     FOR SHARE;
    SELECT coalesce(
             jsonb_object_agg(
               candidato.id::text, candidato.atualizado_em
               ORDER BY candidato.id
             ), '{}'::jsonb
           )
      INTO v_snapshots_candidatos
      FROM public.negocios_candidatos candidato
     WHERE candidato.id = ANY(v_ids_lock);
    IF public.investigacao_jsonb_objeto_tamanho(v_snapshots_candidatos)
         <> cardinality(v_ids_lock) THEN
      RAISE EXCEPTION 'Candidato de origem da investigação não foi encontrado';
    END IF;
    IF v_investigacao.negocio_candidato_id IS NOT NULL THEN
      v_source_candidato_atualizado_em := (
        v_snapshots_candidatos ->>
          v_investigacao.negocio_candidato_id::text
      )::timestamptz;
    END IF;
  END IF;
  -- Uma sucessora corretiva deixa de tratar o rascunho prévio como fonte:
  -- sua origem confirmada é o registro operacional. O plano só pode ser
  -- reaproveitado se candidatos e política ainda forem exatamente os mesmos.
  IF v_snapshots_candidatos
       IS DISTINCT FROM v_investigacao.source_candidatos_atualizados_em
     OR v_source_candidato_atualizado_em
       IS DISTINCT FROM v_investigacao.source_candidato_atualizado_em
     OR v_investigacao.policy_schema_hash IS DISTINCT FROM
          public.investigacao_politica_schema_hash(
            v_investigacao.policy_version
          )
     OR v_investigacao.campos_obrigatorios IS DISTINCT FROM
          public.investigacao_politica_campos(
            v_investigacao.assunto_tipo,
            v_investigacao.policy_version
          ) THEN
    RAISE EXCEPTION USING
      ERRCODE = 'P0001',
      MESSAGE = 'As fontes do plano mudaram; é necessário planejar uma nova rodada',
      DETAIL = jsonb_build_object(
        'codigo', 'PLANEJAMENTO_FONTES_NECESSARIO'
      )::text;
  END IF;
  INSERT INTO public.investigacao_autorizacoes_corretiva (
    txid, backend_pid, recurso, investigacao_id,
    operation_draft_id, pending_action_id, pedido_hash
  ) VALUES (
    txid_current(), pg_backend_pid(), 'obsoletar_predecessora',
    v_investigacao.id, v_investigacao.draft_operacional_origem_id,
    v_investigacao.promocao_origem_id,
    encode(extensions.digest(convert_to(jsonb_build_object(
      'investigacao_id', v_investigacao.id,
      'promocao_origem_id', v_investigacao.promocao_origem_id,
      'snapshot_anterior',
        v_investigacao.registro_operacional_origem_snapshot_ref,
      'motivo', 'registro_operacional_stale'
    )::text, 'UTF8'), 'sha256'), 'hex')
  );
  UPDATE public.investigacao_tarefas
     SET estado_execucao = 'obsoleta',
         lease_executor = NULL, lease_token = NULL,
         lease_expira_em = NULL, lease_chave_id = NULL
   WHERE investigacao_id = v_investigacao.id
     AND estado_execucao IN (
       'pendente', 'em_execucao', 'aguardando_retentativa'
     );
  UPDATE public.investigacoes_revisao
     SET estado_execucao = 'obsoleta',
         obsolescencia_motivo = 'registro_operacional_stale',
         promocao_ativa_id = NULL
   WHERE id = v_investigacao.id;
  v_sucessora_hash := encode(extensions.digest(convert_to(
    jsonb_build_object(
      'investigacao_id', v_sucessora_id,
      'sucessora_de_id', v_investigacao.id,
      'raiz_investigacao_id', v_investigacao.raiz_investigacao_id,
      'geracao', v_investigacao.geracao + 1,
      'sucessao_pedido_hash', v_pedido_hash,
      'promocao_origem_id', v_investigacao.promocao_origem_id,
      'registro_operacional_origem_snapshot_ref', v_snapshot_atual
    )::text, 'UTF8'
  ), 'sha256'), 'hex');
  INSERT INTO public.investigacao_autorizacoes_corretiva (
    txid, backend_pid, recurso, investigacao_id,
    operation_draft_id, pending_action_id, pedido_hash
  ) VALUES (
    txid_current(), pg_backend_pid(), 'criar_sucessora', v_sucessora_id,
    v_investigacao.draft_operacional_origem_id,
    v_investigacao.promocao_origem_id, v_sucessora_hash
  );
  INSERT INTO public.investigacoes_revisao (
    id, raiz_investigacao_id, sucessora_de_id, geracao,
    sucessao_pedido_hash,
    chave_idempotencia, assunto_tipo, assunto_referencia, titulo,
    fluxo_tipo, promocao_origem_id, draft_operacional_origem_id,
    destino_operacional_origem, registro_operacional_origem_id,
    registro_operacional_origem_snapshot_ref, vinculo_operacional_estado,
    source_draft_id, source_draft_atualizado_em,
    negocio_candidato_id, source_candidato_atualizado_em,
    negocio_candidato_ids, source_candidatos_atualizados_em,
    fingerprint_base, plano_hash, plano_canonico, plano_tarefas,
    policy_version, policy_schema_hash, campos_obrigatorios,
    gatilho_tipo, prioridade, contexto_canonico, contexto_nome,
    origem_canal, origem_conversa_id, origem_mensagem_id, escopo,
    estado_execucao, resumo_sanitizado, criado_por
  ) VALUES (
    v_sucessora_id, v_investigacao.raiz_investigacao_id,
    v_investigacao.id, v_investigacao.geracao + 1, v_pedido_hash,
    v_investigacao.chave_idempotencia || ':geracao:'
      || (v_investigacao.geracao + 1)::text || ':' || v_snapshot_atual,
    v_investigacao.assunto_tipo, v_investigacao.assunto_referencia,
    v_investigacao.titulo, 'corretiva_pos_gravacao',
    v_investigacao.promocao_origem_id,
    v_investigacao.draft_operacional_origem_id,
    v_investigacao.destino_operacional_origem,
    v_investigacao.registro_operacional_origem_id, v_snapshot_atual,
    'confirmado', NULL,
    NULL,
    v_investigacao.negocio_candidato_id,
    v_source_candidato_atualizado_em,
    v_investigacao.negocio_candidato_ids,
    v_snapshots_candidatos,
    v_investigacao.fingerprint_base,
    v_investigacao.plano_hash, v_investigacao.plano_canonico,
    v_investigacao.plano_tarefas, v_investigacao.policy_version,
    v_investigacao.policy_schema_hash, v_investigacao.campos_obrigatorios,
    'manual', v_investigacao.prioridade,
    v_investigacao.contexto_canonico, v_investigacao.contexto_nome,
    v_investigacao.origem_canal, v_investigacao.origem_conversa_id,
    v_investigacao.origem_mensagem_id, v_investigacao.escopo,
    'pendente',
    'O registro mudou; as fontes serão cruzadas novamente antes da revisão.',
    p_ator
  );
  INSERT INTO public.investigacao_tarefas (
    investigacao_id, chave_idempotencia, plano_item_ref, adaptador,
    consulta_ref, consulta_schema_version, consulta_spec,
    consulta_canonico, consulta_hash, adaptador_version,
    estado_execucao, tentativas, proxima_execucao_em, fencing_token
  )
  SELECT v_sucessora_id, tarefa.chave_idempotencia,
         tarefa.plano_item_ref, tarefa.adaptador, tarefa.consulta_ref,
         tarefa.consulta_schema_version, tarefa.consulta_spec,
         tarefa.consulta_canonico, tarefa.consulta_hash,
         tarefa.adaptador_version, 'pendente', 0, clock_timestamp(), 0
    FROM public.investigacao_tarefas tarefa
   WHERE tarefa.investigacao_id = v_investigacao.id
   ORDER BY tarefa.id;
  INSERT INTO public.investigacao_eventos (
    investigacao_id, chave_idempotencia, tipo,
    referencia_entidade, resumo_sanitizado
  ) VALUES
  (
    v_investigacao.id,
    'corretiva-stale:' || v_investigacao.id::text || ':' || v_snapshot_atual,
    'investigacao_obsoleta', v_sucessora_id::text,
    'O retrato anterior foi preservado e substituído por uma nova rodada.'
  ),
  (
    v_sucessora_id,
    'corretiva-sucessora:' || v_sucessora_id::text,
    'investigacao_sucessora_criada', v_investigacao.id::text,
    'Nova rodada corretiva criada para o retrato operacional atual.'
  );
  RETURN jsonb_build_object(
    'substituida', true, 'repeticao_idempotente', false,
    'investigacao_sucessora_id', v_sucessora_id,
    'snapshot_anterior', p_snapshot_anterior_esperado,
    'snapshot_atual', v_snapshot_atual,
    'pedido_hash', v_pedido_hash
  );
END;
$$;

-- Contexto opaco para replanejar uma corretiva cujo registro operacional
-- mudou depois da rodada anterior. A leitura segue a mesma ordem global de
-- locks da mutação e sela fontes, política e revisão humana já materializada.
CREATE OR REPLACE FUNCTION public.obter_contexto_replanejamento_corretiva_stale(
  p_investigacao_id uuid,
  p_snapshot_anterior_esperado text,
  p_snapshot_novo_esperado text
)
RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_pre public.investigacoes_revisao%ROWTYPE;
  v_pai public.investigacoes_revisao%ROWTYPE;
  v_promocao public.pending_actions%ROWTYPE;
  v_draft_anexado public.operation_drafts%ROWTYPE;
  v_acao_anexada public.pending_actions%ROWTYPE;
  v_ids_draft uuid[];
  v_ids_candidato uuid[];
  v_ids_pending uuid[];
  v_id uuid;
  v_pending_anexado_pre uuid;
  v_snapshot jsonb;
  v_snapshots_candidatos jsonb;
  v_contexto jsonb;
  v_contexto_hash text;
BEGIN
  IF coalesce(nullif(current_setting('role', true), 'none'), session_user)
       IS DISTINCT FROM 'service_role'
     OR p_investigacao_id IS NULL
     OR p_snapshot_anterior_esperado !~ '^snp_[0-9a-f]{32}$'
     OR p_snapshot_novo_esperado !~ '^snp_[0-9a-f]{32}$'
     OR p_snapshot_novo_esperado = p_snapshot_anterior_esperado THEN
    RAISE EXCEPTION 'Consulta de replanejamento corretivo inválida';
  END IF;
  SELECT * INTO v_pre FROM public.investigacoes_revisao
   WHERE id = p_investigacao_id;
  IF NOT FOUND OR v_pre.fluxo_tipo IS DISTINCT FROM 'corretiva_pos_gravacao'
     OR v_pre.sucessora_de_id IS NULL
     OR v_pre.promocao_origem_id IS NULL
     OR v_pre.draft_operacional_origem_id IS NULL THEN
    RAISE EXCEPTION 'Investigação corretiva ativa não encontrada';
  END IF;
  v_ids_draft := ARRAY[
    v_pre.draft_operacional_origem_id, v_pre.anexado_draft_id
  ]::uuid[];
  SELECT coalesce(array_agg(DISTINCT valor ORDER BY valor), '{}'::uuid[])
    INTO v_ids_draft FROM unnest(v_ids_draft) valor WHERE valor IS NOT NULL;
  FOREACH v_id IN ARRAY v_ids_draft LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
      'investigacao-draft:' || v_id::text, 0));
  END LOOP;
  SELECT coalesce(array_agg(DISTINCT valor ORDER BY valor), '{}'::uuid[])
    INTO v_ids_candidato FROM unnest(v_pre.negocio_candidato_ids) valor;
  FOREACH v_id IN ARRAY v_ids_candidato LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
      'investigacao-candidato:' || v_id::text, 0));
  END LOOP;
  PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
    'investigacao-promocao:' || v_pre.promocao_origem_id::text, 0));
  SELECT pending_action_id INTO v_pending_anexado_pre FROM public.operation_drafts
   WHERE id = v_pre.anexado_draft_id;
  v_ids_pending := ARRAY[
    v_pre.promocao_origem_id, v_pending_anexado_pre
  ]::uuid[];
  SELECT coalesce(array_agg(DISTINCT valor ORDER BY valor), '{}'::uuid[])
    INTO v_ids_pending FROM unnest(v_ids_pending) valor WHERE valor IS NOT NULL;
  PERFORM 1 FROM public.pending_actions WHERE id = ANY(v_ids_pending)
   ORDER BY id FOR SHARE;
  SELECT * INTO v_promocao FROM public.pending_actions
   WHERE id = v_pre.promocao_origem_id;
  SELECT * INTO v_pai FROM public.investigacoes_revisao
   WHERE id = p_investigacao_id FOR SHARE;
  IF NOT FOUND OR v_pai.estado_execucao NOT IN (
       'pendente', 'em_execucao', 'aguardando_retentativa', 'concluida'
     )
     OR v_pai.promocao_origem_id IS DISTINCT FROM v_pre.promocao_origem_id
     OR v_pai.draft_operacional_origem_id
          IS DISTINCT FROM v_pre.draft_operacional_origem_id
     OR v_pai.anexado_draft_id IS DISTINCT FROM v_pre.anexado_draft_id
     OR v_pai.negocio_candidato_ids
          IS DISTINCT FROM v_pre.negocio_candidato_ids
     OR v_pai.registro_operacional_origem_snapshot_ref
          IS DISTINCT FROM p_snapshot_anterior_esperado
     OR v_promocao.acao_tipo IS DISTINCT FROM 'promover_revisao_operacional'
     OR v_promocao.status NOT IN ('executado', 'erro_pos_gravacao') THEN
    RAISE EXCEPTION 'Rodada corretiva mudou; recarregue o contexto';
  END IF;
  PERFORM 1 FROM public.investigacao_tarefas
   WHERE investigacao_id = v_pai.id ORDER BY id FOR SHARE;
  PERFORM 1 FROM public.negocios_candidatos
   WHERE id = ANY(v_ids_candidato) ORDER BY id FOR SHARE;
  SELECT coalesce(jsonb_object_agg(candidato.id::text, candidato.atualizado_em
                                    ORDER BY candidato.id), '{}'::jsonb)
    INTO v_snapshots_candidatos FROM public.negocios_candidatos candidato
   WHERE candidato.id = ANY(v_ids_candidato);
  IF public.investigacao_jsonb_objeto_tamanho(v_snapshots_candidatos)
       <> cardinality(v_ids_candidato) THEN
    RAISE EXCEPTION 'Fonte candidata da corretiva não foi encontrada';
  END IF;
  IF v_pai.anexado_draft_id IS NOT NULL THEN
    SELECT * INTO v_draft_anexado FROM public.operation_drafts
     WHERE id = v_pai.anexado_draft_id FOR SHARE;
    SELECT * INTO v_acao_anexada FROM public.pending_actions
     WHERE id = v_draft_anexado.pending_action_id;
    IF v_draft_anexado.investigacao_origem_id IS DISTINCT FROM v_pai.id
       OR v_draft_anexado.pending_action_id
            IS DISTINCT FROM v_pending_anexado_pre
       OR v_acao_anexada.entidade_id IS DISTINCT FROM v_draft_anexado.id
       OR v_acao_anexada.acao_tipo IS DISTINCT FROM
            'revisar_correcao_pos_gravacao' THEN
      RAISE EXCEPTION 'Revisão humana anexada perdeu seu vínculo';
    END IF;
  END IF;
  v_snapshot := public.investigacao_snapshot_registro_promocao(
    v_pai.destino_operacional_origem,
    v_pai.registro_operacional_origem_id,
    v_pai.promocao_origem_id,
    v_promocao.payload -> 'proposed_record'
  );
  IF coalesce((v_snapshot ->> 'identidade_valida')::boolean, false) IS NOT TRUE
     OR v_snapshot ->> 'snapshot_ref' IS DISTINCT FROM p_snapshot_novo_esperado THEN
    RAISE EXCEPTION 'Retrato operacional atual divergiu do pedido';
  END IF;
  v_contexto := jsonb_build_object(
    'versao', 'replanejamento-corretiva-stale-v1',
    'planejamento_inputs', jsonb_build_object(
      'assunto', jsonb_build_object(
        'tipo', v_pai.assunto_tipo,
        'referencia', v_pai.referencia_publica
      ),
      'origem', jsonb_build_object(
        'canal', coalesce(v_pai.origem_canal, 'desconhecido'),
        'linhagem', coalesce(v_pai.origem_canal, 'desconhecido'),
        'contexto_canonico', v_pai.contexto_canonico,
        'contexto_nome', v_pai.contexto_nome,
        'escopo', v_pai.escopo
      ),
      'consulta_base', jsonb_build_object(
        'modo', 'replanejar_corretiva_stale',
        'negocio_candidato_ids', to_jsonb(v_ids_candidato),
        'promocao_origem_id', v_pai.promocao_origem_id,
        'destino_operacional_origem', v_pai.destino_operacional_origem,
        'registro_operacional_origem_id',
          v_pai.registro_operacional_origem_id
      ),
      'cobertura', 'cobertura_incompleta',
      'instante_referencia', v_pai.atualizado_em
    ),
    'investigacao_id', v_pai.id,
    'raiz_investigacao_id', v_pai.raiz_investigacao_id,
    'geracao_origem', v_pai.geracao,
    'promocao_origem_id', v_pai.promocao_origem_id,
    'draft_operacional_origem_id', v_pai.draft_operacional_origem_id,
    'destino_operacional_origem', v_pai.destino_operacional_origem,
    'registro_operacional_origem_id', v_pai.registro_operacional_origem_id,
    'snapshot_anterior', p_snapshot_anterior_esperado,
    'snapshot_atual', p_snapshot_novo_esperado,
    'negocio_candidato_id', v_pai.negocio_candidato_id,
    'source_candidato_atualizado_em',
      v_snapshots_candidatos ->> v_pai.negocio_candidato_id::text,
    'negocio_candidato_ids', to_jsonb(v_ids_candidato),
    'source_candidatos_atualizados_em', v_snapshots_candidatos,
    'policy_version_origem', v_pai.policy_version,
    'policy_schema_hash_origem', v_pai.policy_schema_hash,
    'campos_obrigatorios_origem', to_jsonb(v_pai.campos_obrigatorios),
    'plano_hash_origem', v_pai.plano_hash,
    'revisao_materializada', v_pai.anexado_draft_id IS NOT NULL,
    'draft_revisao_id', v_pai.anexado_draft_id,
    'pending_action_revisao_id', v_draft_anexado.pending_action_id,
    'draft_revisao_atualizado_em', v_draft_anexado.atualizado_em,
    'pending_action_revisao_atualizado_em', v_acao_anexada.atualizado_em,
    'pending_action_revisao_status', v_acao_anexada.status
  );
  v_contexto_hash := encode(extensions.digest(convert_to(
    v_contexto::text, 'UTF8'), 'sha256'), 'hex');
  RETURN v_contexto || jsonb_build_object('contexto_cas_hash', v_contexto_hash);
END;
$$;

-- Replaneja e substitui atomicamente uma folha corretiva stale. O cliente
-- escolhe apenas um plano fechado; identidade, snapshots, IDs e fingerprint
-- são derivados e atestados novamente no servidor sob locks.
CREATE OR REPLACE FUNCTION public.replanejar_investigacao_corretiva_stale(
  p_investigacao_id uuid,
  p_snapshot_anterior_esperado text,
  p_snapshot_novo_esperado text,
  p_contexto_cas_hash text,
  p_replanejamento jsonb,
  p_ator text,
  p_motivo text
)
RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_pre public.investigacoes_revisao%ROWTYPE;
  v_pai public.investigacoes_revisao%ROWTYPE;
  v_filha public.investigacoes_revisao%ROWTYPE;
  v_promocao public.pending_actions%ROWTYPE;
  v_draft_anexado public.operation_drafts%ROWTYPE;
  v_acao_anexada public.pending_actions%ROWTYPE;
  v_ids_draft uuid[];
  v_ids_candidato uuid[];
  v_ids_pending uuid[];
  v_id uuid;
  v_pending_anexado_pre uuid;
  v_snapshot jsonb;
  v_snapshots_candidatos jsonb;
  v_contexto jsonb;
  v_contexto_hash text;
  v_pedido_hash text;
  v_filha_id uuid;
  v_filha_capacidade_hash text;
  v_campos text[];
  v_policy_version text;
  v_policy_schema_hash text;
  v_plano_tarefas jsonb;
  v_plano_canonico text;
  v_plano_hash text;
  v_fingerprint text;
  v_tarefas_persistidas jsonb;
  v_agora timestamptz := clock_timestamp();
  v_dados_supersedidos jsonb;
  v_inferencias_supersedidas jsonb;
  v_payload_supersedido jsonb;
  v_hash_draft text;
  v_hash_acao text;
  v_evento_supersessao_id uuid;
  v_filha_encontrada boolean := false;
  v_fingerprint_retry text;
BEGIN
  IF coalesce(nullif(current_setting('role', true), 'none'), session_user)
       IS DISTINCT FROM 'service_role'
     OR p_investigacao_id IS NULL
     OR p_snapshot_anterior_esperado !~ '^snp_[0-9a-f]{32}$'
     OR p_snapshot_novo_esperado !~ '^snp_[0-9a-f]{32}$'
     OR p_snapshot_novo_esperado = p_snapshot_anterior_esperado
     OR p_contexto_cas_hash !~ '^[0-9a-f]{64}$'
     OR jsonb_typeof(p_replanejamento) IS DISTINCT FROM 'object'
     OR octet_length(p_replanejamento::text) > 262144
     OR NOT public.investigacao_json_sanitizado(p_replanejamento)
     OR p_replanejamento - ARRAY[
       'policy_version', 'policy_schema_hash', 'campos_obrigatorios',
       'plano_tarefas', 'plano_canonico', 'plano_hash'
     ] <> '{}'::jsonb
     OR public.investigacao_jsonb_objeto_tamanho(p_replanejamento) <> 6
     OR btrim(coalesce(p_ator, '')) = '' OR octet_length(p_ator) > 160
     OR btrim(coalesce(p_motivo, '')) = '' OR octet_length(p_motivo) > 1000
     OR NOT public.investigacao_texto_sanitizado(p_ator)
     OR NOT public.investigacao_texto_publico_sanitizado(p_motivo) THEN
    RAISE EXCEPTION 'Pedido de replanejamento corretivo inválido';
  END IF;
  v_policy_version := p_replanejamento ->> 'policy_version';
  v_policy_schema_hash := p_replanejamento ->> 'policy_schema_hash';
  v_plano_tarefas := p_replanejamento -> 'plano_tarefas';
  v_plano_canonico := p_replanejamento ->> 'plano_canonico';
  v_plano_hash := p_replanejamento ->> 'plano_hash';
  IF jsonb_typeof(p_replanejamento -> 'campos_obrigatorios') <> 'array'
     OR jsonb_typeof(v_plano_tarefas) <> 'array' THEN
    RAISE EXCEPTION 'Plano corretivo possui estrutura inválida';
  END IF;
  SELECT coalesce(array_agg(valor ORDER BY valor), '{}'::text[]) INTO v_campos
    FROM jsonb_array_elements_text(
      p_replanejamento -> 'campos_obrigatorios') valor;
  v_pedido_hash := encode(extensions.digest(convert_to(jsonb_build_object(
    'versao', 'replanejamento-corretiva-stale-v1',
    'investigacao_id', p_investigacao_id,
    'snapshot_anterior', p_snapshot_anterior_esperado,
    'snapshot_novo', p_snapshot_novo_esperado,
    'contexto_cas_hash', p_contexto_cas_hash,
    'replanejamento', p_replanejamento,
    'ator', p_ator, 'motivo', p_motivo
  )::text, 'UTF8'), 'sha256'), 'hex');
  v_filha_id := md5('sucessora-corretiva-planejada:'
    || p_investigacao_id::text || ':' || v_pedido_hash)::uuid;
  SELECT * INTO v_pre FROM public.investigacoes_revisao
   WHERE id = p_investigacao_id;
  IF NOT FOUND OR v_pre.fluxo_tipo IS DISTINCT FROM 'corretiva_pos_gravacao'
     OR v_pre.promocao_origem_id IS NULL THEN
    RAISE EXCEPTION 'Investigação corretiva não encontrada';
  END IF;
  v_ids_draft := ARRAY[
    v_pre.draft_operacional_origem_id, v_pre.anexado_draft_id
  ]::uuid[];
  SELECT coalesce(array_agg(DISTINCT valor ORDER BY valor), '{}'::uuid[])
    INTO v_ids_draft FROM unnest(v_ids_draft) valor WHERE valor IS NOT NULL;
  FOREACH v_id IN ARRAY v_ids_draft LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
      'investigacao-draft:' || v_id::text, 0));
  END LOOP;
  SELECT coalesce(array_agg(DISTINCT valor ORDER BY valor), '{}'::uuid[])
    INTO v_ids_candidato FROM unnest(v_pre.negocio_candidato_ids) valor;
  FOREACH v_id IN ARRAY v_ids_candidato LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
      'investigacao-candidato:' || v_id::text, 0));
  END LOOP;
  PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
    'investigacao-promocao:' || v_pre.promocao_origem_id::text, 0));
  SELECT pending_action_id INTO v_pending_anexado_pre FROM public.operation_drafts
   WHERE id = v_pre.anexado_draft_id;
  v_ids_pending := ARRAY[
    v_pre.promocao_origem_id, v_pending_anexado_pre
  ]::uuid[];
  SELECT coalesce(array_agg(DISTINCT valor ORDER BY valor), '{}'::uuid[])
    INTO v_ids_pending FROM unnest(v_ids_pending) valor WHERE valor IS NOT NULL;
  PERFORM 1 FROM public.pending_actions WHERE id = ANY(v_ids_pending)
   ORDER BY id FOR UPDATE;
  SELECT * INTO v_promocao FROM public.pending_actions
   WHERE id = v_pre.promocao_origem_id;
  SELECT * INTO v_pai FROM public.investigacoes_revisao
   WHERE id = p_investigacao_id FOR UPDATE;
  IF NOT FOUND
     OR v_pai.promocao_origem_id IS DISTINCT FROM v_pre.promocao_origem_id
     OR v_pai.draft_operacional_origem_id
          IS DISTINCT FROM v_pre.draft_operacional_origem_id
     OR v_pai.anexado_draft_id IS DISTINCT FROM v_pre.anexado_draft_id
     OR v_pai.negocio_candidato_ids
          IS DISTINCT FROM v_pre.negocio_candidato_ids THEN
    RAISE EXCEPTION USING ERRCODE = '40001',
      MESSAGE = 'RETRY_CONJUNTO_CORRETIVO_MUDOU: recarregue os locks';
  END IF;
  IF v_pai.estado_execucao = 'obsoleta' THEN
    SELECT * INTO v_filha FROM public.investigacoes_revisao
     WHERE sucessora_de_id = v_pai.id;
    v_filha_encontrada := FOUND;
    SELECT coalesce(jsonb_agg(jsonb_build_object(
      'plano_item_ref', tarefa.plano_item_ref,
      'adaptador', tarefa.adaptador, 'consulta_ref', tarefa.consulta_ref,
      'consulta_schema_version', tarefa.consulta_schema_version,
      'consulta_spec', tarefa.consulta_spec,
      'consulta_canonico', tarefa.consulta_canonico,
      'consulta_hash', tarefa.consulta_hash,
      'adaptador_version', tarefa.adaptador_version
    ) ORDER BY tarefa.plano_item_ref), '[]'::jsonb)
      INTO v_tarefas_persistidas FROM public.investigacao_tarefas tarefa
     WHERE tarefa.investigacao_id = v_filha.id;
    v_fingerprint_retry := encode(extensions.digest(convert_to(jsonb_build_object(
      'versao', 'fingerprint-corretiva-stale-v1',
      'contexto_cas_hash', p_contexto_cas_hash,
      'investigacao_id', v_pai.id,
      'snapshot_operacional', p_snapshot_novo_esperado,
      'source_candidatos_atualizados_em',
        v_filha.source_candidatos_atualizados_em,
      'policy_version', v_policy_version,
      'policy_schema_hash', v_policy_schema_hash,
      'campos_obrigatorios', v_campos,
      'plano_hash', v_plano_hash
    )::text, 'UTF8'), 'sha256'), 'hex');
    IF v_pai.anexado_draft_id IS NOT NULL THEN
      SELECT * INTO v_draft_anexado FROM public.operation_drafts
       WHERE id = v_pai.anexado_draft_id;
      SELECT * INTO v_acao_anexada FROM public.pending_actions
       WHERE id = v_draft_anexado.pending_action_id;
    END IF;
    IF v_pai.obsolescencia_motivo IS DISTINCT FROM 'registro_operacional_stale'
       OR NOT v_filha_encontrada OR v_filha.id IS DISTINCT FROM v_filha_id
       OR v_filha.sucessao_pedido_hash IS DISTINCT FROM v_pedido_hash
       OR v_filha.raiz_investigacao_id IS DISTINCT FROM v_pai.raiz_investigacao_id
       OR v_filha.geracao IS DISTINCT FROM v_pai.geracao + 1
       OR v_filha.fluxo_tipo IS DISTINCT FROM 'corretiva_pos_gravacao'
       OR v_filha.promocao_origem_id IS DISTINCT FROM v_pai.promocao_origem_id
       OR v_filha.draft_operacional_origem_id
            IS DISTINCT FROM v_pai.draft_operacional_origem_id
       OR v_filha.destino_operacional_origem
            IS DISTINCT FROM v_pai.destino_operacional_origem
       OR v_filha.registro_operacional_origem_id
            IS DISTINCT FROM v_pai.registro_operacional_origem_id
       OR v_filha.registro_operacional_origem_snapshot_ref
            IS DISTINCT FROM p_snapshot_novo_esperado
       OR v_filha.vinculo_operacional_estado IS DISTINCT FROM 'confirmado'
       OR v_filha.source_draft_id IS NOT NULL
       OR v_filha.source_draft_atualizado_em IS NOT NULL
       OR v_filha.negocio_candidato_id IS DISTINCT FROM v_pai.negocio_candidato_id
       OR v_filha.negocio_candidato_ids IS DISTINCT FROM v_pai.negocio_candidato_ids
       OR v_filha.fingerprint_base IS DISTINCT FROM v_fingerprint_retry
       OR v_filha.plano_hash IS DISTINCT FROM v_plano_hash
       OR v_filha.plano_canonico IS DISTINCT FROM v_plano_canonico
       OR v_filha.plano_tarefas IS DISTINCT FROM v_plano_tarefas
       OR v_filha.policy_version IS DISTINCT FROM v_policy_version
       OR v_filha.policy_schema_hash IS DISTINCT FROM v_policy_schema_hash
       OR v_filha.campos_obrigatorios IS DISTINCT FROM v_campos
       OR v_tarefas_persistidas IS DISTINCT FROM v_plano_tarefas THEN
      RAISE EXCEPTION 'Retry diverge da sucessora corretiva persistida';
    END IF;
    IF EXISTS (
         SELECT 1 FROM public.investigacao_tarefas tarefa
          WHERE tarefa.investigacao_id = v_pai.id
            AND tarefa.estado_execucao IN (
              'pendente', 'em_execucao', 'aguardando_retentativa'
            )
       )
       OR (
         v_pai.anexado_draft_id IS NOT NULL
         AND (
           v_draft_anexado.status IS DISTINCT FROM 'cancelado'
           OR v_acao_anexada.status NOT IN ('rejeitado', 'cancelado')
           OR NOT EXISTS (
             SELECT 1 FROM public.eventos evento
              WHERE evento.tipo = 'revisao_corretiva_substituida'
                AND evento.entidade_id = v_draft_anexado.id
                AND evento.dados ->> 'investigacao_sucessora_id' = v_filha.id::text
                AND evento.dados ->> 'pedido_hash' = v_pedido_hash
                AND coalesce(
                  (evento.dados ->> 'promovido_para_operacional')::boolean,
                  true
                ) IS FALSE
           )
         )
       ) THEN
      RAISE EXCEPTION 'Retry encontrou estado final incompleto da supersessão';
    END IF;
    RETURN jsonb_build_object(
      'substituida', false, 'repeticao_idempotente', true,
      'investigacao_sucessora_id', v_filha.id,
      'snapshot_atual', v_filha.registro_operacional_origem_snapshot_ref,
      'pedido_hash', v_pedido_hash
    );
  END IF;
  IF v_pai.estado_execucao NOT IN (
       'pendente', 'em_execucao', 'aguardando_retentativa', 'concluida'
     )
     OR EXISTS (
       SELECT 1 FROM public.investigacoes_revisao filha
        WHERE filha.sucessora_de_id = v_pai.id
     )
     OR v_pai.promocao_origem_id IS DISTINCT FROM v_pre.promocao_origem_id
     OR v_pai.registro_operacional_origem_snapshot_ref
          IS DISTINCT FROM p_snapshot_anterior_esperado
     OR v_promocao.acao_tipo IS DISTINCT FROM 'promover_revisao_operacional'
     OR v_promocao.status NOT IN ('executado', 'erro_pos_gravacao') THEN
    RAISE EXCEPTION 'Rodada corretiva mudou; recarregue o contexto';
  END IF;
  PERFORM 1 FROM public.investigacao_tarefas
   WHERE investigacao_id = v_pai.id ORDER BY id FOR UPDATE;
  PERFORM 1 FROM public.negocios_candidatos
   WHERE id = ANY(v_ids_candidato) ORDER BY id FOR SHARE;
  SELECT coalesce(jsonb_object_agg(candidato.id::text, candidato.atualizado_em
                                    ORDER BY candidato.id), '{}'::jsonb)
    INTO v_snapshots_candidatos FROM public.negocios_candidatos candidato
   WHERE candidato.id = ANY(v_ids_candidato);
  IF public.investigacao_jsonb_objeto_tamanho(v_snapshots_candidatos)
       <> cardinality(v_ids_candidato) THEN
    RAISE EXCEPTION 'Fonte candidata da corretiva não foi encontrada';
  END IF;
  IF v_pai.anexado_draft_id IS NOT NULL THEN
    SELECT * INTO v_draft_anexado FROM public.operation_drafts
     WHERE id = v_pai.anexado_draft_id FOR UPDATE;
    SELECT * INTO v_acao_anexada FROM public.pending_actions
     WHERE id = v_draft_anexado.pending_action_id;
    IF v_draft_anexado.investigacao_origem_id IS DISTINCT FROM v_pai.id
       OR v_draft_anexado.pending_action_id
            IS DISTINCT FROM v_pending_anexado_pre
       OR v_acao_anexada.entidade_id IS DISTINCT FROM v_draft_anexado.id
       OR v_acao_anexada.acao_tipo IS DISTINCT FROM
            'revisar_correcao_pos_gravacao' THEN
      RAISE EXCEPTION 'Revisão humana anexada perdeu seu vínculo';
    END IF;
  END IF;
  v_snapshot := public.investigacao_snapshot_registro_promocao(
    v_pai.destino_operacional_origem, v_pai.registro_operacional_origem_id,
    v_pai.promocao_origem_id, v_promocao.payload -> 'proposed_record');
  IF coalesce((v_snapshot ->> 'identidade_valida')::boolean, false) IS NOT TRUE
     OR v_snapshot ->> 'snapshot_ref' IS DISTINCT FROM p_snapshot_novo_esperado THEN
    RAISE EXCEPTION 'Retrato operacional atual divergiu do pedido';
  END IF;
  v_contexto := public.obter_contexto_replanejamento_corretiva_stale(
    p_investigacao_id, p_snapshot_anterior_esperado,
    p_snapshot_novo_esperado);
  v_contexto_hash := v_contexto ->> 'contexto_cas_hash';
  IF v_contexto_hash IS DISTINCT FROM p_contexto_cas_hash THEN
    RAISE EXCEPTION USING ERRCODE = '40001',
      MESSAGE = 'RETRY_CONTEXTO_CORRETIVO_DIVERGIU: fontes mudaram';
  END IF;
  IF public.investigacao_politica_campos(
       v_pai.assunto_tipo, v_policy_version) IS NULL
     OR v_campos IS DISTINCT FROM public.investigacao_politica_campos(
       v_pai.assunto_tipo, v_policy_version)
     OR v_policy_schema_hash IS DISTINCT FROM
       public.investigacao_politica_schema_hash(v_policy_version)
     OR NOT public.investigacao_plano_tarefas_valido(v_plano_tarefas)
     OR v_plano_canonico::jsonb IS DISTINCT FROM jsonb_build_object(
       'tarefas', v_plano_tarefas,
       'campos_obrigatorios', to_jsonb(v_campos),
       'policy_schema_hash', v_policy_schema_hash)
     OR encode(extensions.digest(convert_to(v_plano_canonico, 'UTF8'), 'sha256'), 'hex')
          IS DISTINCT FROM v_plano_hash
     OR v_plano_hash IS NOT DISTINCT FROM v_pai.plano_hash THEN
    RAISE EXCEPTION 'Plano corretivo novo ou política suportada são obrigatórios';
  END IF;
  v_fingerprint := encode(extensions.digest(convert_to(jsonb_build_object(
    'versao', 'fingerprint-corretiva-stale-v1',
    'contexto_cas_hash', p_contexto_cas_hash,
    'investigacao_id', v_pai.id,
    'snapshot_operacional', p_snapshot_novo_esperado,
    'source_candidatos_atualizados_em', v_snapshots_candidatos,
    'policy_version', v_policy_version,
    'policy_schema_hash', v_policy_schema_hash,
    'campos_obrigatorios', v_campos, 'plano_hash', v_plano_hash
  )::text, 'UTF8'), 'sha256'), 'hex');
  IF v_pai.anexado_draft_id IS NOT NULL
     AND v_acao_anexada.status IN ('aguardando_confirmacao', 'em_revisao')
     AND v_draft_anexado.status IN ('em_revisao', 'aguardando_confirmacao') THEN
    v_dados_supersedidos := coalesce(v_draft_anexado.dados_extraidos, '{}'::jsonb)
      || jsonb_build_object('status_confirmacao', 'cancelado');
    v_inferencias_supersedidas := coalesce(v_draft_anexado.inferencias, '{}'::jsonb)
      || jsonb_build_object('supersessao_corretiva', jsonb_build_object(
        'pedido_hash', v_pedido_hash, 'sucessora_id', v_filha_id));
    v_payload_supersedido := coalesce(v_acao_anexada.payload, '{}'::jsonb)
      || jsonb_build_object(
        'dados_extraidos', v_dados_supersedidos,
        'inferencias', v_inferencias_supersedidas,
        'revisao_confinex', jsonb_build_object(
          'atualizado_em', v_agora, 'motivo', p_motivo,
          'modo', 'cancelar', 'acao', 'correcao_supersedida_por_novo_retrato',
          'pedido_hash', v_pedido_hash));
    v_hash_acao := encode(extensions.digest(convert_to(jsonb_build_object(
      'action_id', v_acao_anexada.id,
      'old_atualizado_em', v_acao_anexada.atualizado_em,
      'new_status', 'cancelado',
      'resumo', 'Revisão substituída porque o registro operacional mudou.',
      'payload', v_payload_supersedido,
      'contexto_canonico', v_acao_anexada.contexto_canonico,
      'contexto_nome', v_acao_anexada.contexto_nome,
      'origem_canal', v_acao_anexada.origem_canal,
      'origem_conversa_id', v_acao_anexada.origem_conversa_id,
      'origem_mensagem_id', v_acao_anexada.origem_mensagem_id,
      'escopo', v_acao_anexada.escopo
    )::text, 'UTF8'), 'sha256'), 'hex');
    v_hash_draft := encode(extensions.digest(convert_to(jsonb_build_object(
      'draft_id', v_draft_anexado.id,
      'old_atualizado_em', v_draft_anexado.atualizado_em,
      'new_status', 'cancelado',
      'codigo_sugerido', v_draft_anexado.codigo_sugerido,
      'dados_extraidos', v_dados_supersedidos,
      'campos_pendentes', to_jsonb(v_draft_anexado.campos_pendentes),
      'inferencias', v_inferencias_supersedidas,
      'contexto_canonico', v_draft_anexado.contexto_canonico,
      'contexto_nome', v_draft_anexado.contexto_nome,
      'origem_canal', v_draft_anexado.origem_canal,
      'origem_conversa_id', v_draft_anexado.origem_conversa_id,
      'origem_mensagem_id', v_draft_anexado.origem_mensagem_id,
      'escopo', v_draft_anexado.escopo
    )::text, 'UTF8'), 'sha256'), 'hex');
    INSERT INTO public.investigacao_autorizacoes_corretiva(
      txid, backend_pid, recurso, investigacao_id, operation_draft_id,
      pending_action_id, pedido_hash) VALUES
      (txid_current(), pg_backend_pid(), 'decidir_acao', v_pai.id,
       v_draft_anexado.id, v_acao_anexada.id, v_hash_acao),
      (txid_current(), pg_backend_pid(), 'decidir_draft', v_pai.id,
       v_draft_anexado.id, v_acao_anexada.id, v_hash_draft);
    UPDATE public.pending_actions SET atualizado_em = v_agora,
      status = 'cancelado',
      resumo = 'Revisão substituída porque o registro operacional mudou.',
      payload = v_payload_supersedido WHERE id = v_acao_anexada.id;
    UPDATE public.operation_drafts SET atualizado_em = v_agora,
      status = 'cancelado', dados_extraidos = v_dados_supersedidos,
      inferencias = v_inferencias_supersedidas
      WHERE id = v_draft_anexado.id;
  ELSIF v_pai.anexado_draft_id IS NOT NULL
        AND NOT (v_acao_anexada.status IN ('rejeitado', 'cancelado')
                 AND v_draft_anexado.status = 'cancelado') THEN
    RAISE EXCEPTION 'Revisão humana anexada possui estado incompatível';
  END IF;
  -- O selo de supersessão também é criado quando a revisão humana já estava
  -- rejeitada/cancelada. Sem ele, a primeira execução seria aceita, mas um
  -- retry após COMMIT não conseguiria provar por que aquela revisão pertence
  -- à nova geração planejada.
  IF v_pai.anexado_draft_id IS NOT NULL THEN
    v_evento_supersessao_id := md5('supersessao-corretiva:'
      || v_draft_anexado.id::text || ':' || v_pedido_hash)::uuid;
    INSERT INTO public.eventos(id, tipo, agente, usuario, entidade_tipo,
      entidade_id, origem, origem_canal, origem_conversa_id,
      origem_mensagem_id, contexto_canonico, contexto_nome, escopo,
      status, dados, observacao) VALUES (
      v_evento_supersessao_id, 'revisao_corretiva_substituida', 'confinex',
      p_ator, 'operation_draft', v_draft_anexado.id, 'confinex_revisoes',
      v_draft_anexado.origem_canal, v_draft_anexado.origem_conversa_id,
      v_draft_anexado.origem_mensagem_id, v_draft_anexado.contexto_canonico,
      v_draft_anexado.contexto_nome, v_draft_anexado.escopo, 'registrado',
      jsonb_build_object('draft_id', v_draft_anexado.id,
        'pending_action_id', v_acao_anexada.id,
        'investigacao_sucessora_id', v_filha_id,
        'pedido_hash', v_pedido_hash, 'promovido_para_operacional', false),
      'Revisão anterior preservada e encerrada. Motivo: ' || p_motivo);
  END IF;
  INSERT INTO public.investigacao_autorizacoes_corretiva(
    txid, backend_pid, recurso, investigacao_id, operation_draft_id,
    pending_action_id, pedido_hash) VALUES (
    txid_current(), pg_backend_pid(), 'obsoletar_predecessora', v_pai.id,
    v_pai.draft_operacional_origem_id, v_pai.promocao_origem_id,
    encode(extensions.digest(convert_to(jsonb_build_object(
      'investigacao_id', v_pai.id,
      'promocao_origem_id', v_pai.promocao_origem_id,
      'snapshot_anterior', v_pai.registro_operacional_origem_snapshot_ref,
      'motivo', 'registro_operacional_stale')::text, 'UTF8'), 'sha256'), 'hex'));
  UPDATE public.investigacao_tarefas SET estado_execucao = 'obsoleta',
    lease_executor = NULL, lease_token = NULL, lease_expira_em = NULL,
    lease_chave_id = NULL WHERE investigacao_id = v_pai.id
      AND estado_execucao IN ('pendente','em_execucao','aguardando_retentativa');
  UPDATE public.investigacoes_revisao SET estado_execucao = 'obsoleta',
    obsolescencia_motivo = 'registro_operacional_stale',
    promocao_ativa_id = NULL WHERE id = v_pai.id;
  v_filha_capacidade_hash := encode(extensions.digest(convert_to(jsonb_build_object(
    'investigacao_id', v_filha_id, 'sucessora_de_id', v_pai.id,
    'raiz_investigacao_id', v_pai.raiz_investigacao_id,
    'geracao', v_pai.geracao + 1, 'sucessao_pedido_hash', v_pedido_hash,
    'promocao_origem_id', v_pai.promocao_origem_id,
    'draft_operacional_origem_id', v_pai.draft_operacional_origem_id,
    'destino_operacional_origem', v_pai.destino_operacional_origem,
    'registro_operacional_origem_id', v_pai.registro_operacional_origem_id,
    'registro_operacional_origem_snapshot_ref', p_snapshot_novo_esperado,
    'vinculo_operacional_estado', 'confirmado',
    'negocio_candidato_id', v_pai.negocio_candidato_id,
    'source_candidato_atualizado_em',
      (v_snapshots_candidatos ->> v_pai.negocio_candidato_id::text)::timestamptz,
    'negocio_candidato_ids', v_ids_candidato,
    'source_candidatos_atualizados_em', v_snapshots_candidatos,
    'fingerprint_base', v_fingerprint, 'plano_hash', v_plano_hash,
    'policy_version', v_policy_version,
    'policy_schema_hash', v_policy_schema_hash,
    'campos_obrigatorios', v_campos
  )::text, 'UTF8'), 'sha256'), 'hex');
  INSERT INTO public.investigacao_autorizacoes_corretiva(
    txid, backend_pid, recurso, investigacao_id, operation_draft_id,
    pending_action_id, pedido_hash) VALUES (
    txid_current(), pg_backend_pid(), 'criar_sucessora', v_filha_id,
    v_pai.draft_operacional_origem_id, v_pai.promocao_origem_id,
    v_filha_capacidade_hash);
  INSERT INTO public.investigacoes_revisao(
    id, raiz_investigacao_id, sucessora_de_id, geracao, sucessao_pedido_hash,
    chave_idempotencia, assunto_tipo, assunto_referencia, titulo, fluxo_tipo,
    promocao_origem_id, draft_operacional_origem_id,
    destino_operacional_origem, registro_operacional_origem_id,
    registro_operacional_origem_snapshot_ref, vinculo_operacional_estado,
    source_draft_id, source_draft_atualizado_em, negocio_candidato_id,
    source_candidato_atualizado_em, negocio_candidato_ids,
    source_candidatos_atualizados_em, fingerprint_base, plano_hash,
    plano_canonico, plano_tarefas, policy_version, policy_schema_hash,
    campos_obrigatorios, gatilho_tipo, prioridade, contexto_canonico,
    contexto_nome, origem_canal, origem_conversa_id, origem_mensagem_id,
    escopo, estado_execucao, resumo_sanitizado, criado_por
  ) VALUES (
    v_filha_id, v_pai.raiz_investigacao_id, v_pai.id, v_pai.geracao + 1,
    v_pedido_hash, v_pai.chave_idempotencia || ':stale:' || v_pedido_hash,
    v_pai.assunto_tipo, v_pai.assunto_referencia, v_pai.titulo,
    'corretiva_pos_gravacao', v_pai.promocao_origem_id,
    v_pai.draft_operacional_origem_id, v_pai.destino_operacional_origem,
    v_pai.registro_operacional_origem_id, p_snapshot_novo_esperado,
    'confirmado', NULL, NULL, v_pai.negocio_candidato_id,
    (v_snapshots_candidatos ->> v_pai.negocio_candidato_id::text)::timestamptz,
    v_ids_candidato, v_snapshots_candidatos, v_fingerprint, v_plano_hash,
    v_plano_canonico, v_plano_tarefas, v_policy_version,
    v_policy_schema_hash, v_campos, 'manual', v_pai.prioridade,
    v_pai.contexto_canonico, v_pai.contexto_nome, v_pai.origem_canal,
    v_pai.origem_conversa_id, v_pai.origem_mensagem_id, v_pai.escopo,
    'pendente',
    'O registro mudou; uma nova rodada foi planejada para o retrato atual.',
    p_ator);
  INSERT INTO public.investigacao_tarefas(
    investigacao_id, chave_idempotencia, plano_item_ref, adaptador,
    consulta_ref, consulta_schema_version, consulta_spec, consulta_canonico,
    consulta_hash, adaptador_version, estado_execucao, tentativas,
    proxima_execucao_em, fencing_token)
  SELECT v_filha_id, 'stale:' || v_filha_id::text || ':' ||
      (plano_item.tarefa_json ->> 'plano_item_ref'),
    plano_item.tarefa_json ->> 'plano_item_ref',
    plano_item.tarefa_json ->> 'adaptador',
    plano_item.tarefa_json ->> 'consulta_ref',
    plano_item.tarefa_json ->> 'consulta_schema_version',
    plano_item.tarefa_json -> 'consulta_spec',
    plano_item.tarefa_json ->> 'consulta_canonico',
    plano_item.tarefa_json ->> 'consulta_hash',
    plano_item.tarefa_json ->> 'adaptador_version',
    'pendente', 0, clock_timestamp(), 0
  FROM jsonb_array_elements(v_plano_tarefas)
    AS plano_item(tarefa_json);
  INSERT INTO public.investigacao_eventos(
    investigacao_id, chave_idempotencia, tipo, referencia_entidade,
    resumo_sanitizado) VALUES
  (v_pai.id, 'corretiva-stale-planejada:' || v_pedido_hash,
   'investigacao_obsoleta', v_filha_id::text,
   'O retrato anterior foi preservado e substituído por um novo plano.'),
  (v_filha_id, 'corretiva-sucessora-planejada:' || v_filha_id::text,
   'investigacao_sucessora_replanejada', v_pai.id::text,
   'Nova rodada corretiva criada para o retrato operacional atual.');
  RETURN jsonb_build_object(
    'substituida', true, 'repeticao_idempotente', false,
    'investigacao_sucessora_id', v_filha_id,
    'snapshot_anterior', p_snapshot_anterior_esperado,
    'snapshot_atual', p_snapshot_novo_esperado,
    'pedido_hash', v_pedido_hash);
END;
$$;

CREATE OR REPLACE FUNCTION public.decidir_promocao_operacional(
  p_pending_action_id uuid,
  p_status_esperado text,
  p_status text,
  p_ator text,
  p_motivo text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_acao public.pending_actions%ROWTYPE;
BEGIN
  IF coalesce(
       nullif(current_setting('role', true), 'none'), session_user
     ) IS DISTINCT FROM 'service_role'
     OR p_status_esperado NOT IN (
       'preparada', 'aguardando_confirmacao', 'aprovado_confinex'
     )
     OR p_status NOT IN ('cancelado', 'rejeitado', 'expirado')
     OR btrim(coalesce(p_ator, '')) = ''
     OR btrim(coalesce(p_motivo, '')) = ''
     OR octet_length(p_motivo) > 1000
     OR NOT public.investigacao_texto_sanitizado(p_ator)
     OR NOT public.investigacao_texto_publico_sanitizado(p_motivo) THEN
    RAISE EXCEPTION 'Decisão terminal da promoção inválida';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'investigacao-promocao:' || p_pending_action_id::text, 0
    )
  );
  SELECT * INTO v_acao FROM public.pending_actions
   WHERE id = p_pending_action_id FOR UPDATE;
  IF NOT FOUND
     OR v_acao.acao_tipo IS DISTINCT FROM 'promover_revisao_operacional'
     OR v_acao.promocao_controle_version IS DISTINCT FROM 'lease-v1'
     OR v_acao.status IS DISTINCT FROM p_status_esperado THEN
    RAISE EXCEPTION 'Promoção não está no estado esperado para a decisão';
  END IF;
  INSERT INTO public.investigacao_autorizacoes_promocao (
    txid, backend_pid, pending_action_id, operacao,
    status_anterior, status_novo
  ) VALUES (
    txid_current(), pg_backend_pid(), v_acao.id, 'UPDATE',
    v_acao.status, p_status
  );
  UPDATE public.pending_actions
     SET status = p_status,
         resultado = jsonb_build_object(
           'decisao', p_status, 'ator', p_ator, 'motivo', p_motivo
         )
   WHERE id = v_acao.id AND status = p_status_esperado;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'A promoção mudou durante a decisão';
  END IF;
  DELETE FROM public.investigacao_autorizacoes_promocao
   WHERE txid = txid_current() AND backend_pid = pg_backend_pid()
     AND pending_action_id = v_acao.id AND operacao = 'UPDATE';
  RETURN jsonb_build_object('decidida', true, 'status', p_status);
END;
$$;

-- Investigação e preparação de promoção usam as mesmas travas consultivas.
-- Isso fecha a janela em que uma investigação nasce depois de a tela carregar:
-- a promoção revalida o estado dentro da própria transação, inclusive para
-- clientes antigos que desconhecem o feature flag do frontend.
CREATE OR REPLACE FUNCTION public.serializar_investigacao_revisao()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_id uuid;
  v_ids uuid[];
  v_promocoes_ativas uuid[];
  v_promocoes_terminais uuid[];
  v_promocao_terminal public.pending_actions%ROWTYPE;
  v_resultado_terminal_hash text;
  v_outbox_pedido_hash text;
  v_outbox_id uuid;
  v_classe_terminal text;
  v_outbox public.investigacao_sucessoes_pendentes%ROWTYPE;
  v_sucessora_autorizada boolean := false;
  v_sucessora_complementar_autorizada boolean := false;
  v_sucessora_hash text;
  v_sucessora_complementar_hash text;
  v_pai_sucessora public.investigacoes_revisao%ROWTYPE;
  v_snapshot_operacional_atual jsonb;
BEGIN
  IF NEW.sucessora_de_id IS NOT NULL THEN
    SELECT * INTO v_pai_sucessora
      FROM public.investigacoes_revisao
     WHERE id = NEW.sucessora_de_id
     FOR SHARE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Predecessora da sucessão não foi encontrada';
    END IF;
    v_sucessora_hash := encode(extensions.digest(convert_to(
      jsonb_build_object(
        'investigacao_id', NEW.id,
        'sucessora_de_id', NEW.sucessora_de_id,
        'raiz_investigacao_id', NEW.raiz_investigacao_id,
        'geracao', NEW.geracao,
        'sucessao_pedido_hash', NEW.sucessao_pedido_hash,
        'promocao_origem_id', NEW.promocao_origem_id,
        'draft_operacional_origem_id', NEW.draft_operacional_origem_id,
        'destino_operacional_origem', NEW.destino_operacional_origem,
        'registro_operacional_origem_id',
          NEW.registro_operacional_origem_id,
        'registro_operacional_origem_snapshot_ref',
          NEW.registro_operacional_origem_snapshot_ref,
        'vinculo_operacional_estado', NEW.vinculo_operacional_estado,
        'negocio_candidato_id', NEW.negocio_candidato_id,
        'source_candidato_atualizado_em',
          NEW.source_candidato_atualizado_em,
        'negocio_candidato_ids', NEW.negocio_candidato_ids,
        'source_candidatos_atualizados_em',
          NEW.source_candidatos_atualizados_em,
        'fingerprint_base', NEW.fingerprint_base,
        'plano_hash', NEW.plano_hash,
        'policy_version', NEW.policy_version,
        'policy_schema_hash', NEW.policy_schema_hash,
        'campos_obrigatorios', NEW.campos_obrigatorios
      )::text, 'UTF8'
    ), 'sha256'), 'hex');
    DELETE FROM public.investigacao_autorizacoes_corretiva autorizacao
     WHERE autorizacao.txid = txid_current()
       AND autorizacao.backend_pid = pg_backend_pid()
       AND autorizacao.recurso = 'criar_sucessora'
       AND autorizacao.investigacao_id = NEW.id
       AND autorizacao.operation_draft_id = NEW.draft_operacional_origem_id
       AND autorizacao.pending_action_id = NEW.promocao_origem_id
       AND autorizacao.pedido_hash = v_sucessora_hash;
    v_sucessora_autorizada := FOUND;
    IF NOT v_sucessora_autorizada THEN
      SELECT * INTO v_outbox
        FROM public.investigacao_sucessoes_pendentes
       WHERE id = NEW.sucessao_outbox_id
         AND promocao_id = v_pai_sucessora.promocao_ativa_id
         AND classe_resolvida IS NOT NULL
       FOR SHARE;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'Outbox resolvido da sucessora não foi encontrado';
      END IF;
      v_sucessora_complementar_hash := encode(extensions.digest(convert_to(
        jsonb_build_object(
          'investigacao_id', NEW.id,
          'sucessora_de_id', NEW.sucessora_de_id,
          'raiz_investigacao_id', NEW.raiz_investigacao_id,
          'geracao', NEW.geracao,
          'sucessao_pedido_hash', NEW.sucessao_pedido_hash,
          'sucessao_outbox_id', NEW.sucessao_outbox_id,
          'fluxo_tipo', NEW.fluxo_tipo,
          'promocao_terminal_id', v_pai_sucessora.promocao_ativa_id,
          'resolucao_hash', v_outbox.resolucao_hash,
          'promocao_origem_id', NEW.promocao_origem_id,
          'draft_operacional_origem_id', NEW.draft_operacional_origem_id,
          'destino_operacional_origem', NEW.destino_operacional_origem,
          'registro_operacional_origem_id',
            NEW.registro_operacional_origem_id,
          'registro_operacional_origem_snapshot_ref',
            NEW.registro_operacional_origem_snapshot_ref,
          'vinculo_operacional_estado', NEW.vinculo_operacional_estado,
          'source_draft_id', NEW.source_draft_id,
          'source_draft_atualizado_em', NEW.source_draft_atualizado_em,
          'negocio_candidato_id', NEW.negocio_candidato_id,
          'source_candidato_atualizado_em',
            NEW.source_candidato_atualizado_em,
          'negocio_candidato_ids', NEW.negocio_candidato_ids,
          'source_candidatos_atualizados_em',
            NEW.source_candidatos_atualizados_em,
          'fingerprint_base', NEW.fingerprint_base,
          'plano_hash', NEW.plano_hash,
          'policy_version', NEW.policy_version,
          'policy_schema_hash', NEW.policy_schema_hash,
          'campos_obrigatorios', NEW.campos_obrigatorios
        )::text, 'UTF8'
      ), 'sha256'), 'hex');
      DELETE FROM public.investigacao_autorizacoes_corretiva autorizacao
       WHERE autorizacao.txid = txid_current()
         AND autorizacao.backend_pid = pg_backend_pid()
         AND autorizacao.recurso = 'criar_sucessora_complementar'
         AND autorizacao.investigacao_id = NEW.id
         AND autorizacao.operation_draft_id = NEW.sucessora_de_id
         AND autorizacao.pending_action_id =
               v_pai_sucessora.promocao_ativa_id
         AND autorizacao.pedido_hash = v_sucessora_complementar_hash;
      v_sucessora_complementar_autorizada := FOUND;
    END IF;
  ELSE
    IF NEW.raiz_investigacao_id IS NOT NULL
       AND NEW.raiz_investigacao_id IS DISTINCT FROM NEW.id THEN
      RAISE EXCEPTION 'Raiz de investigação inválida';
    END IF;
    IF NEW.sucessao_outbox_id IS NOT NULL THEN
      RAISE EXCEPTION 'Uma investigação raiz não pertence a outbox de sucessão';
    END IF;
    NEW.raiz_investigacao_id := NEW.id;
    NEW.geracao := 0;
    NEW.sucessao_pedido_hash := NULL;
  END IF;
  IF v_sucessora_autorizada THEN
    IF v_pai_sucessora.raiz_investigacao_id
            IS DISTINCT FROM NEW.raiz_investigacao_id
       OR NEW.geracao IS DISTINCT FROM v_pai_sucessora.geracao + 1
       OR v_pai_sucessora.estado_execucao IS DISTINCT FROM 'obsoleta'
       OR v_pai_sucessora.obsolescencia_motivo
            IS DISTINCT FROM 'registro_operacional_stale'
       OR NEW.fluxo_tipo IS DISTINCT FROM 'corretiva_pos_gravacao'
       OR NEW.raiz_investigacao_id IS NULL
       OR NEW.raiz_investigacao_id = NEW.id
       OR NEW.geracao <= 0
       OR NEW.sucessao_pedido_hash IS NULL
       OR NEW.sucessao_outbox_id IS NOT NULL
       OR num_nonnulls(
         NEW.promocao_origem_id, NEW.draft_operacional_origem_id,
         NEW.destino_operacional_origem,
         NEW.registro_operacional_origem_id,
         NEW.registro_operacional_origem_snapshot_ref,
         NEW.vinculo_operacional_estado
       ) <> 6
       OR NEW.vinculo_operacional_estado <> 'confirmado'
       OR NEW.estado_execucao <> 'pendente'
       OR NEW.estado_resultado IS NOT NULL
       OR NEW.obsolescencia_motivo IS NOT NULL
       OR NEW.promocao_ativa_id IS NOT NULL
       OR NEW.anexado_em IS NOT NULL THEN
      RAISE EXCEPTION 'Sucessora corretiva fora do contrato protegido';
    END IF;
    RETURN NEW;
  ELSIF v_sucessora_complementar_autorizada THEN
    SELECT * INTO v_outbox
      FROM public.investigacao_sucessoes_pendentes
     WHERE id = NEW.sucessao_outbox_id
       AND promocao_id = v_pai_sucessora.promocao_ativa_id
       AND classe_resolvida IS NOT NULL
     FOR SHARE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Outbox da sucessora complementar não foi encontrado';
    END IF;
    SELECT * INTO v_promocao_terminal
      FROM public.pending_actions
     WHERE id = v_outbox.promocao_id
       AND acao_tipo = 'promover_revisao_operacional'
     FOR SHARE;
    IF FOUND AND NEW.fluxo_tipo = 'corretiva_pos_gravacao' THEN
      v_snapshot_operacional_atual :=
        public.investigacao_snapshot_registro_promocao(
          v_promocao_terminal.payload ->> 'target_table',
          v_outbox.registro_reconciliado_id,
          v_outbox.promocao_id,
          v_promocao_terminal.payload -> 'proposed_record'
        );
    END IF;
    IF NOT FOUND
       OR v_pai_sucessora.raiz_investigacao_id
           IS DISTINCT FROM NEW.raiz_investigacao_id
       OR NEW.geracao IS DISTINCT FROM v_pai_sucessora.geracao + 1
       OR v_pai_sucessora.estado_execucao IS DISTINCT FROM 'obsoleta'
       OR v_pai_sucessora.obsolescencia_motivo
            IS DISTINCT FROM 'complementar_promocao_ativa'
       OR v_pai_sucessora.promocao_ativa_id IS NULL
       OR NEW.raiz_investigacao_id IS NULL
       OR NEW.raiz_investigacao_id = NEW.id
       OR NEW.geracao <= 0
       OR NEW.sucessao_pedido_hash IS NULL
       OR NEW.sucessao_outbox_id IS NULL
       OR NEW.estado_execucao <> 'pendente'
       OR NEW.estado_resultado IS NOT NULL
       OR NEW.obsolescencia_motivo IS NOT NULL
       OR NEW.promocao_ativa_id IS NOT NULL
       OR NEW.anexado_em IS NOT NULL
       OR (
         NEW.fluxo_tipo = 'pre_revisao'
         AND v_outbox.classe_resolvida IS DISTINCT FROM 'sem_gravacao'
       )
       OR (
         NEW.fluxo_tipo = 'pre_revisao'
         AND num_nonnulls(
           NEW.promocao_origem_id, NEW.draft_operacional_origem_id,
           NEW.destino_operacional_origem,
           NEW.registro_operacional_origem_id,
           NEW.registro_operacional_origem_snapshot_ref,
           NEW.vinculo_operacional_estado
         ) <> 0
       )
       OR (
         NEW.fluxo_tipo = 'corretiva_pos_gravacao'
         AND (
           v_outbox.classe_resolvida IS DISTINCT FROM 'com_gravacao'
           OR NEW.promocao_origem_id IS DISTINCT FROM v_outbox.promocao_id
           OR NEW.draft_operacional_origem_id IS DISTINCT FROM
                public.investigacao_uuid_texto_seguro(
                  v_promocao_terminal.payload ->> 'source_draft_id'
                )
           OR NEW.destino_operacional_origem IS DISTINCT FROM
                v_promocao_terminal.payload ->> 'target_table'
           OR NEW.registro_operacional_origem_id
                IS DISTINCT FROM v_outbox.registro_reconciliado_id
           OR NEW.registro_operacional_origem_snapshot_ref
                IS DISTINCT FROM
                     v_snapshot_operacional_atual ->> 'snapshot_ref'
           OR coalesce(
                (v_snapshot_operacional_atual ->> 'identidade_valida')::boolean,
                false
              ) IS NOT TRUE
           OR NEW.vinculo_operacional_estado IS DISTINCT FROM 'confirmado'
           OR NEW.source_draft_id IS NOT NULL
           OR NEW.source_draft_atualizado_em IS NOT NULL
         )
       )
       OR (
         NEW.fluxo_tipo = 'corretiva_pos_gravacao'
         AND num_nonnulls(
           NEW.promocao_origem_id, NEW.draft_operacional_origem_id,
           NEW.destino_operacional_origem,
           NEW.registro_operacional_origem_id,
           NEW.registro_operacional_origem_snapshot_ref,
           NEW.vinculo_operacional_estado
         ) <> 6
       ) THEN
      RAISE EXCEPTION 'Sucessora complementar fora do contrato protegido';
    END IF;
    RETURN NEW;
  ELSIF NEW.sucessora_de_id IS NOT NULL THEN
    RAISE EXCEPTION 'Sucessora corretiva exige capacidade transacional';
  END IF;
  IF NEW.fluxo_tipo IS DISTINCT FROM 'pre_revisao'
     OR num_nonnulls(
       NEW.promocao_origem_id, NEW.draft_operacional_origem_id,
       NEW.destino_operacional_origem, NEW.registro_operacional_origem_id,
       NEW.registro_operacional_origem_snapshot_ref,
       NEW.vinculo_operacional_estado
     ) <> 0 THEN
    RAISE EXCEPTION 'A investigação nasce como pré-revisão; vínculo corretivo só é selado pelo desfecho real';
  END IF;
  IF NEW.estado_execucao IS DISTINCT FROM 'pendente'
     OR NEW.estado_resultado IS NOT NULL
     OR NEW.concluida_em IS NOT NULL
     OR NEW.decisao_draft_atualizado_em IS NOT NULL
     OR NEW.decisao_preparacao_hash IS NOT NULL
     OR NEW.materializacao_pedido_hash IS NOT NULL
     OR NEW.obsolescencia_motivo IS NOT NULL
     OR NEW.promocao_ativa_id IS NOT NULL
     OR num_nonnulls(
       NEW.anexo_chave, NEW.anexado_draft_id,
       NEW.anexado_evento_id, NEW.anexado_em
     ) <> 0 THEN
    RAISE EXCEPTION 'A investigação deve nascer pendente e sem resultado ou anexo';
  END IF;
  IF NEW.source_draft_id IS NOT NULL THEN
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended('investigacao-draft:' || NEW.source_draft_id::text, 0)
    );
  END IF;
  SELECT coalesce(array_agg(item ORDER BY item), '{}'::uuid[])
    INTO v_ids
    FROM (SELECT DISTINCT unnest(NEW.negocio_candidato_ids) AS item) AS ids;
  FOREACH v_id IN ARRAY v_ids LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended('investigacao-candidato:' || v_id::text, 0)
    );
  END LOOP;
  -- Se a promoção ganhou a corrida e já foi preparada/assumida, a evidência
  -- nova permanece como investigação complementar. Ela não pode invalidar um
  -- INSERT operacional que já foi autorizado em transação anterior.
  IF NEW.anexado_em IS NULL
     AND NEW.estado_execucao IN (
       'pendente', 'em_execucao', 'aguardando_retentativa', 'concluida'
     ) THEN
    SELECT coalesce(array_agg(DISTINCT acao.id ORDER BY acao.id), '{}'::uuid[])
      INTO v_promocoes_ativas
      FROM public.pending_actions acao
     WHERE acao.acao_tipo = 'promover_revisao_operacional'
       AND acao.status IN (
         'preparada', 'aguardando_confirmacao', 'aprovado_confinex',
         'em_execucao'
       )
       AND EXISTS (
         SELECT 1
           FROM public.operation_drafts draft
          WHERE (
            draft.id = NEW.source_draft_id
            OR (
              cardinality(v_ids) > 0
              AND public.investigacao_ids_candidatos_rascunho(
                    draft.inferencias, draft.dados_extraidos
                  ) && v_ids
            )
          )
            AND (
              acao.entidade_id = draft.id
              OR acao.payload ->> 'source_draft_id' = draft.id::text
            )
       );
    IF cardinality(v_promocoes_ativas) > 1 THEN
      RAISE EXCEPTION 'Mais de uma promoção ativa disputa a mesma investigação';
    END IF;
    IF cardinality(v_promocoes_ativas) = 1 THEN
      -- A transição terminal usa o mesmo lock. Se ela ganhar, relemos o estado
      -- já confirmado; se o INSERT ganhar, o AFTER terminal reabre esta linha.
      -- Não usamos row lock aqui para não inverter pending_action→advisory.
      PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
          'investigacao-promocao:' || v_promocoes_ativas[1]::text, 0
        )
      );
      IF NOT EXISTS (
        SELECT 1
          FROM public.pending_actions promocao
         WHERE promocao.id = v_promocoes_ativas[1]
           AND promocao.acao_tipo = 'promover_revisao_operacional'
           AND promocao.status IN (
             'preparada', 'aguardando_confirmacao', 'aprovado_confinex',
             'em_execucao'
           )
      ) THEN
        v_promocoes_ativas := '{}'::uuid[];
      END IF;
    END IF;
  END IF;
  IF cardinality(v_promocoes_ativas) = 1 THEN
    -- A fundação pode ficar em sombra antes/depois do gate 0002. Sem o trigger
    -- terminal do mediador não há consumidor capaz de reabrir a complementar;
    -- falhar mantém a entrega retentável em vez de persistir uma rodada presa.
    IF NOT EXISTS (
      SELECT 1
        FROM pg_catalog.pg_trigger gatilho
       WHERE gatilho.tgrelid = 'public.pending_actions'::regclass
         AND gatilho.tgname = 'pending_actions_reativa_complementar'
         AND NOT gatilho.tgisinternal
         AND gatilho.tgenabled = 'O'
         AND gatilho.tgfoid =
               'public.reativar_complementar_promocao_sem_gravacao()'::regprocedure
         AND gatilho.tgtype = 17
         AND gatilho.tgqual IS NULL
         AND gatilho.tgattr::text = (
           SELECT atributo.attnum::text
             FROM pg_catalog.pg_attribute atributo
            WHERE atributo.attrelid = 'public.pending_actions'::regclass
              AND atributo.attname = 'status'
              AND NOT atributo.attisdropped
         )
    ) THEN
      RAISE EXCEPTION 'Investigação complementar exige o mediador ativo';
    END IF;
    NEW.estado_execucao := 'obsoleta';
    NEW.estado_resultado := NULL;
    NEW.concluida_em := NULL;
    NEW.obsolescencia_motivo := 'complementar_promocao_ativa';
    NEW.promocao_ativa_id := v_promocoes_ativas[1];
    NEW.resumo_sanitizado :=
      'Evidência recebida depois da preparação; tratar em investigação complementar.';
  ELSIF NEW.anexado_em IS NULL
        AND EXISTS (
          SELECT 1
            FROM pg_catalog.pg_trigger gatilho
           WHERE gatilho.tgrelid = 'public.pending_actions'::regclass
             AND gatilho.tgname = 'pending_actions_reativa_complementar'
             AND NOT gatilho.tgisinternal
             AND gatilho.tgenabled = 'O'
             AND gatilho.tgfoid =
                   'public.reativar_complementar_promocao_sem_gravacao()'::regprocedure
             AND gatilho.tgtype = 17
             AND gatilho.tgqual IS NULL
             AND gatilho.tgattr::text = (
               SELECT atributo.attnum::text
                 FROM pg_catalog.pg_attribute atributo
                WHERE atributo.attrelid = 'public.pending_actions'::regclass
                  AND atributo.attname = 'status'
                  AND NOT atributo.attisdropped
             )
        ) THEN
    -- Uma evidência pode chegar depois que a promoção já terminou; nesse
    -- caso não haverá nova transição de status para acionar o AFTER trigger.
    -- A própria entrada nasce corretiva, com o vínculo terminal atestado.
    SELECT coalesce(array_agg(DISTINCT acao.id ORDER BY acao.id), '{}'::uuid[])
      INTO v_promocoes_terminais
      FROM public.pending_actions acao
     WHERE acao.acao_tipo = 'promover_revisao_operacional'
       AND acao.status IN ('executado', 'erro_pos_gravacao')
       AND EXISTS (
         SELECT 1
           FROM public.operation_drafts draft
          WHERE (
            draft.id = NEW.source_draft_id
            OR (
              cardinality(v_ids) > 0
              AND public.investigacao_ids_candidatos_rascunho(
                    draft.inferencias, draft.dados_extraidos
                  ) && v_ids
            )
          )
            AND (
              acao.entidade_id = draft.id
              OR acao.payload ->> 'source_draft_id' = draft.id::text
            )
       );
    IF cardinality(v_promocoes_terminais) > 1 THEN
      RAISE EXCEPTION 'Mais de uma promoção terminal disputa a evidência nova';
    ELSIF cardinality(v_promocoes_terminais) = 1 THEN
      PERFORM pg_catalog.pg_advisory_xact_lock(
        pg_catalog.hashtextextended(
          'investigacao-promocao:' || v_promocoes_terminais[1]::text, 0
        )
      );
      SELECT * INTO v_promocao_terminal
        FROM public.pending_actions acao
       WHERE acao.id = v_promocoes_terminais[1]
         AND acao.acao_tipo = 'promover_revisao_operacional'
         AND acao.status IN ('executado', 'erro_pos_gravacao')
       FOR SHARE;
      IF FOUND THEN
        v_resultado_terminal_hash := encode(extensions.digest(convert_to(
          coalesce(v_promocao_terminal.resultado, '{}'::jsonb)::text,
          'UTF8'
        ), 'sha256'), 'hex');
        v_classe_terminal := CASE
          WHEN v_promocao_terminal.status = 'executado'
            THEN 'com_gravacao'
          WHEN public.investigacao_uuid_texto_seguro(nullif(
                 v_promocao_terminal.resultado ->> 'target_record_id', ''
               )) IS NOT NULL
            THEN 'com_gravacao'
          ELSE 'incerto'
        END;
        v_outbox_pedido_hash := encode(extensions.digest(convert_to(
          jsonb_build_object(
            'promocao_id', v_promocao_terminal.id,
            'status_terminal', v_promocao_terminal.status,
            'resultado_terminal_hash', v_resultado_terminal_hash,
            'resultado_fencing_token',
              v_promocao_terminal.promocao_resultado_fencing_token,
            'resultado_pedido_hash',
              v_promocao_terminal.promocao_resultado_pedido_hash,
            'classe_desfecho_terminal', v_classe_terminal
          )::text, 'UTF8'
        ), 'sha256'), 'hex');
        v_outbox_id := md5(
          'sucessao-terminal:' || v_promocao_terminal.id::text
        )::uuid;
        INSERT INTO public.investigacao_sucessoes_pendentes (
          id, promocao_id, status_terminal, resultado_terminal_hash,
          pedido_hash, classe_desfecho_terminal, estado
        ) VALUES (
          v_outbox_id, v_promocao_terminal.id,
          v_promocao_terminal.status, v_resultado_terminal_hash,
          v_outbox_pedido_hash, v_classe_terminal,
          CASE WHEN v_classe_terminal = 'incerto'
            THEN 'aguardando_reconciliacao' ELSE 'pendente' END
        ) ON CONFLICT (promocao_id) DO NOTHING;
        SELECT * INTO v_outbox
          FROM public.investigacao_sucessoes_pendentes
         WHERE promocao_id = v_promocao_terminal.id
         FOR UPDATE;
        IF NOT FOUND
           OR v_outbox.id IS DISTINCT FROM v_outbox_id
           OR v_outbox.pedido_hash IS DISTINCT FROM v_outbox_pedido_hash
           OR v_outbox.classe_desfecho_terminal
                IS DISTINCT FROM v_classe_terminal THEN
          RAISE EXCEPTION 'Outbox terminal divergente para a evidência tardia';
        END IF;
        IF v_outbox.estado IN ('concluida', 'falha_permanente') THEN
          UPDATE public.investigacao_sucessoes_pendentes
             SET estado = CASE
                   WHEN classe_resolvida IS NULL
                     THEN 'aguardando_reconciliacao'
                   ELSE 'pendente'
                 END,
                 atualizado_em = clock_timestamp(),
                 ultimo_erro_codigo = NULL,
                 filhas_quantidade = NULL,
                 filhas_mapa_hash = NULL,
                 replanejamento_pedido_hash = NULL,
                 concluida_em = NULL
           WHERE id = v_outbox.id;
        END IF;
        NEW.estado_execucao := 'obsoleta';
        NEW.estado_resultado := NULL;
        NEW.concluida_em := NULL;
        NEW.obsolescencia_motivo := 'complementar_promocao_ativa';
        NEW.promocao_ativa_id := v_promocao_terminal.id;
        NEW.resumo_sanitizado :=
          'Evidência posterior ao desfecho aguardando uma nova rodada planejada.';
      END IF;
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS investigacoes_revisao_serializacao
  ON public.investigacoes_revisao;
CREATE TRIGGER investigacoes_revisao_serializacao
BEFORE INSERT
ON public.investigacoes_revisao
FOR EACH ROW EXECUTE FUNCTION public.serializar_investigacao_revisao();

CREATE OR REPLACE FUNCTION public.proteger_origem_investigacao_revisao()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_selo_esperado text;
  v_materializacao_esperada text;
  v_pedido_hash text;
BEGIN
  v_selo_esperado := jsonb_build_object(
    'investigacao_id', NEW.id,
    'promocao_origem_id', NEW.promocao_origem_id,
    'draft_operacional_origem_id', NEW.draft_operacional_origem_id,
    'destino_operacional_origem', NEW.destino_operacional_origem,
    'registro_operacional_origem_id', NEW.registro_operacional_origem_id,
    'registro_operacional_origem_snapshot_ref',
      NEW.registro_operacional_origem_snapshot_ref,
    'vinculo_operacional_estado', NEW.vinculo_operacional_estado
  )::text;
  IF NEW.fluxo_tipo IS DISTINCT FROM OLD.fluxo_tipo
     OR NEW.raiz_investigacao_id
          IS DISTINCT FROM OLD.raiz_investigacao_id
     OR NEW.sucessora_de_id IS DISTINCT FROM OLD.sucessora_de_id
     OR NEW.geracao IS DISTINCT FROM OLD.geracao
     OR NEW.sucessao_pedido_hash
          IS DISTINCT FROM OLD.sucessao_pedido_hash
     OR NEW.sucessao_outbox_id IS DISTINCT FROM OLD.sucessao_outbox_id
     OR NEW.promocao_origem_id IS DISTINCT FROM OLD.promocao_origem_id
     OR NEW.draft_operacional_origem_id
          IS DISTINCT FROM OLD.draft_operacional_origem_id
     OR NEW.destino_operacional_origem
          IS DISTINCT FROM OLD.destino_operacional_origem
     OR NEW.registro_operacional_origem_id
          IS DISTINCT FROM OLD.registro_operacional_origem_id
     OR NEW.registro_operacional_origem_snapshot_ref
          IS DISTINCT FROM OLD.registro_operacional_origem_snapshot_ref
     OR NEW.vinculo_operacional_estado
          IS DISTINCT FROM OLD.vinculo_operacional_estado THEN
    v_pedido_hash := encode(extensions.digest(
      convert_to(v_selo_esperado, 'UTF8'), 'sha256'
    ), 'hex');
    DELETE FROM public.investigacao_autorizacoes_corretiva autorizacao
     WHERE autorizacao.txid = txid_current()
       AND autorizacao.backend_pid = pg_backend_pid()
       AND autorizacao.recurso = 'selar_investigacao'
       AND autorizacao.investigacao_id = NEW.id
       AND autorizacao.operation_draft_id = NEW.draft_operacional_origem_id
       AND autorizacao.pending_action_id = NEW.promocao_origem_id
       AND autorizacao.pedido_hash = v_pedido_hash;
    IF OLD.fluxo_tipo IS DISTINCT FROM 'pre_revisao'
       OR NEW.fluxo_tipo IS DISTINCT FROM 'corretiva_pos_gravacao'
       OR NOT FOUND THEN
      RAISE EXCEPTION 'O vínculo corretivo só pode ser selado uma vez pelo desfecho da promoção';
    END IF;
  END IF;
  v_materializacao_esperada := jsonb_build_object(
    'investigacao_id', NEW.id,
    'operation_draft_id', NEW.source_draft_id,
    'promocao_origem_id', NEW.promocao_origem_id
  )::text;
  IF NEW.negocio_candidato_id IS DISTINCT FROM OLD.negocio_candidato_id
     OR NEW.negocio_candidato_ids IS DISTINCT FROM OLD.negocio_candidato_ids
     OR NEW.source_candidato_atualizado_em IS DISTINCT FROM OLD.source_candidato_atualizado_em
     OR NEW.source_candidatos_atualizados_em IS DISTINCT FROM OLD.source_candidatos_atualizados_em
     OR NEW.fingerprint_base IS DISTINCT FROM OLD.fingerprint_base
     OR NEW.plano_hash IS DISTINCT FROM OLD.plano_hash
     OR NEW.plano_canonico IS DISTINCT FROM OLD.plano_canonico
     OR NEW.plano_tarefas IS DISTINCT FROM OLD.plano_tarefas
     OR NEW.policy_version IS DISTINCT FROM OLD.policy_version
     OR NEW.policy_schema_hash IS DISTINCT FROM OLD.policy_schema_hash
     OR NEW.campos_obrigatorios IS DISTINCT FROM OLD.campos_obrigatorios THEN
    RAISE EXCEPTION 'A origem e a política da investigação são imutáveis';
  END IF;
  IF NEW.source_draft_id IS DISTINCT FROM OLD.source_draft_id
     OR NEW.source_draft_atualizado_em
          IS DISTINCT FROM OLD.source_draft_atualizado_em THEN
    IF NEW.fluxo_tipo IS DISTINCT FROM 'corretiva_pos_gravacao'
       OR NEW.source_draft_id IS NULL
       OR (
         OLD.source_draft_id IS NOT NULL
         AND OLD.source_draft_id IS DISTINCT FROM
               NEW.draft_operacional_origem_id
       ) THEN
      RAISE EXCEPTION 'O rascunho e seu snapshot temporal não podem ser trocados';
    END IF;
    v_pedido_hash := encode(extensions.digest(
      convert_to(v_materializacao_esperada, 'UTF8'), 'sha256'
    ), 'hex');
    DELETE FROM public.investigacao_autorizacoes_corretiva autorizacao
     WHERE autorizacao.txid = txid_current()
       AND autorizacao.backend_pid = pg_backend_pid()
       AND autorizacao.recurso = 'vincular_draft'
       AND autorizacao.investigacao_id = NEW.id
       AND autorizacao.operation_draft_id = NEW.source_draft_id
       AND autorizacao.pending_action_id = NEW.promocao_origem_id
       AND autorizacao.pedido_hash = v_pedido_hash;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'O rascunho corretivo só pode ser ligado pelo materializador canônico';
    END IF;
  END IF;
  IF (NEW.source_draft_id IS NULL)
     IS DISTINCT FROM (NEW.source_draft_atualizado_em IS NULL) THEN
    RAISE EXCEPTION 'O vínculo do rascunho exige seu snapshot temporal';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS investigacoes_revisao_origem_imutavel
  ON public.investigacoes_revisao;
CREATE TRIGGER investigacoes_revisao_origem_imutavel
BEFORE UPDATE OF source_draft_id, source_draft_atualizado_em,
  raiz_investigacao_id, sucessora_de_id, geracao, sucessao_pedido_hash,
  sucessao_outbox_id,
  negocio_candidato_id, negocio_candidato_ids, source_candidato_atualizado_em,
  source_candidatos_atualizados_em, fingerprint_base, policy_version,
  policy_schema_hash, plano_hash, plano_canonico, plano_tarefas, campos_obrigatorios,
  fluxo_tipo, promocao_origem_id, draft_operacional_origem_id,
  destino_operacional_origem, registro_operacional_origem_id,
  registro_operacional_origem_snapshot_ref,
  vinculo_operacional_estado
ON public.investigacoes_revisao
FOR EACH ROW EXECUTE FUNCTION public.proteger_origem_investigacao_revisao();

CREATE OR REPLACE FUNCTION public.proteger_obsolescencia_investigacao()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_reativacao_hash text;
  v_obsolescencia_hash text;
  v_substituicao_hash text;
  v_consumo_hash text;
  v_filha public.investigacoes_revisao%ROWTYPE;
BEGIN
  IF NEW.obsolescencia_motivo IS NOT DISTINCT FROM OLD.obsolescencia_motivo
     AND NEW.promocao_ativa_id IS NOT DISTINCT FROM OLD.promocao_ativa_id THEN
    RETURN NEW;
  END IF;
  IF OLD.estado_execucao = 'obsoleta'
     AND OLD.obsolescencia_motivo = 'complementar_promocao_ativa'
     AND OLD.promocao_ativa_id IS NOT NULL
     AND NEW.estado_execucao = 'obsoleta'
     AND NEW.obsolescencia_motivo = 'complementar_consumida'
     AND NEW.promocao_ativa_id IS NULL THEN
    SELECT * INTO v_filha
      FROM public.investigacoes_revisao filha
     WHERE filha.sucessora_de_id = OLD.id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'A complementar só pode ser consumida depois da sucessora';
    END IF;
    v_consumo_hash := encode(extensions.digest(convert_to(
      jsonb_build_object(
        'investigacao_id', OLD.id,
        'promocao_id', OLD.promocao_ativa_id,
        'sucessora_id', v_filha.id,
        'sucessao_pedido_hash', v_filha.sucessao_pedido_hash,
        'novo_motivo', 'complementar_consumida'
      )::text, 'UTF8'
    ), 'sha256'), 'hex');
    DELETE FROM public.investigacao_autorizacoes_corretiva autorizacao
     WHERE autorizacao.txid = txid_current()
       AND autorizacao.backend_pid = pg_backend_pid()
       AND autorizacao.recurso = 'consumir_complementar'
       AND autorizacao.investigacao_id = OLD.id
       AND autorizacao.operation_draft_id = OLD.id
       AND autorizacao.pending_action_id = OLD.promocao_ativa_id
       AND autorizacao.pedido_hash = v_consumo_hash;
    IF FOUND THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'O consumo da complementar exige capacidade transacional';
  END IF;
  IF OLD.estado_execucao = 'obsoleta'
     AND OLD.obsolescencia_motivo = 'complementar_promocao_ativa'
     AND OLD.promocao_ativa_id IS NOT NULL
     AND NEW.estado_execucao = 'pendente'
     AND NEW.obsolescencia_motivo IS NULL
     AND NEW.promocao_ativa_id IS NULL THEN
    v_reativacao_hash := encode(extensions.digest(convert_to(
      jsonb_build_object(
        'investigacao_id', NEW.id,
        'promocao_id', OLD.promocao_ativa_id,
        'novo_estado', 'pendente',
        'motivo_anterior', 'complementar_promocao_ativa'
      )::text, 'UTF8'
    ), 'sha256'), 'hex');
    DELETE FROM public.investigacao_autorizacoes_corretiva autorizacao
     WHERE autorizacao.txid = txid_current()
       AND autorizacao.backend_pid = pg_backend_pid()
       AND autorizacao.recurso = 'reativar_complementar'
       AND autorizacao.investigacao_id = NEW.id
       AND autorizacao.operation_draft_id = coalesce(
             OLD.source_draft_id,
             OLD.draft_operacional_origem_id,
             OLD.id
           )
       AND autorizacao.pending_action_id = OLD.promocao_ativa_id
       AND autorizacao.pedido_hash = v_reativacao_hash;
    IF FOUND THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'A reativação complementar exige capacidade transacional';
  END IF;
  IF OLD.fluxo_tipo = 'corretiva_pos_gravacao'
     AND OLD.estado_execucao IN (
       'pendente', 'em_execucao', 'aguardando_retentativa', 'concluida'
     )
     AND OLD.obsolescencia_motivo IS NULL
     AND OLD.promocao_ativa_id IS NULL
     AND NEW.estado_execucao = 'obsoleta'
     AND NEW.obsolescencia_motivo = 'registro_operacional_stale'
     AND NEW.promocao_ativa_id IS NULL THEN
    v_substituicao_hash := encode(extensions.digest(convert_to(
      jsonb_build_object(
        'investigacao_id', NEW.id,
        'promocao_origem_id', OLD.promocao_origem_id,
        'snapshot_anterior',
          OLD.registro_operacional_origem_snapshot_ref,
        'motivo', 'registro_operacional_stale'
      )::text, 'UTF8'
    ), 'sha256'), 'hex');
    DELETE FROM public.investigacao_autorizacoes_corretiva autorizacao
     WHERE autorizacao.txid = txid_current()
       AND autorizacao.backend_pid = pg_backend_pid()
       AND autorizacao.recurso = 'obsoletar_predecessora'
       AND autorizacao.investigacao_id = NEW.id
       AND autorizacao.operation_draft_id =
             OLD.draft_operacional_origem_id
       AND autorizacao.pending_action_id = OLD.promocao_origem_id
       AND autorizacao.pedido_hash = v_substituicao_hash;
    IF FOUND THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'A substituição corretiva só pode ocorrer pela RPC protegida';
  END IF;
  v_obsolescencia_hash := encode(extensions.digest(convert_to(
    jsonb_build_object(
      'investigacao_id', NEW.id,
      'motivo', 'pre_revisao_stale'
    )::text, 'UTF8'
  ), 'sha256'), 'hex');
  IF OLD.obsolescencia_motivo IS NOT NULL
     OR OLD.promocao_ativa_id IS NOT NULL
     OR OLD.estado_execucao NOT IN (
       'pendente', 'em_execucao', 'aguardando_retentativa', 'concluida'
     )
     OR NEW.estado_execucao <> 'obsoleta'
     OR NEW.obsolescencia_motivo <> 'pre_revisao_stale'
     OR NEW.promocao_ativa_id IS NOT NULL THEN
    RAISE EXCEPTION 'O motivo de obsolescência só pode ser selado pela RPC protegida';
  END IF;
  DELETE FROM public.investigacao_autorizacoes_corretiva autorizacao
   WHERE autorizacao.txid = txid_current()
     AND autorizacao.backend_pid = pg_backend_pid()
     AND autorizacao.recurso = 'obsoletar_investigacao'
     AND autorizacao.investigacao_id = NEW.id
     AND autorizacao.operation_draft_id = coalesce(
           OLD.source_draft_id, OLD.draft_operacional_origem_id, OLD.id
         )
     AND autorizacao.pending_action_id = coalesce(
           OLD.promocao_ativa_id, OLD.id
         )
     AND autorizacao.pedido_hash = v_obsolescencia_hash;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'A obsolescência exige capacidade transacional';
  END IF;
  RETURN NEW;
END;
$$;

-- Versão final do trigger terminal: somente grava um outbox determinístico.
-- O nome é preservado para o cutover/rollback já versionado, mas nenhuma
-- investigação é reaberta ou clonada dentro da transação operacional.
CREATE OR REPLACE FUNCTION public.reativar_complementar_promocao_sem_gravacao()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_resultado_hash text;
  v_pedido_hash text;
  v_outbox_id uuid;
  v_classe text;
  v_estado text;
  v_existente public.investigacao_sucessoes_pendentes%ROWTYPE;
BEGIN
  IF TG_OP IS DISTINCT FROM 'UPDATE'
     OR TG_RELID IS DISTINCT FROM 'public.pending_actions'::regclass THEN
    RAISE EXCEPTION 'O outbox terminal só pode rodar no trigger autorizado';
  END IF;
  IF OLD.acao_tipo IS DISTINCT FROM 'promover_revisao_operacional'
     OR NEW.acao_tipo IS DISTINCT FROM OLD.acao_tipo
     OR NEW.status IS NOT DISTINCT FROM OLD.status
     OR NEW.status NOT IN (
       'cancelado', 'rejeitado', 'expirado', 'erro',
       'executado', 'erro_pos_gravacao'
     ) THEN
    RETURN NEW;
  END IF;
  v_resultado_hash := encode(extensions.digest(convert_to(
    coalesce(NEW.resultado, '{}'::jsonb)::text, 'UTF8'
  ), 'sha256'), 'hex');
  v_classe := CASE
    WHEN NEW.status IN ('cancelado', 'rejeitado', 'expirado', 'erro')
      THEN 'sem_gravacao'
    WHEN NEW.status = 'executado'
      THEN 'com_gravacao'
    WHEN public.investigacao_uuid_texto_seguro(
           nullif(NEW.resultado ->> 'target_record_id', '')
         ) IS NOT NULL
      THEN 'com_gravacao'
    ELSE 'incerto'
  END;
  v_estado := CASE
    WHEN v_classe = 'incerto'
      THEN 'aguardando_reconciliacao'
    ELSE 'pendente'
  END;
  v_pedido_hash := encode(extensions.digest(convert_to(
    jsonb_build_object(
      'promocao_id', NEW.id,
      'status_terminal', NEW.status,
      'resultado_terminal_hash', v_resultado_hash,
      'resultado_fencing_token', NEW.promocao_resultado_fencing_token,
      'resultado_pedido_hash', NEW.promocao_resultado_pedido_hash,
      'classe_desfecho_terminal', v_classe
    )::text, 'UTF8'
  ), 'sha256'), 'hex');
  v_outbox_id := md5('sucessao-terminal:' || NEW.id::text)::uuid;
  INSERT INTO public.investigacao_sucessoes_pendentes (
    id, promocao_id, status_terminal, resultado_terminal_hash,
    pedido_hash, classe_desfecho_terminal, estado
  ) VALUES (
    v_outbox_id, NEW.id, NEW.status, v_resultado_hash,
    v_pedido_hash, v_classe, v_estado
  ) ON CONFLICT (promocao_id) DO NOTHING;
  SELECT * INTO v_existente
    FROM public.investigacao_sucessoes_pendentes
   WHERE promocao_id = NEW.id;
  IF NOT FOUND
     OR v_existente.id IS DISTINCT FROM v_outbox_id
     OR v_existente.status_terminal IS DISTINCT FROM NEW.status
     OR v_existente.resultado_terminal_hash IS DISTINCT FROM v_resultado_hash
     OR v_existente.pedido_hash IS DISTINCT FROM v_pedido_hash
     OR v_existente.classe_desfecho_terminal IS DISTINCT FROM v_classe THEN
    RAISE EXCEPTION 'O outbox terminal existente diverge do desfecho confirmado';
  END IF;
  RETURN NEW;
END;
$$;

-- Consome o outbox em transação curta e idempotente. Não insere nem altera
-- dado operacional: apenas preserva o placeholder como pai e cria uma nova
-- investigação com tarefas zeradas para o retrato atual das fontes.
CREATE OR REPLACE FUNCTION public.consumir_sucessoes_promocao_terminal(
  p_promocao_id uuid,
  p_pedido_hash text,
  p_ator text
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_outbox public.investigacao_sucessoes_pendentes%ROWTYPE;
  v_promocao public.pending_actions%ROWTYPE;
  v_pai public.investigacoes_revisao%ROWTYPE;
  v_filha public.investigacoes_revisao%ROWTYPE;
  v_draft public.operation_drafts%ROWTYPE;
  v_ids_draft uuid[];
  v_ids_draft_pos uuid[];
  v_ids_candidatos uuid[];
  v_ids_candidatos_pos uuid[];
  v_pais_pre uuid[];
  v_pais_pos uuid[];
  v_id uuid;
  v_tipo text;
  v_destino text;
  v_registro_id uuid;
  v_snapshot_operacional jsonb;
  v_snapshot_draft timestamptz;
  v_snapshots_candidatos jsonb;
  v_snapshot_principal timestamptz;
  v_fingerprint text;
  v_filha_hash text;
  v_filha_id uuid;
  v_capacidade_hash text;
  v_consumo_hash text;
  v_resolucao_hash text;
  v_resultado_hash text;
  v_filhas jsonb;
  v_filhas_hash text;
  v_filhas_quantidade integer;
  v_criadas integer := 0;
  v_repetidas integer := 0;
BEGIN
  IF coalesce(
       nullif(current_setting('role', true), 'none'), session_user
     ) IS DISTINCT FROM 'service_role'
     OR p_promocao_id IS NULL
     OR p_pedido_hash !~ '^[0-9a-f]{64}$'
     OR btrim(coalesce(p_ator, '')) = ''
     OR octet_length(p_ator) > 160
     OR NOT public.investigacao_texto_sanitizado(p_ator) THEN
    RAISE EXCEPTION 'Pedido de consumo do outbox inválido';
  END IF;
  SELECT * INTO v_outbox
    FROM public.investigacao_sucessoes_pendentes
   WHERE promocao_id = p_promocao_id;
  IF NOT FOUND OR v_outbox.pedido_hash IS DISTINCT FROM p_pedido_hash THEN
    RAISE EXCEPTION 'Outbox terminal não encontrado ou pedido divergente';
  END IF;
  -- Pré-leitura deliberadamente curta: ela define as chaves consultivas que
  -- precisam ser tomadas antes da promoção. Depois do lock da promoção os
  -- mesmos conjuntos são calculados outra vez; qualquer diferença aborta a
  -- transação antes de bloquear/modificar uma complementar.
  SELECT coalesce(array_agg(investigacao.id ORDER BY investigacao.id), '{}'::uuid[])
    INTO v_pais_pre
    FROM public.investigacoes_revisao investigacao
   WHERE investigacao.promocao_ativa_id = p_promocao_id
     AND investigacao.estado_execucao = 'obsoleta'
     AND investigacao.obsolescencia_motivo =
           'complementar_promocao_ativa';
  SELECT coalesce(array_agg(DISTINCT draft_id ORDER BY draft_id), '{}'::uuid[])
    INTO v_ids_draft
    FROM (
      SELECT investigacao.source_draft_id AS draft_id
        FROM public.investigacoes_revisao investigacao
       WHERE investigacao.promocao_ativa_id = p_promocao_id
         AND investigacao.estado_execucao = 'obsoleta'
         AND investigacao.obsolescencia_motivo =
               'complementar_promocao_ativa'
         AND investigacao.source_draft_id IS NOT NULL
    ) drafts;
  FOREACH v_id IN ARRAY v_ids_draft LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended('investigacao-draft:' || v_id::text, 0)
    );
  END LOOP;
  SELECT coalesce(array_agg(DISTINCT candidato_id ORDER BY candidato_id), '{}'::uuid[])
    INTO v_ids_candidatos
    FROM (
      SELECT unnest(investigacao.negocio_candidato_ids) AS candidato_id
        FROM public.investigacoes_revisao investigacao
       WHERE investigacao.promocao_ativa_id = p_promocao_id
         AND investigacao.estado_execucao = 'obsoleta'
         AND investigacao.obsolescencia_motivo =
               'complementar_promocao_ativa'
    ) candidatos;
  FOREACH v_id IN ARRAY v_ids_candidatos LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended('investigacao-candidato:' || v_id::text, 0)
    );
  END LOOP;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'investigacao-promocao:' || p_promocao_id::text, 0
    )
  );
  SELECT * INTO v_promocao
    FROM public.pending_actions
   WHERE id = p_promocao_id
   FOR SHARE;
  SELECT * INTO v_outbox
    FROM public.investigacao_sucessoes_pendentes
   WHERE promocao_id = p_promocao_id
   FOR UPDATE;
  -- NO-GO de concorrência: nenhuma filha pode nascer a partir de uma
  -- pré-leitura que ficou velha enquanto os locks eram adquiridos. Não basta
  -- comparar timestamps individuais; o conjunto de pais e de fontes também
  -- precisa ser exatamente o mesmo.
  SELECT coalesce(array_agg(investigacao.id ORDER BY investigacao.id), '{}'::uuid[])
    INTO v_pais_pos
    FROM public.investigacoes_revisao investigacao
   WHERE investigacao.promocao_ativa_id = p_promocao_id
     AND investigacao.estado_execucao = 'obsoleta'
     AND investigacao.obsolescencia_motivo =
           'complementar_promocao_ativa';
  SELECT coalesce(array_agg(DISTINCT investigacao.source_draft_id
                            ORDER BY investigacao.source_draft_id), '{}'::uuid[])
    INTO v_ids_draft_pos
    FROM public.investigacoes_revisao investigacao
   WHERE investigacao.id = ANY(v_pais_pos)
     AND investigacao.source_draft_id IS NOT NULL;
  SELECT coalesce(array_agg(DISTINCT candidato_id ORDER BY candidato_id), '{}'::uuid[])
    INTO v_ids_candidatos_pos
    FROM public.investigacoes_revisao investigacao
    CROSS JOIN LATERAL unnest(investigacao.negocio_candidato_ids) candidato_id
   WHERE investigacao.id = ANY(v_pais_pos);
  IF v_pais_pos IS DISTINCT FROM v_pais_pre
     OR v_ids_draft_pos IS DISTINCT FROM v_ids_draft
     OR v_ids_candidatos_pos IS DISTINCT FROM v_ids_candidatos THEN
    RAISE EXCEPTION 'RETRY_CONJUNTO_FONTES_MUDOU: complementar criada durante a aquisição dos locks';
  END IF;
  v_resultado_hash := encode(extensions.digest(convert_to(
    coalesce(v_promocao.resultado, '{}'::jsonb)::text, 'UTF8'
  ), 'sha256'), 'hex');
  IF v_promocao.id IS NULL
     OR v_promocao.acao_tipo IS DISTINCT FROM 'promover_revisao_operacional'
     OR v_promocao.status IS DISTINCT FROM v_outbox.status_terminal
     OR v_outbox.pedido_hash IS DISTINCT FROM p_pedido_hash
     OR v_outbox.resultado_terminal_hash IS DISTINCT FROM v_resultado_hash THEN
    RAISE EXCEPTION 'A promoção terminal divergiu do outbox';
  END IF;
  v_destino := nullif(v_promocao.payload ->> 'target_table', '');
  IF v_destino NOT IN ('compras', 'vendas', 'pesagens_caderno', 'abates') THEN
    RAISE EXCEPTION 'Destino terminal fora da lista operacional';
  END IF;
  IF v_outbox.estado = 'concluida' THEN
    SELECT coalesce(jsonb_agg(jsonb_build_object(
             'predecessora_id', filha.sucessora_de_id,
             'sucessora_id', filha.id,
             'sucessao_pedido_hash', filha.sucessao_pedido_hash
           ) ORDER BY filha.sucessora_de_id, filha.id), '[]'::jsonb),
           count(*)::integer
      INTO v_filhas, v_filhas_quantidade
      FROM public.investigacoes_revisao filha
     WHERE filha.sucessao_outbox_id = v_outbox.id;
    v_filhas_hash := encode(extensions.digest(convert_to(
      v_filhas::text, 'UTF8'
    ), 'sha256'), 'hex');
    IF v_outbox.classe_resolvida IS NULL
       OR v_outbox.filhas_quantidade IS DISTINCT FROM v_filhas_quantidade
       OR v_outbox.filhas_mapa_hash IS DISTINCT FROM v_filhas_hash
       OR EXISTS (
         SELECT 1 FROM public.investigacoes_revisao pai
          WHERE pai.promocao_ativa_id = p_promocao_id
            AND pai.estado_execucao = 'obsoleta'
            AND pai.obsolescencia_motivo = 'complementar_promocao_ativa'
       ) THEN
      RAISE EXCEPTION 'Outbox concluído possui mapeamento de sucessão corrompido';
    END IF;
    RETURN jsonb_build_object(
      'processada', true, 'repetida', true,
      'criadas', 0, 'repetidas_existentes', v_filhas_quantidade,
      'pedido_hash', v_outbox.pedido_hash,
      'sucessoras', v_filhas
    );
  END IF;
  IF v_outbox.classe_resolvida IS NULL THEN
    v_registro_id := NULL;
    v_snapshot_operacional := NULL;
    IF v_outbox.classe_desfecho_terminal = 'com_gravacao' THEN
      v_registro_id := public.investigacao_uuid_texto_seguro(
        nullif(v_promocao.resultado ->> 'target_record_id', '')
      );
      IF v_registro_id IS NULL THEN
        RAISE EXCEPTION 'Desfecho com gravação não identifica o registro';
      END IF;
    ELSIF v_outbox.classe_desfecho_terminal = 'incerto' THEN
      -- O advisory da promoção já foi adquirido. Todo INSERT operacional
      -- vinculado usa o mesmo lock antes de gravar; portanto nenhuma linha
      -- válida desta promoção pode aparecer depois desta varredura.
      IF v_destino = 'compras' THEN
        SELECT compra.id INTO v_registro_id
          FROM public.compras compra
         WHERE compra.idempotency_key =
               'promocao_operacional:' || v_promocao.id::text;
      ELSIF v_destino = 'vendas' THEN
        SELECT venda.id INTO v_registro_id FROM public.vendas venda
         WHERE venda.promocao_origem_id = v_promocao.id;
      ELSIF v_destino = 'pesagens_caderno' THEN
        SELECT pesagem.id INTO v_registro_id
          FROM public.pesagens_caderno pesagem
         WHERE pesagem.promocao_origem_id = v_promocao.id;
      ELSE
        SELECT abate.id INTO v_registro_id FROM public.abates abate
         WHERE abate.promocao_origem_id = v_promocao.id;
      END IF;
    END IF;
    IF v_registro_id IS NOT NULL THEN
      v_snapshot_operacional :=
        public.investigacao_snapshot_registro_promocao(
          v_destino, v_registro_id, v_promocao.id,
          v_promocao.payload -> 'proposed_record'
        );
      IF coalesce(
           (v_snapshot_operacional ->> 'identidade_valida')::boolean, false
         ) IS NOT TRUE
         OR coalesce(
           (v_snapshot_operacional ->> 'corresponde')::boolean, false
         ) IS NOT TRUE THEN
        RAISE EXCEPTION 'Registro reconciliado não pertence à promoção';
      END IF;
      v_outbox.classe_resolvida := 'com_gravacao';
    ELSE
      v_outbox.classe_resolvida := 'sem_gravacao';
    END IF;
    v_resolucao_hash := encode(extensions.digest(convert_to(
      jsonb_build_object(
        'outbox_id', v_outbox.id,
        'pedido_hash', v_outbox.pedido_hash,
        'classe_desfecho_terminal', v_outbox.classe_desfecho_terminal,
        'classe_resolvida', v_outbox.classe_resolvida,
        'registro_reconciliado_id', v_registro_id,
        'registro_reconciliado_snapshot_ref',
          v_snapshot_operacional ->> 'snapshot_ref',
        'resolucao_versao', 'terminal-advisory-v1'
      )::text, 'UTF8'
    ), 'sha256'), 'hex');
    UPDATE public.investigacao_sucessoes_pendentes
       SET classe_resolvida = v_outbox.classe_resolvida,
           registro_reconciliado_id = v_registro_id,
           registro_reconciliado_snapshot_ref =
             v_snapshot_operacional ->> 'snapshot_ref',
           resolucao_hash = v_resolucao_hash,
           resolucao_versao = 'terminal-advisory-v1',
           resolvida_em = clock_timestamp(), resolvida_por = p_ator,
           estado = 'pendente', ultima_varredura_em = clock_timestamp(),
           atualizado_em = clock_timestamp()
     WHERE id = v_outbox.id
       AND classe_resolvida IS NULL
       AND pedido_hash = p_pedido_hash;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'A resolução terminal mudou durante o consumo';
    END IF;
    SELECT * INTO v_outbox
      FROM public.investigacao_sucessoes_pendentes
     WHERE id = v_outbox.id
     FOR UPDATE;
  ELSE
    v_registro_id := v_outbox.registro_reconciliado_id;
    IF v_outbox.classe_resolvida = 'com_gravacao' THEN
      v_snapshot_operacional := jsonb_build_object(
        'snapshot_ref', v_outbox.registro_reconciliado_snapshot_ref
      );
    ELSE
      v_snapshot_operacional := NULL;
    END IF;
  END IF;
  PERFORM 1
    FROM public.investigacoes_revisao pai
   WHERE pai.promocao_ativa_id = p_promocao_id
     AND pai.estado_execucao = 'obsoleta'
     AND pai.obsolescencia_motivo = 'complementar_promocao_ativa'
   ORDER BY pai.id
   FOR UPDATE;
  -- Uma corretiva muda a fonte semântica do rascunho para o registro
  -- operacional confirmado. Mesmo que candidatos e política não tenham
  -- mudado, consultas antigas podem conter termos, campos ou janelas do
  -- rascunho anterior. Por isso toda corretiva exige replanejamento explícito.
  IF v_outbox.classe_resolvida = 'com_gravacao'
     OR EXISTS (
    SELECT 1
      FROM public.investigacoes_revisao pai
     WHERE pai.promocao_ativa_id = p_promocao_id
       AND pai.estado_execucao = 'obsoleta'
       AND pai.obsolescencia_motivo = 'complementar_promocao_ativa'
       AND (
         (v_outbox.classe_resolvida = 'sem_gravacao'
          AND pai.source_draft_id IS NOT NULL AND NOT EXISTS (
           SELECT 1 FROM public.operation_drafts draft
            WHERE draft.id = pai.source_draft_id
              AND draft.atualizado_em
                    IS NOT DISTINCT FROM pai.source_draft_atualizado_em
         ))
         OR (
           cardinality(pai.negocio_candidato_ids) > 0
           AND NOT public.investigacao_snapshot_candidatos_atual(
             pai.negocio_candidato_ids,
             pai.source_candidatos_atualizados_em
           )
         )
         OR (
           cardinality(pai.negocio_candidato_ids) = 0
           AND pai.source_candidatos_atualizados_em
                 IS DISTINCT FROM '{}'::jsonb
         )
         OR pai.policy_schema_hash IS DISTINCT FROM
              public.investigacao_politica_schema_hash(pai.policy_version)
         OR pai.campos_obrigatorios IS DISTINCT FROM
              public.investigacao_politica_campos(
                pai.assunto_tipo, pai.policy_version
              )
       )
  ) THEN
    UPDATE public.investigacao_sucessoes_pendentes
       SET ultimo_erro_codigo = 'PLANEJAMENTO_FONTES_NECESSARIO',
           ultima_varredura_em = clock_timestamp(),
           atualizado_em = clock_timestamp(),
           estado = 'aguardando_planejamento'
     WHERE id = v_outbox.id;
    RETURN jsonb_build_object(
      'processada', false,
      'motivo', 'planejamento_fontes_necessario',
      'criadas', 0, 'repetidas', 0
    );
  END IF;
  FOR v_pai IN
    SELECT *
      FROM public.investigacoes_revisao investigacao
     WHERE investigacao.promocao_ativa_id = p_promocao_id
       AND investigacao.estado_execucao = 'obsoleta'
       AND investigacao.obsolescencia_motivo =
             'complementar_promocao_ativa'
     ORDER BY investigacao.id
     FOR UPDATE
  LOOP
    v_tipo := CASE v_outbox.classe_resolvida
      WHEN 'sem_gravacao' THEN 'pre_revisao'
      ELSE 'corretiva_pos_gravacao'
    END;
    v_snapshot_draft := NULL;
    IF v_tipo = 'pre_revisao' AND v_pai.source_draft_id IS NOT NULL THEN
      SELECT * INTO v_draft FROM public.operation_drafts
       WHERE id = v_pai.source_draft_id FOR SHARE;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'Rascunho da complementar não foi encontrado';
      END IF;
      v_snapshot_draft := v_draft.atualizado_em;
      IF v_snapshot_draft
           IS DISTINCT FROM v_pai.source_draft_atualizado_em THEN
        RAISE EXCEPTION 'PLANEJAMENTO_FONTES_NECESSARIO: rascunho mudou após a pré-validação';
      END IF;
    END IF;
    v_snapshots_candidatos := '{}'::jsonb;
    v_snapshot_principal := NULL;
    IF cardinality(v_pai.negocio_candidato_ids) > 0 THEN
      PERFORM 1 FROM public.negocios_candidatos candidato
       WHERE candidato.id = ANY(v_pai.negocio_candidato_ids)
       ORDER BY candidato.id FOR SHARE;
      SELECT coalesce(jsonb_object_agg(
               candidato.id::text, candidato.atualizado_em
               ORDER BY candidato.id
             ), '{}'::jsonb)
        INTO v_snapshots_candidatos
        FROM public.negocios_candidatos candidato
       WHERE candidato.id = ANY(v_pai.negocio_candidato_ids);
      IF public.investigacao_jsonb_objeto_tamanho(v_snapshots_candidatos)
           <> cardinality(v_pai.negocio_candidato_ids) THEN
        RAISE EXCEPTION 'Candidato da complementar não foi encontrado';
      END IF;
      IF v_pai.negocio_candidato_id IS NOT NULL THEN
        v_snapshot_principal := (
          v_snapshots_candidatos ->> v_pai.negocio_candidato_id::text
        )::timestamptz;
      END IF;
    END IF;
    IF v_snapshots_candidatos
         IS DISTINCT FROM v_pai.source_candidatos_atualizados_em
       OR v_snapshot_principal
         IS DISTINCT FROM v_pai.source_candidato_atualizado_em THEN
      RAISE EXCEPTION 'PLANEJAMENTO_FONTES_NECESSARIO: candidatos mudaram após a pré-validação';
    END IF;
    v_filha_hash := encode(extensions.digest(convert_to(
      jsonb_build_object(
        'outbox_pedido_hash', v_outbox.pedido_hash,
        'predecessora_id', v_pai.id,
        'raiz', v_pai.raiz_investigacao_id,
        'geracao', v_pai.geracao + 1,
        'fluxo_tipo', v_tipo,
        'source_draft_id', CASE WHEN v_tipo = 'pre_revisao'
          THEN v_pai.source_draft_id ELSE NULL END,
        'source_draft_atualizado_em', v_snapshot_draft,
        'negocio_candidato_id', v_pai.negocio_candidato_id,
        'source_candidato_atualizado_em', v_snapshot_principal,
        'negocio_candidato_ids', v_pai.negocio_candidato_ids,
        'source_candidatos_atualizados_em', v_snapshots_candidatos,
        'fingerprint_base', v_pai.fingerprint_base,
        'plano_hash', v_pai.plano_hash,
        'policy_version', v_pai.policy_version,
        'policy_schema_hash', v_pai.policy_schema_hash,
        'campos_obrigatorios', v_pai.campos_obrigatorios,
        'resolucao_hash', v_outbox.resolucao_hash,
        'registro_reconciliado_id', v_outbox.registro_reconciliado_id,
        'registro_reconciliado_snapshot_ref',
          v_outbox.registro_reconciliado_snapshot_ref
      )::text, 'UTF8'
    ), 'sha256'), 'hex');
    v_filha_id := md5(
      'sucessora-complementar:' || v_pai.id::text || ':' || v_filha_hash
    )::uuid;
    SELECT * INTO v_filha FROM public.investigacoes_revisao
     WHERE sucessora_de_id = v_pai.id;
    IF FOUND THEN
      RAISE EXCEPTION 'Complementar ativa já possui sucessora; linhagem corrompida';
    END IF;
    -- Reuso só ocorre depois da prova acima de que draft, candidatos e
    -- política continuam exatamente no retrato do pai. Portanto o
    -- fingerprint e o plano canônico são preservados, nunca sintetizados.
    v_fingerprint := v_pai.fingerprint_base;
    v_capacidade_hash := encode(extensions.digest(convert_to(
      jsonb_build_object(
        'investigacao_id', v_filha_id,
        'sucessora_de_id', v_pai.id,
        'raiz_investigacao_id', v_pai.raiz_investigacao_id,
        'geracao', v_pai.geracao + 1,
        'sucessao_pedido_hash', v_filha_hash,
        'sucessao_outbox_id', v_outbox.id,
        'fluxo_tipo', v_tipo,
        'promocao_terminal_id', p_promocao_id,
        'resolucao_hash', v_outbox.resolucao_hash,
        'promocao_origem_id', CASE WHEN v_tipo = 'corretiva_pos_gravacao'
          THEN p_promocao_id ELSE NULL END,
        'draft_operacional_origem_id',
          CASE WHEN v_tipo = 'corretiva_pos_gravacao'
            THEN public.investigacao_uuid_texto_seguro(
              v_promocao.payload ->> 'source_draft_id'
            ) ELSE NULL END,
        'destino_operacional_origem',
          CASE WHEN v_tipo = 'corretiva_pos_gravacao'
            THEN v_destino ELSE NULL END,
        'registro_operacional_origem_id',
          CASE WHEN v_tipo = 'corretiva_pos_gravacao'
            THEN v_outbox.registro_reconciliado_id ELSE NULL END,
        'registro_operacional_origem_snapshot_ref',
          CASE WHEN v_tipo = 'corretiva_pos_gravacao'
            THEN v_outbox.registro_reconciliado_snapshot_ref ELSE NULL END,
        'vinculo_operacional_estado',
          CASE WHEN v_tipo = 'corretiva_pos_gravacao'
            THEN 'confirmado' ELSE NULL END,
        'source_draft_id', CASE WHEN v_tipo = 'pre_revisao'
          THEN v_pai.source_draft_id ELSE NULL END,
        'source_draft_atualizado_em', CASE WHEN v_tipo = 'pre_revisao'
          THEN v_snapshot_draft ELSE NULL END,
        'negocio_candidato_id', v_pai.negocio_candidato_id,
        'source_candidato_atualizado_em', v_snapshot_principal,
        'negocio_candidato_ids', v_pai.negocio_candidato_ids,
        'source_candidatos_atualizados_em', v_snapshots_candidatos,
        'fingerprint_base', v_fingerprint,
        'plano_hash', v_pai.plano_hash,
        'policy_version', v_pai.policy_version,
        'policy_schema_hash', v_pai.policy_schema_hash,
        'campos_obrigatorios', v_pai.campos_obrigatorios
      )::text, 'UTF8'
    ), 'sha256'), 'hex');
    INSERT INTO public.investigacao_autorizacoes_corretiva (
      txid, backend_pid, recurso, investigacao_id,
      operation_draft_id, pending_action_id, pedido_hash
    ) VALUES (
      txid_current(), pg_backend_pid(), 'criar_sucessora_complementar',
      v_filha_id, v_pai.id, p_promocao_id, v_capacidade_hash
    );
    INSERT INTO public.investigacoes_revisao (
      id, raiz_investigacao_id, sucessora_de_id, geracao,
      sucessao_pedido_hash, sucessao_outbox_id,
      chave_idempotencia, assunto_tipo,
      assunto_referencia, titulo, fluxo_tipo, promocao_origem_id,
      draft_operacional_origem_id, destino_operacional_origem,
      registro_operacional_origem_id,
      registro_operacional_origem_snapshot_ref,
      vinculo_operacional_estado, source_draft_id,
      source_draft_atualizado_em, negocio_candidato_id,
      source_candidato_atualizado_em, negocio_candidato_ids,
      source_candidatos_atualizados_em, fingerprint_base, plano_hash,
      plano_canonico, plano_tarefas, policy_version, policy_schema_hash,
      campos_obrigatorios, gatilho_tipo, prioridade, contexto_canonico,
      contexto_nome, origem_canal, origem_conversa_id,
      origem_mensagem_id, escopo, estado_execucao, resumo_sanitizado,
      criado_por
    ) VALUES (
      v_filha_id, v_pai.raiz_investigacao_id, v_pai.id,
      v_pai.geracao + 1, v_filha_hash, v_outbox.id,
      v_pai.chave_idempotencia || ':sucessao:' || v_filha_hash,
      v_pai.assunto_tipo, v_pai.assunto_referencia, v_pai.titulo, v_tipo,
      CASE WHEN v_tipo = 'corretiva_pos_gravacao'
        THEN p_promocao_id ELSE NULL END,
      CASE WHEN v_tipo = 'corretiva_pos_gravacao'
        THEN public.investigacao_uuid_texto_seguro(
          v_promocao.payload ->> 'source_draft_id'
        ) ELSE NULL END,
      CASE WHEN v_tipo = 'corretiva_pos_gravacao'
        THEN v_destino ELSE NULL END,
      CASE WHEN v_tipo = 'corretiva_pos_gravacao'
        THEN v_outbox.registro_reconciliado_id ELSE NULL END,
      CASE WHEN v_tipo = 'corretiva_pos_gravacao'
        THEN v_outbox.registro_reconciliado_snapshot_ref ELSE NULL END,
      CASE WHEN v_tipo = 'corretiva_pos_gravacao'
        THEN 'confirmado' ELSE NULL END,
      CASE WHEN v_tipo = 'pre_revisao'
        THEN v_pai.source_draft_id ELSE NULL END,
      CASE WHEN v_tipo = 'pre_revisao'
        THEN v_snapshot_draft ELSE NULL END,
      v_pai.negocio_candidato_id, v_snapshot_principal,
      v_pai.negocio_candidato_ids, v_snapshots_candidatos,
      v_fingerprint, v_pai.plano_hash, v_pai.plano_canonico,
      v_pai.plano_tarefas, v_pai.policy_version,
      v_pai.policy_schema_hash, v_pai.campos_obrigatorios,
      'timer', v_pai.prioridade, v_pai.contexto_canonico,
      v_pai.contexto_nome, v_pai.origem_canal,
      v_pai.origem_conversa_id, v_pai.origem_mensagem_id,
      v_pai.escopo, 'pendente',
      CASE WHEN v_tipo = 'corretiva_pos_gravacao'
        THEN 'Evidência recebida após a gravação; cruzar fontes antes da correção.'
        ELSE 'Evidência complementar reaberta após encerramento sem gravação.'
      END,
      p_ator
    );
    INSERT INTO public.investigacao_tarefas (
      investigacao_id, chave_idempotencia, plano_item_ref, adaptador,
      consulta_ref, consulta_schema_version, consulta_spec,
      consulta_canonico, consulta_hash, adaptador_version,
      estado_execucao, tentativas, proxima_execucao_em, fencing_token
    )
    SELECT v_filha_id,
           'sucessao:' || v_filha_id::text || ':' || tarefa.plano_item_ref,
           tarefa.plano_item_ref, tarefa.adaptador, tarefa.consulta_ref,
           tarefa.consulta_schema_version, tarefa.consulta_spec,
           tarefa.consulta_canonico, tarefa.consulta_hash,
           tarefa.adaptador_version, 'pendente', 0,
           clock_timestamp(), 0
      FROM public.investigacao_tarefas tarefa
     WHERE tarefa.investigacao_id = v_pai.id
     ORDER BY tarefa.id;
    INSERT INTO public.investigacao_eventos (
      investigacao_id, chave_idempotencia, tipo,
      referencia_entidade, resumo_sanitizado
    ) VALUES (
      v_filha_id, 'sucessora-complementar:' || v_filha_id::text,
      'investigacao_sucessora_criada', v_pai.id::text,
      'Uma nova rodada foi criada sem reutilizar resultados ou leases anteriores.'
    );
    v_consumo_hash := encode(extensions.digest(convert_to(
      jsonb_build_object(
        'investigacao_id', v_pai.id,
        'promocao_id', p_promocao_id,
        'sucessora_id', v_filha_id,
        'sucessao_pedido_hash', v_filha_hash,
        'novo_motivo', 'complementar_consumida'
      )::text, 'UTF8'
    ), 'sha256'), 'hex');
    INSERT INTO public.investigacao_autorizacoes_corretiva (
      txid, backend_pid, recurso, investigacao_id,
      operation_draft_id, pending_action_id, pedido_hash
    ) VALUES (
      txid_current(), pg_backend_pid(), 'consumir_complementar',
      v_pai.id, v_pai.id, p_promocao_id, v_consumo_hash
    );
    UPDATE public.investigacoes_revisao
       SET obsolescencia_motivo = 'complementar_consumida',
           promocao_ativa_id = NULL
     WHERE id = v_pai.id
       AND obsolescencia_motivo = 'complementar_promocao_ativa'
       AND promocao_ativa_id = p_promocao_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'A complementar mudou durante o consumo';
    END IF;
    v_criadas := v_criadas + 1;
  END LOOP;
  SELECT coalesce(jsonb_agg(jsonb_build_object(
           'predecessora_id', filha.sucessora_de_id,
           'sucessora_id', filha.id,
           'sucessao_pedido_hash', filha.sucessao_pedido_hash
         ) ORDER BY filha.sucessora_de_id, filha.id), '[]'::jsonb),
         count(*)::integer
    INTO v_filhas, v_filhas_quantidade
    FROM public.investigacoes_revisao filha
   WHERE filha.sucessao_outbox_id = v_outbox.id;
  v_filhas_hash := encode(extensions.digest(convert_to(
    v_filhas::text, 'UTF8'
  ), 'sha256'), 'hex');
  UPDATE public.investigacao_sucessoes_pendentes
     SET estado = 'concluida', ultima_varredura_em = clock_timestamp(),
         atualizado_em = clock_timestamp(), ultimo_erro_codigo = NULL,
         filhas_quantidade = v_filhas_quantidade,
         filhas_mapa_hash = v_filhas_hash,
         replanejamento_pedido_hash = encode(extensions.digest(convert_to(
           jsonb_build_object(
             'versao', 'reuso-pre-revisao-v1',
             'outbox_id', v_outbox.id,
             'pedido_hash', v_outbox.pedido_hash,
             'resolucao_hash', v_outbox.resolucao_hash,
             'filhas_mapa_hash', v_filhas_hash
           )::text, 'UTF8'
         ), 'sha256'), 'hex'),
         concluida_em = clock_timestamp()
   WHERE id = v_outbox.id
     AND classe_resolvida IS NOT NULL
     AND estado <> 'concluida';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Outbox mudou antes do fechamento atômico';
  END IF;
  RETURN jsonb_build_object(
    'processada', true, 'criadas', v_criadas,
    'repetidas', v_repetidas, 'pedido_hash', v_outbox.pedido_hash,
    'sucessoras', v_filhas
  );
END;
$$;

-- Leitura fechada usada pelo planejador externo. Ela não retorna texto de
-- fontes, evidências ou documentos: somente os identificadores opacos e o
-- retrato que a mutação vai atestar novamente sob locks. O hash CAS impede
-- que um plano calculado para uma rodada antiga seja aceito por engano.
CREATE OR REPLACE FUNCTION public.obter_contexto_replanejamento_sucessoes_promocao_terminal(
  p_promocao_id uuid,
  p_pedido_hash text
)
RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_outbox public.investigacao_sucessoes_pendentes%ROWTYPE;
  v_promocao public.pending_actions%ROWTYPE;
  v_snapshot_operacional jsonb;
  v_predecessoras jsonb;
  v_hash text;
BEGIN
  IF coalesce(nullif(current_setting('role', true), 'none'), session_user)
       IS DISTINCT FROM 'service_role'
     OR p_promocao_id IS NULL
     OR p_pedido_hash !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'Consulta de replanejamento inválida';
  END IF;

  SELECT * INTO v_outbox
    FROM public.investigacao_sucessoes_pendentes
   WHERE promocao_id = p_promocao_id;
  IF NOT FOUND OR v_outbox.pedido_hash IS DISTINCT FROM p_pedido_hash
     OR v_outbox.estado <> 'aguardando_planejamento'
     OR v_outbox.classe_resolvida IS NULL THEN
    RAISE EXCEPTION 'Outbox não está aguardando replanejamento';
  END IF;
  SELECT * INTO v_promocao
    FROM public.pending_actions
   WHERE id = p_promocao_id
     AND acao_tipo = 'promover_revisao_operacional';
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Promoção terminal não encontrada';
  END IF;
  IF v_outbox.classe_resolvida = 'com_gravacao' THEN
    v_snapshot_operacional := public.investigacao_snapshot_registro_promocao(
      v_promocao.payload ->> 'target_table',
      v_outbox.registro_reconciliado_id,
      v_outbox.promocao_id,
      v_promocao.payload -> 'proposed_record'
    );
    -- A resolução original já atestou o retrato aprovado. Nesta rodada a
    -- própria mudança posterior é a razão do replanejamento; exigimos a
    -- proveniência imutável da promoção, não igualdade com o payload antigo.
    IF coalesce((v_snapshot_operacional ->> 'identidade_valida')::boolean, false)
         IS NOT TRUE THEN
      RAISE EXCEPTION 'Registro operacional do replanejamento não corresponde ao outbox';
    END IF;
  END IF;

  SELECT coalesce(jsonb_agg(linha.contexto ORDER BY linha.predecessora_id), '[]'::jsonb)
    INTO v_predecessoras
    FROM (
      SELECT pai.id AS predecessora_id,
             jsonb_build_object(
               'predecessora_id', pai.id,
               'raiz_investigacao_id', pai.raiz_investigacao_id,
               'geracao_origem', pai.geracao,
               'planejamento_inputs', jsonb_build_object(
                 'assunto', jsonb_build_object(
                   'tipo', pai.assunto_tipo,
                   'referencia', pai.referencia_publica
                 ),
                 'origem', jsonb_build_object(
                   'canal', coalesce(pai.origem_canal, 'desconhecido'),
                   'linhagem', coalesce(pai.origem_canal, 'desconhecido'),
                   'contexto_canonico', pai.contexto_canonico,
                   'contexto_nome', pai.contexto_nome,
                   'escopo', pai.escopo
                 ),
                 'consulta_base', jsonb_build_object(
                   'modo', 'replanejar_sucessao_terminal',
                   'source_draft_id', CASE v_outbox.classe_resolvida
                     WHEN 'sem_gravacao' THEN pai.source_draft_id ELSE NULL END,
                   'negocio_candidato_ids', to_jsonb(pai.negocio_candidato_ids),
                   'promocao_origem_id', v_outbox.promocao_id,
                   'destino_operacional_origem', CASE v_outbox.classe_resolvida
                     WHEN 'com_gravacao' THEN v_promocao.payload ->> 'target_table'
                     ELSE NULL END,
                   'registro_operacional_origem_id', CASE v_outbox.classe_resolvida
                     WHEN 'com_gravacao' THEN v_outbox.registro_reconciliado_id
                     ELSE NULL END
                 ),
                 'cobertura', 'cobertura_incompleta',
                 'instante_referencia', pai.atualizado_em
               ),
               'fluxo_tipo', CASE v_outbox.classe_resolvida
                 WHEN 'sem_gravacao' THEN 'pre_revisao'
                 ELSE 'corretiva_pos_gravacao'
               END,
               'source_draft_id', CASE v_outbox.classe_resolvida
                 WHEN 'sem_gravacao' THEN pai.source_draft_id ELSE NULL END,
               'source_draft_atualizado_em', CASE v_outbox.classe_resolvida
                 WHEN 'sem_gravacao' THEN fonte_draft.atualizado_em ELSE NULL END,
               'negocio_candidato_id', pai.negocio_candidato_id,
               'source_candidato_atualizado_em',
                 fonte_candidatos.snapshots ->> pai.negocio_candidato_id::text,
               'negocio_candidato_ids', to_jsonb(pai.negocio_candidato_ids),
               'source_candidatos_atualizados_em', fonte_candidatos.snapshots,
               'promocao_origem_id', CASE v_outbox.classe_resolvida
                 WHEN 'com_gravacao' THEN v_outbox.promocao_id ELSE NULL END,
               'draft_operacional_origem_id', CASE v_outbox.classe_resolvida
                 WHEN 'com_gravacao' THEN public.investigacao_uuid_texto_seguro(
                   v_promocao.payload ->> 'source_draft_id'
                 ) ELSE NULL END,
               'destino_operacional_origem', CASE v_outbox.classe_resolvida
                 WHEN 'com_gravacao' THEN v_promocao.payload ->> 'target_table' ELSE NULL END,
               'registro_operacional_origem_id', CASE v_outbox.classe_resolvida
                 WHEN 'com_gravacao' THEN v_outbox.registro_reconciliado_id ELSE NULL END,
               'registro_operacional_origem_snapshot_ref', CASE v_outbox.classe_resolvida
                 WHEN 'com_gravacao' THEN v_snapshot_operacional ->> 'snapshot_ref' ELSE NULL END,
               'vinculo_operacional_estado', CASE v_outbox.classe_resolvida
                 WHEN 'com_gravacao' THEN 'confirmado' ELSE NULL END,
               'policy_version', pai.policy_version,
               'policy_schema_hash', public.investigacao_politica_schema_hash(pai.policy_version),
               'campos_obrigatorios', to_jsonb(public.investigacao_politica_campos(
                 pai.assunto_tipo, pai.policy_version
               )),
               'contexto_hash', encode(extensions.digest(convert_to(
                 jsonb_build_object(
                   'outbox_id', v_outbox.id,
                   'promocao_id', v_outbox.promocao_id,
                   'pedido_hash', v_outbox.pedido_hash,
                   'status_terminal', v_outbox.status_terminal,
                   'classe_resolvida', v_outbox.classe_resolvida,
                   'resolucao_hash', v_outbox.resolucao_hash,
                   'predecessora_id', pai.id,
                   'raiz_investigacao_id', pai.raiz_investigacao_id,
                   'geracao_origem', pai.geracao,
                   'planejamento_inputs', jsonb_build_object(
                     'assunto', jsonb_build_object(
                       'tipo', pai.assunto_tipo,
                       'referencia', pai.referencia_publica
                     ),
                     'origem', jsonb_build_object(
                       'canal', coalesce(pai.origem_canal, 'desconhecido'),
                       'linhagem', coalesce(pai.origem_canal, 'desconhecido'),
                       'contexto_canonico', pai.contexto_canonico,
                       'contexto_nome', pai.contexto_nome,
                       'escopo', pai.escopo
                     ),
                     'consulta_base', jsonb_build_object(
                       'modo', 'replanejar_sucessao_terminal',
                       'source_draft_id', CASE v_outbox.classe_resolvida
                         WHEN 'sem_gravacao' THEN pai.source_draft_id ELSE NULL END,
                       'negocio_candidato_ids', to_jsonb(pai.negocio_candidato_ids),
                       'promocao_origem_id', v_outbox.promocao_id,
                       'destino_operacional_origem', CASE v_outbox.classe_resolvida
                         WHEN 'com_gravacao' THEN v_promocao.payload ->> 'target_table'
                         ELSE NULL END,
                       'registro_operacional_origem_id', CASE v_outbox.classe_resolvida
                         WHEN 'com_gravacao' THEN v_outbox.registro_reconciliado_id
                         ELSE NULL END
                     ),
                     'cobertura', 'cobertura_incompleta',
                     'instante_referencia', pai.atualizado_em
                   ),
                   'source_draft_id', CASE v_outbox.classe_resolvida
                     WHEN 'sem_gravacao' THEN pai.source_draft_id ELSE NULL END,
                   'source_draft_atualizado_em', CASE v_outbox.classe_resolvida
                     WHEN 'sem_gravacao' THEN fonte_draft.atualizado_em ELSE NULL END,
                   'negocio_candidato_id', pai.negocio_candidato_id,
                   'source_candidato_atualizado_em',
                     fonte_candidatos.snapshots ->> pai.negocio_candidato_id::text,
                   'negocio_candidato_ids', pai.negocio_candidato_ids,
                   'source_candidatos_atualizados_em', fonte_candidatos.snapshots,
                   'promocao_origem_id', CASE v_outbox.classe_resolvida
                     WHEN 'com_gravacao' THEN v_outbox.promocao_id ELSE NULL END,
                   'draft_operacional_origem_id', CASE v_outbox.classe_resolvida
                     WHEN 'com_gravacao' THEN public.investigacao_uuid_texto_seguro(
                       v_promocao.payload ->> 'source_draft_id'
                     ) ELSE NULL END,
                   'destino_operacional_origem', CASE v_outbox.classe_resolvida
                     WHEN 'com_gravacao' THEN v_promocao.payload ->> 'target_table' ELSE NULL END,
                   'registro_operacional_origem_id', CASE v_outbox.classe_resolvida
                     WHEN 'com_gravacao' THEN v_outbox.registro_reconciliado_id ELSE NULL END,
                   'registro_operacional_origem_snapshot_ref', CASE v_outbox.classe_resolvida
                     WHEN 'com_gravacao' THEN v_snapshot_operacional ->> 'snapshot_ref' ELSE NULL END,
                   'vinculo_operacional_estado', CASE v_outbox.classe_resolvida
                     WHEN 'com_gravacao' THEN 'confirmado' ELSE NULL END,
                   'policy_version', pai.policy_version,
                   'policy_schema_hash', public.investigacao_politica_schema_hash(pai.policy_version),
                   'campos_obrigatorios', public.investigacao_politica_campos(
                     pai.assunto_tipo, pai.policy_version
                   )
                 )::text, 'UTF8'
               ), 'sha256'), 'hex')
             ) AS contexto
        FROM public.investigacoes_revisao pai
        LEFT JOIN LATERAL (
          SELECT draft.atualizado_em
            FROM public.operation_drafts draft
           WHERE draft.id = pai.source_draft_id
        ) fonte_draft ON v_outbox.classe_resolvida = 'sem_gravacao'
        LEFT JOIN LATERAL (
          SELECT coalesce(jsonb_object_agg(candidato.id::text, candidato.atualizado_em
                                            ORDER BY candidato.id), '{}'::jsonb) AS snapshots
            FROM public.negocios_candidatos candidato
           WHERE candidato.id = ANY(pai.negocio_candidato_ids)
        ) fonte_candidatos ON true
       WHERE pai.promocao_ativa_id = p_promocao_id
         AND pai.estado_execucao = 'obsoleta'
         AND pai.obsolescencia_motivo = 'complementar_promocao_ativa'
    ) linha;
  IF jsonb_array_length(v_predecessoras) = 0 THEN
    RAISE EXCEPTION 'Outbox aguardando planejamento não possui predecessoras';
  END IF;
  v_hash := encode(extensions.digest(convert_to(
    jsonb_build_object(
      'versao', 'replanejamento-terminal-v1',
      'outbox_id', v_outbox.id,
      'promocao_id', v_outbox.promocao_id,
      'pedido_hash', v_outbox.pedido_hash,
      'resolucao_hash', v_outbox.resolucao_hash,
      'predecessoras', v_predecessoras
    )::text, 'UTF8'
  ), 'sha256'), 'hex');
  RETURN jsonb_build_object(
    'versao', 'replanejamento-terminal-v1',
    'outbox_id', v_outbox.id,
    'promocao_id', v_outbox.promocao_id,
    'pedido_hash', v_outbox.pedido_hash,
    'resolucao_hash', v_outbox.resolucao_hash,
    'predecessoras', v_predecessoras,
    'contexto_cas_hash', v_hash
  );
END;
$$;

-- Materializa de uma vez todas as folhas complementares de uma promoção que
-- terminou. O planejador pode sugerir apenas plano/fingerprint/política para
-- cada predecessora; IDs, vínculos e hashes de capacidade são sempre gerados
-- no servidor. Uma corretiva nunca reaproveita o plano anterior: se o
-- planejador não entregar uma rodada nova, a transação falha antes da escrita.
CREATE OR REPLACE FUNCTION public.replanejar_sucessoes_promocao_terminal(
  p_promocao_id uuid,
  p_pedido_hash text,
  p_contexto_cas_hash text,
  p_replanejamento jsonb,
  p_ator text
)
RETURNS jsonb
LANGUAGE plpgsql
VOLATILE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_outbox public.investigacao_sucessoes_pendentes%ROWTYPE;
  v_promocao public.pending_actions%ROWTYPE;
  v_pai public.investigacoes_revisao%ROWTYPE;
  v_item jsonb;
  v_contexto jsonb;
  v_filhas jsonb;
  v_filhas_hash text;
  v_filhas_quantidade integer;
  v_replanejamento_pedido_hash text;
  v_ids_draft_pre uuid[];
  v_ids_draft_pos uuid[];
  v_ids_candidatos_pre uuid[];
  v_ids_candidatos_pos uuid[];
  v_pais_pre uuid[];
  v_pais_pos uuid[];
  v_id uuid;
  v_tipo text;
  v_source_draft_atualizado_em timestamptz;
  v_source_candidatos jsonb;
  v_source_candidato_atualizado_em timestamptz;
  v_snapshot_operacional_atual_ref text;
  v_policy_schema_hash text;
  v_campos_obrigatorios text[];
  v_plano_tarefas jsonb;
  v_plano_canonico text;
  v_plano_hash text;
  v_fingerprint_base text;
  v_filha_hash text;
  v_filha_id uuid;
  v_capacidade_hash text;
  v_consumo_hash text;
  v_criadas integer := 0;
BEGIN
  IF coalesce(nullif(current_setting('role', true), 'none'), session_user)
       IS DISTINCT FROM 'service_role'
     OR p_promocao_id IS NULL
     OR p_pedido_hash !~ '^[0-9a-f]{64}$'
     OR p_contexto_cas_hash !~ '^[0-9a-f]{64}$'
     OR btrim(coalesce(p_ator, '')) = ''
     OR octet_length(p_ator) > 160
     OR NOT public.investigacao_texto_sanitizado(p_ator)
     OR jsonb_typeof(p_replanejamento) <> 'object'
     OR p_replanejamento - ARRAY[
       'versao', 'outbox_id', 'promocao_id', 'pedido_hash',
       'contexto_cas_hash', 'predecessoras'
     ] <> '{}'::jsonb
     OR public.investigacao_jsonb_objeto_tamanho(p_replanejamento) <> 6
     OR p_replanejamento ->> 'versao' <> 'replanejamento-terminal-v1'
     OR p_replanejamento ->> 'outbox_id' !~ '^[0-9a-f-]{36}$'
     OR p_replanejamento ->> 'promocao_id' IS DISTINCT FROM p_promocao_id::text
     OR p_replanejamento ->> 'pedido_hash' IS DISTINCT FROM p_pedido_hash
     OR p_replanejamento ->> 'contexto_cas_hash' IS DISTINCT FROM p_contexto_cas_hash
     OR jsonb_typeof(p_replanejamento -> 'predecessoras') <> 'array'
     OR jsonb_array_length(p_replanejamento -> 'predecessoras') = 0 THEN
    RAISE EXCEPTION 'Pedido de replanejamento inválido';
  END IF;
  IF EXISTS (
    SELECT 1 FROM jsonb_array_elements(p_replanejamento -> 'predecessoras') item
     WHERE jsonb_typeof(item) <> 'object'
        OR item - ARRAY[
          'predecessora_id', 'contexto_hash', 'plano_hash',
          'plano_canonico', 'plano_tarefas',
          'policy_version', 'policy_schema_hash', 'campos_obrigatorios'
        ] <> '{}'::jsonb
        OR public.investigacao_jsonb_objeto_tamanho(item) <> 8
        OR item ->> 'predecessora_id' !~ '^[0-9a-f-]{36}$'
        OR item ->> 'contexto_hash' !~ '^[0-9a-f]{64}$'
        OR item ->> 'plano_hash' !~ '^[0-9a-f]{64}$'
        OR jsonb_typeof(item -> 'plano_canonico') <> 'string'
        OR jsonb_typeof(item -> 'plano_tarefas') <> 'array'
        OR jsonb_typeof(item -> 'campos_obrigatorios') <> 'array'
  ) THEN
    RAISE EXCEPTION 'Predecessoras de replanejamento fora do contrato fechado';
  END IF;
  v_replanejamento_pedido_hash := encode(extensions.digest(convert_to(
    p_replanejamento::text, 'UTF8'
  ), 'sha256'), 'hex');

  -- Pré-leitura para a ordenação global dos locks de fonte. Toda a leitura é
  -- conferida novamente depois do lock da promoção; se surgir pai/fonte, a
  -- transação inteira é abortada e o planejador deve obter novo contexto.
  SELECT coalesce(array_agg(pai.id ORDER BY pai.id), '{}'::uuid[])
    INTO v_pais_pre
    FROM public.investigacoes_revisao pai
   WHERE pai.promocao_ativa_id = p_promocao_id
     AND pai.estado_execucao = 'obsoleta'
     AND pai.obsolescencia_motivo = 'complementar_promocao_ativa';
  SELECT coalesce(array_agg(DISTINCT pai.source_draft_id ORDER BY pai.source_draft_id),
                   '{}'::uuid[])
    INTO v_ids_draft_pre
    FROM public.investigacoes_revisao pai
   WHERE pai.id = ANY(v_pais_pre) AND pai.source_draft_id IS NOT NULL;
  SELECT coalesce(array_agg(DISTINCT candidato_id ORDER BY candidato_id), '{}'::uuid[])
    INTO v_ids_candidatos_pre
    FROM public.investigacoes_revisao pai
    CROSS JOIN LATERAL unnest(pai.negocio_candidato_ids) candidato_id
   WHERE pai.id = ANY(v_pais_pre);
  FOREACH v_id IN ARRAY v_ids_draft_pre LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended('investigacao-draft:' || v_id::text, 0)
    );
  END LOOP;
  FOREACH v_id IN ARRAY v_ids_candidatos_pre LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended('investigacao-candidato:' || v_id::text, 0)
    );
  END LOOP;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('investigacao-promocao:' || p_promocao_id::text, 0)
  );
  SELECT * INTO v_promocao FROM public.pending_actions
   WHERE id = p_promocao_id FOR SHARE;
  SELECT * INTO v_outbox FROM public.investigacao_sucessoes_pendentes
   WHERE promocao_id = p_promocao_id FOR UPDATE;
  IF NOT FOUND OR v_outbox.pedido_hash IS DISTINCT FROM p_pedido_hash
     OR v_outbox.id::text IS DISTINCT FROM p_replanejamento ->> 'outbox_id'
     OR v_promocao.acao_tipo IS DISTINCT FROM 'promover_revisao_operacional'
     OR v_promocao.status IS DISTINCT FROM v_outbox.status_terminal THEN
    RAISE EXCEPTION 'Outbox terminal divergiu antes do replanejamento';
  END IF;
  IF v_outbox.estado = 'concluida' THEN
    SELECT coalesce(jsonb_agg(jsonb_build_object(
             'predecessora_id', filha.sucessora_de_id,
             'sucessora_id', filha.id,
             'sucessao_pedido_hash', filha.sucessao_pedido_hash
           ) ORDER BY filha.sucessora_de_id, filha.id), '[]'::jsonb),
           count(*)::integer
      INTO v_filhas, v_filhas_quantidade
      FROM public.investigacoes_revisao filha
     WHERE filha.sucessao_outbox_id = v_outbox.id;
    v_filhas_hash := encode(extensions.digest(convert_to(
      v_filhas::text, 'UTF8'
    ), 'sha256'), 'hex');
    IF v_outbox.replanejamento_pedido_hash
         IS DISTINCT FROM v_replanejamento_pedido_hash
       OR v_outbox.filhas_quantidade IS DISTINCT FROM v_filhas_quantidade
       OR v_outbox.filhas_mapa_hash IS DISTINCT FROM v_filhas_hash THEN
      RAISE EXCEPTION 'Retry de replanejamento diverge do pedido/mapa concluído';
    END IF;
    RETURN jsonb_build_object('processada', true, 'repetida', true,
      'pedido_hash', v_outbox.pedido_hash, 'sucessoras', v_filhas);
  END IF;
  IF v_outbox.estado IS DISTINCT FROM 'aguardando_planejamento'
     OR v_outbox.classe_resolvida IS NULL THEN
    RAISE EXCEPTION 'Outbox não está pronto para replanejamento';
  END IF;
  SELECT coalesce(array_agg(pai.id ORDER BY pai.id), '{}'::uuid[])
    INTO v_pais_pos
    FROM public.investigacoes_revisao pai
   WHERE pai.promocao_ativa_id = p_promocao_id
     AND pai.estado_execucao = 'obsoleta'
     AND pai.obsolescencia_motivo = 'complementar_promocao_ativa';
  SELECT coalesce(array_agg(DISTINCT pai.source_draft_id ORDER BY pai.source_draft_id),
                   '{}'::uuid[])
    INTO v_ids_draft_pos
    FROM public.investigacoes_revisao pai
   WHERE pai.id = ANY(v_pais_pos) AND pai.source_draft_id IS NOT NULL;
  SELECT coalesce(array_agg(DISTINCT candidato_id ORDER BY candidato_id), '{}'::uuid[])
    INTO v_ids_candidatos_pos
    FROM public.investigacoes_revisao pai
    CROSS JOIN LATERAL unnest(pai.negocio_candidato_ids) candidato_id
   WHERE pai.id = ANY(v_pais_pos);
  IF v_pais_pre IS DISTINCT FROM v_pais_pos
     OR v_ids_draft_pre IS DISTINCT FROM v_ids_draft_pos
     OR v_ids_candidatos_pre IS DISTINCT FROM v_ids_candidatos_pos THEN
    RAISE EXCEPTION 'RETRY_CONJUNTO_FONTES_MUDOU: replanejamento recebeu nova fonte ou predecessora';
  END IF;
  PERFORM 1 FROM public.investigacoes_revisao pai
   WHERE pai.id = ANY(v_pais_pos) ORDER BY pai.id FOR UPDATE;
  PERFORM 1 FROM public.operation_drafts draft
   WHERE draft.id = ANY(v_ids_draft_pos) ORDER BY draft.id FOR SHARE;
  PERFORM 1 FROM public.negocios_candidatos candidato
   WHERE candidato.id = ANY(v_ids_candidatos_pos) ORDER BY candidato.id FOR SHARE;
  -- A corretiva não pode confiar apenas na referência persistida no outbox:
  -- trava a linha operacional atual e a leitura CAS abaixo refaz a prova de
  -- identidade/correspondência contra ela.
  IF v_outbox.classe_resolvida = 'com_gravacao' THEN
    IF v_promocao.payload ->> 'target_table' = 'compras' THEN
      PERFORM 1 FROM public.compras registro
       WHERE registro.id = v_outbox.registro_reconciliado_id FOR SHARE;
    ELSIF v_promocao.payload ->> 'target_table' = 'vendas' THEN
      PERFORM 1 FROM public.vendas registro
       WHERE registro.id = v_outbox.registro_reconciliado_id FOR SHARE;
    ELSIF v_promocao.payload ->> 'target_table' = 'pesagens_caderno' THEN
      PERFORM 1 FROM public.pesagens_caderno registro
       WHERE registro.id = v_outbox.registro_reconciliado_id FOR SHARE;
    ELSIF v_promocao.payload ->> 'target_table' = 'abates' THEN
      PERFORM 1 FROM public.abates registro
       WHERE registro.id = v_outbox.registro_reconciliado_id FOR SHARE;
    ELSE
      RAISE EXCEPTION 'Destino operacional inválido para replanejamento';
    END IF;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Registro operacional do replanejamento não foi encontrado';
    END IF;
  END IF;

  v_contexto := public.obter_contexto_replanejamento_sucessoes_promocao_terminal(
    p_promocao_id, p_pedido_hash
  );
  IF v_contexto ->> 'contexto_cas_hash' IS DISTINCT FROM p_contexto_cas_hash THEN
    RAISE EXCEPTION 'RETRY_CONTEXTO_CAS_DIVERGIU: fontes ou política mudaram';
  END IF;
  IF (SELECT coalesce(array_agg((item ->> 'predecessora_id')::uuid ORDER BY
                  (item ->> 'predecessora_id')::uuid), '{}'::uuid[])
        FROM jsonb_array_elements(p_replanejamento -> 'predecessoras') item)
       IS DISTINCT FROM v_pais_pos THEN
    RAISE EXCEPTION 'Plano não cobre exatamente todas as complementares ativas';
  END IF;

  -- Primeiro valida todos os planos. Nenhum pai é consumido até que o lote
  -- inteiro tenha passado por CAS, política e contrato das tarefas.
  FOR v_pai IN SELECT * FROM public.investigacoes_revisao pai
                 WHERE pai.id = ANY(v_pais_pos) ORDER BY pai.id FOR UPDATE LOOP
    SELECT item INTO v_item FROM jsonb_array_elements(p_replanejamento -> 'predecessoras') item
     WHERE (item ->> 'predecessora_id')::uuid = v_pai.id;
    SELECT coalesce(array_agg(valor #>> '{}' ORDER BY valor #>> '{}'), '{}'::text[])
      INTO v_campos_obrigatorios
      FROM jsonb_array_elements(v_item -> 'campos_obrigatorios') valor;
    v_plano_tarefas := v_item -> 'plano_tarefas';
    v_plano_canonico := v_item ->> 'plano_canonico';
    v_plano_hash := v_item ->> 'plano_hash';
    v_policy_schema_hash := public.investigacao_politica_schema_hash(
      v_item ->> 'policy_version'
    );
    IF NOT EXISTS (
         SELECT 1 FROM jsonb_array_elements(v_contexto -> 'predecessoras') contexto
          WHERE (contexto ->> 'predecessora_id')::uuid = v_pai.id
            AND contexto ->> 'contexto_hash' IS NOT DISTINCT FROM
                  v_item ->> 'contexto_hash'
       )
       OR v_item ->> 'policy_version' IS DISTINCT FROM v_pai.policy_version
       OR v_item ->> 'policy_schema_hash' IS DISTINCT FROM v_policy_schema_hash
       OR v_campos_obrigatorios IS DISTINCT FROM public.investigacao_politica_campos(
            v_pai.assunto_tipo, v_item ->> 'policy_version'
          )
       OR NOT public.investigacao_plano_tarefas_valido(v_plano_tarefas)
       OR v_plano_canonico::jsonb IS DISTINCT FROM jsonb_build_object(
            'tarefas', v_plano_tarefas,
            'campos_obrigatorios', to_jsonb(v_campos_obrigatorios),
            'policy_schema_hash', v_policy_schema_hash
          )
       OR encode(extensions.digest(convert_to(v_plano_canonico, 'UTF8'), 'sha256'), 'hex')
            IS DISTINCT FROM v_plano_hash THEN
      RAISE EXCEPTION 'Plano ou política de replanejamento inválido';
    END IF;
    IF v_outbox.classe_resolvida = 'com_gravacao'
       AND v_plano_hash IS NOT DISTINCT FROM v_pai.plano_hash THEN
      RAISE EXCEPTION 'PLANEJAMENTO_CORRETIVO_NAO_REPLANEJADO: clone de plano corretivo bloqueado';
    END IF;
  END LOOP;

  FOR v_pai IN SELECT * FROM public.investigacoes_revisao pai
                 WHERE pai.id = ANY(v_pais_pos) ORDER BY pai.id FOR UPDATE LOOP
    SELECT item INTO v_item FROM jsonb_array_elements(p_replanejamento -> 'predecessoras') item
     WHERE (item ->> 'predecessora_id')::uuid = v_pai.id;
    SELECT coalesce(array_agg(valor #>> '{}' ORDER BY valor #>> '{}'), '{}'::text[])
      INTO v_campos_obrigatorios
      FROM jsonb_array_elements(v_item -> 'campos_obrigatorios') valor;
    v_plano_tarefas := v_item -> 'plano_tarefas';
    v_plano_canonico := v_item ->> 'plano_canonico';
    v_plano_hash := v_item ->> 'plano_hash';
    v_tipo := CASE v_outbox.classe_resolvida
      WHEN 'sem_gravacao' THEN 'pre_revisao' ELSE 'corretiva_pos_gravacao' END;
    v_source_draft_atualizado_em := NULL;
    IF v_tipo = 'pre_revisao' AND v_pai.source_draft_id IS NOT NULL THEN
      SELECT draft.atualizado_em INTO v_source_draft_atualizado_em
        FROM public.operation_drafts draft WHERE draft.id = v_pai.source_draft_id FOR SHARE;
      IF NOT FOUND THEN RAISE EXCEPTION 'Fonte draft do replanejamento não encontrada'; END IF;
    ELSIF v_tipo = 'pre_revisao'
          AND cardinality(v_pai.negocio_candidato_ids) = 0 THEN
      RAISE EXCEPTION 'Replanejamento pré-revisão sem fonte draft ou candidata';
    END IF;
    SELECT coalesce(jsonb_object_agg(candidato.id::text, candidato.atualizado_em
                                     ORDER BY candidato.id), '{}'::jsonb)
      INTO v_source_candidatos
      FROM public.negocios_candidatos candidato
     WHERE candidato.id = ANY(v_pai.negocio_candidato_ids);
    IF public.investigacao_jsonb_objeto_tamanho(v_source_candidatos)
         <> cardinality(v_pai.negocio_candidato_ids) THEN
      RAISE EXCEPTION 'Fonte candidata do replanejamento não encontrada';
    END IF;
    v_source_candidato_atualizado_em := (v_source_candidatos ->>
      v_pai.negocio_candidato_id::text)::timestamptz;
    SELECT contexto ->> 'registro_operacional_origem_snapshot_ref'
      INTO v_snapshot_operacional_atual_ref
      FROM jsonb_array_elements(v_contexto -> 'predecessoras') contexto
     WHERE (contexto ->> 'predecessora_id')::uuid = v_pai.id;
    -- Não aceitamos fingerprint do planejador. O selo é derivado pelo banco
    -- do retrato já travado, da política e do plano validado.
    v_fingerprint_base := encode(extensions.digest(convert_to(jsonb_build_object(
      'versao', 'fingerprint-replanejada-v1',
      'contexto_cas_hash', p_contexto_cas_hash,
      'predecessora_id', v_pai.id,
      'fluxo_tipo', v_tipo,
      'source_draft_id', CASE WHEN v_tipo = 'pre_revisao' THEN v_pai.source_draft_id END,
      'source_draft_atualizado_em', v_source_draft_atualizado_em,
      'negocio_candidato_id', v_pai.negocio_candidato_id,
      'source_candidato_atualizado_em', v_source_candidato_atualizado_em,
      'negocio_candidato_ids', v_pai.negocio_candidato_ids,
      'source_candidatos_atualizados_em', v_source_candidatos,
      'resolucao_hash', v_outbox.resolucao_hash,
      'plano_hash', v_plano_hash,
      'policy_version', v_item ->> 'policy_version',
      'policy_schema_hash', v_item ->> 'policy_schema_hash',
      'campos_obrigatorios', v_campos_obrigatorios
    )::text, 'UTF8'), 'sha256'), 'hex');
    v_filha_hash := encode(extensions.digest(convert_to(jsonb_build_object(
      'replanejamento_contexto_hash', p_contexto_cas_hash,
      'outbox_pedido_hash', v_outbox.pedido_hash,
      'predecessora_id', v_pai.id,
      'raiz_investigacao_id', v_pai.raiz_investigacao_id,
      'geracao', v_pai.geracao + 1,
      'fluxo_tipo', v_tipo,
      'source_draft_id', CASE WHEN v_tipo = 'pre_revisao' THEN v_pai.source_draft_id END,
      'source_draft_atualizado_em', v_source_draft_atualizado_em,
      'negocio_candidato_id', v_pai.negocio_candidato_id,
      'source_candidato_atualizado_em', v_source_candidato_atualizado_em,
      'negocio_candidato_ids', v_pai.negocio_candidato_ids,
      'source_candidatos_atualizados_em', v_source_candidatos,
      'resolucao_hash', v_outbox.resolucao_hash,
      'promocao_origem_id', CASE WHEN v_tipo = 'corretiva_pos_gravacao' THEN p_promocao_id END,
      'draft_operacional_origem_id', CASE WHEN v_tipo = 'corretiva_pos_gravacao'
        THEN public.investigacao_uuid_texto_seguro(v_promocao.payload ->> 'source_draft_id') END,
      'destino_operacional_origem', CASE WHEN v_tipo = 'corretiva_pos_gravacao'
        THEN v_promocao.payload ->> 'target_table' END,
      'registro_operacional_origem_id', CASE WHEN v_tipo = 'corretiva_pos_gravacao'
        THEN v_outbox.registro_reconciliado_id END,
      'registro_operacional_origem_snapshot_ref', CASE WHEN v_tipo = 'corretiva_pos_gravacao'
        THEN v_snapshot_operacional_atual_ref END,
      'vinculo_operacional_estado', CASE WHEN v_tipo = 'corretiva_pos_gravacao' THEN 'confirmado' END,
      'fingerprint_base', v_fingerprint_base,
      'plano_hash', v_plano_hash,
      'policy_version', v_item ->> 'policy_version',
      'policy_schema_hash', v_item ->> 'policy_schema_hash',
      'campos_obrigatorios', v_campos_obrigatorios
    )::text, 'UTF8'), 'sha256'), 'hex');
    v_filha_id := md5('sucessora-replanejada:' || v_pai.id::text || ':' || v_filha_hash)::uuid;
    v_capacidade_hash := encode(extensions.digest(convert_to(jsonb_build_object(
      'investigacao_id', v_filha_id, 'sucessora_de_id', v_pai.id,
      'raiz_investigacao_id', v_pai.raiz_investigacao_id,
      'geracao', v_pai.geracao + 1, 'sucessao_pedido_hash', v_filha_hash,
      'sucessao_outbox_id', v_outbox.id, 'fluxo_tipo', v_tipo,
      'promocao_terminal_id', p_promocao_id, 'resolucao_hash', v_outbox.resolucao_hash,
      'promocao_origem_id', CASE WHEN v_tipo = 'corretiva_pos_gravacao' THEN p_promocao_id END,
      'draft_operacional_origem_id', CASE WHEN v_tipo = 'corretiva_pos_gravacao'
        THEN public.investigacao_uuid_texto_seguro(v_promocao.payload ->> 'source_draft_id') END,
      'destino_operacional_origem', CASE WHEN v_tipo = 'corretiva_pos_gravacao'
        THEN v_promocao.payload ->> 'target_table' END,
      'registro_operacional_origem_id', CASE WHEN v_tipo = 'corretiva_pos_gravacao'
        THEN v_outbox.registro_reconciliado_id END,
      'registro_operacional_origem_snapshot_ref', CASE WHEN v_tipo = 'corretiva_pos_gravacao'
        THEN v_snapshot_operacional_atual_ref END,
      'vinculo_operacional_estado', CASE WHEN v_tipo = 'corretiva_pos_gravacao' THEN 'confirmado' END,
      'source_draft_id', CASE WHEN v_tipo = 'pre_revisao' THEN v_pai.source_draft_id END,
      'source_draft_atualizado_em', v_source_draft_atualizado_em,
      'negocio_candidato_id', v_pai.negocio_candidato_id,
      'source_candidato_atualizado_em', v_source_candidato_atualizado_em,
      'negocio_candidato_ids', v_pai.negocio_candidato_ids,
      'source_candidatos_atualizados_em', v_source_candidatos,
      'fingerprint_base', v_fingerprint_base, 'plano_hash', v_plano_hash,
      'policy_version', v_item ->> 'policy_version',
      'policy_schema_hash', v_item ->> 'policy_schema_hash',
      'campos_obrigatorios', v_campos_obrigatorios
    )::text, 'UTF8'), 'sha256'), 'hex');
    INSERT INTO public.investigacao_autorizacoes_corretiva(
      txid, backend_pid, recurso, investigacao_id, operation_draft_id,
      pending_action_id, pedido_hash
    ) VALUES (txid_current(), pg_backend_pid(), 'criar_sucessora_complementar',
      v_filha_id, v_pai.id, p_promocao_id, v_capacidade_hash);
    INSERT INTO public.investigacoes_revisao (
      id, raiz_investigacao_id, sucessora_de_id, geracao, sucessao_pedido_hash,
      sucessao_outbox_id, chave_idempotencia, assunto_tipo, assunto_referencia,
      titulo, fluxo_tipo, promocao_origem_id, draft_operacional_origem_id,
      destino_operacional_origem, registro_operacional_origem_id,
      registro_operacional_origem_snapshot_ref, vinculo_operacional_estado,
      source_draft_id, source_draft_atualizado_em, negocio_candidato_id,
      source_candidato_atualizado_em, negocio_candidato_ids,
      source_candidatos_atualizados_em, fingerprint_base, plano_hash,
      plano_canonico, plano_tarefas, policy_version, policy_schema_hash,
      campos_obrigatorios, gatilho_tipo, prioridade, contexto_canonico,
      contexto_nome, origem_canal, origem_conversa_id, origem_mensagem_id,
      escopo, estado_execucao, resumo_sanitizado, criado_por
    ) VALUES (
      v_filha_id, v_pai.raiz_investigacao_id, v_pai.id, v_pai.geracao + 1,
      v_filha_hash, v_outbox.id, v_pai.chave_idempotencia || ':replanejada:' || v_filha_hash,
      v_pai.assunto_tipo, v_pai.assunto_referencia, v_pai.titulo, v_tipo,
      CASE WHEN v_tipo = 'corretiva_pos_gravacao' THEN p_promocao_id END,
      CASE WHEN v_tipo = 'corretiva_pos_gravacao' THEN public.investigacao_uuid_texto_seguro(
        v_promocao.payload ->> 'source_draft_id') END,
      CASE WHEN v_tipo = 'corretiva_pos_gravacao' THEN v_promocao.payload ->> 'target_table' END,
      CASE WHEN v_tipo = 'corretiva_pos_gravacao' THEN v_outbox.registro_reconciliado_id END,
      CASE WHEN v_tipo = 'corretiva_pos_gravacao' THEN v_snapshot_operacional_atual_ref END,
      CASE WHEN v_tipo = 'corretiva_pos_gravacao' THEN 'confirmado' END,
      CASE WHEN v_tipo = 'pre_revisao' THEN v_pai.source_draft_id END,
      CASE WHEN v_tipo = 'pre_revisao' THEN v_source_draft_atualizado_em END,
      v_pai.negocio_candidato_id, v_source_candidato_atualizado_em,
      v_pai.negocio_candidato_ids, v_source_candidatos, v_fingerprint_base,
      v_plano_hash, v_plano_canonico, v_plano_tarefas, v_item ->> 'policy_version',
      v_item ->> 'policy_schema_hash', v_campos_obrigatorios, 'outbox',
      v_pai.prioridade, v_pai.contexto_canonico, v_pai.contexto_nome,
      v_pai.origem_canal, v_pai.origem_conversa_id, v_pai.origem_mensagem_id,
      v_pai.escopo, 'pendente',
      CASE WHEN v_tipo = 'corretiva_pos_gravacao'
        THEN 'Rodada corretiva replanejada após confirmação da gravação operacional.'
        ELSE 'Rodada complementar replanejada após desfecho sem gravação.' END,
      p_ator
    );
    INSERT INTO public.investigacao_tarefas (
      investigacao_id, chave_idempotencia, plano_item_ref, adaptador,
      consulta_ref, consulta_schema_version, consulta_spec, consulta_canonico,
      consulta_hash, adaptador_version, estado_execucao, tentativas,
      proxima_execucao_em, fencing_token
    ) SELECT v_filha_id, 'replanejada:' || v_filha_id::text || ':' ||
               (plano_item.tarefa_json ->> 'plano_item_ref'),
             plano_item.tarefa_json ->> 'plano_item_ref',
             plano_item.tarefa_json ->> 'adaptador',
             plano_item.tarefa_json ->> 'consulta_ref',
             plano_item.tarefa_json ->> 'consulta_schema_version',
             plano_item.tarefa_json -> 'consulta_spec',
             plano_item.tarefa_json ->> 'consulta_canonico',
             plano_item.tarefa_json ->> 'consulta_hash',
             plano_item.tarefa_json ->> 'adaptador_version', 'pendente', 0,
             clock_timestamp(), 0
        FROM jsonb_array_elements(v_plano_tarefas)
          AS plano_item(tarefa_json);
    INSERT INTO public.investigacao_eventos (
      investigacao_id, chave_idempotencia, tipo, referencia_entidade, resumo_sanitizado
    ) VALUES (v_filha_id, 'sucessora-replanejada:' || v_filha_id::text,
      'investigacao_sucessora_replanejada', v_pai.id::text,
      'Nova rodada criada com plano atestado para o retrato atual das fontes.');
    v_consumo_hash := encode(extensions.digest(convert_to(jsonb_build_object(
      'investigacao_id', v_pai.id, 'promocao_id', p_promocao_id,
      'sucessora_id', v_filha_id, 'sucessao_pedido_hash', v_filha_hash,
      'novo_motivo', 'complementar_consumida'
    )::text, 'UTF8'), 'sha256'), 'hex');
    INSERT INTO public.investigacao_autorizacoes_corretiva(
      txid, backend_pid, recurso, investigacao_id, operation_draft_id,
      pending_action_id, pedido_hash
    ) VALUES (txid_current(), pg_backend_pid(), 'consumir_complementar',
      v_pai.id, v_pai.id, p_promocao_id, v_consumo_hash);
    UPDATE public.investigacoes_revisao
       SET obsolescencia_motivo = 'complementar_consumida', promocao_ativa_id = NULL
     WHERE id = v_pai.id
       AND estado_execucao = 'obsoleta'
       AND obsolescencia_motivo = 'complementar_promocao_ativa'
       AND promocao_ativa_id = p_promocao_id;
    IF NOT FOUND THEN RAISE EXCEPTION 'Predecessora mudou durante replanejamento'; END IF;
    v_criadas := v_criadas + 1;
  END LOOP;
  SELECT coalesce(jsonb_agg(jsonb_build_object(
           'predecessora_id', filha.sucessora_de_id, 'sucessora_id', filha.id,
           'sucessao_pedido_hash', filha.sucessao_pedido_hash
         ) ORDER BY filha.sucessora_de_id, filha.id), '[]'::jsonb), count(*)::integer
    INTO v_filhas, v_filhas_quantidade
    FROM public.investigacoes_revisao filha WHERE filha.sucessao_outbox_id = v_outbox.id;
  v_filhas_hash := encode(extensions.digest(convert_to(v_filhas::text, 'UTF8'), 'sha256'), 'hex');
  UPDATE public.investigacao_sucessoes_pendentes
     SET estado = 'concluida', filhas_quantidade = v_filhas_quantidade,
         filhas_mapa_hash = v_filhas_hash, concluida_em = clock_timestamp(),
         replanejamento_pedido_hash = v_replanejamento_pedido_hash,
         ultimo_erro_codigo = NULL, ultima_varredura_em = clock_timestamp(),
         atualizado_em = clock_timestamp()
   WHERE id = v_outbox.id AND estado = 'aguardando_planejamento'
     AND classe_resolvida IS NOT NULL;
  IF NOT FOUND THEN RAISE EXCEPTION 'Outbox mudou antes do mapa atômico'; END IF;
  RETURN jsonb_build_object('processada', true, 'repetida', false,
    'criadas', v_criadas, 'pedido_hash', v_outbox.pedido_hash,
    'sucessoras', v_filhas);
END;
$$;

CREATE OR REPLACE FUNCTION public.listar_sucessoes_promocao_terminal_pendentes(
  p_limite integer DEFAULT 20
)
RETURNS TABLE (
  promocao_id uuid,
  pedido_hash text
)
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
  IF coalesce(
       nullif(current_setting('role', true), 'none'), session_user
     ) IS DISTINCT FROM 'service_role'
     OR p_limite IS NULL OR p_limite < 1 OR p_limite > 100 THEN
    RAISE EXCEPTION 'Consulta do outbox inválida';
  END IF;
  RETURN QUERY
  SELECT outbox.promocao_id, outbox.pedido_hash
    FROM public.investigacao_sucessoes_pendentes outbox
   WHERE outbox.estado IN (
     'pendente', 'aguardando_reconciliacao', 'aguardando_planejamento'
   )
   ORDER BY outbox.criado_em, outbox.id
   LIMIT p_limite;
END;
$$;

CREATE OR REPLACE FUNCTION public.saude_investigacoes_proativas()
RETURNS jsonb
LANGUAGE plpgsql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_resultado jsonb;
BEGIN
  IF coalesce(
       nullif(current_setting('role', true), 'none'), session_user
     ) IS DISTINCT FROM 'service_role' THEN
    RAISE EXCEPTION 'Consulta de saúde não autorizada';
  END IF;
  SELECT jsonb_build_object(
    'outbox_pendente', count(*) FILTER (WHERE estado = 'pendente'),
    'outbox_aguardando_reconciliacao',
      count(*) FILTER (WHERE estado = 'aguardando_reconciliacao'),
    'outbox_aguardando_planejamento',
      count(*) FILTER (WHERE estado = 'aguardando_planejamento'),
    'outbox_falha_permanente',
      count(*) FILTER (WHERE estado = 'falha_permanente'),
    'outbox_pendentes_antigas', count(*) FILTER (
      WHERE estado IN (
        'pendente', 'aguardando_reconciliacao', 'aguardando_planejamento'
      )
        AND criado_em < clock_timestamp() - interval '15 minutes'
    ),
    'outbox_idade_maxima_segundos', coalesce(max(extract(epoch FROM
      (clock_timestamp() - criado_em))) FILTER (
        WHERE estado IN (
          'pendente', 'aguardando_reconciliacao', 'aguardando_planejamento'
        )
      ), 0),
    'tarefas_lease_expirada', (
      SELECT count(*) FROM public.investigacao_tarefas tarefa
       WHERE tarefa.estado_execucao = 'em_execucao'
         AND tarefa.lease_expira_em < clock_timestamp()
    ),
    'promocoes_lease_expirada', (
      SELECT count(*) FROM public.pending_actions promocao
       WHERE promocao.acao_tipo = 'promover_revisao_operacional'
         AND promocao.status = 'em_execucao'
         AND promocao.promocao_lease_expira_em < clock_timestamp()
    ),
    'capacidades_residuais', (
      (SELECT count(*) FROM public.investigacao_autorizacoes_corretiva)
      +
      (SELECT count(*) FROM public.investigacao_autorizacoes_promocao)
    ),
    'capacidades_orfas', (
      (SELECT count(*) FROM public.investigacao_autorizacoes_corretiva)
      +
      (SELECT count(*) FROM public.investigacao_autorizacoes_promocao)
    )
  ) INTO v_resultado
  FROM public.investigacao_sucessoes_pendentes;
  RETURN v_resultado;
END;
$$;

DROP TRIGGER IF EXISTS investigacoes_revisao_obsolescencia_protegida
  ON public.investigacoes_revisao;
CREATE TRIGGER investigacoes_revisao_obsolescencia_protegida
BEFORE UPDATE OF obsolescencia_motivo, promocao_ativa_id
ON public.investigacoes_revisao
FOR EACH ROW EXECUTE FUNCTION public.proteger_obsolescencia_investigacao();

-- Os dois campos abaixo são uma atestação interna da decisão humana. Nem o
-- executor service_role pode escrevê-los diretamente: somente a RPC fechada
-- de preparação abre, por transação, o marcador exato conferido pelo trigger.
CREATE OR REPLACE FUNCTION public.proteger_atestacao_decisao_investigacao()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_atestacao_hash text;
BEGIN
  IF NEW.decisao_draft_atualizado_em
       IS NOT DISTINCT FROM OLD.decisao_draft_atualizado_em
     AND NEW.decisao_preparacao_hash
       IS NOT DISTINCT FROM OLD.decisao_preparacao_hash THEN
    RETURN NEW;
  END IF;
  IF OLD.decisao_draft_atualizado_em IS NOT NULL
     OR OLD.decisao_preparacao_hash IS NOT NULL
     OR NEW.decisao_draft_atualizado_em IS NULL
     OR NEW.decisao_preparacao_hash IS NULL THEN
    RAISE EXCEPTION 'A atestação da decisão é imutável e não pode ser removida';
  END IF;
  v_atestacao_hash := encode(extensions.digest(convert_to(
    jsonb_build_object(
      'investigacao_id', NEW.id,
      'draft_atualizado_em', NEW.decisao_draft_atualizado_em,
      'preparacao_hash', NEW.decisao_preparacao_hash
    )::text, 'UTF8'
  ), 'sha256'), 'hex');
  DELETE FROM public.investigacao_autorizacoes_corretiva autorizacao
   WHERE autorizacao.txid = txid_current()
     AND autorizacao.backend_pid = pg_backend_pid()
     AND autorizacao.recurso = 'atestar_decisao'
     AND autorizacao.investigacao_id = NEW.id
     AND autorizacao.operation_draft_id = coalesce(
           NEW.anexado_draft_id, NEW.source_draft_id, NEW.id
         )
     AND autorizacao.pending_action_id = NEW.id
     AND autorizacao.pedido_hash = v_atestacao_hash;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'A atestação da decisão só pode ser gravada pela preparação protegida';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS investigacoes_revisao_atestacao_protegida
  ON public.investigacoes_revisao;
CREATE TRIGGER investigacoes_revisao_atestacao_protegida
BEFORE UPDATE OF decisao_draft_atualizado_em, decisao_preparacao_hash
ON public.investigacoes_revisao
FOR EACH ROW EXECUTE FUNCTION public.proteger_atestacao_decisao_investigacao();

CREATE OR REPLACE FUNCTION public.investigacao_snapshot_candidatos_atual(
  p_ids uuid[],
  p_snapshots jsonb
)
RETURNS boolean
LANGUAGE sql
STABLE
STRICT
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
  SELECT cardinality(p_ids) > 0
    AND (SELECT count(*) FROM public.negocios_candidatos candidato
          WHERE candidato.id = ANY (p_ids)) = cardinality(p_ids)
    AND NOT EXISTS (
      SELECT 1
        FROM public.negocios_candidatos candidato
       WHERE candidato.id = ANY (p_ids)
         AND (
           NOT (p_snapshots ? candidato.id::text)
           OR candidato.atualizado_em IS DISTINCT FROM
                public.investigacao_instante_texto_seguro(
                  p_snapshots ->> candidato.id::text
                )
         )
    );
$$;

CREATE OR REPLACE FUNCTION public.exigir_investigacao_anexada_para_promocao(
  p_draft_id uuid,
  p_preparacao_hash text DEFAULT NULL
)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_draft public.operation_drafts%ROWTYPE;
  v_id uuid;
  v_ids uuid[];
  v_principal text;
BEGIN
  IF p_draft_id IS NULL THEN
    RAISE EXCEPTION 'Promoção sem rascunho de origem identificado';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('investigacao-draft:' || p_draft_id::text, 0)
  );
  SELECT * INTO v_draft
    FROM public.operation_drafts
   WHERE id = p_draft_id
   FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Rascunho de origem da promoção não encontrado';
  END IF;
  IF v_draft.revisao_tipo = 'corretiva_pos_gravacao'
     OR EXISTS (
       SELECT 1 FROM public.investigacoes_revisao investigacao
        WHERE investigacao.fluxo_tipo = 'corretiva_pos_gravacao'
          AND (
            investigacao.source_draft_id = p_draft_id
            OR investigacao.anexado_draft_id = p_draft_id
          )
     ) THEN
    RAISE EXCEPTION 'Revisão corretiva pós-gravação não pode gerar outra promoção';
  END IF;
  v_ids := public.investigacao_ids_candidatos_rascunho(
    v_draft.inferencias, v_draft.dados_extraidos
  );
  FOREACH v_id IN ARRAY v_ids LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended('investigacao-candidato:' || v_id::text, 0)
    );
  END LOOP;
  PERFORM 1
    FROM public.negocios_candidatos candidato
   WHERE candidato.id = ANY (v_ids)
   ORDER BY candidato.id
   FOR SHARE;
  IF NOT EXISTS (
    SELECT 1
      FROM public.investigacoes_revisao investigacao
     WHERE investigacao.source_draft_id = p_draft_id
        OR investigacao.anexado_draft_id = p_draft_id
        OR investigacao.negocio_candidato_ids && v_ids
  ) THEN
    RAISE EXCEPTION 'Este rascunho ainda não possui investigação registrada';
  END IF;
  PERFORM 1
    FROM public.investigacoes_revisao investigacao
   WHERE (
     investigacao.source_draft_id = p_draft_id
     OR investigacao.negocio_candidato_ids && v_ids
   )
     AND investigacao.obsolescencia_motivo IS DISTINCT FROM
           'complementar_promocao_ativa'
     AND (
       investigacao.estado_execucao IN (
         'pendente', 'em_execucao', 'aguardando_retentativa'
       )
       OR (
         investigacao.estado_execucao = 'concluida'
         AND investigacao.anexado_em IS NULL
       )
     )
   ORDER BY investigacao.id;
  IF FOUND THEN
    RAISE EXCEPTION 'A investigação precisa terminar e ser anexada antes da promoção';
  END IF;
  -- Evidência que chegou depois da preparação pode ficar fora do caminho de
  -- uma promoção já autorizada. Se essa promoção terminar sem tentativa de
  -- gravação, porém, a evidência não pode desaparecer para sempre: uma nova
  -- rodada concluída, anexada e posterior à complementar é obrigatória antes
  -- de qualquer retry. Executado/erro pós-gravação seguem reconciliação, não
  -- uma segunda promoção do mesmo rascunho.
  IF EXISTS (
    SELECT 1
      FROM public.investigacoes_revisao complementar
      JOIN public.pending_actions promocao
        ON promocao.id = complementar.promocao_ativa_id
     WHERE complementar.obsolescencia_motivo = 'complementar_promocao_ativa'
       AND complementar.estado_execucao = 'obsoleta'
       AND promocao.status IN ('cancelado', 'rejeitado', 'expirado', 'erro')
       AND (
         complementar.source_draft_id = p_draft_id
         OR complementar.negocio_candidato_ids && v_ids
       )
       AND NOT EXISTS (
         SELECT 1
           FROM public.investigacoes_revisao sucessora
          WHERE sucessora.anexado_draft_id = p_draft_id
            AND sucessora.sucessora_de_id = complementar.id
            AND sucessora.raiz_investigacao_id
                  IS NOT DISTINCT FROM complementar.raiz_investigacao_id
            AND sucessora.estado_execucao = 'concluida'
            AND sucessora.anexado_em IS NOT NULL
            AND sucessora.obsolescencia_motivo IS DISTINCT FROM
                  'complementar_promocao_ativa'
            AND sucessora.anexado_draft_atualizado_em
                  IS NOT DISTINCT FROM v_draft.atualizado_em
            AND (
              cardinality(v_ids) = 0
              OR (
                sucessora.negocio_candidato_ids = v_ids
                AND public.investigacao_snapshot_candidatos_atual(
                      sucessora.negocio_candidato_ids,
                      sucessora.source_candidatos_atualizados_em
                    ) IS TRUE
              )
            )
       )
  ) THEN
    RAISE EXCEPTION 'Há evidência recebida após a promoção cancelada; conclua e anexe uma nova investigação antes de tentar novamente';
  END IF;
  -- Depois que um assunto entrou no fluxo investigado, ausência de bloqueio
  -- ativo não basta: deve existir atestado anexado ao retrato atual. Assim a
  -- obsolescência de uma rodada stale nunca abre uma janela para promoção.
  IF EXISTS (
    SELECT 1
      FROM public.investigacoes_revisao investigacao
     WHERE investigacao.source_draft_id = p_draft_id
        OR investigacao.anexado_draft_id = p_draft_id
        OR investigacao.negocio_candidato_ids && v_ids
  ) AND EXISTS (
    SELECT 1
      FROM public.investigacoes_revisao investigacao
     WHERE (
       investigacao.source_draft_id = p_draft_id
       OR investigacao.anexado_draft_id = p_draft_id
       OR investigacao.negocio_candidato_ids && v_ids
     )
       AND investigacao.obsolescencia_motivo IS DISTINCT FROM
             'complementar_promocao_ativa'
  ) AND NOT EXISTS (
    SELECT 1
      FROM public.investigacoes_revisao investigacao
     WHERE investigacao.anexado_draft_id = p_draft_id
       AND investigacao.anexado_em IS NOT NULL
       AND investigacao.estado_execucao = 'concluida'
       AND public.investigacao_evidencias_fontes_atuais(investigacao.id)
       AND (
         investigacao.anexado_draft_atualizado_em
               IS NOT DISTINCT FROM v_draft.atualizado_em
         OR (
           p_preparacao_hash ~ '^[0-9a-f]{64}$'
           AND investigacao.decisao_draft_atualizado_em
                 IS NOT DISTINCT FROM v_draft.atualizado_em
           AND investigacao.decisao_preparacao_hash
                 IS NOT DISTINCT FROM p_preparacao_hash
         )
       )
       AND (
         cardinality(v_ids) = 0
         OR (
           investigacao.negocio_candidato_ids = v_ids
           AND public.investigacao_snapshot_candidatos_atual(
                 investigacao.negocio_candidato_ids,
                 investigacao.source_candidatos_atualizados_em
               ) IS TRUE
         )
       )
  ) THEN
    RAISE EXCEPTION 'Os dados mudaram; conclua e anexe a investigação do retrato atual';
  END IF;
END;
$$;

CREATE OR REPLACE FUNCTION public.proteger_pending_action_permanente()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_draft_id uuid;
  v_pedido_hash text;
  v_investigacao_corretiva_id uuid;
  v_promocao_corretiva_id uuid;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.acao_tipo IS NOT DISTINCT FROM 'revisar_correcao_pos_gravacao'
       OR OLD.promocao_controle_version IS NOT DISTINCT FROM 'lease-v1' THEN
      RAISE EXCEPTION 'Ação controlada não pode ser apagada; use decisão auditada';
    END IF;
    RETURN OLD;
  END IF;
  IF NEW.acao_tipo IS NOT DISTINCT FROM 'revisar_correcao_pos_gravacao' THEN
    IF NEW.executavel
       OR NEW.entidade_tipo IS DISTINCT FROM 'operation_draft'
       OR NEW.entidade_id IS NULL
       OR (NEW.status IN (
         'aguardando_confirmacao', 'em_revisao', 'rejeitado', 'cancelado'
       )) IS NOT TRUE
       OR public.investigacao_json_possui_chave(NEW.payload, ARRAY[
         'target_table', 'proposed_record', 'idempotency',
         'idempotency_key', 'promocao_controle_version'
       ]) THEN
      RAISE EXCEPTION 'Revisão corretiva é exclusivamente humana e não executável';
    END IF;
    IF TG_OP = 'INSERT' THEN
      IF NEW.status IS DISTINCT FROM 'aguardando_confirmacao' THEN
        RAISE EXCEPTION 'Revisão corretiva deve nascer aguardando confirmação';
      END IF;
      v_pedido_hash := encode(extensions.digest(convert_to(
        jsonb_build_object(
          'draft_id', NEW.entidade_id,
          'action_id', NEW.id,
          'acao_tipo', NEW.acao_tipo,
          'entidade_tipo', NEW.entidade_tipo,
          'status', NEW.status,
          'executavel', NEW.executavel,
          'payload', NEW.payload
        )::text, 'UTF8'
      ), 'sha256'), 'hex');
      DELETE FROM public.investigacao_autorizacoes_corretiva autorizacao
       WHERE autorizacao.txid = txid_current()
         AND autorizacao.backend_pid = pg_backend_pid()
         AND autorizacao.recurso = 'inserir_acao'
         AND autorizacao.operation_draft_id = NEW.entidade_id
         AND autorizacao.pending_action_id = NEW.id
         AND autorizacao.pedido_hash = v_pedido_hash;
      IF NOT FOUND THEN
        RAISE EXCEPTION 'Ação corretiva só pode nascer no materializador canônico';
      END IF;
      RETURN NEW;
    END IF;
  END IF;
  IF TG_OP = 'UPDATE'
     AND (
       OLD.acao_tipo IS NOT DISTINCT FROM 'revisar_correcao_pos_gravacao'
       OR NEW.acao_tipo IS NOT DISTINCT FROM 'revisar_correcao_pos_gravacao'
     )
     AND (
       NEW.acao_tipo IS DISTINCT FROM OLD.acao_tipo
       OR NEW.executavel IS DISTINCT FROM OLD.executavel
       OR NEW.entidade_tipo IS DISTINCT FROM OLD.entidade_tipo
       OR NEW.entidade_id IS DISTINCT FROM OLD.entidade_id
  ) THEN
    RAISE EXCEPTION 'A identidade não executável da revisão corretiva é imutável';
  END IF;
  IF TG_OP = 'UPDATE'
     AND OLD.acao_tipo IS NOT DISTINCT FROM 'revisar_correcao_pos_gravacao' THEN
    IF OLD.status IN ('rejeitado', 'cancelado') THEN
      IF NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'Decisão corretiva encerrada é imutável';
      END IF;
      RETURN NEW;
    END IF;
    IF NEW.status IS DISTINCT FROM OLD.status
       AND (CASE OLD.status
         WHEN 'aguardando_confirmacao' THEN NEW.status IN (
           'em_revisao', 'rejeitado', 'cancelado'
         )
         WHEN 'em_revisao' THEN NEW.status IN (
           'aguardando_confirmacao', 'rejeitado', 'cancelado'
         )
         ELSE false
       END) IS NOT TRUE THEN
      RAISE EXCEPTION 'Transição da revisão corretiva não é permitida';
    END IF;
    SELECT draft.investigacao_origem_id, draft.promocao_origem_id
      INTO v_investigacao_corretiva_id, v_promocao_corretiva_id
      FROM public.operation_drafts draft
     WHERE draft.id = NEW.entidade_id
       AND draft.revisao_tipo = 'corretiva_pos_gravacao';
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Ação corretiva perdeu o vínculo com seu rascunho';
    END IF;
    v_pedido_hash := encode(extensions.digest(convert_to(
      jsonb_build_object(
        'action_id', NEW.id,
        'old_atualizado_em', OLD.atualizado_em,
        'new_status', NEW.status,
        'resumo', NEW.resumo,
        'payload', NEW.payload,
        'contexto_canonico', NEW.contexto_canonico,
        'contexto_nome', NEW.contexto_nome,
        'origem_canal', NEW.origem_canal,
        'origem_conversa_id', NEW.origem_conversa_id,
        'origem_mensagem_id', NEW.origem_mensagem_id,
        'escopo', NEW.escopo
      )::text, 'UTF8'
    ), 'sha256'), 'hex');
    DELETE FROM public.investigacao_autorizacoes_corretiva autorizacao
     WHERE autorizacao.txid = txid_current()
       AND autorizacao.backend_pid = pg_backend_pid()
       AND autorizacao.recurso = 'decidir_acao'
       AND autorizacao.investigacao_id = v_investigacao_corretiva_id
       AND autorizacao.operation_draft_id = NEW.entidade_id
       AND autorizacao.pending_action_id = NEW.id
       AND autorizacao.pedido_hash = v_pedido_hash;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Revisão corretiva só pode mudar pela decisão atômica';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.acao_tipo IS NOT DISTINCT FROM 'promover_revisao_operacional'
     AND NEW.status IS NULL THEN
    RAISE EXCEPTION 'Promoção operacional exige status explícito';
  END IF;
  IF TG_OP = 'UPDATE'
     AND OLD.acao_tipo IS NOT DISTINCT FROM 'promover_revisao_operacional'
     AND OLD.promocao_controle_version IS NOT DISTINCT FROM 'lease-v1'
     AND NEW.promocao_controle_version IS DISTINCT FROM 'lease-v1' THEN
    RAISE EXCEPTION 'O controle concorrente da promoção é imutável';
  END IF;
  IF NEW.acao_tipo IS NOT DISTINCT FROM 'promover_revisao_operacional'
     AND (
       NEW.promocao_controle_version IS NOT DISTINCT FROM 'lease-v1'
       OR (
         TG_OP = 'UPDATE'
         AND OLD.promocao_controle_version IS NOT DISTINCT FROM 'lease-v1'
       )
     ) THEN
    DELETE FROM public.investigacao_autorizacoes_promocao autorizacao
     WHERE autorizacao.txid = txid_current()
       AND autorizacao.backend_pid = pg_backend_pid()
       AND autorizacao.pending_action_id = NEW.id
       AND autorizacao.operacao = TG_OP
       AND autorizacao.status_anterior IS NOT DISTINCT FROM
             CASE WHEN TG_OP = 'UPDATE' THEN OLD.status ELSE NULL END
       AND autorizacao.status_novo IS NOT DISTINCT FROM NEW.status;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Promoção controlada só pode mudar pelas RPCs com CAS e fencing';
    END IF;
  END IF;
  IF TG_OP = 'UPDATE'
     AND (
       OLD.acao_tipo IS NOT DISTINCT FROM 'promover_revisao_operacional'
       OR NEW.acao_tipo IS NOT DISTINCT FROM 'promover_revisao_operacional'
     ) THEN
    IF NEW.acao_tipo IS DISTINCT FROM OLD.acao_tipo THEN
      RAISE EXCEPTION 'Uma ação comum não pode ser convertida em promoção, nem o inverso';
    END IF;
    IF NEW.entidade_tipo IS DISTINCT FROM OLD.entidade_tipo
       OR NEW.entidade_id IS DISTINCT FROM OLD.entidade_id
       OR NEW.origem_canal IS DISTINCT FROM OLD.origem_canal
       OR NEW.origem_conversa_id IS DISTINCT FROM OLD.origem_conversa_id
       OR NEW.origem_mensagem_id IS DISTINCT FROM OLD.origem_mensagem_id
       OR NEW.payload -> 'source_draft_id'
            IS DISTINCT FROM OLD.payload -> 'source_draft_id'
       OR NEW.payload -> 'source_pending_action_id'
            IS DISTINCT FROM OLD.payload -> 'source_pending_action_id'
       OR NEW.payload -> 'target_table'
            IS DISTINCT FROM OLD.payload -> 'target_table'
       OR NEW.payload -> 'proposed_record'
            IS DISTINCT FROM OLD.payload -> 'proposed_record'
       OR NEW.payload -> 'origem_canal'
            IS DISTINCT FROM OLD.payload -> 'origem_canal'
       OR NEW.payload -> 'origem_conversa_id'
            IS DISTINCT FROM OLD.payload -> 'origem_conversa_id'
       OR NEW.payload -> 'origem_mensagem_id'
            IS DISTINCT FROM OLD.payload -> 'origem_mensagem_id' THEN
      RAISE EXCEPTION 'A origem e o conteúdo operacional da promoção são imutáveis';
    END IF;
  END IF;
  -- O selo terminal precede qualquer retorno de cancelamento/rejeição. Nem
  -- o papel executor pode reescrever payload, resultado ou auditoria depois
  -- do desfecho; reconciliação é um novo evento, nunca muta esta linha.
  IF TG_OP = 'UPDATE'
     AND OLD.acao_tipo IS NOT DISTINCT FROM 'promover_revisao_operacional'
     AND (OLD.status = ANY(ARRAY[
       'executado', 'erro_pos_gravacao', 'erro',
       'cancelado', 'rejeitado', 'expirado'
     ])) IS TRUE THEN
    IF NEW IS DISTINCT FROM OLD THEN
      RAISE EXCEPTION 'Promoção encerrada é imutável; reconciliações usam novo evento auditado';
    END IF;
    RETURN NEW;
  END IF;
  -- Grafo monotônico da promoção. A criação começa sempre aguardando a
  -- confirmação; `preparada` só é tolerado como legado anterior ao gate. Uma
  -- ação assumida nunca pode regressar e ser executada pela segunda vez.
  IF TG_OP = 'INSERT'
     AND NEW.acao_tipo IS NOT DISTINCT FROM 'promover_revisao_operacional'
     AND NEW.status IS DISTINCT FROM 'aguardando_confirmacao' THEN
    RAISE EXCEPTION 'Nova promoção deve começar aguardando confirmação';
  END IF;
  IF TG_OP = 'UPDATE'
     AND OLD.acao_tipo IS NOT DISTINCT FROM 'promover_revisao_operacional'
     AND NEW.status IS DISTINCT FROM OLD.status
     AND (CASE OLD.status
       WHEN 'preparada' THEN NEW.status = ANY(ARRAY[
         'aguardando_confirmacao', 'cancelado', 'rejeitado', 'expirado'
       ])
       WHEN 'aguardando_confirmacao' THEN NEW.status = ANY(ARRAY[
         'aprovado_confinex', 'em_execucao',
         'cancelado', 'rejeitado', 'expirado'
       ])
       WHEN 'aprovado_confinex' THEN NEW.status = ANY(ARRAY[
         'em_execucao', 'cancelado', 'rejeitado', 'expirado'
       ])
       WHEN 'em_execucao' THEN NEW.status = ANY(ARRAY[
         'executado', 'erro_pos_gravacao', 'erro'
       ])
       ELSE false
     END) IS NOT TRUE THEN
    RAISE EXCEPTION 'Transição de estado da promoção não é permitida';
  END IF;
  -- Cancelar, rejeitar ou expirar uma promoção reduz capacidade operacional.
  -- Essas transições precisam continuar possíveis mesmo quando o atestado do
  -- rascunho ficou obsoleto; a imutabilidade da origem/payload acima continua
  -- valendo. Estados que podem executar seguem obrigatoriamente pelo gate.
  IF TG_OP = 'UPDATE'
     AND OLD.acao_tipo IS NOT DISTINCT FROM 'promover_revisao_operacional'
     AND (NEW.status = ANY(ARRAY[
       'cancelado', 'rejeitado', 'expirado'
     ])) IS TRUE THEN
    IF (OLD.status = ANY(ARRAY[
         'em_execucao', 'executado', 'erro_pos_gravacao',
         'cancelado', 'rejeitado', 'expirado'
       ])) IS TRUE
       AND NEW.status IS DISTINCT FROM OLD.status THEN
      RAISE EXCEPTION 'Promoção em execução ou encerrada não admite outra decisão terminal';
    END IF;
    RETURN NEW;
  END IF;
  -- Depois que o executor assumiu a ação, o rascunho é marcado realizado
  -- antes da atualização final da pendência. Nessa fase o atestado naturalmente
  -- ficou anterior, mas não pode impedir o registro do desfecho real. Só são
  -- aceitos os dois estados pós-gravação, com ID e destino coerentes.
  IF TG_OP = 'UPDATE'
     AND OLD.acao_tipo IS NOT DISTINCT FROM 'promover_revisao_operacional'
     AND OLD.status IS NOT DISTINCT FROM 'em_execucao'
     AND (NEW.status = ANY(ARRAY[
       'executado', 'erro_pos_gravacao'
     ])) IS TRUE THEN
    IF NEW.resultado ->> 'target_table'
         IS DISTINCT FROM NEW.payload ->> 'target_table' THEN
      RAISE EXCEPTION 'Desfecho pós-gravação diverge do destino autorizado';
    END IF;
    IF public.investigacao_uuid_texto_seguro(
         nullif(NEW.resultado ->> 'target_record_id', '')
       ) IS NOT NULL THEN
      IF coalesce(
           (NEW.resultado ->> 'promovido_para_operacional')::boolean, false
         ) IS NOT TRUE THEN
        RAISE EXCEPTION 'Registro identificado deve constar como promovido';
      END IF;
    ELSIF NEW.status = 'erro_pos_gravacao' THEN
      IF coalesce(
           (NEW.resultado ->> 'promovido_para_operacional')::boolean, true
         ) IS NOT FALSE
         OR coalesce(
           (NEW.resultado ->> 'requer_reconciliacao')::boolean, false
         ) IS NOT TRUE
         OR NEW.resultado ->> 'estado_idempotencia' IS DISTINCT FROM 'uncertain'
         OR NEW.payload #>> '{idempotency,state}' IS DISTINCT FROM 'uncertain'
         OR NEW.payload #>> '{idempotency,key}'
              IS DISTINCT FROM NEW.resultado ->> 'idempotency_key'
         OR (
           NEW.payload ->> 'target_table' = 'compras'
           AND NEW.resultado ->> 'idempotency_key'
                 IS DISTINCT FROM 'promocao_operacional:' || NEW.id::text
         )
         OR (
           NEW.payload ->> 'target_table' <> 'compras'
           AND NEW.resultado -> 'idempotency_key' <> 'null'::jsonb
         ) THEN
        RAISE EXCEPTION 'Resultado incerto exige reconciliação e chave coerente';
      END IF;
    ELSE
      RAISE EXCEPTION 'Execução concluída exige o registro operacional identificado';
    END IF;
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE'
     AND NEW.acao_tipo IS NOT DISTINCT FROM 'promover_revisao_operacional'
     AND (NEW.status = ANY(ARRAY[
       'executado', 'erro_pos_gravacao'
     ])) IS TRUE
     AND OLD.status IS DISTINCT FROM 'em_execucao' THEN
    RAISE EXCEPTION 'Promoção só pode encerrar depois de ser assumida pelo executor';
  END IF;
  IF NEW.acao_tipo IS NOT DISTINCT FROM 'promover_revisao_operacional'
     AND (NEW.status = ANY(ARRAY[
       'preparada', 'aguardando_confirmacao', 'aprovado_confinex',
       'em_execucao', 'executado', 'erro_pos_gravacao'
     ])) IS TRUE THEN
    IF jsonb_typeof(NEW.payload -> 'source_draft_id')
         IS DISTINCT FROM 'string'
       OR nullif(NEW.payload ->> 'source_draft_id', '') IS NULL THEN
      RAISE EXCEPTION 'Promoção ativa exige um rascunho de origem explícito';
    END IF;
    v_draft_id := public.investigacao_uuid_texto_seguro(
      NEW.payload ->> 'source_draft_id'
    );
    IF v_draft_id IS NULL OR NEW.entidade_id IS DISTINCT FROM v_draft_id THEN
      RAISE EXCEPTION 'Promoção e rascunho de origem não são coerentes';
    END IF;
    IF EXISTS (
      SELECT 1 FROM public.operation_drafts draft
       WHERE draft.id = v_draft_id
         AND draft.revisao_tipo = 'corretiva_pos_gravacao'
    ) THEN
      RAISE EXCEPTION 'Revisão corretiva pós-gravação nunca pode ser promovida';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

-- Gate removível da feature: apenas consulta o atestado vigente. Toda a
-- imutabilidade de promoções lease-v1 e do subgrafo corretivo permanece no
-- gatilho anterior, inclusive durante sombra e depois de um rollback.
CREATE OR REPLACE FUNCTION public.bloquear_pending_action_com_investigacao()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_draft_id uuid;
BEGIN
  IF NEW.acao_tipo IS NOT DISTINCT FROM 'revisar_correcao_pos_gravacao' THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'INSERT'
     AND NEW.acao_tipo IS NOT DISTINCT FROM 'promover_revisao_operacional'
     AND NEW.promocao_controle_version IS DISTINCT FROM 'lease-v1' THEN
    RAISE EXCEPTION 'Nova promoção exige controle concorrente lease-v1';
  END IF;
  IF NEW.acao_tipo IS NOT DISTINCT FROM 'promover_revisao_operacional'
     AND (NEW.status = ANY(ARRAY[
       'preparada', 'aguardando_confirmacao', 'aprovado_confinex',
       'em_execucao', 'executado', 'erro_pos_gravacao'
     ])) IS TRUE THEN
    v_draft_id := public.investigacao_uuid_texto_seguro(
      NEW.payload ->> 'source_draft_id'
    );
    IF v_draft_id IS NULL OR NEW.entidade_id IS DISTINCT FROM v_draft_id THEN
      RAISE EXCEPTION 'Promoção e rascunho de origem não são coerentes';
    END IF;
    IF EXISTS (
      SELECT 1 FROM public.operation_drafts draft
       WHERE draft.id = v_draft_id
         AND draft.revisao_tipo = 'corretiva_pos_gravacao'
    ) THEN
      RAISE EXCEPTION 'Revisão corretiva pós-gravação nunca pode ser promovida';
    END IF;
    PERFORM public.exigir_investigacao_anexada_para_promocao(
      v_draft_id,
      nullif(NEW.promocao_preparacao_hash, '')
    );
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.proteger_draft_corretivo_permanente()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_pedido_hash text;
  v_acao public.pending_actions%ROWTYPE;
BEGIN
  IF TG_OP = 'DELETE' THEN
    IF OLD.revisao_tipo IS NOT DISTINCT FROM 'corretiva_pos_gravacao' THEN
      RAISE EXCEPTION 'Rascunho corretivo não pode ser apagado; use decisão auditada';
    END IF;
    RETURN OLD;
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.revisao_tipo IS DISTINCT FROM 'corretiva_pos_gravacao' THEN
      RETURN NEW;
    END IF;
    IF NEW.investigacao_origem_id IS NULL
       OR NEW.promocao_origem_id IS NULL
       OR NEW.pending_action_id IS NULL
       OR NEW.tipo_operacao IS DISTINCT FROM 'correcao_pos_gravacao'
       OR NEW.entidade_final_tipo IS DISTINCT FROM 'correcao_pos_gravacao'
       OR NEW.entidade_final_id IS NOT NULL
       OR NEW.status IS DISTINCT FROM 'em_revisao'
       OR NEW.dados_extraidos ->> 'status_confirmacao' = 'promocao_preparada'
       OR NEW.inferencias ->> 'status_confirmacao' = 'promocao_preparada' THEN
      RAISE EXCEPTION 'Rascunho corretivo possui marcador executável ou origem incompleta';
    END IF;
    v_pedido_hash := encode(extensions.digest(convert_to(
      jsonb_build_object(
        'investigacao_id', NEW.investigacao_origem_id,
        'promocao_origem_id', NEW.promocao_origem_id,
        'draft_id', NEW.id,
        'pending_action_id', NEW.pending_action_id,
        'revisao_tipo', NEW.revisao_tipo,
        'tipo_operacao', NEW.tipo_operacao,
        'entidade_final_tipo', NEW.entidade_final_tipo,
        'status', NEW.status
      )::text, 'UTF8'
    ), 'sha256'), 'hex');
    DELETE FROM public.investigacao_autorizacoes_corretiva autorizacao
     WHERE autorizacao.txid = txid_current()
       AND autorizacao.backend_pid = pg_backend_pid()
       AND autorizacao.recurso = 'inserir_draft'
       AND autorizacao.investigacao_id = NEW.investigacao_origem_id
       AND autorizacao.operation_draft_id = NEW.id
       AND autorizacao.pending_action_id = NEW.pending_action_id
       AND autorizacao.pedido_hash = v_pedido_hash;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Rascunho corretivo só pode nascer no materializador canônico';
    END IF;
    SELECT * INTO v_acao FROM public.pending_actions
     WHERE id = NEW.pending_action_id;
    IF NOT FOUND
       OR v_acao.acao_tipo IS DISTINCT FROM 'revisar_correcao_pos_gravacao'
       OR v_acao.entidade_tipo IS DISTINCT FROM 'operation_draft'
       OR v_acao.entidade_id IS DISTINCT FROM NEW.id
       OR v_acao.executavel
       OR NOT EXISTS (
         SELECT 1 FROM public.investigacoes_revisao investigacao
          WHERE investigacao.id = NEW.investigacao_origem_id
            AND investigacao.fluxo_tipo = 'corretiva_pos_gravacao'
            AND investigacao.promocao_origem_id = NEW.promocao_origem_id
       ) THEN
      RAISE EXCEPTION 'Rascunho e ação corretiva não possuem vínculo exato';
    END IF;
    RETURN NEW;
  END IF;
  IF OLD.revisao_tipo IS DISTINCT FROM 'corretiva_pos_gravacao'
     AND NEW.revisao_tipo IS DISTINCT FROM 'corretiva_pos_gravacao' THEN
    RETURN NEW;
  END IF;
  IF NEW.revisao_tipo IS DISTINCT FROM OLD.revisao_tipo
     OR NEW.investigacao_origem_id IS DISTINCT FROM OLD.investigacao_origem_id
     OR NEW.promocao_origem_id IS DISTINCT FROM OLD.promocao_origem_id
     OR NEW.tipo_operacao IS DISTINCT FROM OLD.tipo_operacao
     OR NEW.entidade_final_tipo IS DISTINCT FROM OLD.entidade_final_tipo
     OR NEW.entidade_final_id IS DISTINCT FROM OLD.entidade_final_id
     OR NEW.pending_action_id IS DISTINCT FROM OLD.pending_action_id
     OR NEW.status = 'realizado'
     OR NEW.dados_extraidos ->> 'status_confirmacao' = 'promocao_preparada'
     OR NEW.inferencias ->> 'status_confirmacao' = 'promocao_preparada'
     OR (NEW.status IN (
       'em_revisao', 'aguardando_confirmacao', 'cancelado'
     )) IS NOT TRUE THEN
    RAISE EXCEPTION 'Rascunho corretivo não admite capacidade operacional ou troca de origem';
  END IF;
  IF OLD.status = 'cancelado' THEN
    IF NEW IS DISTINCT FROM OLD THEN
      RAISE EXCEPTION 'Rascunho corretivo encerrado é imutável';
    END IF;
    RETURN NEW;
  END IF;
  v_pedido_hash := encode(extensions.digest(convert_to(
    jsonb_build_object(
      'draft_id', NEW.id,
      'old_atualizado_em', OLD.atualizado_em,
      'new_status', NEW.status,
      'codigo_sugerido', NEW.codigo_sugerido,
      'dados_extraidos', NEW.dados_extraidos,
      'campos_pendentes', to_jsonb(NEW.campos_pendentes),
      'inferencias', NEW.inferencias,
      'contexto_canonico', NEW.contexto_canonico,
      'contexto_nome', NEW.contexto_nome,
      'origem_canal', NEW.origem_canal,
      'origem_conversa_id', NEW.origem_conversa_id,
      'origem_mensagem_id', NEW.origem_mensagem_id,
      'escopo', NEW.escopo
    )::text, 'UTF8'
  ), 'sha256'), 'hex');
  DELETE FROM public.investigacao_autorizacoes_corretiva autorizacao
   WHERE autorizacao.txid = txid_current()
     AND autorizacao.backend_pid = pg_backend_pid()
     AND autorizacao.recurso IN ('decidir_draft', 'anexar_draft_corretivo')
     AND autorizacao.investigacao_id = NEW.investigacao_origem_id
     AND autorizacao.operation_draft_id = NEW.id
     AND autorizacao.pending_action_id = NEW.pending_action_id
     AND autorizacao.pedido_hash = v_pedido_hash;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Rascunho corretivo só pode mudar pelo anexo ou decisão atômica autorizados';
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.bloquear_draft_com_investigacao()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_id uuid;
  v_ids uuid[];
  v_principal text;
  v_prov_old jsonb;
  v_prov_new jsonb;
  v_pedido_hash text;
  v_acao_corretiva public.pending_actions%ROWTYPE;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RETURN OLD;
  END IF;
  IF TG_OP = 'INSERT' THEN
    RETURN NEW;
  END IF;
  IF OLD.revisao_tipo IS NOT DISTINCT FROM 'corretiva_pos_gravacao'
     OR NEW.revisao_tipo IS NOT DISTINCT FROM 'corretiva_pos_gravacao' THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'DELETE' THEN
    IF OLD.revisao_tipo IS NOT DISTINCT FROM 'corretiva_pos_gravacao' THEN
      RAISE EXCEPTION 'Rascunho corretivo não pode ser apagado; use decisão auditada';
    END IF;
    RETURN OLD;
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF NEW.revisao_tipo IS DISTINCT FROM 'corretiva_pos_gravacao' THEN
      RETURN NEW;
    END IF;
    IF NEW.investigacao_origem_id IS NULL
       OR NEW.promocao_origem_id IS NULL
       OR NEW.pending_action_id IS NULL
       OR NEW.tipo_operacao IS DISTINCT FROM 'correcao_pos_gravacao'
       OR NEW.entidade_final_tipo IS DISTINCT FROM 'correcao_pos_gravacao'
       OR NEW.entidade_final_id IS NOT NULL
       OR NEW.status IS DISTINCT FROM 'em_revisao'
       OR NEW.dados_extraidos ->> 'status_confirmacao' = 'promocao_preparada'
       OR NEW.inferencias ->> 'status_confirmacao' = 'promocao_preparada' THEN
      RAISE EXCEPTION 'Rascunho corretivo possui marcador executável ou origem incompleta';
    END IF;
    v_pedido_hash := encode(extensions.digest(convert_to(
      jsonb_build_object(
        'investigacao_id', NEW.investigacao_origem_id,
        'promocao_origem_id', NEW.promocao_origem_id,
        'draft_id', NEW.id,
        'pending_action_id', NEW.pending_action_id,
        'revisao_tipo', NEW.revisao_tipo,
        'tipo_operacao', NEW.tipo_operacao,
        'entidade_final_tipo', NEW.entidade_final_tipo,
        'status', NEW.status
      )::text, 'UTF8'
    ), 'sha256'), 'hex');
    DELETE FROM public.investigacao_autorizacoes_corretiva autorizacao
     WHERE autorizacao.txid = txid_current()
       AND autorizacao.backend_pid = pg_backend_pid()
       AND autorizacao.recurso = 'inserir_draft'
       AND autorizacao.investigacao_id = NEW.investigacao_origem_id
       AND autorizacao.operation_draft_id = NEW.id
       AND autorizacao.pending_action_id = NEW.pending_action_id
       AND autorizacao.pedido_hash = v_pedido_hash;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Rascunho corretivo só pode nascer no materializador canônico';
    END IF;
    SELECT * INTO v_acao_corretiva
      FROM public.pending_actions
     WHERE id = NEW.pending_action_id;
    IF NOT FOUND
       OR v_acao_corretiva.acao_tipo
            IS DISTINCT FROM 'revisar_correcao_pos_gravacao'
       OR v_acao_corretiva.entidade_tipo IS DISTINCT FROM 'operation_draft'
       OR v_acao_corretiva.entidade_id IS DISTINCT FROM NEW.id
       OR v_acao_corretiva.executavel THEN
      RAISE EXCEPTION 'Rascunho e ação corretiva não possuem vínculo exato';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM public.investigacoes_revisao investigacao
       WHERE investigacao.id = NEW.investigacao_origem_id
         AND investigacao.fluxo_tipo = 'corretiva_pos_gravacao'
         AND investigacao.promocao_origem_id = NEW.promocao_origem_id
    ) THEN
      RAISE EXCEPTION 'Rascunho corretivo não corresponde à investigação selada';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.revisao_tipo IS DISTINCT FROM OLD.revisao_tipo
     OR NEW.investigacao_origem_id IS DISTINCT FROM OLD.investigacao_origem_id
     OR NEW.promocao_origem_id IS DISTINCT FROM OLD.promocao_origem_id
     OR (
       OLD.revisao_tipo = 'corretiva_pos_gravacao'
       AND (
         NEW.tipo_operacao IS DISTINCT FROM OLD.tipo_operacao
         OR NEW.entidade_final_tipo IS DISTINCT FROM OLD.entidade_final_tipo
         OR NEW.entidade_final_id IS DISTINCT FROM OLD.entidade_final_id
         OR NEW.pending_action_id IS DISTINCT FROM OLD.pending_action_id
       )
     ) THEN
    RAISE EXCEPTION 'O tipo e a origem do rascunho de revisão são imutáveis';
  END IF;
  IF OLD.revisao_tipo = 'corretiva_pos_gravacao'
     AND (
       NEW.status = 'realizado'
       OR NEW.entidade_final_id IS NOT NULL
       OR NEW.dados_extraidos ->> 'status_confirmacao' = 'promocao_preparada'
       OR NEW.inferencias ->> 'status_confirmacao' = 'promocao_preparada'
     ) THEN
    RAISE EXCEPTION 'Rascunho corretivo não cria nem prepara registro operacional';
  END IF;
  IF OLD.revisao_tipo = 'corretiva_pos_gravacao' THEN
    IF OLD.status = 'cancelado' THEN
      IF NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'Rascunho corretivo encerrado é imutável';
      END IF;
      RETURN NEW;
    END IF;
    IF (NEW.status IN (
      'em_revisao', 'aguardando_confirmacao', 'cancelado'
    )) IS NOT TRUE THEN
      RAISE EXCEPTION 'Estado do rascunho corretivo não é permitido';
    END IF;
    v_pedido_hash := encode(extensions.digest(convert_to(
      jsonb_build_object(
        'draft_id', NEW.id,
        'old_atualizado_em', OLD.atualizado_em,
        'new_status', NEW.status,
        'codigo_sugerido', NEW.codigo_sugerido,
        'dados_extraidos', NEW.dados_extraidos,
        'campos_pendentes', to_jsonb(NEW.campos_pendentes),
        'inferencias', NEW.inferencias,
        'contexto_canonico', NEW.contexto_canonico,
        'contexto_nome', NEW.contexto_nome,
        'origem_canal', NEW.origem_canal,
        'origem_conversa_id', NEW.origem_conversa_id,
        'origem_mensagem_id', NEW.origem_mensagem_id,
        'escopo', NEW.escopo
      )::text, 'UTF8'
    ), 'sha256'), 'hex');
    DELETE FROM public.investigacao_autorizacoes_corretiva autorizacao
     WHERE autorizacao.txid = txid_current()
       AND autorizacao.backend_pid = pg_backend_pid()
       AND autorizacao.recurso = 'decidir_draft'
       AND autorizacao.investigacao_id = NEW.investigacao_origem_id
       AND autorizacao.operation_draft_id = NEW.id
       AND autorizacao.pending_action_id = NEW.pending_action_id
       AND autorizacao.pedido_hash = v_pedido_hash;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Rascunho corretivo só pode mudar pela decisão atômica';
    END IF;
    RETURN NEW;
  END IF;
  v_prov_old := jsonb_strip_nulls(jsonb_build_object(
    'inferencias', jsonb_strip_nulls(jsonb_build_object(
      'staging_candidato_id', OLD.inferencias -> 'staging_candidato_id',
      'staging_candidato_ids', OLD.inferencias -> 'staging_candidato_ids',
      'staging_candidatos_atualizados_em',
        OLD.inferencias -> 'staging_candidatos_atualizados_em',
      'fingerprint_grupo', OLD.inferencias -> 'fingerprint_grupo',
      'fingerprint_base', OLD.inferencias -> 'fingerprint_base'
    )),
    'dados_extraidos', jsonb_strip_nulls(jsonb_build_object(
      'staging_candidato_id', OLD.dados_extraidos -> 'staging_candidato_id',
      'staging_candidato_ids', OLD.dados_extraidos -> 'staging_candidato_ids',
      'staging_candidatos_atualizados_em',
        OLD.dados_extraidos -> 'staging_candidatos_atualizados_em',
      'fingerprint_grupo', OLD.dados_extraidos -> 'fingerprint_grupo',
      'fingerprint_base', OLD.dados_extraidos -> 'fingerprint_base'
    ))
  ));
  v_prov_new := jsonb_strip_nulls(jsonb_build_object(
    'inferencias', jsonb_strip_nulls(jsonb_build_object(
      'staging_candidato_id', NEW.inferencias -> 'staging_candidato_id',
      'staging_candidato_ids', NEW.inferencias -> 'staging_candidato_ids',
      'staging_candidatos_atualizados_em',
        NEW.inferencias -> 'staging_candidatos_atualizados_em',
      'fingerprint_grupo', NEW.inferencias -> 'fingerprint_grupo',
      'fingerprint_base', NEW.inferencias -> 'fingerprint_base'
    )),
    'dados_extraidos', jsonb_strip_nulls(jsonb_build_object(
      'staging_candidato_id', NEW.dados_extraidos -> 'staging_candidato_id',
      'staging_candidato_ids', NEW.dados_extraidos -> 'staging_candidato_ids',
      'staging_candidatos_atualizados_em',
        NEW.dados_extraidos -> 'staging_candidatos_atualizados_em',
      'fingerprint_grupo', NEW.dados_extraidos -> 'fingerprint_grupo',
      'fingerprint_base', NEW.dados_extraidos -> 'fingerprint_base'
    ))
  ));
  IF v_prov_old <> '{}'::jsonb AND v_prov_new IS DISTINCT FROM v_prov_old THEN
    RAISE EXCEPTION 'A proveniência de staging do rascunho é imutável';
  END IF;
  IF NEW.dados_extraidos ->> 'status_confirmacao' = 'promocao_preparada'
     AND coalesce(OLD.dados_extraidos ->> 'status_confirmacao', '')
       IS DISTINCT FROM 'promocao_preparada' THEN
    -- O AFTER UPDATE observa a linha final depois de todos os gatilhos BEFORE.
    -- Nunca espere por advisory aqui: vínculo/anexo seguem advisory→linha. O
    -- try-lock falha fechado e elimina a espera circular linha→advisory.
    IF NOT pg_catalog.pg_try_advisory_xact_lock(
      pg_catalog.hashtextextended('investigacao-draft:' || NEW.id::text, 0)
    ) THEN
      RAISE EXCEPTION 'A revisão está sendo conferida; tente preparar novamente';
    END IF;
    SELECT coalesce(array_agg(DISTINCT id ORDER BY id), '{}'::uuid[])
      INTO v_ids
      FROM unnest(
        public.investigacao_ids_candidatos_rascunho(
          NEW.inferencias, NEW.dados_extraidos
        ) || public.investigacao_ids_candidatos_rascunho(
          OLD.inferencias, OLD.dados_extraidos
        )
      ) AS id;
    FOREACH v_id IN ARRAY v_ids LOOP
      IF NOT pg_catalog.pg_try_advisory_xact_lock(
        pg_catalog.hashtextextended('investigacao-candidato:' || v_id::text, 0)
      ) THEN
        RAISE EXCEPTION 'As evidências estão sendo conferidas; tente preparar novamente';
      END IF;
    END LOOP;
    PERFORM 1
      FROM public.investigacoes_revisao investigacao
     WHERE (
       investigacao.source_draft_id = NEW.id
       OR investigacao.negocio_candidato_ids && v_ids
     )
       AND (
         investigacao.estado_execucao IN (
           'pendente', 'em_execucao', 'aguardando_retentativa'
         )
         OR (
           investigacao.estado_execucao = 'concluida'
           AND investigacao.anexado_em IS NULL
         )
       );
    IF FOUND THEN
      RAISE EXCEPTION 'A investigação precisa terminar e ser anexada antes da promoção';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE FUNCTION public.decidir_revisao_corretiva(
  p_operation_draft_id uuid,
  p_pending_action_id uuid,
  p_pedido jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_draft public.operation_drafts%ROWTYPE;
  v_acao public.pending_actions%ROWTYPE;
  v_modo text;
  v_status_draft text;
  v_status_acao text;
  v_evento_tipo text;
  v_motivo text;
  v_ator text := 'mediador_investigacoes';
  v_agora timestamptz := clock_timestamp();
  v_contexto jsonb;
  v_dados jsonb;
  v_inferencias jsonb;
  v_campos text[];
  v_payload jsonb;
  v_pedido_hash text;
  v_evento_id uuid;
  v_hash_draft text;
  v_hash_acao text;
BEGIN
  IF coalesce(nullif(current_setting('role', true), 'none'), session_user)
       IS DISTINCT FROM 'service_role'
     OR p_operation_draft_id IS NULL
     OR p_pending_action_id IS NULL
     OR p_pedido IS NULL
     OR jsonb_typeof(p_pedido) <> 'object'
     OR octet_length(p_pedido::text) > 262144
     OR p_pedido - ARRAY[
       'versao', 'modo', 'draft_atualizado_em', 'action_atualizado_em',
       'dados_extraidos', 'inferencias', 'campos_pendentes',
       'codigo_sugerido', 'resumo', 'contexto', 'motivo'
     ] <> '{}'::jsonb
     OR public.investigacao_jsonb_objeto_tamanho(p_pedido) <> 11
     OR p_pedido ->> 'versao' IS DISTINCT FROM '1'
     OR NOT public.investigacao_json_sanitizado(p_pedido) THEN
    RAISE EXCEPTION 'Pedido de decisão corretiva inválido';
  END IF;
  v_modo := p_pedido ->> 'modo';
  v_motivo := btrim(coalesce(p_pedido ->> 'motivo', ''));
  v_contexto := p_pedido -> 'contexto';
  v_dados := p_pedido -> 'dados_extraidos';
  v_inferencias := p_pedido -> 'inferencias';
  IF v_modo IS NULL
     OR v_modo NOT IN ('salvar', 'voltar_confirmacao', 'rejeitar', 'cancelar')
     OR (v_modo IN ('rejeitar', 'cancelar') AND v_motivo = '')
     OR octet_length(v_motivo) > 1000
     OR (v_motivo <> '' AND NOT public.investigacao_texto_publico_sanitizado(v_motivo))
     OR jsonb_typeof(v_contexto) <> 'object'
     OR v_contexto - ARRAY[
       'contexto_canonico', 'contexto_nome', 'origem_canal',
       'origem_conversa_id', 'origem_mensagem_id', 'escopo'
     ] <> '{}'::jsonb
     OR public.investigacao_jsonb_objeto_tamanho(v_contexto) <> 6
     OR jsonb_typeof(v_dados) <> 'object'
     OR jsonb_typeof(v_inferencias) <> 'object'
     OR jsonb_typeof(p_pedido -> 'campos_pendentes') <> 'array'
     OR jsonb_array_length(p_pedido -> 'campos_pendentes') > 100
     OR public.investigacao_json_possui_chave(p_pedido, ARRAY[
       'target_table', 'proposed_record', 'idempotency',
       'idempotency_key', 'promocao_controle_version'
     ]) THEN
    RAISE EXCEPTION 'Conteúdo da decisão corretiva fora do contrato humano';
  END IF;
  SELECT coalesce(array_agg(valor), '{}'::text[]) INTO v_campos
    FROM jsonb_array_elements_text(p_pedido -> 'campos_pendentes') valor;
  IF EXISTS (
    SELECT 1 FROM unnest(v_campos) campo
     WHERE btrim(campo) = ''
        OR octet_length(campo) > 500
        OR NOT public.investigacao_texto_publico_sanitizado(campo)
  ) THEN
    RAISE EXCEPTION 'Pendência corretiva inválida';
  END IF;
  v_status_draft := CASE v_modo
    WHEN 'salvar' THEN 'em_revisao'
    WHEN 'voltar_confirmacao' THEN 'aguardando_confirmacao'
    ELSE 'cancelado'
  END;
  v_status_acao := CASE v_modo
    WHEN 'salvar' THEN 'em_revisao'
    WHEN 'voltar_confirmacao' THEN 'aguardando_confirmacao'
    WHEN 'rejeitar' THEN 'rejeitado'
    ELSE 'cancelado'
  END;
  v_evento_tipo := CASE v_modo
    WHEN 'salvar' THEN 'ajustes_corretivos_salvos'
    WHEN 'voltar_confirmacao' THEN 'correcao_devolvida_para_confirmacao'
    WHEN 'rejeitar' THEN 'revisao_corretiva_rejeitada'
    ELSE 'revisao_corretiva_cancelada'
  END;
  v_pedido_hash := encode(extensions.digest(
    convert_to(p_pedido::text, 'UTF8'), 'sha256'
  ), 'hex');
  v_inferencias := v_inferencias || jsonb_build_object(
    'decisao_corretiva', jsonb_build_object(
      'pedido_hash', v_pedido_hash,
      'modo', v_modo
    )
  );
  v_evento_id := md5(
    'decisao-corretiva:' || p_operation_draft_id::text || ':'
      || (p_pedido ->> 'draft_atualizado_em') || ':' || v_pedido_hash
  )::uuid;
  -- Toda mutação do par humano segue D advisory → pending_action → draft.
  -- A mesma ordem é usada pela supersessão stale para impedir o ciclo
  -- pending↔draft quando uma decisão chega ao mesmo tempo.
  PERFORM pg_catalog.pg_advisory_xact_lock(pg_catalog.hashtextextended(
    'investigacao-draft:' || p_operation_draft_id::text, 0));
  SELECT * INTO v_acao FROM public.pending_actions
   WHERE id = p_pending_action_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Pendência corretiva não encontrada';
  END IF;
  SELECT * INTO v_draft FROM public.operation_drafts
   WHERE id = p_operation_draft_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Rascunho corretivo não encontrado';
  END IF;
  IF v_draft.revisao_tipo IS DISTINCT FROM 'corretiva_pos_gravacao'
     OR v_draft.pending_action_id IS DISTINCT FROM v_acao.id
     OR v_acao.acao_tipo IS DISTINCT FROM 'revisar_correcao_pos_gravacao'
     OR v_acao.entidade_id IS DISTINCT FROM v_draft.id
     OR v_acao.entidade_tipo IS DISTINCT FROM 'operation_draft'
     OR v_acao.executavel THEN
    RAISE EXCEPTION 'Rascunho e ação não formam uma revisão corretiva válida';
  END IF;
  -- Contexto e origem são evidência imutável da revisão. O navegador ainda
  -- os envia para permitir uma verificação de concorrência legível, mas nunca
  -- pode escolhê-los nem reescrevê-los. Ambos os registros vinculados precisam
  -- concordar com o retrato persistido antes de qualquer decisão.
  IF nullif(v_contexto ->> 'contexto_canonico', '')
       IS DISTINCT FROM v_draft.contexto_canonico
     OR nullif(v_contexto ->> 'contexto_nome', '')
       IS DISTINCT FROM v_draft.contexto_nome
     OR nullif(v_contexto ->> 'origem_canal', '')
       IS DISTINCT FROM v_draft.origem_canal
     OR nullif(v_contexto ->> 'origem_conversa_id', '')
       IS DISTINCT FROM v_draft.origem_conversa_id
     OR nullif(v_contexto ->> 'origem_mensagem_id', '')
       IS DISTINCT FROM v_draft.origem_mensagem_id
     OR nullif(v_contexto ->> 'escopo', '')
       IS DISTINCT FROM v_draft.escopo
     OR v_acao.contexto_canonico IS DISTINCT FROM v_draft.contexto_canonico
     OR v_acao.contexto_nome IS DISTINCT FROM v_draft.contexto_nome
     OR v_acao.origem_canal IS DISTINCT FROM v_draft.origem_canal
     OR v_acao.origem_conversa_id IS DISTINCT FROM v_draft.origem_conversa_id
     OR v_acao.origem_mensagem_id IS DISTINCT FROM v_draft.origem_mensagem_id
     OR v_acao.escopo IS DISTINCT FROM v_draft.escopo THEN
    RAISE EXCEPTION 'O contexto e a origem da revisão corretiva são imutáveis';
  END IF;
  IF EXISTS (SELECT 1 FROM public.eventos WHERE id = v_evento_id) THEN
    IF v_draft.status IS DISTINCT FROM v_status_draft
       OR v_acao.status IS DISTINCT FROM v_status_acao
       OR v_draft.inferencias #>> '{decisao_corretiva,pedido_hash}'
            IS DISTINCT FROM v_pedido_hash
       OR v_acao.payload #>> '{revisao_confinex,pedido_hash}'
            IS DISTINCT FROM v_pedido_hash
       OR NOT EXISTS (
         SELECT 1 FROM public.eventos evento
          WHERE evento.id = v_evento_id
            AND evento.tipo = v_evento_tipo
            AND evento.entidade_tipo = 'operation_draft'
            AND evento.entidade_id = v_draft.id
            AND evento.dados ->> 'pending_action_id' = v_acao.id::text
            AND evento.dados ->> 'pedido_hash' = v_pedido_hash
            AND evento.dados ->> 'acao' = v_modo
            AND coalesce(
              (evento.dados ->> 'promovido_para_operacional')::boolean, true
            ) IS FALSE
       ) THEN
      RAISE EXCEPTION 'Repetição diverge da decisão corretiva já registrada';
    END IF;
    RETURN jsonb_build_object(
      'decidida', false, 'repeticao_idempotente', true,
      'status', v_status_acao
    );
  END IF;
  IF v_draft.atualizado_em IS DISTINCT FROM
          (p_pedido ->> 'draft_atualizado_em')::timestamptz
     OR v_acao.atualizado_em IS DISTINCT FROM
          (p_pedido ->> 'action_atualizado_em')::timestamptz THEN
    RAISE EXCEPTION 'A revisão corretiva mudou; recarregue antes de decidir';
  END IF;
  IF v_draft.status = 'cancelado'
     OR v_acao.status IN ('rejeitado', 'cancelado') THEN
    RAISE EXCEPTION 'A revisão corretiva já foi encerrada';
  END IF;
  IF v_inferencias -> 'fingerprint_base'
       IS DISTINCT FROM v_draft.inferencias -> 'fingerprint_base' THEN
    RAISE EXCEPTION 'A origem da investigação corretiva é imutável';
  END IF;
  v_dados := v_dados || jsonb_build_object(
    'status_confirmacao', CASE v_modo
      WHEN 'salvar' THEN 'em_revisao'
      WHEN 'voltar_confirmacao' THEN 'aguardando_confirmacao'
      ELSE v_status_acao
    END
  );
  v_payload := v_acao.payload || jsonb_build_object(
    'dados_extraidos', v_dados,
    'campos_pendentes', to_jsonb(v_campos),
    'inferencias', v_inferencias,
    'revisao_confinex', jsonb_build_object(
      'atualizado_em', v_agora,
      'motivo', nullif(v_motivo, ''),
      'modo', v_modo,
      'acao', v_evento_tipo,
      'pedido_hash', v_pedido_hash
    )
  );
  v_hash_draft := encode(extensions.digest(convert_to(jsonb_build_object(
    'draft_id', v_draft.id,
    'old_atualizado_em', v_draft.atualizado_em,
    'new_status', v_status_draft,
    'codigo_sugerido', nullif(p_pedido ->> 'codigo_sugerido', ''),
    'dados_extraidos', v_dados,
    'campos_pendentes', to_jsonb(v_campos),
    'inferencias', v_inferencias,
    'contexto_canonico', v_draft.contexto_canonico,
    'contexto_nome', v_draft.contexto_nome,
    'origem_canal', v_draft.origem_canal,
    'origem_conversa_id', v_draft.origem_conversa_id,
    'origem_mensagem_id', v_draft.origem_mensagem_id,
    'escopo', v_draft.escopo
  )::text, 'UTF8'), 'sha256'), 'hex');
  v_hash_acao := encode(extensions.digest(convert_to(jsonb_build_object(
    'action_id', v_acao.id,
    'old_atualizado_em', v_acao.atualizado_em,
    'new_status', v_status_acao,
    'resumo', p_pedido ->> 'resumo',
    'payload', v_payload,
    'contexto_canonico', v_draft.contexto_canonico,
    'contexto_nome', v_draft.contexto_nome,
    'origem_canal', v_draft.origem_canal,
    'origem_conversa_id', v_draft.origem_conversa_id,
    'origem_mensagem_id', v_draft.origem_mensagem_id,
    'escopo', v_draft.escopo
  )::text, 'UTF8'), 'sha256'), 'hex');
  INSERT INTO public.investigacao_autorizacoes_corretiva (
    txid, backend_pid, recurso, investigacao_id,
    operation_draft_id, pending_action_id, pedido_hash
  ) VALUES
    (txid_current(), pg_backend_pid(), 'decidir_draft',
     v_draft.investigacao_origem_id, v_draft.id, v_acao.id, v_hash_draft),
    (txid_current(), pg_backend_pid(), 'decidir_acao',
     v_draft.investigacao_origem_id, v_draft.id, v_acao.id, v_hash_acao);
  UPDATE public.operation_drafts
     SET atualizado_em = v_agora,
         status = v_status_draft,
         codigo_sugerido = nullif(p_pedido ->> 'codigo_sugerido', ''),
         dados_extraidos = v_dados,
         campos_pendentes = v_campos,
         inferencias = v_inferencias
   WHERE id = v_draft.id AND atualizado_em = v_draft.atualizado_em;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'O rascunho mudou durante a decisão corretiva';
  END IF;
  UPDATE public.pending_actions
     SET atualizado_em = v_agora,
         status = v_status_acao,
         resumo = p_pedido ->> 'resumo',
         payload = v_payload
   WHERE id = v_acao.id AND atualizado_em = v_acao.atualizado_em;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'A pendência mudou durante a decisão corretiva';
  END IF;
  INSERT INTO public.eventos (
    id, tipo, agente, usuario, entidade_tipo, entidade_id, origem,
    origem_canal, origem_conversa_id, origem_mensagem_id,
    contexto_canonico, contexto_nome, escopo, status, dados, observacao
  ) VALUES (
    v_evento_id, v_evento_tipo, 'confinex', v_ator,
    'operation_draft', v_draft.id, 'confinex_revisoes',
    v_draft.origem_canal,
    v_draft.origem_conversa_id,
    v_draft.origem_mensagem_id,
    v_draft.contexto_canonico,
    v_draft.contexto_nome,
    v_draft.escopo, 'registrado',
    jsonb_build_object(
      'draft_id', v_draft.id,
      'pending_action_id', v_acao.id,
      'acao', v_modo,
      'pedido_hash', v_pedido_hash,
      'promovido_para_operacional', false
    ),
    CASE WHEN v_motivo = ''
      THEN 'Decisão corretiva registrada sem criar lançamento.'
      ELSE 'Decisão corretiva registrada. Motivo: ' || v_motivo
    END
  );
  IF EXISTS (
    SELECT 1 FROM public.investigacao_autorizacoes_corretiva autorizacao
     WHERE autorizacao.txid = txid_current()
       AND autorizacao.backend_pid = pg_backend_pid()
       AND autorizacao.investigacao_id = v_draft.investigacao_origem_id
       AND autorizacao.operation_draft_id = v_draft.id
       AND autorizacao.pending_action_id = v_acao.id
  ) THEN
    RAISE EXCEPTION 'A decisão corretiva deixou capacidade interna sem consumo';
  END IF;
  RETURN jsonb_build_object(
    'decidida', true, 'repeticao_idempotente', false,
    'status', v_status_acao
  );
END;
$$;

DROP TRIGGER IF EXISTS pending_actions_protecao_permanente
  ON public.pending_actions;
CREATE TRIGGER pending_actions_protecao_permanente
BEFORE INSERT OR UPDATE OR DELETE ON public.pending_actions
FOR EACH ROW EXECUTE FUNCTION public.proteger_pending_action_permanente();

DROP TRIGGER IF EXISTS operation_drafts_protecao_corretiva_permanente
  ON public.operation_drafts;
CREATE TRIGGER operation_drafts_protecao_corretiva_permanente
BEFORE INSERT OR UPDATE OR DELETE ON public.operation_drafts
FOR EACH ROW EXECUTE FUNCTION public.proteger_draft_corretivo_permanente();

ALTER TABLE public.investigacoes_revisao ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.investigacao_tarefas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.investigacao_evidencias ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.investigacao_alternativas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.investigacao_alternativa_evidencias ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.investigacao_pendencias ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.investigacao_eventos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.investigacao_entregas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.investigacao_adaptador_credenciais ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.investigacao_adaptadores_config ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.investigacao_credenciais_revogadas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.investigacao_configuracao_ativacao ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.investigacao_autorizacoes_promocao ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.investigacao_autorizacoes_corretiva ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.investigacao_sucessoes_pendentes ENABLE ROW LEVEL SECURITY;

REVOKE ALL ON public.investigacoes_revisao FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.investigacao_tarefas FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.investigacao_evidencias FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.investigacao_alternativas FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.investigacao_alternativa_evidencias FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.investigacao_pendencias FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.investigacao_eventos FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.investigacao_entregas FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.investigacao_adaptador_credenciais
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.investigacao_adaptadores_config
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.investigacao_credenciais_revogadas
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.investigacao_configuracao_ativacao
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.investigacao_autorizacoes_promocao
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.investigacao_autorizacoes_corretiva
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.investigacao_sucessoes_pendentes
  FROM PUBLIC, anon, authenticated, service_role;

GRANT SELECT, INSERT ON public.investigacoes_revisao TO service_role;
GRANT SELECT, INSERT ON public.investigacao_tarefas TO service_role;
GRANT SELECT ON public.investigacao_evidencias TO service_role;
GRANT SELECT ON public.investigacao_alternativas TO service_role;
GRANT SELECT ON public.investigacao_alternativa_evidencias TO service_role;
GRANT SELECT ON public.investigacao_pendencias TO service_role;
GRANT SELECT, INSERT ON public.investigacao_eventos TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.investigacao_entregas TO service_role;
GRANT USAGE ON SCHEMA extensions TO service_role;
GRANT EXECUTE ON FUNCTION extensions.digest(bytea, text) TO service_role;
GRANT EXECUTE ON FUNCTION extensions.gen_random_bytes(integer) TO service_role;

REVOKE ALL ON public.v_investigacoes_revisao
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.v_investigacoes_revisao_bloqueios
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.v_investigacoes_revisao_materializacao
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.v_investigacao_alternativas
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.v_investigacao_evidencias
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON public.v_investigacao_pendencias
  FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON public.v_investigacoes_revisao TO authenticated;
GRANT SELECT ON public.v_investigacoes_revisao_bloqueios TO authenticated;
GRANT SELECT ON public.v_investigacoes_revisao_materializacao TO service_role;
GRANT SELECT ON public.v_investigacao_alternativas TO authenticated;
GRANT SELECT ON public.v_investigacao_evidencias TO authenticated;
GRANT SELECT ON public.v_investigacao_pendencias TO authenticated;

-- Falha a própria migração caso algum privilégio efetivo contradiga o
-- isolamento esperado. Isso verifica o catálogo, não apenas o texto dos GRANTs.
DO $$
DECLARE
  v_tabela text;
  v_reg regclass;
  v_owner oid;
  v_service_role oid;
BEGIN
  SELECT oid INTO v_service_role FROM pg_roles WHERE rolname = 'service_role';
  FOREACH v_tabela IN ARRAY ARRAY[
    'investigacoes_revisao', 'investigacao_tarefas',
    'investigacao_evidencias', 'investigacao_alternativas',
    'investigacao_alternativa_evidencias', 'investigacao_pendencias',
    'investigacao_eventos', 'investigacao_entregas',
    'investigacao_adaptador_credenciais',
    'investigacao_adaptadores_config',
    'investigacao_credenciais_revogadas',
    'investigacao_configuracao_ativacao',
    'investigacao_autorizacoes_promocao',
    'investigacao_autorizacoes_corretiva',
    'investigacao_sucessoes_pendentes'
  ] LOOP
    v_reg := ('public.' || v_tabela)::regclass;
    SELECT relowner INTO v_owner FROM pg_class WHERE oid = v_reg::oid;
    IF has_table_privilege('authenticated', 'public.' || v_tabela, 'SELECT')
       OR has_table_privilege('authenticated', 'public.' || v_tabela, 'INSERT')
       OR has_table_privilege('authenticated', 'public.' || v_tabela, 'UPDATE')
       OR has_table_privilege('authenticated', 'public.' || v_tabela, 'DELETE')
       OR has_table_privilege('authenticated', 'public.' || v_tabela, 'TRUNCATE')
       OR has_table_privilege('authenticated', 'public.' || v_tabela, 'TRIGGER')
       OR has_table_privilege('anon', 'public.' || v_tabela, 'SELECT')
       OR has_table_privilege('anon', 'public.' || v_tabela, 'INSERT')
       OR has_table_privilege('anon', 'public.' || v_tabela, 'UPDATE')
       OR has_table_privilege('anon', 'public.' || v_tabela, 'DELETE') THEN
      RAISE EXCEPTION 'Privilégio indevido de authenticated em %', v_tabela;
    END IF;
    IF v_owner <> (SELECT oid FROM pg_roles WHERE rolname = current_user)
       OR NOT (SELECT relrowsecurity FROM pg_class WHERE oid = v_reg::oid)
       OR EXISTS (
         SELECT 1
           FROM pg_class classe
           CROSS JOIN LATERAL aclexplode(
             coalesce(classe.relacl, acldefault('r', classe.relowner))
           ) privilegio
          WHERE classe.oid = v_reg::oid
            AND privilegio.grantee <> v_owner
            AND (
              privilegio.grantee <> v_service_role
              OR privilegio.is_grantable
              OR NOT (
                privilegio.privilege_type = 'SELECT'
                  AND v_tabela IN (
                    'investigacoes_revisao', 'investigacao_tarefas',
                    'investigacao_evidencias', 'investigacao_alternativas',
                    'investigacao_alternativa_evidencias',
                    'investigacao_pendencias', 'investigacao_eventos',
                    'investigacao_entregas'
                  )
                OR privilegio.privilege_type = 'INSERT'
                  AND v_tabela IN (
                    'investigacoes_revisao', 'investigacao_tarefas',
                    'investigacao_eventos', 'investigacao_entregas'
                  )
                OR privilegio.privilege_type = 'UPDATE'
                  AND v_tabela = 'investigacao_entregas'
              )
            )
       ) THEN
      RAISE EXCEPTION 'Owner/RLS/ACL fora do inventário em %', v_tabela;
    END IF;
    IF EXISTS (
      SELECT 1
        FROM pg_attribute coluna
       WHERE coluna.attrelid = v_reg::oid
         AND coluna.attnum > 0
         AND NOT coluna.attisdropped
         AND coluna.attacl IS NOT NULL
         AND cardinality(coluna.attacl) > 0
    ) THEN
      RAISE EXCEPTION 'Grant por coluna fora do inventário em %', v_tabela;
    END IF;
  END LOOP;
  FOREACH v_reg IN ARRAY ARRAY[
    'public.pending_actions'::regclass,
    'public.operation_drafts'::regclass,
    'public.negocios_candidatos'::regclass
  ] LOOP
    IF EXISTS (
      SELECT 1
        FROM pg_attribute coluna
       WHERE coluna.attrelid = v_reg::oid
         AND coluna.attnum > 0
         AND NOT coluna.attisdropped
         AND coluna.attacl IS NOT NULL
         AND cardinality(coluna.attacl) > 0
    ) THEN
      RAISE EXCEPTION 'Grant por coluna legado impede a fase sombra em %', v_reg;
    END IF;
  END LOOP;
  IF has_table_privilege(
       'service_role', 'public.investigacao_adaptador_credenciais', 'SELECT'
     )
     OR has_table_privilege(
       'service_role', 'public.investigacao_adaptadores_config', 'SELECT'
     )
     OR has_table_privilege(
       'service_role', 'public.investigacao_credenciais_revogadas', 'SELECT'
     )
     OR has_table_privilege(
       'service_role', 'public.investigacao_configuracao_ativacao', 'SELECT'
     )
     OR has_table_privilege(
       'service_role', 'public.investigacao_autorizacoes_promocao', 'SELECT'
     ) THEN
    RAISE EXCEPTION 'Broker service_role não pode ler segredo nem autoatestar sua implantação';
  END IF;
  IF NOT has_table_privilege('service_role', 'public.investigacao_eventos', 'SELECT')
     OR NOT has_table_privilege('service_role', 'public.investigacao_eventos', 'INSERT')
     OR has_table_privilege('service_role', 'public.investigacao_eventos', 'UPDATE')
     OR has_table_privilege('service_role', 'public.investigacao_eventos', 'DELETE') THEN
    RAISE EXCEPTION 'Trilha técnica não está append-only para service_role';
  END IF;
  IF NOT has_schema_privilege('service_role', 'extensions', 'USAGE')
     OR NOT has_function_privilege(
       'service_role', 'extensions.digest(bytea,text)', 'EXECUTE'
     )
     OR NOT has_function_privilege(
       'service_role', 'extensions.gen_random_bytes(integer)', 'EXECUTE'
     ) THEN
    RAISE EXCEPTION 'service_role não pode usar primitivas qualificadas de pgcrypto';
  END IF;
  FOREACH v_tabela IN ARRAY ARRAY[
    'investigacao_evidencias', 'investigacao_alternativas',
    'investigacao_alternativa_evidencias', 'investigacao_pendencias'
  ] LOOP
    IF has_table_privilege('service_role', 'public.' || v_tabela, 'INSERT') THEN
      RAISE EXCEPTION 'Resultado deve ser publicado somente pela RPC atômica: %', v_tabela;
    END IF;
  END LOOP;
END;
$$;

CREATE OR REPLACE FUNCTION public.investigacao_plano_materializado(
  p_investigacao_id uuid
)
RETURNS boolean
LANGUAGE sql
STABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
  SELECT EXISTS (
    SELECT 1
      FROM public.investigacoes_revisao investigacao
     WHERE investigacao.id = p_investigacao_id
       AND (
         SELECT count(*)
           FROM public.investigacao_tarefas tarefa
          WHERE tarefa.investigacao_id = investigacao.id
       ) = jsonb_array_length(investigacao.plano_tarefas)
       AND NOT EXISTS (
         SELECT 1
           FROM jsonb_array_elements(investigacao.plano_tarefas) item
          WHERE NOT EXISTS (
            SELECT 1
              FROM public.investigacao_tarefas tarefa
             WHERE tarefa.investigacao_id = investigacao.id
               AND tarefa.plano_item_ref = item ->> 'plano_item_ref'
               AND tarefa.adaptador = item ->> 'adaptador'
               AND tarefa.adaptador_version = item ->> 'adaptador_version'
               AND tarefa.consulta_ref = item ->> 'consulta_ref'
               AND tarefa.consulta_schema_version = item ->> 'consulta_schema_version'
               AND tarefa.consulta_spec = item -> 'consulta_spec'
               AND tarefa.consulta_canonico = item ->> 'consulta_canonico'
               AND tarefa.consulta_hash = item ->> 'consulta_hash'
          )
       )
  );
$$;

CREATE OR REPLACE FUNCTION public.investigacao_cobertura_sintese(
  p_investigacao_id uuid
)
RETURNS text
LANGUAGE sql
STABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
  SELECT CASE
    WHEN bool_or(tarefa.estado_cobertura NOT IN (
      'completa', 'vazio_com_cobertura'
    )) THEN 'cobertura_incompleta'
    WHEN bool_and(tarefa.estado_cobertura = 'vazio_com_cobertura')
      THEN 'vazio_com_cobertura'
    ELSE 'completa'
  END
    FROM public.investigacao_tarefas tarefa
    JOIN public.investigacoes_revisao investigacao
      ON investigacao.id = tarefa.investigacao_id
   WHERE tarefa.investigacao_id = p_investigacao_id
     AND tarefa.adaptador <> 'sintese'
  GROUP BY investigacao.plano_tarefas
  HAVING count(*) = jsonb_array_length(investigacao.plano_tarefas) - 1
     AND bool_and(tarefa.estado_execucao IN ('concluida', 'cancelada', 'obsoleta'))
     AND bool_and(tarefa.estado_cobertura IS NOT NULL)
     AND public.investigacao_plano_materializado(p_investigacao_id);
$$;

CREATE OR REPLACE FUNCTION public.assumir_tarefa_investigacao(
  p_adaptador text,
  p_executor text,
  p_lease_segundos integer DEFAULT 120
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_tarefa public.investigacao_tarefas%ROWTYPE;
  v_config_version text;
BEGIN
  IF p_adaptador IS NULL OR p_adaptador NOT IN (
    'agronotas', 'ofx', 'ima', 'telegram', 'wey', 'outro', 'sintese'
  ) THEN
    RAISE EXCEPTION 'Adaptador obrigatório e registrado';
  END IF;
  IF btrim(coalesce(p_executor, '')) = '' THEN
    RAISE EXCEPTION 'Executor obrigatório';
  END IF;
  IF p_lease_segundos < 30 OR p_lease_segundos > 900 THEN
    RAISE EXCEPTION 'Lease deve ficar entre 30 e 900 segundos';
  END IF;
  IF p_adaptador <> 'sintese' THEN
    SELECT config.adaptador_version INTO v_config_version
      FROM public.investigacao_adaptadores_config config
     WHERE config.adaptador = p_adaptador
       AND config.habilitado;
    IF v_config_version IS NULL THEN
      RETURN NULL;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended(
      'investigacao-config:' || p_adaptador || ':' || v_config_version, 0
    ));
    PERFORM 1
      FROM public.investigacao_adaptadores_config config
     WHERE config.adaptador = p_adaptador
       AND config.adaptador_version = v_config_version
       AND config.habilitado
     FOR UPDATE;
    IF NOT FOUND THEN
      RETURN NULL;
    END IF;
  END IF;

  WITH candidata AS (
    SELECT tarefa_candidata.id, emissor.chave_id AS lease_chave_id
      FROM public.investigacao_tarefas tarefa_candidata
      JOIN public.investigacoes_revisao investigacao_pai
        ON investigacao_pai.id = tarefa_candidata.investigacao_id
      LEFT JOIN LATERAL (
        SELECT credencial.chave_id
          FROM public.investigacao_adaptadores_config config
          JOIN public.investigacao_adaptador_credenciais credencial
            ON credencial.adaptador = config.adaptador
           AND credencial.adaptador_version = config.adaptador_version
         WHERE config.adaptador = tarefa_candidata.adaptador
           AND config.adaptador_version = tarefa_candidata.adaptador_version
           AND config.habilitado
           AND clock_timestamp() >= credencial.valida_desde
           AND clock_timestamp() < credencial.emite_ate
           AND clock_timestamp() + make_interval(secs => p_lease_segundos)
                 < credencial.aceita_ate
           AND NOT EXISTS (
             SELECT 1 FROM public.investigacao_credenciais_revogadas revogada
              WHERE revogada.adaptador = credencial.adaptador
                AND revogada.adaptador_version = credencial.adaptador_version
                AND revogada.chave_id = credencial.chave_id
           )
         ORDER BY credencial.valida_desde DESC, credencial.chave_id
         LIMIT 1
      ) emissor ON tarefa_candidata.adaptador <> 'sintese'
     WHERE (
       tarefa_candidata.estado_execucao IN ('pendente', 'aguardando_retentativa')
       OR (
         tarefa_candidata.estado_execucao = 'em_execucao'
         AND tarefa_candidata.lease_expira_em < clock_timestamp()
       )
     )
       AND tarefa_candidata.adaptador = p_adaptador
       AND tarefa_candidata.proxima_execucao_em <= clock_timestamp()
       AND investigacao_pai.estado_execucao IN (
         'pendente', 'em_execucao', 'aguardando_retentativa'
       )
       AND (
         tarefa_candidata.lease_expira_em IS NULL
         OR tarefa_candidata.lease_expira_em < clock_timestamp()
       )
       AND (
         (
           tarefa_candidata.adaptador <> 'sintese'
           AND emissor.chave_id IS NOT NULL
         )
         OR (
           tarefa_candidata.adaptador = 'sintese'
           AND
           public.investigacao_plano_materializado(
             tarefa_candidata.investigacao_id
           )
           AND public.investigacao_cobertura_sintese(
             tarefa_candidata.investigacao_id
           ) IS NOT NULL
         )
       )
     ORDER BY (tarefa_candidata.adaptador = 'sintese'),
       tarefa_candidata.proxima_execucao_em, tarefa_candidata.criado_em,
       tarefa_candidata.id
     FOR UPDATE OF tarefa_candidata SKIP LOCKED
     LIMIT 1
  )
  UPDATE public.investigacao_tarefas tarefa
     SET estado_execucao = 'em_execucao',
         tentativas = tarefa.tentativas + 1,
         lease_executor = p_executor,
         lease_token = gen_random_uuid(),
         lease_expira_em = clock_timestamp() + make_interval(secs => p_lease_segundos),
         lease_chave_id = candidata.lease_chave_id,
         retentativa_lease_token = NULL,
         retentativa_fencing_token = NULL,
         retentativa_executor = NULL,
         retentativa_pedido_hash = NULL,
         fencing_token = tarefa.fencing_token + 1,
         iniciado_em = coalesce(tarefa.iniciado_em, now())
    FROM candidata
   WHERE tarefa.id = candidata.id
  RETURNING tarefa.* INTO v_tarefa;

  IF v_tarefa.id IS NULL THEN
    RETURN NULL;
  END IF;
  RETURN to_jsonb(v_tarefa);
END;
$$;

-- O broker pode devolver uma tarefa sem fatos quando o processo do adaptador
-- cai antes de assinar. A operação só agenda retentativa com backoff; não cria
-- evidência, resultado conclusivo nem estado terminal sem atestado da fonte.
CREATE OR REPLACE FUNCTION public.adiar_tarefa_investigacao(
  p_tarefa_id uuid,
  p_lease_token uuid,
  p_fencing_token bigint,
  p_executor text,
  p_atraso_segundos integer,
  p_erro_codigo text,
  p_erro_sanitizado text
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_pai_id uuid;
  v_tarefa public.investigacao_tarefas%ROWTYPE;
  v_pedido_hash text;
BEGIN
  IF p_atraso_segundos < 30 OR p_atraso_segundos > 86400
     OR btrim(coalesce(p_executor, '')) = ''
     OR btrim(coalesce(p_erro_codigo, '')) = ''
     OR NOT public.investigacao_texto_sanitizado(p_erro_codigo)
     OR NOT public.investigacao_texto_sanitizado(p_erro_sanitizado) THEN
    RAISE EXCEPTION 'Contrato de retentativa inválido';
  END IF;
  v_pedido_hash := encode(extensions.digest(convert_to(
    public.investigacao_json_canonico(jsonb_build_object(
      'schema_version', 'investigacao-retentativa-v1',
      'tarefa_id', p_tarefa_id,
      'lease_token', p_lease_token,
      'fencing_token', p_fencing_token::text,
      'executor', p_executor,
      'atraso_segundos', p_atraso_segundos,
      'erro_codigo', p_erro_codigo,
      'erro_sanitizado', p_erro_sanitizado
    )), 'UTF8'
  ), 'sha256'), 'hex');
  SELECT investigacao_id INTO v_pai_id
    FROM public.investigacao_tarefas WHERE id = p_tarefa_id;
  IF v_pai_id IS NULL THEN
    RAISE EXCEPTION 'Tarefa não encontrada';
  END IF;
  PERFORM 1 FROM public.investigacoes_revisao
   WHERE id = v_pai_id FOR UPDATE;
  SELECT * INTO v_tarefa FROM public.investigacao_tarefas
   WHERE id = p_tarefa_id AND investigacao_id = v_pai_id FOR UPDATE;
  IF FOUND
     AND v_tarefa.estado_execucao = 'aguardando_retentativa'
     AND v_tarefa.retentativa_lease_token IS NOT DISTINCT FROM p_lease_token
     AND v_tarefa.retentativa_fencing_token IS NOT DISTINCT FROM p_fencing_token
     AND v_tarefa.retentativa_executor IS NOT DISTINCT FROM p_executor THEN
    IF v_tarefa.retentativa_pedido_hash IS DISTINCT FROM v_pedido_hash THEN
      RAISE EXCEPTION 'A mesma tentativa não pode agendar uma retentativa divergente';
    END IF;
    RETURN true;
  END IF;
  IF NOT FOUND
     OR v_tarefa.estado_execucao <> 'em_execucao'
     OR v_tarefa.lease_token IS DISTINCT FROM p_lease_token
     OR v_tarefa.fencing_token IS DISTINCT FROM p_fencing_token
     OR v_tarefa.lease_executor IS DISTINCT FROM p_executor
     OR v_tarefa.lease_expira_em <= clock_timestamp() THEN
    RAISE EXCEPTION 'Lease divergente para retentativa';
  END IF;
  UPDATE public.investigacao_tarefas
     SET estado_execucao = 'aguardando_retentativa',
         proxima_execucao_em = clock_timestamp()
           + make_interval(secs => p_atraso_segundos),
         erro_codigo = p_erro_codigo,
         erro_sanitizado = p_erro_sanitizado,
         retentativa_lease_token = p_lease_token,
         retentativa_fencing_token = p_fencing_token,
         retentativa_executor = p_executor,
         retentativa_pedido_hash = v_pedido_hash,
         lease_executor = NULL, lease_token = NULL, lease_expira_em = NULL,
         lease_chave_id = NULL
   WHERE id = v_tarefa.id;
  INSERT INTO public.investigacao_eventos (
    investigacao_id, chave_idempotencia, tipo, referencia_entidade,
    resumo_sanitizado
  ) VALUES (
    v_tarefa.investigacao_id,
    'tarefa-retry:' || v_tarefa.id::text || ':' || p_fencing_token::text,
    'tarefa_retentativa', v_tarefa.id::text,
    'A fonte ficou indisponível e uma nova tentativa foi agendada sem criar evidência.'
  ) ON CONFLICT (chave_idempotencia) DO NOTHING;
  RETURN true;
END;
$$;

CREATE OR REPLACE FUNCTION public.publicar_resultado_tarefa_investigacao(
  p_tarefa_id uuid,
  p_lease_token uuid,
  p_fencing_token bigint,
  p_estado_cobertura text,
  p_estado_resultado text,
  p_bundle jsonb DEFAULT '{}'::jsonb,
  p_atestado_cobertura jsonb DEFAULT NULL,
  p_resumo_sanitizado text DEFAULT NULL,
  p_erro_codigo text DEFAULT NULL,
  p_erro_sanitizado text DEFAULT NULL
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_tarefa_pre public.investigacao_tarefas%ROWTYPE;
  v_tarefa public.investigacao_tarefas%ROWTYPE;
  v_investigacao public.investigacoes_revisao%ROWTYPE;
  v_config public.investigacao_adaptadores_config%ROWTYPE;
  v_item jsonb;
  v_evidencias jsonb := coalesce(p_bundle -> 'evidencias', '[]'::jsonb);
  v_alternativas jsonb := coalesce(p_bundle -> 'alternativas', '[]'::jsonb);
  v_pendencias jsonb := coalesce(p_bundle -> 'pendencias', '[]'::jsonb);
  v_ligacoes jsonb := coalesce(p_bundle -> 'ligacoes', '[]'::jsonb);
  v_alternativa_id uuid;
  v_evidencia_id uuid;
  v_quantidade_alternativas integer;
  v_quantidade_snapshots integer;
  v_quantidade_pendencias integer;
  v_estado_resultado_derivado text;
  v_tem_contraprova boolean := false;
  v_cobertura_derivada text;
  v_campos_obrigatorios text[];
  v_tem_campo_ausente boolean;
  v_existe_alternativa_parcial boolean := false;
  v_confianca_esperada numeric;
  v_classificacao_esperada text;
  v_pedido_hash text;
  v_quantidade_repetida integer;
  v_proveniencia jsonb;
BEGIN
  IF p_estado_cobertura IS NULL OR p_estado_cobertura NOT IN (
       'completa', 'vazio_com_cobertura', 'cobertura_incompleta',
       'indisponivel', 'reautenticacao_necessaria', 'erro_permanente'
     )
     OR p_estado_resultado IS NULL OR p_estado_resultado NOT IN (
       'alternativa_unica', 'alternativas_multiplas', 'divergente',
       'evidencia_insuficiente', 'cobertura_incompleta'
     ) THEN
    RAISE EXCEPTION 'Cobertura e resultado precisam ser estados válidos';
  END IF;
  IF p_bundle IS NULL
     OR jsonb_typeof(p_bundle) <> 'object'
     OR NOT public.investigacao_json_sanitizado(p_bundle)
     OR p_bundle - ARRAY[
       'evidencias', 'alternativas', 'pendencias', 'ligacoes'
     ] <> '{}'::jsonb THEN
    RAISE EXCEPTION 'Bundle inválido ou não sanitizado';
  END IF;
  IF jsonb_typeof(v_evidencias) <> 'array'
     OR jsonb_typeof(v_alternativas) <> 'array'
     OR jsonb_typeof(v_pendencias) <> 'array'
     OR jsonb_typeof(v_ligacoes) <> 'array' THEN
    RAISE EXCEPTION 'Coleções do bundle precisam ser listas';
  END IF;
  IF octet_length(p_bundle::text) > 262144
     OR jsonb_array_length(v_evidencias) > 200
     OR jsonb_array_length(v_alternativas) > 50
     OR jsonb_array_length(v_pendencias) > 100
     OR jsonb_array_length(v_ligacoes) > 500
     OR octet_length(coalesce(p_resumo_sanitizado, '')) > 1000
     OR octet_length(coalesce(p_erro_sanitizado, '')) > 1000 THEN
    RAISE EXCEPTION 'Bundle excede os limites operacionais da investigação';
  END IF;
  -- A resposta pode se perder depois do COMMIT. O atestado cobre a requisição
  -- inteira e permite reconhecer somente a repetição byte-semântica do
  -- mesmo lease; um payload diferente nunca herda o sucesso anterior.
  v_pedido_hash := encode(extensions.digest(convert_to(
    jsonb_build_object(
      'estado_cobertura', p_estado_cobertura,
      'estado_resultado', p_estado_resultado,
      'bundle', p_bundle,
      'atestado_cobertura', p_atestado_cobertura,
      'resumo_sanitizado', p_resumo_sanitizado,
      'erro_codigo', p_erro_codigo,
      'erro_sanitizado', p_erro_sanitizado
    )::text,
    'UTF8'
  ), 'sha256'), 'hex');

  -- A ordem global de locks para transições agregadas é sempre
  -- investigação-pai -> tarefas. A pré-leitura descobre o pai sem manter lock;
  -- depois ambos são revalidados sob os locks definitivos.
  SELECT * INTO v_tarefa_pre
    FROM public.investigacao_tarefas
   WHERE id = p_tarefa_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Tarefa de investigação não encontrada';
  END IF;
  SELECT * INTO v_investigacao
    FROM public.investigacoes_revisao
   WHERE id = v_tarefa_pre.investigacao_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Investigação da tarefa não encontrada';
  END IF;
  SELECT * INTO v_tarefa
    FROM public.investigacao_tarefas
   WHERE id = p_tarefa_id
   FOR UPDATE;
  IF NOT FOUND
     OR v_tarefa.investigacao_id IS DISTINCT FROM v_tarefa_pre.investigacao_id THEN
    RAISE EXCEPTION 'Tarefa mudou durante a aquisição dos locks';
  END IF;
  IF v_tarefa.estado_execucao = 'concluida' THEN
    IF v_tarefa.resultado_lease_token IS DISTINCT FROM p_lease_token
       OR v_tarefa.resultado_fencing_token IS DISTINCT FROM p_fencing_token
       OR v_tarefa.resultado_pedido_hash IS DISTINCT FROM v_pedido_hash THEN
      RAISE EXCEPTION 'Resultado já concluído por outra tentativa ou pedido divergente';
    END IF;
    SELECT count(*) INTO v_quantidade_repetida
      FROM public.investigacao_evidencias evidencia
     WHERE evidencia.tarefa_id = v_tarefa.id
       AND evidencia.tarefa_lease_token = p_lease_token
       AND evidencia.tarefa_fencing_token = p_fencing_token;
    RETURN jsonb_build_object(
      'publicado', false,
      'repeticao_idempotente', true,
      'tarefa_id', v_tarefa.id,
      'fencing_token', p_fencing_token,
      'evidencias', v_quantidade_repetida,
      'alternativas', (
        SELECT count(*) FROM public.investigacao_alternativas alternativa
         WHERE alternativa.tarefa_id = v_tarefa.id
           AND alternativa.tarefa_lease_token = p_lease_token
           AND alternativa.tarefa_fencing_token = p_fencing_token
      ),
      'pendencias', (
        SELECT count(*) FROM public.investigacao_pendencias pendencia
         WHERE pendencia.tarefa_id = v_tarefa.id
           AND pendencia.tarefa_lease_token = p_lease_token
           AND pendencia.tarefa_fencing_token = p_fencing_token
      )
    );
  END IF;
  IF v_tarefa.estado_execucao <> 'em_execucao'
     OR v_tarefa.lease_token IS DISTINCT FROM p_lease_token
     OR v_tarefa.fencing_token IS DISTINCT FROM p_fencing_token
     OR v_tarefa.lease_expira_em <= clock_timestamp() THEN
    RAISE EXCEPTION 'Lease inválido, vencido ou já concluído';
  END IF;
  -- A prova é validada atomicamente antes do primeiro INSERT do bundle. O
  -- HMAC vincula adaptador, versão, consulta, tarefa, investigação,
  -- lease/fencing, cobertura e o hash canônico do bundle inteiro.
  IF v_tarefa.adaptador = 'sintese' THEN
    IF p_atestado_cobertura IS NOT NULL
       AND p_atestado_cobertura <> 'null'::jsonb THEN
      RAISE EXCEPTION 'Síntese deriva cobertura do banco e não aceita prova de adaptador';
    END IF;
  ELSIF public.investigacao_prova_cobertura_valida(
    v_tarefa.id, v_tarefa.investigacao_id, p_lease_token, p_fencing_token,
    p_estado_cobertura, p_estado_resultado, p_atestado_cobertura, p_bundle,
    p_resumo_sanitizado, p_erro_codigo, p_erro_sanitizado
  ) IS NOT TRUE THEN
    RAISE EXCEPTION 'Prova de cobertura do adaptador ausente, divergente ou inválida';
  END IF;
  v_campos_obrigatorios := v_investigacao.campos_obrigatorios;
  IF v_tarefa.adaptador <> 'sintese' THEN
    SELECT * INTO v_config
      FROM public.investigacao_adaptadores_config config
     WHERE config.adaptador = v_tarefa.adaptador
       AND config.adaptador_version = v_tarefa.adaptador_version
       AND config.habilitado;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Manifesto habilitado do adaptador não encontrado';
    END IF;
  END IF;

  IF v_tarefa.adaptador = 'sintese' THEN
    IF NOT public.investigacao_plano_materializado(v_tarefa.investigacao_id) THEN
      RAISE EXCEPTION 'A síntese exige todas as tarefas do plano imutável';
    END IF;
    IF EXISTS (
      SELECT 1
        FROM public.investigacao_tarefas
       WHERE investigacao_id = v_tarefa.investigacao_id
         AND id <> p_tarefa_id
         AND estado_execucao NOT IN ('concluida', 'cancelada', 'obsoleta')
    ) THEN
      RAISE EXCEPTION 'A síntese não pode encerrar antes das tarefas-fonte';
    END IF;
    v_cobertura_derivada := public.investigacao_cobertura_sintese(
      v_tarefa.investigacao_id
    );
    IF v_cobertura_derivada IS NULL
       OR p_estado_cobertura IS DISTINCT FROM v_cobertura_derivada THEN
      RAISE EXCEPTION 'A cobertura da síntese deve refletir todas as fontes planejadas';
    END IF;
    IF jsonb_array_length(v_evidencias) <> 0 THEN
      RAISE EXCEPTION 'A síntese não publica evidências de fonte';
    END IF;
    IF p_estado_cobertura IN (
         'cobertura_incompleta', 'indisponivel',
         'reautenticacao_necessaria', 'erro_permanente'
       ) AND p_estado_resultado <> 'cobertura_incompleta' THEN
      RAISE EXCEPTION 'Cobertura geral incompleta exige resultado incompleto';
    END IF;
  ELSIF jsonb_array_length(v_alternativas) <> 0
     OR jsonb_array_length(v_pendencias) <> 0
     OR jsonb_array_length(v_ligacoes) <> 0 THEN
    RAISE EXCEPTION 'Tarefa-fonte publica somente suas evidências';
  ELSIF p_estado_cobertura = 'vazio_com_cobertura'
     AND jsonb_array_length(v_evidencias) <> 0 THEN
    RAISE EXCEPTION 'Fonte declarada vazia não pode publicar evidência';
  ELSIF p_estado_cobertura = 'completa'
     AND jsonb_array_length(v_evidencias) = 0 THEN
    RAISE EXCEPTION 'Fonte completa sem evidência deve declarar vazio com cobertura';
  ELSIF p_estado_cobertura IN (
      'cobertura_incompleta', 'indisponivel',
      'reautenticacao_necessaria', 'erro_permanente'
    ) AND p_estado_resultado <> 'cobertura_incompleta' THEN
    RAISE EXCEPTION 'Falha de cobertura não pode declarar resultado conclusivo';
  ELSIF p_estado_cobertura IN ('completa', 'vazio_com_cobertura')
     AND p_estado_resultado <> 'evidencia_insuficiente' THEN
    RAISE EXCEPTION 'Tarefa-fonte publica fatos; somente a síntese declara alternativas ou divergência';
  END IF;

  FOR v_item IN SELECT value FROM jsonb_array_elements(v_evidencias) LOOP
    IF jsonb_typeof(v_item) <> 'object'
       OR public.investigacao_jsonb_objeto_tamanho(v_item) <> 15
       OR v_item - ARRAY[
      'id_logico', 'fonte_tipo', 'fonte_tabela', 'fonte_registro_id',
      'registro_origem_ref', 'snapshot_fonte_ref',
      'linhagem', 'chave_natural_hash', 'referencia_opaca',
      'fatos_normalizados', 'provas_campos', 'provas_campos_canonico',
      'provas_campos_hash',
      'resumo_sanitizado', 'evidenciado_em'
       ] <> '{}'::jsonb
       OR jsonb_typeof(v_item -> 'id_logico') <> 'string'
       OR public.investigacao_uuid_texto_seguro(v_item ->> 'id_logico') IS NULL
       OR v_item ->> 'fonte_tipo' NOT IN (
         'nf', 'gta', 'ofx', 'ima', 'telegram', 'wey', 'b3', 'planilha', 'outro'
       )
       OR NOT EXISTS (
         SELECT 1
           FROM public.investigacao_adaptadores_config config
          WHERE config.adaptador = v_tarefa.adaptador
            AND config.adaptador_version = v_tarefa.adaptador_version
            AND config.habilitado
            AND v_item ->> 'fonte_tipo' = ANY(config.fontes_tipo_permitidas)
            AND (
              nullif(v_item ->> 'fonte_tabela', '') IS NULL
              OR nullif(v_item ->> 'fonte_tabela', '')
                   = ANY(config.tabelas_permitidas)
            )
       )
       OR (
         v_item ? 'fonte_tabela'
         AND v_item -> 'fonte_tabela' <> 'null'::jsonb
         AND v_item ->> 'fonte_tabela' NOT IN (
           'notas_fiscais_xml_raw', 'transacoes_banco_staging',
           'fontes_importacao', 'evidencias_negocio', 'negocios_candidatos',
           'operation_drafts', 'pending_actions', 'eventos', 'compras', 'vendas',
           'abates', 'pesagens_caderno', 'memorias_agentes', 'contexto_handoff'
         )
       )
       OR (
         v_item ? 'fonte_registro_id'
         AND v_item -> 'fonte_registro_id' <> 'null'::jsonb
         AND (
           jsonb_typeof(v_item -> 'fonte_registro_id') <> 'string'
           OR public.investigacao_uuid_texto_seguro(
             v_item ->> 'fonte_registro_id'
           ) IS NULL
         )
       )
       OR (
         nullif(v_item ->> 'fonte_registro_id', '') IS NULL
         AND (
           v_item -> 'registro_origem_ref' <> 'null'::jsonb
           OR v_item -> 'snapshot_fonte_ref' <> 'null'::jsonb
         )
       )
       OR (
         nullif(v_item ->> 'fonte_registro_id', '') IS NOT NULL
         AND (
           v_item ->> 'registro_origem_ref' !~ '^src_[0-9a-f]{32}$'
           OR v_item ->> 'snapshot_fonte_ref' !~ '^snp_[0-9a-f]{32}$'
         )
       )
       OR v_item ->> 'linhagem' !~ '^lin_[0-9a-f]{32}$'
       OR v_item ->> 'chave_natural_hash' !~ '^[0-9a-f]{64}$'
       OR (
         v_item ? 'referencia_opaca'
         AND v_item -> 'referencia_opaca' <> 'null'::jsonb
         AND (
           jsonb_typeof(v_item -> 'referencia_opaca') <> 'string'
           OR v_item ->> 'referencia_opaca' !~ '^ref_[0-9a-f]{32}$'
         )
       )
       OR jsonb_typeof(v_item -> 'fatos_normalizados') <> 'object'
       OR v_item -> 'fatos_normalizados' = '{}'::jsonb
       OR octet_length((v_item -> 'fatos_normalizados')::text) > 16384
       OR public.investigacao_jsonb_objeto_tamanho(
            v_item -> 'fatos_normalizados'
          ) > 32
       OR EXISTS (
         SELECT 1
           FROM jsonb_each(v_item -> 'fatos_normalizados') fato
          WHERE fato.key NOT IN (
            'operacao_id', 'negocio_id', 'negocio', 'data', 'data_compra',
            'data_emissao', 'data_abate', 'data_folha', 'quantidade',
            'cabecas', 'peso_total_kg', 'peso_medio_kg', 'peso_liquido_kg',
            'peso_carcaca_total', 'peso_kg', 'preco_arroba', 'valor_total',
            'valor_bruto', 'valor_liquido', 'prazo_dias',
            'prazo_recebimento', 'vencimento', 'desconto_barriga_kg',
            'documento', 'numero_nf', 'relacao_negocio', 'lote', 'contexto',
            'contexto_operacional', 'categoria', 'sexo', 'fornecedor',
            'contraparte', 'pagamento', 'valor', 'decisao_humana'
          )
             OR jsonb_typeof(fato.value) NOT IN (
               'string', 'number', 'boolean', 'null'
             )
             OR (
               jsonb_typeof(fato.value) = 'string'
               AND octet_length(fato.value #>> '{}') > 500
             )
       )
       OR NOT public.investigacao_json_publico_sanitizado(
         v_item -> 'fatos_normalizados'
       )
       OR NOT public.investigacao_provas_campos_validas(
         v_item -> 'fatos_normalizados', v_item -> 'provas_campos',
         v_item ->> 'provas_campos_canonico',
         v_item ->> 'provas_campos_hash'
       )
       OR EXISTS (
         SELECT 1 FROM jsonb_each(v_item -> 'provas_campos' -> 'campos') prova
          WHERE NOT public.investigacao_identidade_permitida_adaptador(
            v_tarefa.adaptador, nullif(prova.value ->> 'identidade_tipo', '')
          )
       )
       OR NOT public.investigacao_texto_publico_sanitizado(
         v_item ->> 'resumo_sanitizado'
       )
       OR octet_length(v_item ->> 'resumo_sanitizado') > 1000
       OR (
         v_item -> 'evidenciado_em' <> 'null'::jsonb
         AND (
           jsonb_typeof(v_item -> 'evidenciado_em') <> 'string'
           OR public.investigacao_instante_texto_seguro(
                v_item ->> 'evidenciado_em'
              ) IS NULL
         )
       ) THEN
      RAISE EXCEPTION 'Evidência contém campo fora do contrato da fonte';
    END IF;
  END LOOP;
  FOR v_item IN SELECT value FROM jsonb_array_elements(v_alternativas) LOOP
    IF jsonb_typeof(v_item) <> 'object'
       OR public.investigacao_jsonb_objeto_tamanho(v_item) <> 10
       OR v_item - ARRAY[
      'id_logico', 'chave_idempotencia', 'titulo', 'campos_snapshot',
      'confianca_campos', 'confianca_geral', 'classificacao',
      'regra_confianca_version', 'justificativa_sanitizada', 'origem_modelo'
       ] <> '{}'::jsonb
       OR jsonb_typeof(v_item -> 'id_logico') <> 'string'
       OR public.investigacao_uuid_texto_seguro(v_item ->> 'id_logico') IS NULL
       OR jsonb_typeof(v_item -> 'chave_idempotencia') <> 'string'
       OR btrim(v_item ->> 'chave_idempotencia') = ''
       OR jsonb_typeof(v_item -> 'titulo') <> 'string'
       OR NOT public.investigacao_texto_publico_sanitizado(v_item ->> 'titulo')
       OR jsonb_typeof(v_item -> 'campos_snapshot') <> 'object'
       OR v_item -> 'campos_snapshot' = '{}'::jsonb
       OR octet_length((v_item -> 'campos_snapshot')::text) > 16384
       OR public.investigacao_jsonb_objeto_tamanho(
            v_item -> 'campos_snapshot'
          ) > 32
       OR EXISTS (
         SELECT 1
           FROM jsonb_each(v_item -> 'campos_snapshot') fato
          WHERE fato.key NOT IN (
            'operacao_id', 'negocio_id', 'negocio', 'data', 'data_compra',
            'data_emissao', 'data_abate', 'data_folha', 'quantidade',
            'cabecas', 'peso_total_kg', 'peso_medio_kg', 'peso_liquido_kg',
            'peso_carcaca_total', 'peso_kg', 'preco_arroba', 'valor_total',
            'valor_bruto', 'valor_liquido', 'prazo_dias',
            'prazo_recebimento', 'vencimento', 'desconto_barriga_kg',
            'documento', 'numero_nf', 'relacao_negocio', 'lote', 'contexto',
            'contexto_operacional', 'categoria', 'sexo', 'fornecedor',
            'contraparte', 'pagamento', 'valor', 'decisao_humana'
          )
             OR jsonb_typeof(fato.value) NOT IN (
               'string', 'number', 'boolean', 'null'
             )
             OR (
               jsonb_typeof(fato.value) = 'string'
               AND octet_length(fato.value #>> '{}') > 500
             )
       )
       OR NOT public.investigacao_json_publico_sanitizado(
         v_item -> 'campos_snapshot'
       )
       OR NOT public.investigacao_confianca_campos_valida(
         v_item -> 'confianca_campos'
       )
       OR jsonb_typeof(v_item -> 'confianca_geral') <> 'number'
       OR v_item ->> 'classificacao' NOT IN (
         'possivel', 'provavel', 'forte', 'ambiguo'
       )
       OR v_item ->> 'regra_confianca_version'
            IS DISTINCT FROM 'confianca-deterministica-v2'
       OR NOT public.investigacao_texto_publico_sanitizado(
         v_item ->> 'justificativa_sanitizada'
       )
       OR octet_length(v_item ->> 'titulo') > 240
       OR octet_length(v_item ->> 'justificativa_sanitizada') > 1000
       OR jsonb_typeof(v_item -> 'origem_modelo') <> 'boolean'
       OR EXISTS (
         SELECT 1
           FROM jsonb_object_keys(v_item -> 'campos_snapshot') campo
          WHERE NOT (v_item -> 'confianca_campos' ? campo)
       )
       OR EXISTS (
         SELECT 1
           FROM jsonb_object_keys(v_item -> 'confianca_campos') campo
          WHERE NOT (v_item -> 'campos_snapshot' ? campo)
       ) THEN
      RAISE EXCEPTION 'Alternativa contém campo fora do contrato do correlator';
    END IF;
    SELECT EXISTS (
      SELECT 1
        FROM unnest(v_campos_obrigatorios) campo
       WHERE NOT (v_item -> 'campos_snapshot' ? campo)
          OR v_item -> 'campos_snapshot' -> campo = 'null'::jsonb
          OR (
            jsonb_typeof(v_item -> 'campos_snapshot' -> campo) = 'string'
            AND btrim(v_item -> 'campos_snapshot' ->> campo) = ''
          )
    ) INTO v_tem_campo_ausente;
    IF v_tem_campo_ausente THEN
      v_existe_alternativa_parcial := true;
      IF (v_item ->> 'confianca_geral')::numeric IS DISTINCT FROM 0::numeric
         OR v_item ->> 'classificacao' IS DISTINCT FROM 'ambiguo' THEN
        RAISE EXCEPTION 'Alternativa parcial precisa permanecer ambígua e sem confiança geral';
      END IF;
    ELSE
      SELECT min((avaliacao.value ->> 'confianca')::numeric)
        INTO v_confianca_esperada
        FROM jsonb_each(v_item -> 'confianca_campos') avaliacao;
      IF p_estado_cobertura IS DISTINCT FROM 'completa' THEN
        v_confianca_esperada := least(v_confianca_esperada, 0.35);
      END IF;
      v_classificacao_esperada := CASE v_confianca_esperada
        WHEN 0::numeric THEN 'ambiguo'
        WHEN 0.35::numeric THEN 'possivel'
        WHEN 0.7::numeric THEN 'provavel'
        WHEN 0.95::numeric THEN 'forte'
        ELSE NULL
      END;
      IF v_confianca_esperada IS NULL
         OR v_classificacao_esperada IS NULL
         OR (v_item ->> 'confianca_geral')::numeric
              IS DISTINCT FROM v_confianca_esperada
         OR v_item ->> 'classificacao'
              IS DISTINCT FROM v_classificacao_esperada THEN
        RAISE EXCEPTION 'Confiança geral não corresponde aos campos e à cobertura';
      END IF;
    END IF;
  END LOOP;
  FOR v_item IN SELECT value FROM jsonb_array_elements(v_pendencias) LOOP
    IF jsonb_typeof(v_item) <> 'object'
       OR public.investigacao_jsonb_objeto_tamanho(v_item) <> 7
       OR v_item - ARRAY[
      'id_logico', 'chave_idempotencia', 'tipo', 'campo', 'fonte_tipo',
      'descricao_sanitizada', 'estado'
    ] <> '{}'::jsonb THEN
      RAISE EXCEPTION 'Pendência contém campo fora do contrato da síntese';
    END IF;
  END LOOP;
  FOR v_item IN SELECT value FROM jsonb_array_elements(v_ligacoes) LOOP
    IF jsonb_typeof(v_item) <> 'object'
       OR public.investigacao_jsonb_objeto_tamanho(v_item) <> 6
       OR v_item - ARRAY[
      'alternativa_id_logico', 'evidencia_id_logico',
      'evidencia_tarefa_id', 'papel',
      'campos_suportados', 'campos_contestados'
    ] <> '{}'::jsonb
       OR public.investigacao_uuid_texto_seguro(
         v_item ->> 'evidencia_tarefa_id'
       ) IS NULL
       OR v_item ->> 'papel' NOT IN ('favoravel', 'contraria')
       OR jsonb_typeof(v_item -> 'campos_suportados') <> 'array'
       OR jsonb_typeof(v_item -> 'campos_contestados') <> 'array' THEN
      RAISE EXCEPTION 'Ligação contém campo fora do contrato da síntese';
    END IF;
  END LOOP;

  FOR v_item IN SELECT value FROM jsonb_array_elements(v_evidencias) LOOP
    v_proveniencia := CASE
      WHEN public.investigacao_uuid_texto_seguro(
             nullif(v_item ->> 'fonte_registro_id', '')
           ) IS NOT NULL
        THEN public.investigacao_proveniencia_registro(
          v_tarefa.adaptador,
          v_tarefa.adaptador_version,
          nullif(v_item ->> 'fonte_tabela', ''),
          public.investigacao_uuid_texto_seguro(
            nullif(v_item ->> 'fonte_registro_id', '')
          )
        )
      ELSE NULL
    END;
    IF nullif(v_item ->> 'fonte_registro_id', '') IS NOT NULL
       AND v_proveniencia IS NULL THEN
      RAISE EXCEPTION 'Registro de origem da evidência não existe no snapshot atual';
    END IF;
    IF v_proveniencia IS NOT NULL
       AND (
         v_item ->> 'registro_origem_ref'
           IS DISTINCT FROM v_proveniencia ->> 'registro_ref'
         OR v_item ->> 'snapshot_fonte_ref'
           IS DISTINCT FROM v_proveniencia ->> 'snapshot_ref'
       ) THEN
      RAISE EXCEPTION 'A fonte mudou desde a leitura assinada pelo adaptador';
    END IF;
    INSERT INTO public.investigacao_evidencias (
      id_logico, investigacao_id, tarefa_id, tarefa_lease_token,
      tarefa_fencing_token, fonte_tipo, fonte_tabela, fonte_registro_id,
      origem_classe, autoridade_fonte, dataset_ref, registro_origem_ref,
      snapshot_fonte_ref, ancestral_ref, linhagem, chave_natural_hash,
      referencia_opaca,
      fatos_normalizados,
      classificacao, confianca, provas_campos, provas_campos_canonico,
      provas_campos_hash,
      regra_confianca_version, resumo_sanitizado, evidenciado_em
    ) VALUES (
      v_item ->> 'id_logico', v_tarefa.investigacao_id, v_tarefa.id,
      p_lease_token, p_fencing_token, v_item ->> 'fonte_tipo',
      nullif(v_item ->> 'fonte_tabela', ''),
      public.investigacao_uuid_texto_seguro(
        nullif(v_item ->> 'fonte_registro_id', '')
      ),
      CASE
        WHEN nullif(v_item ->> 'fonte_tabela', '') = ANY(v_config.tabelas_nativas)
         AND public.investigacao_uuid_texto_seguro(
               nullif(v_item ->> 'fonte_registro_id', '')
             ) IS NOT NULL
          THEN 'nativa'
        ELSE 'derivada'
      END,
      v_config.autoridade_fonte,
      'dst_' || substr(encode(extensions.digest(convert_to(
        jsonb_build_array(
          v_config.autoridade_fonte,
          coalesce(nullif(v_item ->> 'fonte_tabela', ''), 'sem_tabela')
        )::text, 'UTF8'
      ), 'sha256'), 'hex'), 1, 32),
      coalesce(
        v_proveniencia ->> 'registro_ref',
        'src_' || substr(encode(extensions.digest(convert_to(
          jsonb_build_array('derivada', v_tarefa.adaptador,
            v_item ->> 'chave_natural_hash')::text, 'UTF8'
        ), 'sha256'), 'hex'), 1, 32)
      ),
      coalesce(
        v_proveniencia ->> 'snapshot_ref',
        'snp_' || substr(encode(extensions.digest(convert_to(
          jsonb_build_array('derivada', v_tarefa.id,
            v_item ->> 'chave_natural_hash')::text, 'UTF8'
        ), 'sha256'), 'hex'), 1, 32)
      ),
      coalesce(
        v_proveniencia ->> 'ancestral_ref',
        'anc_' || substr(encode(extensions.digest(convert_to(
          jsonb_build_array('derivada', v_tarefa.adaptador,
            v_item ->> 'chave_natural_hash')::text, 'UTF8'
        ), 'sha256'), 'hex'), 1, 32)
      ),
      v_item ->> 'linhagem', v_item ->> 'chave_natural_hash',
      nullif(v_item ->> 'referencia_opaca', ''),
      coalesce(v_item -> 'fatos_normalizados', '{}'::jsonb),
      'inconclusivo', NULL, v_item -> 'provas_campos',
      v_item ->> 'provas_campos_canonico', v_item ->> 'provas_campos_hash',
      'aguardando-correlator-v1',
      v_item ->> 'resumo_sanitizado',
      public.investigacao_instante_texto_seguro(
        nullif(v_item ->> 'evidenciado_em', '')
      )
    );
  END LOOP;

  FOR v_item IN SELECT value FROM jsonb_array_elements(v_alternativas) LOOP
    INSERT INTO public.investigacao_alternativas (
      id_logico, investigacao_id, tarefa_id, tarefa_lease_token,
      tarefa_fencing_token, chave_idempotencia, titulo, campos_snapshot,
      confianca_campos, confianca_geral, classificacao,
      regra_confianca_version, justificativa_sanitizada, origem_modelo
    ) VALUES (
      v_item ->> 'id_logico', v_tarefa.investigacao_id, v_tarefa.id,
      p_lease_token, p_fencing_token, v_item ->> 'chave_idempotencia',
      v_item ->> 'titulo', coalesce(v_item -> 'campos_snapshot', '{}'::jsonb),
      coalesce(v_item -> 'confianca_campos', '{}'::jsonb),
      nullif(v_item ->> 'confianca_geral', '')::numeric,
      v_item ->> 'classificacao', v_item ->> 'regra_confianca_version',
      v_item ->> 'justificativa_sanitizada',
      coalesce((v_item ->> 'origem_modelo')::boolean, false)
    );
  END LOOP;

  FOR v_item IN SELECT value FROM jsonb_array_elements(v_pendencias) LOOP
    INSERT INTO public.investigacao_pendencias (
      id_logico, investigacao_id, tarefa_id, tarefa_lease_token,
      tarefa_fencing_token, chave_idempotencia, tipo, campo, fonte_tipo,
      descricao_sanitizada, estado
    ) VALUES (
      v_item ->> 'id_logico', v_tarefa.investigacao_id, v_tarefa.id,
      p_lease_token, p_fencing_token, v_item ->> 'chave_idempotencia',
      v_item ->> 'tipo', nullif(v_item ->> 'campo', ''),
      nullif(v_item ->> 'fonte_tipo', ''), v_item ->> 'descricao_sanitizada',
      coalesce(v_item ->> 'estado', 'aberta')
    );
  END LOOP;

  FOR v_item IN SELECT value FROM jsonb_array_elements(v_ligacoes) LOOP
    SELECT alternativa.id INTO STRICT v_alternativa_id
      FROM public.investigacao_alternativas alternativa
     WHERE alternativa.investigacao_id = v_tarefa.investigacao_id
       AND alternativa.tarefa_id = v_tarefa.id
       AND alternativa.tarefa_lease_token = p_lease_token
       AND alternativa.tarefa_fencing_token = p_fencing_token
       AND alternativa.id_logico = v_item ->> 'alternativa_id_logico';
    SELECT evidencia.id INTO STRICT v_evidencia_id
      FROM public.investigacao_evidencias evidencia
      JOIN public.investigacao_tarefas tarefa_fonte
        ON tarefa_fonte.id = evidencia.tarefa_id
       AND tarefa_fonte.investigacao_id = evidencia.investigacao_id
     WHERE evidencia.investigacao_id = v_tarefa.investigacao_id
       AND evidencia.id_logico = v_item ->> 'evidencia_id_logico'
       AND evidencia.tarefa_id = public.investigacao_uuid_texto_seguro(
         v_item ->> 'evidencia_tarefa_id'
       )
       AND tarefa_fonte.estado_execucao = 'concluida'
       AND tarefa_fonte.resultado_lease_token = evidencia.tarefa_lease_token
       AND tarefa_fonte.resultado_fencing_token = evidencia.tarefa_fencing_token;
    IF v_item ->> 'papel' = 'favoravel' AND (
      ARRAY(
        SELECT campo
          FROM jsonb_array_elements_text(v_item -> 'campos_suportados') campo
         ORDER BY campo
      ) IS DISTINCT FROM ARRAY(
        SELECT campo.key
          FROM public.investigacao_alternativas alternativa
          JOIN public.investigacao_evidencias evidencia
            ON evidencia.id = v_evidencia_id
           AND evidencia.investigacao_id = alternativa.investigacao_id
          CROSS JOIN LATERAL jsonb_each(alternativa.campos_snapshot) campo
         WHERE alternativa.id = v_alternativa_id
           AND alternativa.investigacao_id = v_tarefa.investigacao_id
           AND evidencia.fatos_normalizados ? campo.key
           AND evidencia.fatos_normalizados -> campo.key
                 IS NOT DISTINCT FROM campo.value
         ORDER BY campo.key
      )
      OR EXISTS (
        SELECT 1
          FROM public.investigacao_alternativas alternativa
          JOIN public.investigacao_evidencias evidencia
            ON evidencia.id = v_evidencia_id
           AND evidencia.investigacao_id = alternativa.investigacao_id
          CROSS JOIN LATERAL jsonb_each(alternativa.campos_snapshot) campo
         WHERE alternativa.id = v_alternativa_id
           AND alternativa.investigacao_id = v_tarefa.investigacao_id
           AND evidencia.fatos_normalizados ? campo.key
           AND evidencia.fatos_normalizados -> campo.key
                 IS DISTINCT FROM campo.value
      )
    ) THEN
      RAISE EXCEPTION 'Evidência favorável precisa confirmar exatamente os campos declarados';
    END IF;
    IF v_item ->> 'papel' = 'contraria' AND ARRAY(
      SELECT campo
        FROM jsonb_array_elements_text(v_item -> 'campos_contestados') campo
       ORDER BY campo
    ) IS DISTINCT FROM ARRAY(
      SELECT campo.key
        FROM public.investigacao_alternativas alternativa
        JOIN public.investigacao_evidencias evidencia
          ON evidencia.id = v_evidencia_id
         AND evidencia.investigacao_id = alternativa.investigacao_id
        CROSS JOIN LATERAL jsonb_each(alternativa.campos_snapshot) campo
       WHERE alternativa.id = v_alternativa_id
         AND alternativa.investigacao_id = v_tarefa.investigacao_id
         AND evidencia.fatos_normalizados ? campo.key
         AND evidencia.fatos_normalizados -> campo.key
               IS DISTINCT FROM campo.value
       ORDER BY campo.key
    ) THEN
      RAISE EXCEPTION 'Evidência contrária precisa contestar exatamente os campos declarados';
    END IF;
    INSERT INTO public.investigacao_alternativa_evidencias (
      investigacao_id, alternativa_id, evidencia_id, papel,
      campos_suportados, campos_contestados
    ) VALUES (
      v_tarefa.investigacao_id, v_alternativa_id, v_evidencia_id,
      v_item ->> 'papel',
      ARRAY(SELECT jsonb_array_elements_text(v_item -> 'campos_suportados')),
      ARRAY(SELECT jsonb_array_elements_text(v_item -> 'campos_contestados'))
    );
  END LOOP;

  IF v_tarefa.adaptador = 'sintese'
     AND public.investigacao_alternativas_suportadas(
       v_tarefa.investigacao_id, v_tarefa.id, p_lease_token, p_fencing_token
     ) IS NOT TRUE THEN
    RAISE EXCEPTION 'Alternativa sem evidência favorável do mesmo campo, valor e linhagem';
  END IF;

  SELECT count(*) INTO v_quantidade_alternativas
    FROM public.investigacao_alternativas alternativa
   WHERE alternativa.investigacao_id = v_tarefa.investigacao_id
     AND alternativa.tarefa_id = v_tarefa.id
     AND alternativa.tarefa_lease_token = p_lease_token
     AND alternativa.tarefa_fencing_token = p_fencing_token
     ;
  SELECT count(DISTINCT alternativa.campos_snapshot)
    INTO v_quantidade_snapshots
    FROM public.investigacao_alternativas alternativa
   WHERE alternativa.investigacao_id = v_tarefa.investigacao_id
     AND alternativa.tarefa_id = v_tarefa.id
     AND alternativa.tarefa_lease_token = p_lease_token
     AND alternativa.tarefa_fencing_token = p_fencing_token;
  SELECT count(*) INTO v_quantidade_pendencias
    FROM public.investigacao_pendencias pendencia
   WHERE pendencia.investigacao_id = v_tarefa.investigacao_id
     AND pendencia.tarefa_id = v_tarefa.id
     AND pendencia.tarefa_lease_token = p_lease_token
     AND pendencia.tarefa_fencing_token = p_fencing_token
     AND pendencia.estado = 'aberta';

  IF v_tarefa.adaptador = 'sintese' THEN
    SELECT EXISTS (
      SELECT 1
        FROM public.investigacao_alternativa_evidencias ligacao
        JOIN public.investigacao_alternativas alternativa
          ON alternativa.id = ligacao.alternativa_id
       WHERE ligacao.investigacao_id = v_tarefa.investigacao_id
         AND alternativa.tarefa_id = v_tarefa.id
         AND alternativa.tarefa_lease_token = p_lease_token
         AND alternativa.tarefa_fencing_token = p_fencing_token
         AND ligacao.papel = 'contraria'
    ) INTO v_tem_contraprova;

    IF p_estado_cobertura IN (
      'cobertura_incompleta', 'indisponivel',
      'reautenticacao_necessaria', 'erro_permanente'
    ) THEN
      v_estado_resultado_derivado := 'cobertura_incompleta';
    ELSIF v_tem_contraprova THEN
      v_estado_resultado_derivado := 'divergente';
    ELSIF v_quantidade_alternativas = 0
       OR v_existe_alternativa_parcial
       OR EXISTS (
         SELECT 1
           FROM public.investigacao_alternativas alternativa
          WHERE alternativa.investigacao_id = v_tarefa.investigacao_id
            AND alternativa.tarefa_id = v_tarefa.id
            AND alternativa.tarefa_lease_token = p_lease_token
            AND alternativa.tarefa_fencing_token = p_fencing_token
            AND alternativa.confianca_geral < 0.7
       ) THEN
      v_estado_resultado_derivado := 'evidencia_insuficiente';
    ELSIF v_quantidade_alternativas = 1
       AND v_quantidade_snapshots = 1 THEN
      v_estado_resultado_derivado := 'alternativa_unica';
    ELSIF v_quantidade_alternativas >= 2
       AND v_quantidade_snapshots >= 2 THEN
      v_estado_resultado_derivado := 'alternativas_multiplas';
    ELSE
      v_estado_resultado_derivado := 'evidencia_insuficiente';
    END IF;
    IF p_estado_resultado IS DISTINCT FROM v_estado_resultado_derivado THEN
      RAISE EXCEPTION 'Resultado declarado não corresponde ao estado derivado das provas';
    END IF;

    IF v_existe_alternativa_parcial OR v_quantidade_alternativas = 0 THEN
      IF p_estado_resultado NOT IN (
        'evidencia_insuficiente', 'cobertura_incompleta'
      ) THEN
        RAISE EXCEPTION 'Alternativa parcial não pode encerrar a investigação como conclusiva';
      END IF;
      IF v_quantidade_pendencias = 0 OR EXISTS (
        SELECT 1
          FROM unnest(v_campos_obrigatorios) campo
         WHERE (
           v_quantidade_alternativas = 0
           OR EXISTS (
           SELECT 1
             FROM jsonb_array_elements(v_alternativas) alternativa
            WHERE NOT (alternativa -> 'campos_snapshot' ? campo)
               OR alternativa -> 'campos_snapshot' -> campo = 'null'::jsonb
               OR (
                 jsonb_typeof(alternativa -> 'campos_snapshot' -> campo) = 'string'
                 AND btrim(alternativa -> 'campos_snapshot' ->> campo) = ''
               )
           )
         )
           AND NOT EXISTS (
             SELECT 1
               FROM public.investigacao_pendencias pendencia
              WHERE pendencia.investigacao_id = v_tarefa.investigacao_id
                AND pendencia.tarefa_id = v_tarefa.id
                AND pendencia.tarefa_lease_token = p_lease_token
                AND pendencia.tarefa_fencing_token = p_fencing_token
                AND pendencia.estado = 'aberta'
                AND pendencia.campo = campo
           )
      ) THEN
        RAISE EXCEPTION 'Alternativa parcial exige pendência aberta para cada campo ausente';
      END IF;
    END IF;
    IF p_estado_resultado = 'alternativa_unica' AND v_quantidade_alternativas <> 1 THEN
      RAISE EXCEPTION 'Resultado único exige exatamente uma alternativa explicável';
    ELSIF p_estado_resultado = 'alternativas_multiplas' AND (
      v_quantidade_alternativas < 2 OR v_quantidade_snapshots < 2
    ) THEN
      RAISE EXCEPTION 'Resultado múltiplo exige ao menos duas versões realmente distintas';
    ELSIF p_estado_resultado = 'divergente' AND (
      v_quantidade_alternativas = 0 OR NOT EXISTS (
        SELECT 1
          FROM public.investigacao_alternativa_evidencias ligacao
          JOIN public.investigacao_alternativas alternativa
            ON alternativa.id = ligacao.alternativa_id
         WHERE ligacao.investigacao_id = v_tarefa.investigacao_id
           AND alternativa.tarefa_id = v_tarefa.id
           AND alternativa.tarefa_lease_token = p_lease_token
           AND alternativa.tarefa_fencing_token = p_fencing_token
           AND ligacao.papel = 'contraria'
      )
    ) THEN
      RAISE EXCEPTION 'Resultado divergente exige alternativa e evidência contrária';
    ELSIF p_estado_resultado IN ('evidencia_insuficiente', 'cobertura_incompleta')
       AND v_quantidade_pendencias = 0 THEN
      RAISE EXCEPTION 'Resultado incompleto exige pendência humana explícita';
    END IF;
    IF p_estado_resultado IN (
      'alternativa_unica', 'alternativas_multiplas', 'divergente'
    ) AND EXISTS (
      SELECT 1
        FROM public.investigacao_alternativas alternativa
       WHERE alternativa.investigacao_id = v_tarefa.investigacao_id
         AND alternativa.tarefa_id = v_tarefa.id
         AND alternativa.tarefa_lease_token = p_lease_token
         AND alternativa.tarefa_fencing_token = p_fencing_token
         AND alternativa.classificacao IN ('possivel', 'provavel', 'forte', 'ambiguo')
         AND NOT EXISTS (
           SELECT 1
             FROM public.investigacao_alternativa_evidencias ligacao
            WHERE ligacao.investigacao_id = alternativa.investigacao_id
              AND ligacao.alternativa_id = alternativa.id
              AND ligacao.papel = 'favoravel'
         )
    ) THEN
      RAISE EXCEPTION 'Toda alternativa precisa apontar sua evidência favorável';
    END IF;
    IF NOT public.investigacao_evidencias_fontes_atuais(
      v_tarefa.investigacao_id
    ) THEN
      RAISE EXCEPTION 'Uma fonte mudou durante a síntese; reconsulte antes de concluir';
    END IF;
  END IF;

  UPDATE public.investigacao_tarefas
     SET estado_execucao = 'concluida', estado_cobertura = p_estado_cobertura,
         prova_cobertura = CASE
           WHEN v_tarefa.adaptador = 'sintese' THEN NULL
           ELSE p_atestado_cobertura
         END,
         estado_resultado = p_estado_resultado,
         resumo_sanitizado = p_resumo_sanitizado, erro_codigo = p_erro_codigo,
         erro_sanitizado = p_erro_sanitizado, concluido_em = clock_timestamp(),
         resultado_lease_token = p_lease_token,
         resultado_fencing_token = p_fencing_token,
         resultado_pedido_hash = v_pedido_hash, lease_executor = NULL,
         lease_token = NULL, lease_expira_em = NULL, lease_chave_id = NULL
   WHERE id = v_tarefa.id;

  IF v_tarefa.adaptador = 'sintese' THEN
    UPDATE public.investigacoes_revisao
       SET estado_execucao = 'concluida', estado_resultado = p_estado_resultado,
           resumo_sanitizado = p_resumo_sanitizado,
           concluida_em = clock_timestamp()
     WHERE id = v_tarefa.investigacao_id
       AND estado_execucao NOT IN ('cancelada', 'obsoleta');
  END IF;

  INSERT INTO public.investigacao_eventos (
    investigacao_id, chave_idempotencia, tipo, referencia_entidade,
    resumo_sanitizado
  ) VALUES (
    v_tarefa.investigacao_id,
    'tarefa-concluida:' || p_tarefa_id::text || ':' || p_lease_token::text,
    'tarefa_concluida', p_tarefa_id::text,
    coalesce(p_resumo_sanitizado, 'Tarefa concluída sem resumo público.')
  ) ON CONFLICT (chave_idempotencia) DO NOTHING;
  RETURN jsonb_build_object(
    'publicado', true, 'tarefa_id', v_tarefa.id,
    'fencing_token', p_fencing_token,
    'evidencias', jsonb_array_length(v_evidencias),
    'alternativas', jsonb_array_length(v_alternativas),
    'pendencias', jsonb_array_length(v_pendencias)
  );
END;
$$;

CREATE OR REPLACE FUNCTION public.concluir_tarefa_investigacao(
  p_tarefa_id uuid,
  p_lease_token uuid,
  p_fencing_token bigint,
  p_estado_cobertura text,
  p_estado_resultado text,
  p_resumo_sanitizado text DEFAULT NULL,
  p_erro_codigo text DEFAULT NULL,
  p_erro_sanitizado text DEFAULT NULL,
  p_prova_cobertura jsonb DEFAULT NULL
)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
  PERFORM public.publicar_resultado_tarefa_investigacao(
    p_tarefa_id, p_lease_token, p_fencing_token, p_estado_cobertura,
    p_estado_resultado,
    '{}'::jsonb, p_prova_cobertura,
    p_resumo_sanitizado, p_erro_codigo,
    p_erro_sanitizado
  );
  RETURN true;
END;
$$;

-- Fotografia completa da rodada de cada tarefa. O mapa é comparado somente
-- depois que pai e todas as tarefas estão travados; o máximo isolado não é
-- suficiente porque uma tarefa não máxima pode avançar sem mudar o máximo.
CREATE OR REPLACE FUNCTION public.investigacao_fencing_snapshot(
  p_investigacao_id uuid
)
RETURNS jsonb
LANGUAGE sql
STABLE
STRICT
SECURITY INVOKER
SET search_path = pg_catalog, public
AS $$
  SELECT coalesce(
    jsonb_object_agg(
      tarefa.id::text,
      jsonb_build_object(
        'fencing_token', tarefa.fencing_token,
        'estado_execucao', tarefa.estado_execucao,
        'resultado_lease_token', tarefa.resultado_lease_token,
        'resultado_fencing_token', tarefa.resultado_fencing_token
      ) ORDER BY tarefa.id
    ),
    '{}'::jsonb
  )
    FROM public.investigacao_tarefas tarefa
   WHERE tarefa.investigacao_id = p_investigacao_id;
$$;

-- Uma investigação concluída contra uma versão anterior do formulário não pode
-- permanecer bloqueando a revisão para sempre. Esta RPC só aceita o snapshot e
-- o mapa completo de fencing observado pelo mediador, prova a edição sob lock e
-- torna pai e tarefas obsoletos na mesma transação, preservando resultados e
-- registrando o motivo na trilha append-only.
CREATE OR REPLACE FUNCTION public.obsoletar_investigacao_por_mudanca_draft(
  p_investigacao_id uuid,
  p_source_draft_atualizado_em timestamptz,
  p_fencing_esperado jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_investigacao public.investigacoes_revisao%ROWTYPE;
  v_draft public.operation_drafts%ROWTYPE;
  v_draft_id uuid;
  v_snapshot timestamptz;
  v_fencing_atual jsonb;
  v_evento_chave text;
  v_autorizacao_hash text;
BEGIN
  IF p_investigacao_id IS NULL
     OR p_source_draft_atualizado_em IS NULL
     OR p_fencing_esperado IS NULL
     OR jsonb_typeof(p_fencing_esperado) IS DISTINCT FROM 'object'
     OR p_fencing_esperado = '{}'::jsonb THEN
    RAISE EXCEPTION 'Obsolescência exige investigação, snapshot e fencing válidos';
  END IF;

  SELECT source_draft_id, source_draft_atualizado_em
    INTO v_draft_id, v_snapshot
    FROM public.investigacoes_revisao
   WHERE id = p_investigacao_id;
  IF NOT FOUND OR v_draft_id IS NULL OR v_snapshot IS NULL THEN
    RAISE EXCEPTION 'Investigação vinculada ao rascunho não encontrada';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('investigacao-draft:' || v_draft_id::text, 0)
  );

  SELECT * INTO v_investigacao
    FROM public.investigacoes_revisao
   WHERE id = p_investigacao_id
   FOR UPDATE;
  IF v_investigacao.source_draft_id IS DISTINCT FROM v_draft_id
     OR v_investigacao.source_draft_atualizado_em IS DISTINCT FROM v_snapshot
     OR v_snapshot IS DISTINCT FROM p_source_draft_atualizado_em THEN
    RAISE EXCEPTION 'Snapshot da investigação mudou; recarregue antes de obsoletar';
  END IF;
  v_evento_chave := 'investigacao-obsoleta-draft:'
    || p_investigacao_id::text || ':' || md5(p_fencing_esperado::text);
  IF v_investigacao.estado_execucao = 'obsoleta' THEN
    IF NOT EXISTS (
      SELECT 1 FROM public.investigacao_eventos
       WHERE chave_idempotencia = v_evento_chave
         AND investigacao_id = p_investigacao_id
         AND tipo = 'investigacao_obsoleta'
    ) THEN
      RAISE EXCEPTION 'A rodada obsoleta não corresponde ao fencing informado';
    END IF;
    RETURN jsonb_build_object(
      'obsoleta', false, 'anexada', false,
      'motivo', 'investigacao_ja_obsoleta'
    );
  END IF;
  IF v_investigacao.estado_execucao NOT IN (
       'pendente', 'em_execucao', 'aguardando_retentativa', 'concluida'
     ) OR v_investigacao.anexado_em IS NOT NULL THEN
    RAISE EXCEPTION 'Somente investigação ativa e ainda não anexada pode ficar obsoleta';
  END IF;

  PERFORM 1
    FROM public.investigacao_tarefas
   WHERE investigacao_id = p_investigacao_id
   ORDER BY id
   FOR UPDATE;
  v_fencing_atual := public.investigacao_fencing_snapshot(
    p_investigacao_id
  );
  IF v_fencing_atual IS DISTINCT FROM p_fencing_esperado THEN
    RAISE EXCEPTION 'Fencing da investigação mudou; recarregue antes de obsoletar';
  END IF;

  SELECT * INTO v_draft
    FROM public.operation_drafts
   WHERE id = v_draft_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Rascunho de origem não encontrado';
  END IF;
  IF v_draft.atualizado_em IS NOT DISTINCT FROM v_snapshot THEN
    RAISE EXCEPTION 'O rascunho não mudou; a investigação continua válida';
  END IF;

  UPDATE public.investigacao_tarefas
     SET estado_execucao = 'obsoleta',
         lease_executor = NULL, lease_token = NULL, lease_expira_em = NULL,
         lease_chave_id = NULL
   WHERE investigacao_id = p_investigacao_id;
  v_autorizacao_hash := encode(extensions.digest(convert_to(
    jsonb_build_object(
      'investigacao_id', p_investigacao_id,
      'motivo', 'pre_revisao_stale'
    )::text, 'UTF8'
  ), 'sha256'), 'hex');
  INSERT INTO public.investigacao_autorizacoes_corretiva (
    txid, backend_pid, recurso, investigacao_id,
    operation_draft_id, pending_action_id, pedido_hash
  ) VALUES (
    txid_current(), pg_backend_pid(), 'obsoletar_investigacao',
    p_investigacao_id, v_draft_id, p_investigacao_id,
    v_autorizacao_hash
  );
  UPDATE public.investigacoes_revisao
     SET estado_execucao = 'obsoleta', estado_resultado = NULL,
         concluida_em = NULL,
         obsolescencia_motivo = 'pre_revisao_stale',
         promocao_ativa_id = NULL,
         resumo_sanitizado =
           'A revisão mudou; o resultado anterior foi preservado apenas para auditoria.'
  WHERE id = p_investigacao_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'A investigação mudou durante a obsolescência';
  END IF;

  INSERT INTO public.investigacao_eventos (
    investigacao_id, chave_idempotencia, tipo, referencia_entidade,
    resumo_sanitizado
  ) VALUES (
    p_investigacao_id, v_evento_chave, 'investigacao_obsoleta',
    v_draft_id::text,
    'A revisão foi editada; uma nova investigação deve usar o retrato atualizado.'
  ) ON CONFLICT (chave_idempotencia) DO NOTHING;

  RETURN jsonb_build_object(
    'obsoleta', true, 'anexada', false,
    'motivo', 'rascunho_alterado',
    'novo_snapshot', v_draft.atualizado_em
  );
END;
$$;

-- Uma rodada anterior ao rascunho pode ficar stale quando qualquer candidato
-- muda. Esta transição exige os snapshots e o mapa de fencing exatos, preserva
-- todos os resultados antigos e libera somente o bloqueio daquela fotografia.
CREATE OR REPLACE FUNCTION public.obsoletar_investigacao_por_mudanca_candidatos(
  p_investigacao_id uuid,
  p_source_candidatos_atualizados_em jsonb,
  p_fencing_esperado jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_investigacao public.investigacoes_revisao%ROWTYPE;
  v_ids uuid[];
  v_ids_lock uuid[];
  v_snapshots jsonb;
  v_id uuid;
  v_fencing_atual jsonb;
  v_evento_chave text;
  v_autorizacao_hash text;
BEGIN
  IF p_investigacao_id IS NULL
     OR p_source_candidatos_atualizados_em IS NULL
     OR jsonb_typeof(p_source_candidatos_atualizados_em) IS DISTINCT FROM 'object'
     OR p_fencing_esperado IS NULL
     OR jsonb_typeof(p_fencing_esperado) IS DISTINCT FROM 'object'
     OR p_fencing_esperado = '{}'::jsonb THEN
    RAISE EXCEPTION 'Obsolescência exige investigação, mapa e fencing válidos';
  END IF;
  SELECT negocio_candidato_ids, source_candidatos_atualizados_em
    INTO v_ids, v_snapshots
    FROM public.investigacoes_revisao
   WHERE id = p_investigacao_id;
  IF NOT FOUND OR cardinality(v_ids) = 0 THEN
    RAISE EXCEPTION 'Investigação de candidatos não encontrada';
  END IF;
  IF v_snapshots IS DISTINCT FROM p_source_candidatos_atualizados_em THEN
    RAISE EXCEPTION 'Mapa da investigação mudou; recarregue antes de obsoletar';
  END IF;
  SELECT coalesce(array_agg(item ORDER BY item), '{}'::uuid[])
    INTO v_ids_lock
    FROM (
      SELECT DISTINCT unnest(v_ids) AS item
    ) AS ids;
  FOREACH v_id IN ARRAY v_ids_lock LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended('investigacao-candidato:' || v_id::text, 0)
    );
  END LOOP;
  SELECT * INTO v_investigacao
    FROM public.investigacoes_revisao
   WHERE id = p_investigacao_id
   FOR UPDATE;
  IF v_investigacao.source_draft_id IS NOT NULL
     OR v_investigacao.negocio_candidato_ids IS DISTINCT FROM v_ids
     OR v_investigacao.source_candidatos_atualizados_em IS DISTINCT FROM v_snapshots THEN
    RAISE EXCEPTION 'A origem da investigação mudou; recarregue antes de obsoletar';
  END IF;
  v_evento_chave := 'investigacao-obsoleta-candidatos:'
    || p_investigacao_id::text || ':' || md5(p_fencing_esperado::text);
  IF v_investigacao.estado_execucao = 'obsoleta' THEN
    IF NOT EXISTS (
      SELECT 1 FROM public.investigacao_eventos
       WHERE chave_idempotencia = v_evento_chave
         AND investigacao_id = p_investigacao_id
         AND tipo = 'investigacao_obsoleta'
    ) THEN
      RAISE EXCEPTION 'A rodada obsoleta não corresponde ao fencing informado';
    END IF;
    RETURN jsonb_build_object(
      'obsoleta', false, 'anexada', false,
      'motivo', 'investigacao_ja_obsoleta'
    );
  END IF;
  IF v_investigacao.estado_execucao NOT IN (
       'pendente', 'em_execucao', 'aguardando_retentativa', 'concluida'
     ) OR v_investigacao.anexado_em IS NOT NULL THEN
    RAISE EXCEPTION 'Somente investigação ativa e não anexada pode ficar obsoleta';
  END IF;
  PERFORM 1
    FROM public.investigacao_tarefas
   WHERE investigacao_id = p_investigacao_id
   ORDER BY id
   FOR UPDATE;
  v_fencing_atual := public.investigacao_fencing_snapshot(
    p_investigacao_id
  );
  IF v_fencing_atual IS DISTINCT FROM p_fencing_esperado THEN
    RAISE EXCEPTION 'Fencing da investigação mudou; recarregue antes de obsoletar';
  END IF;
  PERFORM 1
    FROM public.negocios_candidatos
   WHERE id = ANY (v_ids_lock)
   ORDER BY id
   FOR UPDATE;
  IF public.investigacao_snapshot_candidatos_atual(v_ids, v_snapshots) IS TRUE THEN
    RAISE EXCEPTION 'Os candidatos não mudaram; a investigação continua válida';
  END IF;

  UPDATE public.investigacao_tarefas
     SET estado_execucao = 'obsoleta', lease_executor = NULL,
         lease_token = NULL, lease_expira_em = NULL, lease_chave_id = NULL
   WHERE investigacao_id = p_investigacao_id;
  v_autorizacao_hash := encode(extensions.digest(convert_to(
    jsonb_build_object(
      'investigacao_id', p_investigacao_id,
      'motivo', 'pre_revisao_stale'
    )::text, 'UTF8'
  ), 'sha256'), 'hex');
  INSERT INTO public.investigacao_autorizacoes_corretiva (
    txid, backend_pid, recurso, investigacao_id,
    operation_draft_id, pending_action_id, pedido_hash
  ) VALUES (
    txid_current(), pg_backend_pid(), 'obsoletar_investigacao',
    p_investigacao_id, p_investigacao_id, p_investigacao_id,
    v_autorizacao_hash
  );
  UPDATE public.investigacoes_revisao
     SET estado_execucao = 'obsoleta', estado_resultado = NULL,
         concluida_em = NULL,
         obsolescencia_motivo = 'pre_revisao_stale',
         promocao_ativa_id = NULL,
         resumo_sanitizado =
           'Os dados de origem mudaram; o resultado anterior ficou somente na auditoria.'
  WHERE id = p_investigacao_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'A investigação mudou durante a obsolescência';
  END IF;

  INSERT INTO public.investigacao_eventos (
    investigacao_id, chave_idempotencia, tipo, resumo_sanitizado
  ) VALUES (
    p_investigacao_id, v_evento_chave, 'investigacao_obsoleta',
    'As fontes mudaram; uma nova investigação deve usar o retrato atualizado.'
  ) ON CONFLICT (chave_idempotencia) DO NOTHING;

  RETURN jsonb_build_object(
    'obsoleta', true, 'anexada', false,
    'motivo', 'candidatos_alterados'
  );
END;
$$;

CREATE OR REPLACE FUNCTION public.vincular_investigacao_rascunho(
  p_investigacao_id uuid,
  p_draft_id uuid
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_investigacao public.investigacoes_revisao%ROWTYPE;
  v_draft public.operation_drafts%ROWTYPE;
  v_candidato record;
  v_id uuid;
  v_draft_pre uuid;
  v_candidato_principal_draft text;
  v_ids_pre uuid[];
  v_ids_lock uuid[];
  v_ids_atual uuid[];
  v_ids_investigacao uuid[];
  v_ids_draft uuid[];
  v_timestamps_draft jsonb;
  v_fingerprint_draft text;
  v_quantidade_candidatos integer := 0;
  v_ja_vinculada boolean := false;
BEGIN
  -- Mesma ordem do guard de promoção: advisory do draft, advisories dos
  -- candidatos e só então locks de linha. Se o snapshot mudar durante esta
  -- pré-leitura, a operação falha fechada e deve ser replanejada.
  SELECT source_draft_id, negocio_candidato_ids
    INTO v_draft_pre, v_ids_pre
    FROM public.investigacoes_revisao
   WHERE id = p_investigacao_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Investigação não encontrada';
  END IF;
  IF v_draft_pre IS NOT NULL AND v_draft_pre <> p_draft_id THEN
    RAISE EXCEPTION 'Investigação já vinculada a outro rascunho';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('investigacao-draft:' || p_draft_id::text, 0)
  );
  SELECT coalesce(array_agg(item ORDER BY item), '{}'::uuid[])
    INTO v_ids_lock
    FROM (SELECT DISTINCT unnest(v_ids_pre) AS item) AS ids;
  FOREACH v_id IN ARRAY v_ids_lock LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended('investigacao-candidato:' || v_id::text, 0)
    );
  END LOOP;

  SELECT * INTO v_investigacao
    FROM public.investigacoes_revisao
   WHERE id = p_investigacao_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Investigação não encontrada';
  END IF;
  IF v_investigacao.anexado_em IS NOT NULL THEN
    RAISE EXCEPTION 'Investigação já anexada não pode trocar de rascunho';
  END IF;
  IF v_investigacao.negocio_candidato_ids IS DISTINCT FROM v_ids_pre THEN
    RAISE EXCEPTION 'O grupo da investigação mudou durante o vínculo';
  END IF;
  IF v_investigacao.source_draft_id IS NOT NULL THEN
    IF v_investigacao.source_draft_id <> p_draft_id THEN
      RAISE EXCEPTION 'Investigação já vinculada a outro rascunho';
    END IF;
    v_ja_vinculada := true;
  END IF;
  SELECT coalesce(array_agg(item ORDER BY item), '{}'::uuid[])
    INTO v_ids_investigacao
    FROM (
      SELECT DISTINCT unnest(v_investigacao.negocio_candidato_ids) AS item
    ) AS ids;
  IF cardinality(v_ids_investigacao) = 0
     OR v_investigacao.negocio_candidato_id IS NULL
     OR NOT (v_investigacao.negocio_candidato_id = ANY (v_ids_investigacao)) THEN
    RAISE EXCEPTION 'Vínculo tardio exige todos os candidatos de staging identificados';
  END IF;

  SELECT * INTO v_draft
    FROM public.operation_drafts
   WHERE id = p_draft_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Rascunho não encontrado';
  END IF;
  v_candidato_principal_draft := coalesce(
    v_draft.inferencias ->> 'staging_candidato_id',
    v_draft.dados_extraidos ->> 'staging_candidato_id'
  );
  v_ids_draft := public.investigacao_ids_candidatos_rascunho(
    v_draft.inferencias, v_draft.dados_extraidos
  );
  IF v_ids_draft IS DISTINCT FROM v_ids_investigacao
     OR v_candidato_principal_draft IS DISTINCT FROM v_investigacao.negocio_candidato_id::text THEN
    RAISE EXCEPTION 'O rascunho não contém o mesmo grupo de candidatos investigado';
  END IF;
  v_fingerprint_draft := coalesce(
    v_draft.inferencias ->> 'fingerprint_grupo',
    v_draft.dados_extraidos ->> 'fingerprint_grupo',
    v_draft.inferencias ->> 'fingerprint_base',
    v_draft.dados_extraidos ->> 'fingerprint_base'
  );
  v_timestamps_draft := coalesce(
    v_draft.inferencias -> 'staging_candidatos_atualizados_em',
    v_draft.dados_extraidos -> 'staging_candidatos_atualizados_em',
    CASE
      WHEN v_candidato_principal_draft IS NOT NULL
       AND coalesce(
         v_draft.inferencias ->> 'staging_candidato_atualizado_em',
         v_draft.dados_extraidos ->> 'staging_candidato_atualizado_em'
       ) IS NOT NULL
      THEN jsonb_build_object(
        v_candidato_principal_draft,
        coalesce(
          v_draft.inferencias ->> 'staging_candidato_atualizado_em',
          v_draft.dados_extraidos ->> 'staging_candidato_atualizado_em'
        )
      )
      ELSE '{}'::jsonb
    END
  );
  IF v_fingerprint_draft IS NULL
     OR v_fingerprint_draft IS DISTINCT FROM v_investigacao.fingerprint_base
     OR jsonb_typeof(v_timestamps_draft) <> 'object'
     OR public.investigacao_jsonb_objeto_tamanho(v_timestamps_draft)
          <> cardinality(v_ids_investigacao)
     OR public.investigacao_jsonb_objeto_tamanho(
          v_investigacao.source_candidatos_atualizados_em
        )
          <> cardinality(v_ids_investigacao) THEN
    RAISE EXCEPTION 'O candidato ou rascunho não confere com o snapshot da investigação';
  END IF;

  FOR v_candidato IN
    SELECT id, atualizado_em
      FROM public.negocios_candidatos
     WHERE id = ANY (v_ids_investigacao)
     ORDER BY id
     FOR UPDATE
  LOOP
    v_quantidade_candidatos := v_quantidade_candidatos + 1;
    IF NOT (v_investigacao.source_candidatos_atualizados_em ? v_candidato.id::text)
       OR NOT (v_timestamps_draft ? v_candidato.id::text)
       OR v_candidato.atualizado_em IS DISTINCT FROM
            public.investigacao_instante_texto_seguro(
              v_investigacao.source_candidatos_atualizados_em
                ->> v_candidato.id::text
            )
       OR v_candidato.atualizado_em IS DISTINCT FROM
            public.investigacao_instante_texto_seguro(
              v_timestamps_draft ->> v_candidato.id::text
            ) THEN
      RAISE EXCEPTION 'Um candidato mudou depois do início da investigação';
    END IF;
  END LOOP;
  IF v_quantidade_candidatos <> cardinality(v_ids_investigacao)
     OR v_investigacao.source_candidato_atualizado_em IS DISTINCT FROM
        public.investigacao_instante_texto_seguro(
          v_investigacao.source_candidatos_atualizados_em
            ->> v_investigacao.negocio_candidato_id::text
        ) THEN
    RAISE EXCEPTION 'O grupo de candidatos está incompleto ou inconsistente';
  END IF;

  IF v_ja_vinculada THEN
    RETURN jsonb_build_object(
      'vinculado', false, 'motivo', 'rascunho_ja_vinculado_validado',
      'operation_draft_id', p_draft_id
    );
  END IF;

  UPDATE public.investigacoes_revisao
     SET source_draft_id = v_draft.id,
         source_draft_atualizado_em = v_draft.atualizado_em
   WHERE id = v_investigacao.id;

  RETURN jsonb_build_object(
    'vinculado', true,
    'operation_draft_id', v_draft.id,
    'source_draft_atualizado_em', v_draft.atualizado_em
  );
END;
$$;

CREATE OR REPLACE FUNCTION public.anexar_investigacao_revisao(
  p_investigacao_id uuid
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_investigacao public.investigacoes_revisao%ROWTYPE;
  v_draft public.operation_drafts%ROWTYPE;
  v_id uuid;
  v_draft_pre uuid;
  v_ids_pre uuid[];
  v_evento_id uuid := md5('investigacao_revisao:anexo:' || p_investigacao_id::text)::uuid;
  v_pendencias text[];
  v_alternativas jsonb;
  v_quantidade_alternativas integer;
  v_quantidade_snapshots integer;
  v_quantidade_pendencias integer;
  v_fencing_snapshot jsonb;
  v_draft_anexado_em timestamptz;
  v_acao_corretiva_id uuid;
  v_capacidade_hash text;
  v_dados_novos jsonb;
  v_campos_novos text[];
  v_inferencias_novas jsonb;
BEGIN
  SELECT source_draft_id, negocio_candidato_ids
    INTO v_draft_pre, v_ids_pre
    FROM public.investigacoes_revisao
   WHERE id = p_investigacao_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Investigação não encontrada';
  END IF;
  IF v_draft_pre IS NULL THEN
    RAISE EXCEPTION 'A investigação precisa apontar um rascunho já existente';
  END IF;
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('investigacao-draft:' || v_draft_pre::text, 0)
  );
  FOR v_id IN
    SELECT DISTINCT unnest(v_ids_pre) AS item ORDER BY item
  LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended('investigacao-candidato:' || v_id::text, 0)
    );
  END LOOP;

  SELECT * INTO v_investigacao
    FROM public.investigacoes_revisao
   WHERE id = p_investigacao_id
   FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'Investigação não encontrada';
  END IF;
  IF v_investigacao.source_draft_id IS DISTINCT FROM v_draft_pre
     OR v_investigacao.negocio_candidato_ids IS DISTINCT FROM v_ids_pre THEN
    RAISE EXCEPTION 'A origem da investigação mudou durante o anexo';
  END IF;
  IF v_investigacao.fluxo_tipo = 'corretiva_pos_gravacao' THEN
    SELECT draft.pending_action_id
      INTO v_acao_corretiva_id
      FROM public.operation_drafts draft
     WHERE draft.id = v_investigacao.source_draft_id
     FOR UPDATE;
    IF NOT FOUND OR v_acao_corretiva_id IS NULL THEN
      RAISE EXCEPTION 'A revisão corretiva não possui subgrafo materializado';
    END IF;
    v_capacidade_hash := encode(extensions.digest(convert_to(
      jsonb_build_object(
        'investigacao_id', v_investigacao.id,
        'operation_draft_id', v_investigacao.source_draft_id,
        'pending_action_id', v_acao_corretiva_id
      )::text, 'UTF8'
    ), 'sha256'), 'hex');
    DELETE FROM public.investigacao_autorizacoes_corretiva autorizacao
     WHERE autorizacao.txid = txid_current()
       AND autorizacao.backend_pid = pg_backend_pid()
       AND autorizacao.recurso = 'anexar_corretiva'
       AND autorizacao.investigacao_id = v_investigacao.id
       AND autorizacao.operation_draft_id = v_investigacao.source_draft_id
       AND autorizacao.pending_action_id = v_acao_corretiva_id
       AND autorizacao.pedido_hash = v_capacidade_hash;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Revisão corretiva só pode ser anexada pelo materializador canônico';
    END IF;
  END IF;
  IF v_investigacao.anexado_draft_id IS NOT NULL THEN
    RETURN jsonb_build_object(
      'anexada', false,
      'motivo', 'investigacao_ja_anexada',
      'operation_draft_id', v_investigacao.anexado_draft_id,
      'evento_id', v_investigacao.anexado_evento_id
    );
  END IF;
  IF v_investigacao.source_draft_id IS NULL THEN
    RAISE EXCEPTION 'A investigação precisa apontar um rascunho já existente';
  END IF;
  IF NOT public.investigacao_evidencias_fontes_atuais(v_investigacao.id) THEN
    RAISE EXCEPTION 'Uma fonte mudou; reconsulte antes de anexar a investigação';
  END IF;
  IF v_investigacao.fluxo_tipo <> 'corretiva_pos_gravacao'
     AND cardinality(v_investigacao.negocio_candidato_ids) > 0 THEN
    -- Revalida candidatos, timestamps e fingerprint imediatamente antes do
    -- anexo. O vínculo pode ter sido criado em uma transação anterior.
    PERFORM public.vincular_investigacao_rascunho(
      v_investigacao.id,
      v_investigacao.source_draft_id
    );
  END IF;

  SELECT * INTO v_draft
    FROM public.operation_drafts
   WHERE id = v_investigacao.source_draft_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Rascunho de origem não encontrado';
  END IF;
  IF (v_draft.status IN (
    'rascunho', 'aguardando_confirmacao', 'confirmado_telegram', 'em_revisao'
  )) IS NOT TRUE THEN
    RAISE EXCEPTION 'A revisão já foi encerrada; preserve a evidência em investigação complementar';
  END IF;
  IF EXISTS (
    SELECT 1
      FROM public.pending_actions acao
     WHERE (
       acao.entidade_id = v_draft.id
       OR acao.payload ->> 'source_draft_id' = v_draft.id::text
     )
       AND acao.acao_tipo = 'promover_revisao_operacional'
       AND acao.status IN (
         'preparada', 'aguardando_confirmacao', 'aprovado_confinex',
         'em_execucao', 'executado', 'erro_pos_gravacao'
       )
  ) THEN
    RAISE EXCEPTION 'Há promoção preparada, aguardando ou executada para este rascunho';
  END IF;
  IF v_investigacao.source_draft_atualizado_em IS NULL
     OR v_draft.atualizado_em IS DISTINCT FROM v_investigacao.source_draft_atualizado_em THEN
    v_fencing_snapshot := public.investigacao_fencing_snapshot(
      v_investigacao.id
    );
    RETURN public.obsoletar_investigacao_por_mudanca_draft(
      v_investigacao.id,
      v_investigacao.source_draft_atualizado_em,
      v_fencing_snapshot
    );
  END IF;
  IF v_investigacao.estado_execucao IS DISTINCT FROM 'concluida'
     OR v_investigacao.estado_resultado IS NULL THEN
    RAISE EXCEPTION 'Conclua a investigação e registre seu resultado';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.investigacao_tarefas
     WHERE investigacao_id = v_investigacao.id
       AND estado_execucao NOT IN ('concluida', 'cancelada', 'obsoleta')
  ) OR NOT EXISTS (
    SELECT 1 FROM public.investigacao_tarefas
     WHERE investigacao_id = v_investigacao.id
  ) THEN
    RAISE EXCEPTION 'As buscas da investigação ainda não estão encerradas';
  END IF;
  -- Linhas de uma tentativa que perdeu o lease permanecem na trilha privada,
  -- mas todas as consultas abaixo selecionam exclusivamente o resultado da
  -- tentativa que concluiu a tarefa. Assim um crash não contamina nem trava a
  -- retomada por outro worker.
  IF EXISTS (SELECT 1 FROM public.eventos WHERE id = v_evento_id) THEN
    RAISE EXCEPTION 'Anexo parcial ou chave determinística em conflito';
  END IF;

  IF EXISTS (
    SELECT 1
      FROM public.investigacao_tarefas tarefa
     WHERE tarefa.investigacao_id = v_investigacao.id
       AND tarefa.adaptador = 'sintese'
       AND tarefa.estado_execucao = 'concluida'
       AND public.investigacao_alternativas_suportadas(
         tarefa.investigacao_id, tarefa.id,
         tarefa.resultado_lease_token, tarefa.resultado_fencing_token
       ) IS NOT TRUE
  ) THEN
    RAISE EXCEPTION 'O resultado perdeu o vínculo verificável com suas evidências';
  END IF;

  SELECT count(*) INTO v_quantidade_alternativas
    FROM public.investigacao_alternativas alternativa
    JOIN public.investigacao_tarefas tarefa
      ON tarefa.id = alternativa.tarefa_id
     AND tarefa.investigacao_id = alternativa.investigacao_id
   WHERE alternativa.investigacao_id = v_investigacao.id
     AND alternativa.classificacao IN ('possivel', 'provavel', 'forte', 'ambiguo')
     AND tarefa.estado_execucao = 'concluida'
     AND tarefa.resultado_lease_token = alternativa.tarefa_lease_token
     AND tarefa.resultado_fencing_token = alternativa.tarefa_fencing_token;
  SELECT count(DISTINCT alternativa.campos_snapshot)
    INTO v_quantidade_snapshots
    FROM public.investigacao_alternativas alternativa
    JOIN public.investigacao_tarefas tarefa
      ON tarefa.id = alternativa.tarefa_id
     AND tarefa.investigacao_id = alternativa.investigacao_id
   WHERE alternativa.investigacao_id = v_investigacao.id
     AND tarefa.estado_execucao = 'concluida'
     AND tarefa.resultado_lease_token = alternativa.tarefa_lease_token
     AND tarefa.resultado_fencing_token = alternativa.tarefa_fencing_token;
  SELECT count(*) INTO v_quantidade_pendencias
    FROM public.investigacao_pendencias pendencia
    JOIN public.investigacao_tarefas tarefa
      ON tarefa.id = pendencia.tarefa_id
     AND tarefa.investigacao_id = pendencia.investigacao_id
   WHERE pendencia.investigacao_id = v_investigacao.id
     AND pendencia.estado = 'aberta'
     AND tarefa.estado_execucao = 'concluida'
     AND tarefa.resultado_lease_token = pendencia.tarefa_lease_token
     AND tarefa.resultado_fencing_token = pendencia.tarefa_fencing_token;

  IF v_investigacao.estado_resultado = 'alternativa_unica'
     AND v_quantidade_alternativas <> 1 THEN
    RAISE EXCEPTION 'Resultado único exige exatamente uma alternativa explicável';
  END IF;
  IF v_investigacao.estado_resultado = 'alternativas_multiplas'
     AND (v_quantidade_alternativas < 2 OR v_quantidade_snapshots < 2) THEN
    RAISE EXCEPTION 'Resultado múltiplo exige ao menos duas versões realmente distintas';
  END IF;
  IF v_investigacao.estado_resultado = 'divergente'
     AND v_quantidade_alternativas = 0 THEN
    RAISE EXCEPTION 'Resultado divergente exige alternativa explicável';
  END IF;
  IF v_investigacao.estado_resultado = 'divergente' AND NOT EXISTS (
    SELECT 1
      FROM public.investigacao_alternativa_evidencias ligacao
      JOIN public.investigacao_alternativas alternativa
        ON alternativa.id = ligacao.alternativa_id
       AND alternativa.investigacao_id = ligacao.investigacao_id
      JOIN public.investigacao_tarefas tarefa_alternativa
        ON tarefa_alternativa.id = alternativa.tarefa_id
       AND tarefa_alternativa.investigacao_id = alternativa.investigacao_id
      JOIN public.investigacao_evidencias evidencia
        ON evidencia.id = ligacao.evidencia_id
       AND evidencia.investigacao_id = ligacao.investigacao_id
      JOIN public.investigacao_tarefas tarefa_evidencia
        ON tarefa_evidencia.id = evidencia.tarefa_id
       AND tarefa_evidencia.investigacao_id = evidencia.investigacao_id
     WHERE ligacao.investigacao_id = v_investigacao.id
       AND ligacao.papel = 'contraria'
       AND tarefa_alternativa.estado_execucao = 'concluida'
       AND tarefa_alternativa.resultado_lease_token = alternativa.tarefa_lease_token
       AND tarefa_alternativa.resultado_fencing_token = alternativa.tarefa_fencing_token
       AND tarefa_evidencia.estado_execucao = 'concluida'
       AND tarefa_evidencia.resultado_lease_token = evidencia.tarefa_lease_token
       AND tarefa_evidencia.resultado_fencing_token = evidencia.tarefa_fencing_token
  ) THEN
    RAISE EXCEPTION 'Resultado divergente exige evidência contrária explícita';
  END IF;
  IF v_investigacao.estado_resultado IN (
       'alternativa_unica', 'alternativas_multiplas', 'divergente'
     ) AND EXISTS (
       SELECT 1
         FROM public.investigacao_alternativas alternativa
         JOIN public.investigacao_tarefas tarefa_alternativa
           ON tarefa_alternativa.id = alternativa.tarefa_id
          AND tarefa_alternativa.investigacao_id = alternativa.investigacao_id
        WHERE alternativa.investigacao_id = v_investigacao.id
          AND alternativa.classificacao IN ('possivel', 'provavel', 'forte', 'ambiguo')
          AND tarefa_alternativa.estado_execucao = 'concluida'
          AND tarefa_alternativa.resultado_lease_token = alternativa.tarefa_lease_token
          AND tarefa_alternativa.resultado_fencing_token = alternativa.tarefa_fencing_token
          AND NOT EXISTS (
            SELECT 1
              FROM public.investigacao_alternativa_evidencias ligacao
              JOIN public.investigacao_evidencias evidencia
                ON evidencia.id = ligacao.evidencia_id
               AND evidencia.investigacao_id = ligacao.investigacao_id
              JOIN public.investigacao_tarefas tarefa_evidencia
                ON tarefa_evidencia.id = evidencia.tarefa_id
               AND tarefa_evidencia.investigacao_id = evidencia.investigacao_id
             WHERE ligacao.investigacao_id = alternativa.investigacao_id
               AND ligacao.alternativa_id = alternativa.id
               AND ligacao.papel = 'favoravel'
               AND tarefa_evidencia.estado_execucao = 'concluida'
               AND tarefa_evidencia.resultado_lease_token = evidencia.tarefa_lease_token
               AND tarefa_evidencia.resultado_fencing_token = evidencia.tarefa_fencing_token
          )
     ) THEN
    RAISE EXCEPTION 'Toda alternativa precisa apontar sua evidência favorável';
  END IF;
  IF v_investigacao.estado_resultado IN (
       'evidencia_insuficiente', 'cobertura_incompleta'
     ) AND v_quantidade_pendencias = 0 THEN
    RAISE EXCEPTION 'Resultado incompleto exige pendência humana explícita';
  END IF;

  SELECT coalesce(
           array_agg(pendencia.descricao_sanitizada ORDER BY pendencia.id_logico),
           ARRAY[]::text[]
         )
    INTO v_pendencias
    FROM public.investigacao_pendencias pendencia
    JOIN public.investigacao_tarefas tarefa
      ON tarefa.id = pendencia.tarefa_id
     AND tarefa.investigacao_id = pendencia.investigacao_id
   WHERE pendencia.investigacao_id = v_investigacao.id
     AND pendencia.estado = 'aberta'
     AND tarefa.estado_execucao = 'concluida'
     AND tarefa.resultado_lease_token = pendencia.tarefa_lease_token
     AND tarefa.resultado_fencing_token = pendencia.tarefa_fencing_token;

  SELECT coalesce(jsonb_agg(
    jsonb_build_object(
      'titulo', alternativa.titulo,
      'referencia_interna', alternativa.referencia_publica,
      'investigacao_ref', v_investigacao.referencia_publica,
      'dados', alternativa.campos_snapshot,
      'confianca', alternativa.confianca_geral,
      -- A fila recebe apenas a projeção humana. Hashes de identidade,
      -- linhagens, inputs e regras permanecem nas tabelas privadas.
      'confianca_campos', (
        SELECT coalesce(jsonb_object_agg(
          avaliacao.key,
          jsonb_build_object(
            'classificacao', avaliacao.value ->> 'classificacao',
            'confianca', avaliacao.value -> 'confianca'
          ) ORDER BY avaliacao.key
        ), '{}'::jsonb)
          FROM jsonb_each(alternativa.confianca_campos) avaliacao
      ),
      'justificativas', jsonb_build_array(alternativa.justificativa_sanitizada),
      'evidencias', coalesce((
        SELECT jsonb_agg(evidencia.resumo_sanitizado ORDER BY evidencia.id_logico)
          FROM public.investigacao_alternativa_evidencias ligacao
          JOIN public.investigacao_evidencias evidencia
            ON evidencia.id = ligacao.evidencia_id
           AND evidencia.investigacao_id = ligacao.investigacao_id
          JOIN public.investigacao_tarefas tarefa_evidencia
            ON tarefa_evidencia.id = evidencia.tarefa_id
           AND tarefa_evidencia.investigacao_id = evidencia.investigacao_id
         WHERE ligacao.alternativa_id = alternativa.id
           AND ligacao.investigacao_id = alternativa.investigacao_id
           AND ligacao.papel = 'favoravel'
           AND evidencia.resumo_sanitizado IS NOT NULL
           AND tarefa_evidencia.estado_execucao = 'concluida'
           AND tarefa_evidencia.resultado_lease_token = evidencia.tarefa_lease_token
           AND tarefa_evidencia.resultado_fencing_token = evidencia.tarefa_fencing_token
      ), '[]'::jsonb),
      'evidencias_detalhadas', coalesce((
        SELECT jsonb_agg(jsonb_build_object(
                 'resumo', evidencia.resumo_sanitizado,
                 'campos_confirmados', to_jsonb(ligacao.campos_suportados)
               ) ORDER BY evidencia.id_logico)
          FROM public.investigacao_alternativa_evidencias ligacao
          JOIN public.investigacao_evidencias evidencia
            ON evidencia.id = ligacao.evidencia_id
           AND evidencia.investigacao_id = ligacao.investigacao_id
          JOIN public.investigacao_tarefas tarefa_evidencia
            ON tarefa_evidencia.id = evidencia.tarefa_id
           AND tarefa_evidencia.investigacao_id = evidencia.investigacao_id
         WHERE ligacao.alternativa_id = alternativa.id
           AND ligacao.investigacao_id = alternativa.investigacao_id
           AND ligacao.papel = 'favoravel'
           AND evidencia.resumo_sanitizado IS NOT NULL
           AND tarefa_evidencia.estado_execucao = 'concluida'
           AND tarefa_evidencia.resultado_lease_token = evidencia.tarefa_lease_token
           AND tarefa_evidencia.resultado_fencing_token = evidencia.tarefa_fencing_token
      ), '[]'::jsonb),
      'evidencias_contrarias', coalesce((
        SELECT jsonb_agg(evidencia.resumo_sanitizado ORDER BY evidencia.id_logico)
          FROM public.investigacao_alternativa_evidencias ligacao
          JOIN public.investigacao_evidencias evidencia
            ON evidencia.id = ligacao.evidencia_id
           AND evidencia.investigacao_id = ligacao.investigacao_id
          JOIN public.investigacao_tarefas tarefa_evidencia
            ON tarefa_evidencia.id = evidencia.tarefa_id
           AND tarefa_evidencia.investigacao_id = evidencia.investigacao_id
         WHERE ligacao.alternativa_id = alternativa.id
           AND ligacao.investigacao_id = alternativa.investigacao_id
           AND ligacao.papel = 'contraria'
           AND evidencia.resumo_sanitizado IS NOT NULL
           AND tarefa_evidencia.estado_execucao = 'concluida'
           AND tarefa_evidencia.resultado_lease_token = evidencia.tarefa_lease_token
           AND tarefa_evidencia.resultado_fencing_token = evidencia.tarefa_fencing_token
      ), '[]'::jsonb)
      ,
      'evidencias_contrarias_detalhadas', coalesce((
        SELECT jsonb_agg(jsonb_build_object(
                 'resumo', evidencia.resumo_sanitizado,
                 'campos_contestados', to_jsonb(ligacao.campos_contestados)
               ) ORDER BY evidencia.id_logico)
          FROM public.investigacao_alternativa_evidencias ligacao
          JOIN public.investigacao_evidencias evidencia
            ON evidencia.id = ligacao.evidencia_id
           AND evidencia.investigacao_id = ligacao.investigacao_id
          JOIN public.investigacao_tarefas tarefa_evidencia
            ON tarefa_evidencia.id = evidencia.tarefa_id
           AND tarefa_evidencia.investigacao_id = evidencia.investigacao_id
         WHERE ligacao.alternativa_id = alternativa.id
           AND ligacao.investigacao_id = alternativa.investigacao_id
           AND ligacao.papel = 'contraria'
           AND evidencia.resumo_sanitizado IS NOT NULL
           AND tarefa_evidencia.estado_execucao = 'concluida'
           AND tarefa_evidencia.resultado_lease_token = evidencia.tarefa_lease_token
           AND tarefa_evidencia.resultado_fencing_token = evidencia.tarefa_fencing_token
      ), '[]'::jsonb)
    ) ORDER BY alternativa.id_logico
  ), '[]'::jsonb)
  INTO v_alternativas
  FROM public.investigacao_alternativas alternativa
  JOIN public.investigacao_tarefas tarefa_alternativa
    ON tarefa_alternativa.id = alternativa.tarefa_id
   AND tarefa_alternativa.investigacao_id = alternativa.investigacao_id
  WHERE alternativa.investigacao_id = v_investigacao.id
    AND alternativa.classificacao IN ('possivel', 'provavel', 'forte', 'ambiguo')
    AND tarefa_alternativa.estado_execucao = 'concluida'
    AND tarefa_alternativa.resultado_lease_token = alternativa.tarefa_lease_token
    AND tarefa_alternativa.resultado_fencing_token = alternativa.tarefa_fencing_token;

  v_dados_novos := coalesce(v_draft.dados_extraidos, '{}'::jsonb)
    || jsonb_build_object(
         -- A tela mostra somente a rodada atual. O histórico das rodadas fica
         -- em `investigacoes_revisao` e nos eventos, sem misturar alternativas
         -- obsoletas com as evidências que sustentam a decisão corrente.
         'versoes_revisao', v_alternativas,
         'investigacoes_revisao',
           coalesce(v_draft.dados_extraidos -> 'investigacoes_revisao', '[]'::jsonb)
           || jsonb_build_array(jsonb_build_object(
             'referencia', v_investigacao.referencia_publica,
             'titulo', v_investigacao.titulo,
             'estado_resultado', v_investigacao.estado_resultado
           ))
       );
  v_campos_novos := ARRAY(
    SELECT DISTINCT pendencia
      FROM unnest(
        coalesce(v_draft.campos_pendentes, ARRAY[]::text[]) || v_pendencias
      ) AS pendencia
     WHERE btrim(pendencia) <> ''
     ORDER BY pendencia
  );
  v_inferencias_novas := coalesce(v_draft.inferencias, '{}'::jsonb)
    || jsonb_build_object(
      'ultima_investigacao_ref', v_investigacao.referencia_publica,
      'exige_confirmacao', true,
      'promovido_para_operacional', false
    );
  IF v_investigacao.fluxo_tipo = 'corretiva_pos_gravacao' THEN
    INSERT INTO public.investigacao_autorizacoes_corretiva (
      txid, backend_pid, recurso, investigacao_id,
      operation_draft_id, pending_action_id, pedido_hash
    ) VALUES (
      txid_current(), pg_backend_pid(), 'anexar_draft_corretivo',
      v_investigacao.id, v_draft.id, v_draft.pending_action_id,
      encode(extensions.digest(convert_to(jsonb_build_object(
        'draft_id', v_draft.id,
        'old_atualizado_em', v_draft.atualizado_em,
        'new_status', v_draft.status,
        'codigo_sugerido', v_draft.codigo_sugerido,
        'dados_extraidos', v_dados_novos,
        'campos_pendentes', to_jsonb(v_campos_novos),
        'inferencias', v_inferencias_novas,
        'contexto_canonico', v_draft.contexto_canonico,
        'contexto_nome', v_draft.contexto_nome,
        'origem_canal', v_draft.origem_canal,
        'origem_conversa_id', v_draft.origem_conversa_id,
        'origem_mensagem_id', v_draft.origem_mensagem_id,
        'escopo', v_draft.escopo
      )::text, 'UTF8'), 'sha256'), 'hex')
    );
  END IF;
  UPDATE public.operation_drafts
     SET dados_extraidos = v_dados_novos,
         campos_pendentes = v_campos_novos,
         inferencias = v_inferencias_novas,
         atualizado_em = now()
   WHERE id = v_draft.id
  RETURNING atualizado_em INTO v_draft_anexado_em;

  INSERT INTO public.eventos (
    id, tipo, agente, usuario, entidade_tipo, entidade_id, origem, origem_canal,
    origem_conversa_id, origem_mensagem_id, contexto_canonico, contexto_nome,
    escopo, status, dados, observacao
  ) VALUES (
    v_evento_id, 'investigacao_anexada_a_revisao', 'sistema', 'sistema',
    'operation_draft', v_draft.id, 'investigacoes_revisao',
    coalesce(v_investigacao.origem_canal, v_draft.origem_canal),
    coalesce(v_investigacao.origem_conversa_id, v_draft.origem_conversa_id),
    coalesce(v_investigacao.origem_mensagem_id, v_draft.origem_mensagem_id),
    coalesce(v_investigacao.contexto_canonico, v_draft.contexto_canonico),
    coalesce(v_investigacao.contexto_nome, v_draft.contexto_nome),
    coalesce(v_investigacao.escopo, v_draft.escopo), 'pendente',
    jsonb_build_object(
      'investigacao_id', v_investigacao.id,
      'source_draft_id', v_draft.id,
      'estado_resultado', v_investigacao.estado_resultado,
      'promovido_para_operacional', false
    ),
    'Evidências anexadas ao rascunho existente; nenhum lançamento foi criado ou preparado.'
  );

  UPDATE public.investigacoes_revisao
     SET anexo_chave = 'anexo:' || v_investigacao.id::text,
         anexado_draft_id = v_draft.id,
         anexado_evento_id = v_evento_id,
         anexado_em = now(),
         anexado_draft_atualizado_em = v_draft_anexado_em
   WHERE id = v_investigacao.id;

  INSERT INTO public.investigacao_eventos (
    investigacao_id, chave_idempotencia, tipo, referencia_entidade,
    resumo_sanitizado
  ) VALUES (
    v_investigacao.id, 'anexo:' || v_investigacao.id::text,
    'evidencia_anexada', v_draft.id::text,
    'Evidências anexadas ao rascunho existente sem criar nova revisão.'
  ) ON CONFLICT (chave_idempotencia) DO NOTHING;

  RETURN jsonb_build_object(
    'anexada', true,
    'operation_draft_id', v_draft.id,
    'evento_id', v_evento_id
  );
END;
$$;

-- Único caminho autorizado para tornar visível na fila um candidato que foi
-- investigado antes da materialização. As três linhas e o anexo são criados na
-- mesma transação; qualquer divergência de snapshot desfaz todo o conjunto.
CREATE OR REPLACE FUNCTION public.materializar_revisao_investigada(
  p_investigacao_id uuid,
  p_operation_draft jsonb,
  p_pending_action jsonb,
  p_evento jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_investigacao_pre public.investigacoes_revisao%ROWTYPE;
  v_investigacao public.investigacoes_revisao%ROWTYPE;
  v_id uuid;
  v_ids_lock uuid[];
  v_draft_id uuid;
  v_action_id uuid;
  v_evento_id uuid;
  v_anexo jsonb;
  v_pedido_hash text;
  v_fluxo_corretivo boolean := false;
  v_acao_tipo_esperado text;
  v_evento_tipo_esperado text;
  v_promocao_origem public.pending_actions%ROWTYPE;
  v_draft_operacional_origem public.operation_drafts%ROWTYPE;
  v_proveniencia_operacional jsonb;
BEGIN
  IF p_operation_draft IS NULL OR jsonb_typeof(p_operation_draft) <> 'object'
     OR p_pending_action IS NULL OR jsonb_typeof(p_pending_action) <> 'object'
     OR p_evento IS NULL OR jsonb_typeof(p_evento) <> 'object' THEN
    RAISE EXCEPTION 'A materialização exige três objetos completos';
  END IF;
  IF public.investigacao_jsonb_objeto_tamanho(p_operation_draft) <> 16
     OR public.investigacao_jsonb_objeto_tamanho(p_pending_action) <> 17
     OR public.investigacao_jsonb_objeto_tamanho(p_evento) <> 18
     OR NOT public.investigacao_json_sanitizado(p_operation_draft)
     OR NOT public.investigacao_json_sanitizado(p_pending_action)
     OR NOT public.investigacao_json_sanitizado(p_evento)
     OR p_operation_draft - ARRAY[
       'id', 'agente', 'status', 'tipo_operacao', 'entidade_final_tipo',
       'confianca', 'dados_extraidos', 'campos_pendentes', 'inferencias',
       'pending_action_id', 'origem_canal', 'origem_conversa_id',
       'origem_mensagem_id', 'contexto_canonico', 'contexto_nome', 'escopo'
     ] <> '{}'::jsonb
     OR p_pending_action - ARRAY[
       'id', 'agente', 'usuario_solicitante', 'canal', 'acao_tipo',
       'entidade_tipo', 'entidade_id', 'resumo', 'payload', 'resultado',
       'status', 'origem_canal', 'origem_conversa_id', 'origem_mensagem_id',
       'contexto_canonico', 'contexto_nome', 'escopo'
     ] <> '{}'::jsonb
     OR p_evento - ARRAY[
       'id', 'tipo', 'agente', 'usuario', 'entidade_tipo', 'entidade_id',
       'origem', 'origem_canal', 'origem_conversa_id', 'origem_mensagem_id',
       'contexto_canonico', 'contexto_nome', 'escopo', 'status', 'fonte_ref',
       'confianca', 'dados', 'observacao'
     ] <> '{}'::jsonb THEN
    RAISE EXCEPTION 'A materialização contém campo fora do contrato fechado';
  END IF;

  SELECT * INTO v_investigacao_pre
    FROM public.investigacoes_revisao
   WHERE id = p_investigacao_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Investigação não encontrada';
  END IF;
  v_fluxo_corretivo :=
    v_investigacao_pre.fluxo_tipo = 'corretiva_pos_gravacao';
  v_acao_tipo_esperado := CASE WHEN v_fluxo_corretivo
    THEN 'revisar_correcao_pos_gravacao'
    ELSE 'revisar_consolidacao_negocio'
  END;
  v_evento_tipo_esperado := CASE WHEN v_fluxo_corretivo
    THEN 'correcao_pos_gravacao_enviada_para_revisao'
    ELSE 'candidato_consolidado_enviado_para_revisao'
  END;

  v_pedido_hash := encode(extensions.digest(convert_to(
    jsonb_build_object(
      'operation_draft', p_operation_draft,
      'pending_action', p_pending_action,
      'evento', p_evento
    )::text,
    'UTF8'
  ), 'sha256'), 'hex');

  v_draft_id := (p_operation_draft ->> 'id')::uuid;
  v_action_id := (p_pending_action ->> 'id')::uuid;
  v_evento_id := (p_evento ->> 'id')::uuid;
  IF (p_operation_draft ->> 'pending_action_id')::uuid IS DISTINCT FROM v_action_id
     OR (p_pending_action ->> 'entidade_id')::uuid IS DISTINCT FROM v_draft_id
     OR p_pending_action ->> 'entidade_tipo' IS DISTINCT FROM 'operation_draft'
     OR p_pending_action ->> 'acao_tipo' IS DISTINCT FROM v_acao_tipo_esperado
     OR p_pending_action ->> 'status' IS DISTINCT FROM 'aguardando_confirmacao'
     OR p_operation_draft ->> 'status' IS DISTINCT FROM 'em_revisao'
     OR (
       NOT v_fluxo_corretivo
       AND p_operation_draft ->> 'tipo_operacao'
             IS DISTINCT FROM 'consolidacao_compra_planilha'
     )
     OR (
       v_fluxo_corretivo
       AND (
         p_operation_draft ->> 'tipo_operacao'
           IS DISTINCT FROM 'correcao_pos_gravacao'
         OR p_operation_draft ->> 'entidade_final_tipo'
              IS DISTINCT FROM 'correcao_pos_gravacao'
         OR coalesce(
              (p_pending_action -> 'payload' ->> 'executavel')::boolean, true
            ) IS NOT FALSE
         OR (p_pending_action -> 'payload') - ARRAY[
              'operation_draft_id', 'fingerprint_base', 'dados_extraidos',
              'campos_pendentes', 'executavel', 'promovido_para_operacional'
            ] <> '{}'::jsonb
         OR public.investigacao_jsonb_objeto_tamanho(
              p_pending_action -> 'payload'
            ) <> 6
         OR (p_evento -> 'dados') - ARRAY[
              'operation_draft_id', 'pending_action_id',
              'fingerprint_base', 'promovido_para_operacional'
            ] <> '{}'::jsonb
         OR public.investigacao_jsonb_objeto_tamanho(
              p_evento -> 'dados'
            ) <> 4
         OR p_pending_action -> 'resultado' IS DISTINCT FROM '{}'::jsonb
         OR EXISTS (
           SELECT 1
             FROM jsonb_object_keys(
               p_operation_draft -> 'dados_extraidos'
             ) AS chave
            WHERE chave NOT IN (
              'operacao_negocio', 'referencia_negocio', 'tipo_negocio',
              'lote', 'fornecedor', 'contraparte', 'cabecas', 'quantidade',
              'categoria', 'sexo', 'peso_total_kg', 'peso_medio_kg',
              'peso_liquido_kg', 'peso_carcaca_total', 'preco_arroba',
              'valor_total', 'valor_bruto', 'valor_liquido', 'data',
              'data_compra', 'data_abate', 'previsao_recebimento',
              'prazo_recebimento', 'pagamento', 'documento', 'numero_nf',
              'destino', 'situacao', 'acao_recomendada', 'evidencia',
              'contexto_nome', 'contexto_operacional', 'grupo_telegram'
            )
         )
         OR (p_operation_draft -> 'inferencias') - ARRAY[
              'fingerprint_base', 'exige_confirmacao',
              'promovido_para_operacional'
            ] <> '{}'::jsonb
         OR public.investigacao_jsonb_objeto_tamanho(
              p_operation_draft -> 'inferencias'
            ) <> 3
         OR p_pending_action -> 'payload' ->> 'operation_draft_id'
              IS DISTINCT FROM v_draft_id::text
         OR p_evento ->> 'fonte_ref'
              IS DISTINCT FROM v_investigacao_pre.referencia_publica
         OR public.investigacao_json_possui_chave(
              p_operation_draft, ARRAY[
                'target_table', 'proposed_record', 'idempotency',
                'idempotency_key', 'promocao_controle_version'
              ]
            )
         OR public.investigacao_json_possui_chave(
              p_pending_action, ARRAY[
                'target_table', 'proposed_record', 'idempotency',
                'idempotency_key', 'promocao_controle_version'
              ]
            )
         OR public.investigacao_json_possui_chave(
              p_evento, ARRAY[
                'target_table', 'proposed_record', 'idempotency',
                'idempotency_key', 'promocao_controle_version'
              ]
            )
       )
     )
     OR (
       NOT v_fluxo_corretivo
       AND p_operation_draft ->> 'entidade_final_tipo' IS DISTINCT FROM 'compras'
     )
     OR (p_evento ->> 'entidade_id')::uuid IS DISTINCT FROM v_draft_id
     OR p_evento ->> 'entidade_tipo' IS DISTINCT FROM 'operation_draft'
     OR p_evento ->> 'tipo'
          IS DISTINCT FROM v_evento_tipo_esperado
     OR coalesce((p_pending_action -> 'payload' ->> 'promovido_para_operacional')::boolean, true)
     OR coalesce((p_evento -> 'dados' ->> 'promovido_para_operacional')::boolean, true)
     OR p_operation_draft -> 'dados_extraidos'
          IS DISTINCT FROM p_pending_action -> 'payload' -> 'dados_extraidos'
     OR p_operation_draft -> 'campos_pendentes'
          IS DISTINCT FROM p_pending_action -> 'payload' -> 'campos_pendentes'
     OR p_operation_draft -> 'inferencias' -> 'fingerprint_base'
          IS DISTINCT FROM p_pending_action -> 'payload' -> 'fingerprint_base'
     OR p_operation_draft -> 'inferencias' -> 'fingerprint_base'
          IS DISTINCT FROM p_evento -> 'dados' -> 'fingerprint_base'
     OR jsonb_typeof(
          p_operation_draft -> 'inferencias' -> 'fingerprint_base'
        ) IS DISTINCT FROM 'string'
     OR p_operation_draft -> 'inferencias' ->> 'fingerprint_base'
          !~ '^[0-9a-f]{64}$'
     OR p_operation_draft -> 'inferencias' ->> 'fingerprint_base'
          IS DISTINCT FROM v_investigacao_pre.fingerprint_base
     OR p_evento -> 'dados' ->> 'pending_action_id'
          IS DISTINCT FROM v_action_id::text
     OR p_evento -> 'dados' ->> 'operation_draft_id'
          IS DISTINCT FROM v_draft_id::text THEN
    RAISE EXCEPTION 'A tripla não representa uma revisão segura de staging';
  END IF;

  -- Pré-leitura sem lock de linha. A ordem global é sempre draft, candidatos
  -- e, só então, a linha da investigação.
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended('investigacao-draft:' || v_draft_id::text, 0)
  );
  SELECT coalesce(array_agg(item ORDER BY item), '{}'::uuid[])
    INTO v_ids_lock
    FROM (
      SELECT DISTINCT unnest(v_investigacao_pre.negocio_candidato_ids) AS item
    ) AS ids;
  FOREACH v_id IN ARRAY v_ids_lock LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended('investigacao-candidato:' || v_id::text, 0)
    );
  END LOOP;

  SELECT * INTO v_investigacao
    FROM public.investigacoes_revisao
   WHERE id = p_investigacao_id
   FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Investigação removida durante a materialização';
  END IF;
  -- Uma segunda sessão pode ter feito a pré-leitura antes de esperar pelos
  -- advisory locks. Se a primeira já concluiu, reconhecemos a repetição aqui,
  -- sob lock, antes de comparar o snapshot pré-lock. O hash e as três linhas
  -- finais precisam ser byte-semanticamente equivalentes ao mesmo pedido.
  IF v_investigacao.anexado_em IS NOT NULL THEN
    IF v_investigacao.fluxo_tipo
          IS DISTINCT FROM v_investigacao_pre.fluxo_tipo
       OR v_investigacao.fingerprint_base
          IS DISTINCT FROM v_investigacao_pre.fingerprint_base
       OR v_investigacao.anexado_draft_id IS DISTINCT FROM v_draft_id
       OR v_investigacao.materializacao_pedido_hash
            IS DISTINCT FROM v_pedido_hash
       OR NOT EXISTS (
         SELECT 1
           FROM public.operation_drafts draft
           JOIN public.pending_actions acao
             ON acao.id = draft.pending_action_id
           JOIN public.eventos evento
             ON evento.id = v_evento_id
            AND evento.entidade_tipo = 'operation_draft'
            AND evento.entidade_id = draft.id
          WHERE draft.id = v_draft_id
            AND acao.id = v_action_id
            -- O hash prova o pedido, mas o retry também precisa reatestar cada
            -- campo público persistido. Assim uma mutação posterior de origem,
            -- contexto, autoria ou conteúdo nunca herda o sucesso anterior.
            AND to_jsonb(draft) @> p_operation_draft
            AND to_jsonb(acao) @> p_pending_action
            AND to_jsonb(evento) @> p_evento
            AND draft.agente IS NOT DISTINCT FROM
                  p_operation_draft ->> 'agente'
            AND draft.status IS NOT DISTINCT FROM
                  p_operation_draft ->> 'status'
            AND draft.tipo_operacao IS NOT DISTINCT FROM
                  p_operation_draft ->> 'tipo_operacao'
            AND draft.entidade_final_tipo IS NOT DISTINCT FROM
                  p_operation_draft ->> 'entidade_final_tipo'
            AND draft.confianca IS NOT DISTINCT FROM
                  (p_operation_draft ->> 'confianca')::numeric
            AND draft.dados_extraidos IS NOT DISTINCT FROM
                  p_operation_draft -> 'dados_extraidos'
            AND draft.campos_pendentes IS NOT DISTINCT FROM ARRAY(
              SELECT jsonb_array_elements_text(
                coalesce(
                  p_operation_draft -> 'campos_pendentes', '[]'::jsonb
                )
              )
            )
            AND draft.inferencias IS NOT DISTINCT FROM
                  p_operation_draft -> 'inferencias'
            AND acao.acao_tipo = v_acao_tipo_esperado
            AND acao.entidade_tipo IS NOT DISTINCT FROM
                  p_pending_action ->> 'entidade_tipo'
            AND acao.entidade_id = v_draft_id
            AND acao.resumo IS NOT DISTINCT FROM
                  p_pending_action ->> 'resumo'
            AND acao.payload IS NOT DISTINCT FROM
                  p_pending_action -> 'payload'
            AND acao.resultado IS NOT DISTINCT FROM
                  p_pending_action -> 'resultado'
            AND acao.status IS NOT DISTINCT FROM
                  p_pending_action ->> 'status'
            AND acao.executavel IS NOT DISTINCT FROM (NOT v_fluxo_corretivo)
            AND draft.revisao_tipo = CASE WHEN v_fluxo_corretivo
              THEN 'corretiva_pos_gravacao' ELSE 'pre_revisao' END
            AND draft.entidade_final_id IS NULL
            AND draft.investigacao_origem_id IS NOT DISTINCT FROM
              CASE WHEN v_fluxo_corretivo THEN v_investigacao.id ELSE NULL END
            AND draft.promocao_origem_id IS NOT DISTINCT FROM
              CASE WHEN v_fluxo_corretivo
                THEN v_investigacao.promocao_origem_id ELSE NULL END
            AND evento.tipo IS NOT DISTINCT FROM p_evento ->> 'tipo'
            AND evento.agente IS NOT DISTINCT FROM p_evento ->> 'agente'
            AND evento.usuario IS NOT DISTINCT FROM p_evento ->> 'usuario'
            AND evento.origem IS NOT DISTINCT FROM p_evento ->> 'origem'
            AND evento.status IS NOT DISTINCT FROM p_evento ->> 'status'
            AND evento.fonte_ref IS NOT DISTINCT FROM p_evento ->> 'fonte_ref'
            AND evento.confianca IS NOT DISTINCT FROM
                  (p_evento ->> 'confianca')::numeric
            AND evento.dados IS NOT DISTINCT FROM p_evento -> 'dados'
            AND evento.observacao IS NOT DISTINCT FROM
                  p_evento ->> 'observacao'
       ) THEN
      RAISE EXCEPTION 'Repetição diverge da tripla já materializada';
    END IF;
    RETURN jsonb_build_object(
      'materializada', false, 'motivo', 'investigacao_ja_materializada',
      'operation_draft_id', v_investigacao.anexado_draft_id,
      'pending_action_id', v_action_id,
      'evento_materializacao_id', v_evento_id,
      'evento_anexo_id', v_investigacao.anexado_evento_id
    );
  END IF;
  IF v_investigacao.source_draft_id
       IS DISTINCT FROM v_investigacao_pre.source_draft_id
     OR v_investigacao.source_draft_atualizado_em
       IS DISTINCT FROM v_investigacao_pre.source_draft_atualizado_em
     OR v_investigacao.negocio_candidato_id
       IS DISTINCT FROM v_investigacao_pre.negocio_candidato_id
     OR v_investigacao.negocio_candidato_ids
       IS DISTINCT FROM v_investigacao_pre.negocio_candidato_ids
     OR v_investigacao.source_candidato_atualizado_em
       IS DISTINCT FROM v_investigacao_pre.source_candidato_atualizado_em
     OR v_investigacao.source_candidatos_atualizados_em
       IS DISTINCT FROM v_investigacao_pre.source_candidatos_atualizados_em
     OR v_investigacao.fingerprint_base
       IS DISTINCT FROM v_investigacao_pre.fingerprint_base
     OR v_investigacao.policy_version
       IS DISTINCT FROM v_investigacao_pre.policy_version
     OR v_investigacao.estado_execucao
       IS DISTINCT FROM v_investigacao_pre.estado_execucao
     OR v_investigacao.estado_resultado
       IS DISTINCT FROM v_investigacao_pre.estado_resultado
     OR v_investigacao.anexado_em
       IS DISTINCT FROM v_investigacao_pre.anexado_em
     OR v_investigacao.fluxo_tipo
       IS DISTINCT FROM v_investigacao_pre.fluxo_tipo
     OR v_investigacao.promocao_origem_id
       IS DISTINCT FROM v_investigacao_pre.promocao_origem_id
     OR v_investigacao.draft_operacional_origem_id
       IS DISTINCT FROM v_investigacao_pre.draft_operacional_origem_id
     OR v_investigacao.destino_operacional_origem
       IS DISTINCT FROM v_investigacao_pre.destino_operacional_origem
     OR v_investigacao.registro_operacional_origem_id
       IS DISTINCT FROM v_investigacao_pre.registro_operacional_origem_id
     OR v_investigacao.registro_operacional_origem_snapshot_ref
       IS DISTINCT FROM
          v_investigacao_pre.registro_operacional_origem_snapshot_ref
     OR v_investigacao.vinculo_operacional_estado
       IS DISTINCT FROM v_investigacao_pre.vinculo_operacional_estado THEN
    RAISE EXCEPTION 'A investigação mudou durante a aquisição de locks';
  END IF;
  IF v_fluxo_corretivo THEN
    SELECT * INTO v_promocao_origem
      FROM public.pending_actions
     WHERE id = v_investigacao.promocao_origem_id
     FOR KEY SHARE;
    SELECT * INTO v_draft_operacional_origem
      FROM public.operation_drafts
     WHERE id = v_investigacao.draft_operacional_origem_id
     FOR KEY SHARE;
    IF NOT FOUND
       OR v_promocao_origem.id IS NULL
       OR v_promocao_origem.acao_tipo
            IS DISTINCT FROM 'promover_revisao_operacional'
       OR v_promocao_origem.status NOT IN ('executado', 'erro_pos_gravacao')
       OR public.investigacao_uuid_texto_seguro(
            v_promocao_origem.payload ->> 'source_draft_id'
          ) IS DISTINCT FROM v_draft_operacional_origem.id
       OR v_promocao_origem.payload ->> 'target_table'
            IS DISTINCT FROM v_investigacao.destino_operacional_origem THEN
      RAISE EXCEPTION 'A origem da revisão corretiva não corresponde ao desfecho terminal';
    END IF;
    IF v_investigacao.vinculo_operacional_estado = 'confirmado' THEN
      v_proveniencia_operacional :=
        public.investigacao_snapshot_registro_promocao(
          v_investigacao.destino_operacional_origem,
          v_investigacao.registro_operacional_origem_id,
          v_investigacao.promocao_origem_id,
          v_promocao_origem.payload -> 'proposed_record'
        );
      IF coalesce(
           (v_proveniencia_operacional ->> 'identidade_valida')::boolean,
           false
         ) IS NOT TRUE THEN
        RAISE EXCEPTION USING
          ERRCODE = 'P0001',
          MESSAGE = 'Vínculo operacional inválido para revisão corretiva',
          DETAIL = jsonb_build_object(
            'codigo', 'CORRETIVA_IDENTIDADE_INVALIDA',
            'investigacao_ref', v_investigacao.referencia_publica
          )::text;
      END IF;
      IF v_proveniencia_operacional ->> 'snapshot_ref'
           IS DISTINCT FROM
             v_investigacao.registro_operacional_origem_snapshot_ref THEN
        RAISE EXCEPTION USING
          ERRCODE = 'P0001',
          MESSAGE = 'Retrato operacional mudou antes da revisão corretiva',
          DETAIL = jsonb_build_object(
            'codigo', 'CORRETIVA_SNAPSHOT_STALE',
            'investigacao_ref', v_investigacao.referencia_publica,
            'snapshot_esperado',
              v_investigacao.registro_operacional_origem_snapshot_ref,
            'snapshot_atual',
              v_proveniencia_operacional ->> 'snapshot_ref'
          )::text;
      END IF;
      -- O helper anterior mantém FOR SHARE até o fim da transação; a
      -- proveniência e o snapshot pertencem à mesma versão estabilizada.
    ELSIF v_investigacao.registro_operacional_origem_id IS NOT NULL
       OR v_investigacao.registro_operacional_origem_snapshot_ref IS NOT NULL THEN
      RAISE EXCEPTION 'Vínculo operacional incerto não pode transportar um registro confirmado';
    END IF;
  END IF;
  IF v_investigacao.anexado_em IS NOT NULL THEN
    IF v_investigacao.anexado_draft_id IS DISTINCT FROM v_draft_id
       OR v_investigacao.materializacao_pedido_hash
            IS DISTINCT FROM v_pedido_hash
       OR NOT EXISTS (
         SELECT 1
           FROM public.operation_drafts draft
           JOIN public.pending_actions acao
             ON acao.id = draft.pending_action_id
           JOIN public.eventos evento
             ON evento.id = v_evento_id
            AND evento.entidade_tipo = 'operation_draft'
            AND evento.entidade_id = draft.id
          WHERE draft.id = v_draft_id
            AND acao.id = v_action_id
            AND to_jsonb(draft) @> p_operation_draft
            AND to_jsonb(acao) @> p_pending_action
            AND to_jsonb(evento) @> p_evento
            AND acao.acao_tipo = v_acao_tipo_esperado
            AND acao.executavel IS NOT DISTINCT FROM (NOT v_fluxo_corretivo)
            AND draft.revisao_tipo = CASE WHEN v_fluxo_corretivo
              THEN 'corretiva_pos_gravacao' ELSE 'pre_revisao' END
            AND draft.tipo_operacao = CASE WHEN v_fluxo_corretivo
              THEN 'correcao_pos_gravacao'
              ELSE 'consolidacao_compra_planilha' END
            AND draft.entidade_final_tipo = CASE WHEN v_fluxo_corretivo
              THEN 'correcao_pos_gravacao' ELSE 'compras' END
            AND draft.entidade_final_id IS NULL
            AND draft.investigacao_origem_id IS NOT DISTINCT FROM
              CASE WHEN v_fluxo_corretivo THEN v_investigacao.id ELSE NULL END
            AND draft.promocao_origem_id IS NOT DISTINCT FROM
              CASE WHEN v_fluxo_corretivo
                THEN v_investigacao.promocao_origem_id ELSE NULL END
            AND evento.tipo = v_evento_tipo_esperado
            AND evento.dados ->> 'pending_action_id' = v_action_id::text
            AND evento.dados ->> 'operation_draft_id' = v_draft_id::text
       ) THEN
      RAISE EXCEPTION 'Repetição diverge da tripla já materializada';
    END IF;
    RETURN jsonb_build_object(
      'materializada', false, 'motivo', 'investigacao_ja_materializada',
      'operation_draft_id', v_investigacao.anexado_draft_id,
      'pending_action_id', v_action_id,
      'evento_materializacao_id', v_evento_id,
      'evento_anexo_id', v_investigacao.anexado_evento_id
    );
  END IF;
  IF v_investigacao.estado_execucao <> 'concluida'
     OR v_investigacao.estado_resultado IS NULL
     OR (
       NOT v_fluxo_corretivo
       AND (
         v_investigacao.source_draft_id IS NOT NULL
         OR cardinality(v_investigacao.negocio_candidato_ids) = 0
       )
     )
     OR (
       v_fluxo_corretivo
       AND (
         v_investigacao.promocao_origem_id IS NULL
         OR v_investigacao.draft_operacional_origem_id IS NULL
         OR v_investigacao.destino_operacional_origem IS NULL
         OR v_investigacao.vinculo_operacional_estado IS NULL
       )
  ) THEN
    RAISE EXCEPTION 'A investigação ainda não pode materializar uma revisão';
  END IF;
  IF NOT public.investigacao_evidencias_fontes_atuais(v_investigacao.id) THEN
    RAISE EXCEPTION 'Uma fonte mudou; reconsulte antes de materializar a revisão';
  END IF;
  IF EXISTS (SELECT 1 FROM public.operation_drafts WHERE id = v_draft_id)
     OR EXISTS (SELECT 1 FROM public.pending_actions WHERE id = v_action_id)
     OR EXISTS (SELECT 1 FROM public.eventos WHERE id = v_evento_id) THEN
    RAISE EXCEPTION 'IDs determinísticos já existem fora da transação esperada';
  END IF;

  IF v_fluxo_corretivo THEN
    INSERT INTO public.investigacao_autorizacoes_corretiva (
      txid, backend_pid, recurso, investigacao_id,
      operation_draft_id, pending_action_id, pedido_hash
    ) VALUES
    (
      txid_current(), pg_backend_pid(), 'inserir_acao',
      v_investigacao.id, v_draft_id, v_action_id,
      encode(extensions.digest(convert_to(jsonb_build_object(
        'draft_id', v_draft_id,
        'action_id', v_action_id,
        'acao_tipo', p_pending_action ->> 'acao_tipo',
        'entidade_tipo', p_pending_action ->> 'entidade_tipo',
        'status', p_pending_action ->> 'status',
        'executavel', false,
        'payload', p_pending_action -> 'payload'
      )::text, 'UTF8'), 'sha256'), 'hex')
    ),
    (
      txid_current(), pg_backend_pid(), 'inserir_draft',
      v_investigacao.id, v_draft_id, v_action_id,
      encode(extensions.digest(convert_to(jsonb_build_object(
        'investigacao_id', v_investigacao.id,
        'promocao_origem_id', v_investigacao.promocao_origem_id,
        'draft_id', v_draft_id,
        'pending_action_id', v_action_id,
        'revisao_tipo', 'corretiva_pos_gravacao',
        'tipo_operacao', p_operation_draft ->> 'tipo_operacao',
        'entidade_final_tipo', p_operation_draft ->> 'entidade_final_tipo',
        'status', p_operation_draft ->> 'status'
      )::text, 'UTF8'), 'sha256'), 'hex')
    );
  END IF;

  INSERT INTO public.pending_actions (
    id, agente, usuario_solicitante, canal, acao_tipo, entidade_tipo,
    entidade_id, resumo, payload, resultado, status, origem_canal,
    origem_conversa_id, origem_mensagem_id, contexto_canonico, contexto_nome,
    escopo, executavel
  ) VALUES (
    v_action_id, p_pending_action ->> 'agente',
    p_pending_action ->> 'usuario_solicitante', p_pending_action ->> 'canal',
    p_pending_action ->> 'acao_tipo', p_pending_action ->> 'entidade_tipo',
    v_draft_id, p_pending_action ->> 'resumo', p_pending_action -> 'payload',
    p_pending_action -> 'resultado', p_pending_action ->> 'status',
    p_pending_action ->> 'origem_canal',
    p_pending_action ->> 'origem_conversa_id',
    p_pending_action ->> 'origem_mensagem_id',
    p_pending_action ->> 'contexto_canonico',
    p_pending_action ->> 'contexto_nome', p_pending_action ->> 'escopo',
    NOT v_fluxo_corretivo
  );

  INSERT INTO public.operation_drafts (
    id, agente, status, tipo_operacao, entidade_final_tipo, confianca,
    dados_extraidos, campos_pendentes, inferencias, pending_action_id,
    origem_canal, origem_conversa_id, origem_mensagem_id, contexto_canonico,
    contexto_nome, escopo, revisao_tipo, investigacao_origem_id,
    promocao_origem_id
  ) VALUES (
    v_draft_id, p_operation_draft ->> 'agente',
    p_operation_draft ->> 'status', p_operation_draft ->> 'tipo_operacao',
    p_operation_draft ->> 'entidade_final_tipo',
    (p_operation_draft ->> 'confianca')::numeric,
    p_operation_draft -> 'dados_extraidos',
    ARRAY(
      SELECT jsonb_array_elements_text(
        coalesce(p_operation_draft -> 'campos_pendentes', '[]'::jsonb)
      )
    ),
    p_operation_draft -> 'inferencias', v_action_id,
    p_operation_draft ->> 'origem_canal',
    p_operation_draft ->> 'origem_conversa_id',
    p_operation_draft ->> 'origem_mensagem_id',
    p_operation_draft ->> 'contexto_canonico',
    p_operation_draft ->> 'contexto_nome', p_operation_draft ->> 'escopo',
    CASE WHEN v_fluxo_corretivo THEN 'corretiva_pos_gravacao'
      ELSE 'pre_revisao' END,
    CASE WHEN v_fluxo_corretivo THEN v_investigacao.id ELSE NULL END,
    CASE WHEN v_fluxo_corretivo THEN v_investigacao.promocao_origem_id
      ELSE NULL END
  );

  INSERT INTO public.eventos (
    id, tipo, agente, usuario, entidade_tipo, entidade_id, origem,
    origem_canal, origem_conversa_id, origem_mensagem_id, contexto_canonico,
    contexto_nome, escopo, status, fonte_ref, confianca, dados, observacao
  ) VALUES (
    v_evento_id, p_evento ->> 'tipo', p_evento ->> 'agente',
    p_evento ->> 'usuario', p_evento ->> 'entidade_tipo', v_draft_id,
    p_evento ->> 'origem', p_evento ->> 'origem_canal',
    p_evento ->> 'origem_conversa_id', p_evento ->> 'origem_mensagem_id',
    p_evento ->> 'contexto_canonico', p_evento ->> 'contexto_nome',
    p_evento ->> 'escopo', p_evento ->> 'status', p_evento ->> 'fonte_ref',
    (p_evento ->> 'confianca')::numeric, p_evento -> 'dados',
    p_evento ->> 'observacao'
  );

  IF v_fluxo_corretivo THEN
    INSERT INTO public.investigacao_autorizacoes_corretiva (
      txid, backend_pid, recurso, investigacao_id,
      operation_draft_id, pending_action_id, pedido_hash
    ) VALUES (
      txid_current(), pg_backend_pid(), 'vincular_draft',
      v_investigacao.id, v_draft_id, v_investigacao.promocao_origem_id,
      encode(extensions.digest(convert_to(jsonb_build_object(
        'investigacao_id', v_investigacao.id,
        'operation_draft_id', v_draft_id,
        'promocao_origem_id', v_investigacao.promocao_origem_id
      )::text, 'UTF8'), 'sha256'), 'hex')
    );
    UPDATE public.investigacoes_revisao
       SET source_draft_id = v_draft_id,
           source_draft_atualizado_em = (
             SELECT atualizado_em FROM public.operation_drafts
              WHERE id = v_draft_id
           )
     WHERE id = v_investigacao.id
       AND fluxo_tipo = 'corretiva_pos_gravacao';
    IF NOT FOUND THEN
      RAISE EXCEPTION 'A investigação corretiva mudou durante a materialização';
    END IF;
    INSERT INTO public.investigacao_autorizacoes_corretiva (
      txid, backend_pid, recurso, investigacao_id,
      operation_draft_id, pending_action_id, pedido_hash
    ) VALUES (
      txid_current(), pg_backend_pid(), 'anexar_corretiva',
      v_investigacao.id, v_draft_id, v_action_id,
      encode(extensions.digest(convert_to(jsonb_build_object(
        'investigacao_id', v_investigacao.id,
        'operation_draft_id', v_draft_id,
        'pending_action_id', v_action_id
      )::text, 'UTF8'), 'sha256'), 'hex')
    );
  ELSE
    PERFORM public.vincular_investigacao_rascunho(
      p_investigacao_id, v_draft_id
    );
  END IF;
  v_anexo := public.anexar_investigacao_revisao(p_investigacao_id);
  UPDATE public.investigacoes_revisao
     SET materializacao_pedido_hash = v_pedido_hash
   WHERE id = p_investigacao_id;
  RETURN jsonb_build_object(
    'materializada', true,
    'operation_draft_id', v_draft_id,
    'pending_action_id', v_action_id,
    'evento_materializacao_id', v_evento_id,
    'anexo', v_anexo
  );
END;
$$;

-- Com a investigação ativada, a preparação deixa de fazer quatro requests.
-- Esta RPC cria o estado visual, a aprovação da pendência-fonte, a ação de
-- promoção e o evento na mesma transação. Ela não escreve em tabela operacional.
CREATE OR REPLACE FUNCTION public.preparar_promocao_revisao_investigada(
  p_operation_draft_id uuid,
  p_pending_action_origem_id uuid,
  p_pedido jsonb
)
RETURNS jsonb
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  v_draft public.operation_drafts%ROWTYPE;
  v_draft_pre public.operation_drafts%ROWTYPE;
  v_acao_origem public.pending_actions%ROWTYPE;
  v_acao_existente public.pending_actions%ROWTYPE;
  v_id uuid;
  v_ids_pre uuid[];
  v_ids_lock uuid[];
  v_ids_atual uuid[];
  v_principal text;
  v_target text;
  v_proposto jsonb;
  v_proposto_derivado jsonb;
  v_hash text;
  v_chave text;
  v_promocao_id uuid;
  v_evento_id uuid;
  v_codigo text;
  v_dados jsonb;
  v_dados_efetivos jsonb;
  v_dados_operacionais_auditados jsonb;
  v_inferencias jsonb;
  v_campos text[];
  v_obrigatorios text[];
  v_draft_decidido_em timestamptz;
  v_investigacoes_atestadas uuid[];
  v_decisao jsonb;
  v_decisao_auditoria jsonb;
  v_deltas_decisao jsonb := '{}'::jsonb;
  v_investigacao_decisao_id uuid;
  v_alternativa_decisao_id uuid;
  v_investigacao_ref text;
  v_alternativa_ref text;
  v_snapshot_escolhido jsonb;
  v_atestacao_hash text;
BEGIN
  IF coalesce(
       nullif(current_setting('role', true), 'none'), session_user
     ) IS DISTINCT FROM 'service_role' THEN
    RAISE EXCEPTION 'A preparação protegida exige o mediador autorizado';
  END IF;
  IF p_operation_draft_id IS NULL OR p_pending_action_origem_id IS NULL
     OR p_pedido IS NULL OR jsonb_typeof(p_pedido) <> 'object'
     OR p_pedido - ARRAY[
       'versao', 'target_table', 'source_draft_atualizado_em',
       'source_pending_action_atualizado_em', 'codigo_sugerido',
       'dados_revisados', 'inferencias', 'campos_pendentes', 'proposed_record'
     ] <> '{}'::jsonb
     OR public.investigacao_jsonb_objeto_tamanho(p_pedido) <> 9
     OR coalesce((p_pedido ->> 'versao')::integer, 0) <> 1
     OR NOT public.investigacao_json_sanitizado(p_pedido) THEN
    RAISE EXCEPTION 'Pedido de preparação inválido ou não sanitizado';
  END IF;
  v_target := p_pedido ->> 'target_table';
  v_proposto := p_pedido -> 'proposed_record';
  v_dados := p_pedido -> 'dados_revisados';
  v_inferencias := p_pedido -> 'inferencias';
  IF (v_target IN ('compras', 'vendas', 'pesagens_caderno', 'abates')) IS NOT TRUE
     OR jsonb_typeof(v_proposto) IS DISTINCT FROM 'object'
     OR jsonb_typeof(v_dados) IS DISTINCT FROM 'object'
     OR jsonb_typeof(v_inferencias) IS DISTINCT FROM 'object'
     OR jsonb_typeof(p_pedido -> 'campos_pendentes') IS DISTINCT FROM 'array'
     OR EXISTS (
       SELECT 1 FROM jsonb_array_elements(p_pedido -> 'campos_pendentes') item
        WHERE jsonb_typeof(item) <> 'string'
     ) THEN
    RAISE EXCEPTION 'Estrutura da preparação inválida';
  END IF;
  IF (v_target = 'compras' AND (
        v_proposto - ARRAY[
          'operacao_id', 'origem_registro', 'telegram_msg_id', 'obs', 'data',
          'quantidade', 'peso_total_kg', 'preco_arroba', 'valor_total',
          'prazo_dias'
        ] <> '{}'::jsonb
        OR NOT (v_proposto ?& ARRAY['operacao_id', 'data', 'quantidade', 'valor_total'])
      ))
     OR (v_target = 'vendas' AND (
        v_proposto - ARRAY[
          'data_abate', 'cabecas', 'peso_carcaca_total', 'preco_arroba',
          'valor_bruto', 'funrural', 'prazo_recebimento', 'romaneio'
        ] <> '{}'::jsonb
        OR NOT (v_proposto ?& ARRAY[
          'data_abate', 'cabecas', 'peso_carcaca_total', 'valor_bruto',
          'prazo_recebimento'
        ])
      ))
     OR (v_target = 'pesagens_caderno' AND (
        v_proposto - ARRAY[
          'contexto', 'data_folha', 'peso_kg', 'foto_ref', 'origem',
          'conferido', 'obs'
        ] <> '{}'::jsonb
        OR NOT (v_proposto ?& ARRAY['contexto', 'data_folha', 'peso_kg'])
      ))
     OR (v_target = 'abates' AND (
        v_proposto - ARRAY[
          'data_abate', 'lote', 'cabecas', 'peso_liquido_kg', 'valor_liquido'
        ] <> '{}'::jsonb
        OR NOT (v_proposto ?& ARRAY[
          'data_abate', 'lote', 'cabecas', 'peso_liquido_kg'
        ])
      )) THEN
    RAISE EXCEPTION 'Prévia contém campo fora do destino ou obrigatório ausente';
  END IF;
  v_obrigatorios := CASE v_target
    WHEN 'compras' THEN ARRAY['operacao_id', 'data', 'quantidade', 'valor_total']
    WHEN 'vendas' THEN ARRAY[
      'data_abate', 'cabecas', 'peso_carcaca_total', 'valor_bruto',
      'prazo_recebimento'
    ]
    WHEN 'pesagens_caderno' THEN ARRAY['contexto', 'data_folha', 'peso_kg']
    ELSE ARRAY['data_abate', 'lote', 'cabecas', 'peso_liquido_kg']
  END;
  IF EXISTS (
    SELECT 1 FROM unnest(v_obrigatorios) chave
     WHERE NOT (v_proposto ? chave)
        OR v_proposto -> chave = 'null'::jsonb
        OR (
          jsonb_typeof(v_proposto -> chave) = 'string'
          AND btrim(v_proposto ->> chave) = ''
        )
  ) THEN
    RAISE EXCEPTION 'Prévia contém campo obrigatório vazio';
  END IF;

  v_hash := encode(extensions.digest(
    convert_to(p_pedido::text, 'UTF8'), 'sha256'
  ), 'hex');
  v_chave := 'preparacao:' || p_operation_draft_id::text || ':'
    || (p_pedido ->> 'source_draft_atualizado_em') || ':' || v_hash;
  v_promocao_id := md5('acao:' || v_chave)::uuid;
  v_evento_id := md5('evento:' || v_chave)::uuid;

  SELECT * INTO v_draft_pre
    FROM public.operation_drafts
   WHERE id = p_operation_draft_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Rascunho não encontrado';
  END IF;
  IF v_draft_pre.revisao_tipo = 'corretiva_pos_gravacao'
     OR EXISTS (
       SELECT 1 FROM public.investigacoes_revisao investigacao
        WHERE investigacao.fluxo_tipo = 'corretiva_pos_gravacao'
          AND (
            investigacao.source_draft_id = p_operation_draft_id
            OR investigacao.anexado_draft_id = p_operation_draft_id
          )
     ) THEN
    RAISE EXCEPTION 'Revisão corretiva pós-gravação não pode gerar promoção';
  END IF;
  v_ids_pre := public.investigacao_ids_candidatos_rascunho(
    v_draft_pre.inferencias, v_draft_pre.dados_extraidos
  );
  PERFORM pg_catalog.pg_advisory_xact_lock(
    pg_catalog.hashtextextended(
      'investigacao-draft:' || p_operation_draft_id::text, 0
    )
  );
  SELECT coalesce(array_agg(item ORDER BY item), '{}'::uuid[])
    INTO v_ids_lock
    FROM (SELECT DISTINCT unnest(v_ids_pre) AS item) AS ids;
  FOREACH v_id IN ARRAY v_ids_lock LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtextextended('investigacao-candidato:' || v_id::text, 0)
    );
  END LOOP;

  -- Retry incerto: conferir o resultado determinístico antes dos snapshots,
  -- pois a primeira execução já renovou atualizado_em.
  SELECT * INTO v_acao_existente
    FROM public.pending_actions
   WHERE id = v_promocao_id;
  IF FOUND THEN
    IF v_acao_existente.acao_tipo IS DISTINCT FROM 'promover_revisao_operacional'
       OR v_acao_existente.promocao_controle_version IS DISTINCT FROM 'lease-v1'
       OR v_acao_existente.entidade_id IS DISTINCT FROM p_operation_draft_id
       OR v_acao_existente.payload ->> 'source_pending_action_id'
            IS DISTINCT FROM p_pending_action_origem_id::text
       OR v_acao_existente.promocao_preparacao_chave
            IS DISTINCT FROM v_chave
       OR v_acao_existente.promocao_preparacao_hash IS DISTINCT FROM v_hash
       OR (v_acao_existente.status IN (
         'preparada', 'aguardando_confirmacao', 'aprovado_confinex',
         'em_execucao', 'executado', 'erro_pos_gravacao'
       )) IS NOT TRUE
       OR NOT EXISTS (SELECT 1 FROM public.eventos WHERE id = v_evento_id) THEN
      RAISE EXCEPTION 'A chave determinística já pertence a outro pedido';
    END IF;
    RETURN jsonb_build_object(
      'preparada', true, 'estado', v_acao_existente.status,
      'repeticao_idempotente', true
    );
  END IF;

  SELECT * INTO v_draft
    FROM public.operation_drafts
   WHERE id = p_operation_draft_id
   FOR UPDATE;
  SELECT * INTO v_acao_origem
    FROM public.pending_actions
   WHERE id = p_pending_action_origem_id
   FOR UPDATE;
  IF NOT FOUND
     OR v_draft.pending_action_id IS DISTINCT FROM p_pending_action_origem_id
     OR v_acao_origem.entidade_id IS DISTINCT FROM p_operation_draft_id THEN
    RAISE EXCEPTION 'Rascunho e pendência-fonte não estão ligados';
  END IF;
  IF v_draft.revisao_tipo = 'corretiva_pos_gravacao'
     OR v_acao_origem.acao_tipo = 'revisar_correcao_pos_gravacao'
     OR NOT v_acao_origem.executavel THEN
    RAISE EXCEPTION 'Ação humana não executável não pode ser preparada para promoção';
  END IF;
  IF (v_draft.status IN (
       'rascunho', 'aguardando_confirmacao', 'confirmado_telegram', 'em_revisao'
     )) IS NOT TRUE
     OR v_draft.entidade_final_id IS NOT NULL
     OR (v_acao_origem.acao_tipo IN (
       'revisar_compra', 'revisar_consolidacao_negocio',
       'revisar_indexacao_nota_fiscal', 'revisar_pesagem',
       'revisar_romaneio', 'revisar_venda_abate'
     )) IS NOT TRUE
     OR (v_acao_origem.status IN (
       'aguardando_confirmacao', 'confirmado_telegram', 'em_revisao'
     )) IS NOT TRUE
     OR EXISTS (
       SELECT 1 FROM public.pending_actions promocao
        WHERE promocao.acao_tipo = 'promover_revisao_operacional'
          AND (
            promocao.entidade_id = v_draft.id
            OR promocao.payload ->> 'source_draft_id' = v_draft.id::text
          )
          AND promocao.status IN (
            'preparada', 'aguardando_confirmacao', 'aprovado_confinex',
            'em_execucao', 'executado', 'erro_pos_gravacao'
          )
     ) THEN
    RAISE EXCEPTION 'A revisão já foi encerrada ou possui promoção preparada';
  END IF;
  v_ids_atual := public.investigacao_ids_candidatos_rascunho(
    v_draft.inferencias, v_draft.dados_extraidos
  );
  IF v_ids_atual IS DISTINCT FROM v_ids_lock
     OR v_draft.atualizado_em IS DISTINCT FROM
          (p_pedido ->> 'source_draft_atualizado_em')::timestamptz
     OR v_acao_origem.atualizado_em IS DISTINCT FROM
          (p_pedido ->> 'source_pending_action_atualizado_em')::timestamptz THEN
    RAISE EXCEPTION 'A revisão mudou; recarregue antes de preparar';
  END IF;
  PERFORM public.exigir_investigacao_anexada_para_promocao(v_draft.id);

  v_dados_efetivos := coalesce(v_draft.dados_extraidos, '{}'::jsonb)
    || v_dados || jsonb_build_object('status_confirmacao', 'promocao_preparada');
  v_proposto_derivado := CASE v_target
    WHEN 'compras' THEN jsonb_build_object(
      'operacao_id', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos, ARRAY['operacao_id']
      ),
      'origem_registro', 'confinex_revisoes',
      'telegram_msg_id', to_jsonb(v_draft.origem_mensagem_id),
      'obs', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos, ARRAY['resumo', 'situacao']
      ),
      'data', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos, ARRAY['data_compra', 'data', 'data_emissao']
      ),
      'quantidade', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos,
        ARRAY['quantidade', 'cabecas', 'dados_lidos.cabecas', 'valores_base.quantidade']
      ),
      'peso_total_kg', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos,
        ARRAY[
          'peso_total_kg', 'peso_liquido_kg', 'dados_lidos.peso_liquido_kg',
          'valores_base.peso_total_kg'
        ]
      ),
      'preco_arroba', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos,
        ARRAY['preco_arroba', 'valor_arroba', 'valores_base.preco_arroba']
      ),
      'valor_total', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos,
        ARRAY['valor_total', 'valor_total_base', 'valor_bruto', 'valores_base.valor_total_base']
      ),
      'prazo_dias', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos, ARRAY['prazo_dias', 'valores_base.prazo_dias']
      )
    )
    WHEN 'vendas' THEN jsonb_build_object(
      'data_abate', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos,
        ARRAY['data_abate', 'dados_lidos.data_abate', 'data', 'data_emissao']
      ),
      'cabecas', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos, ARRAY['cabecas', 'quantidade', 'dados_lidos.cabecas']
      ),
      'peso_carcaca_total', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos,
        ARRAY[
          'peso_carcaca_total', 'peso_liquido_kg',
          'dados_lidos.peso_liquido_kg', 'peso_total_kg'
        ]
      ),
      'preco_arroba', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos, ARRAY['preco_arroba', 'valor_arroba']
      ),
      'valor_bruto', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos, ARRAY['valor_bruto', 'dados_lidos.valor_bruto', 'valor_total']
      ),
      'funrural', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos, ARRAY['funrural', 'dados_lidos.funrural']
      ),
      'prazo_recebimento', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos,
        ARRAY[
          'prazo_recebimento', 'vencimento',
          'dados_lidos.vencimento', 'data_recebimento'
        ]
      ),
      'romaneio', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos, ARRAY['documento']
      )
    )
    WHEN 'pesagens_caderno' THEN jsonb_build_object(
      'contexto', CASE
        WHEN v_dados_efetivos ->> 'tipo_negocio' = 'boi_balanca'
          THEN to_jsonb('boi_balanca'::text)
        ELSE public.investigacao_jsonb_primeiro_valor(
          v_dados_efetivos, ARRAY['contexto_operacional', 'tipo_negocio']
        )
      END,
      'data_folha', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos,
        ARRAY['data_folha', 'data', 'data_emissao', 'data_compra', 'data_abate']
      ),
      'peso_kg', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos,
        ARRAY['peso_kg', 'peso_total_kg', 'peso_liquido_kg', 'dados_lidos.peso_liquido_kg']
      ),
      'foto_ref', to_jsonb(v_draft.origem_mensagem_id),
      'origem', 'confinex_revisoes',
      'conferido', true,
      'obs', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos, ARRAY['resumo', 'situacao']
      )
    )
    ELSE jsonb_build_object(
      'data_abate', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos,
        ARRAY['data_abate', 'dados_lidos.data_abate', 'data', 'data_emissao']
      ),
      'lote', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos, ARRAY['lote', 'dados_lidos.lote']
      ),
      'cabecas', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos, ARRAY['cabecas', 'quantidade', 'dados_lidos.cabecas']
      ),
      'peso_liquido_kg', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos, ARRAY['peso_liquido_kg', 'dados_lidos.peso_liquido_kg']
      ),
      'valor_liquido', public.investigacao_jsonb_primeiro_valor(
        v_dados_efetivos, ARRAY['valor_liquido', 'dados_lidos.valor_liquido']
      )
    )
  END;
  IF v_proposto IS DISTINCT FROM v_proposto_derivado THEN
    RAISE EXCEPTION 'A prévia operacional diverge dos dados efetivamente revisados';
  END IF;
  v_proposto := v_proposto_derivado;
  v_dados := v_dados_efetivos;
  v_dados_operacionais_auditados := v_dados_efetivos || v_proposto;

  -- O navegador só transporta referências opacas. A semântica da decisão é
  -- resolvida aqui contra a rodada anexada ao retrato atual; refs, snapshot e
  -- deltas nunca são aceitos como verdade apenas porque vieram do cliente.
  v_decisao := v_dados -> 'decisao_versao';
  IF v_decisao IS NOT NULL THEN
    IF jsonb_typeof(v_decisao) <> 'object'
       OR v_decisao - ARRAY[
         'titulo', 'escolhida_na_revisao', 'alternativa_ref',
         'investigacao_ref', 'campos_snapshot'
       ] <> '{}'::jsonb
       OR public.investigacao_jsonb_objeto_tamanho(v_decisao) <> 5
       OR jsonb_typeof(v_decisao -> 'escolhida_na_revisao') <> 'boolean'
       OR coalesce(
            (v_decisao ->> 'escolhida_na_revisao')::boolean, false
          ) IS NOT TRUE
       OR v_decisao ->> 'alternativa_ref' !~ '^alt_[0-9a-f]{32}$'
       OR v_decisao ->> 'investigacao_ref' !~ '^inv_[0-9a-f]{32}$'
       OR jsonb_typeof(v_decisao -> 'campos_snapshot') <> 'object'
       OR v_decisao -> 'campos_snapshot' = '{}'::jsonb
       OR NOT public.investigacao_json_publico_sanitizado(
            v_decisao -> 'campos_snapshot'
          ) THEN
      RAISE EXCEPTION 'A escolha da versão não possui atestado íntegro';
    END IF;
    v_alternativa_ref := v_decisao ->> 'alternativa_ref';
    v_investigacao_ref := v_decisao ->> 'investigacao_ref';
    v_snapshot_escolhido := v_decisao -> 'campos_snapshot';
    SELECT investigacao.id, alternativa.id
      INTO v_investigacao_decisao_id, v_alternativa_decisao_id
      FROM public.investigacoes_revisao investigacao
      JOIN public.investigacao_alternativas alternativa
        ON alternativa.investigacao_id = investigacao.id
      JOIN public.investigacao_tarefas tarefa
        ON tarefa.id = alternativa.tarefa_id
       AND tarefa.investigacao_id = alternativa.investigacao_id
     WHERE investigacao.anexado_draft_id = v_draft.id
       AND investigacao.anexado_em IS NOT NULL
       AND investigacao.estado_execucao = 'concluida'
       AND investigacao.anexado_draft_atualizado_em
             IS NOT DISTINCT FROM v_draft.atualizado_em
       AND investigacao.referencia_publica = v_investigacao_ref
       AND alternativa.referencia_publica = v_alternativa_ref
       AND alternativa.titulo IS NOT DISTINCT FROM v_decisao ->> 'titulo'
       AND alternativa.campos_snapshot IS NOT DISTINCT FROM v_snapshot_escolhido
       AND tarefa.estado_execucao = 'concluida'
       AND tarefa.resultado_lease_token = alternativa.tarefa_lease_token
       AND tarefa.resultado_fencing_token = alternativa.tarefa_fencing_token
       AND (
         cardinality(v_ids_atual) = 0
         OR (
           investigacao.negocio_candidato_ids = v_ids_atual
           AND public.investigacao_snapshot_candidatos_atual(
                 investigacao.negocio_candidato_ids,
                 investigacao.source_candidatos_atualizados_em
               ) IS TRUE
         )
       )
     ORDER BY investigacao.id, alternativa.id
     LIMIT 1;
    IF v_investigacao_decisao_id IS NULL THEN
      RAISE EXCEPTION 'A versão escolhida não pertence à investigação anexada atual';
    END IF;
    IF NOT EXISTS (
      SELECT 1
        FROM jsonb_array_elements(
          CASE
            WHEN jsonb_typeof(v_draft.dados_extraidos -> 'versoes_revisao') = 'array'
              THEN v_draft.dados_extraidos -> 'versoes_revisao'
            ELSE '[]'::jsonb
          END
        ) AS versao
       WHERE versao ->> 'referencia_interna' = v_alternativa_ref
         AND versao ->> 'investigacao_ref' = v_investigacao_ref
         AND versao -> 'dados' IS NOT DISTINCT FROM v_snapshot_escolhido
    ) THEN
      RAISE EXCEPTION 'A versão escolhida não está na rodada exibida atualmente';
    END IF;
    SELECT coalesce(jsonb_object_agg(
             chave,
             jsonb_build_object(
               'anterior_draft', coalesce(
                 v_draft.dados_extraidos -> chave, 'null'::jsonb
               ),
               'opcao', coalesce(
                 v_snapshot_escolhido -> chave, 'null'::jsonb
               ),
               'revisado', coalesce(
                 v_dados_operacionais_auditados -> chave, 'null'::jsonb
               ),
               'origem', CASE
                 WHEN v_snapshot_escolhido ? chave
                      AND v_snapshot_escolhido -> chave IS NOT DISTINCT FROM
                            v_dados_operacionais_auditados -> chave
                   THEN 'opcao'
                 WHEN v_draft.dados_extraidos ? chave
                      AND v_draft.dados_extraidos -> chave IS NOT DISTINCT FROM
                            v_dados_operacionais_auditados -> chave
                   THEN 'mantido'
                 ELSE 'edicao_manual'
               END
             ) ORDER BY chave
           ), '{}'::jsonb)
      INTO v_deltas_decisao
      FROM (
        SELECT chave
          FROM jsonb_object_keys(
            coalesce(v_draft.dados_extraidos, '{}'::jsonb)
          ) chave
        UNION
        SELECT chave FROM jsonb_object_keys(v_snapshot_escolhido) chave
        UNION
        SELECT chave
          FROM jsonb_object_keys(v_dados_operacionais_auditados) chave
      ) chaves
     WHERE chave NOT IN (
       'versoes_revisao', 'status_confirmacao', 'decisao_versao',
       'origem_registro', 'telegram_msg_id', 'foto_ref', 'origem',
       'conferido', 'obs'
     )
       AND (
         v_draft.dados_extraidos -> chave IS DISTINCT FROM
           v_dados_operacionais_auditados -> chave
         OR (
           v_snapshot_escolhido ? chave
           AND v_snapshot_escolhido -> chave IS DISTINCT FROM
                 v_dados_operacionais_auditados -> chave
         )
       );
    v_decisao_auditoria := jsonb_build_object(
      'modo', 'alternativa_escolhida',
      'investigacao_ref', v_investigacao_ref,
      'alternativa_ref', v_alternativa_ref,
      'snapshot_hash', encode(extensions.digest(
        convert_to(v_snapshot_escolhido::text, 'UTF8'), 'sha256'
      ), 'hex'),
      'deltas_manuais', v_deltas_decisao
    );
  ELSE
    SELECT coalesce(jsonb_object_agg(
             chave,
             jsonb_build_object(
               'anterior', coalesce(v_draft.dados_extraidos -> chave, 'null'::jsonb),
               'revisado', coalesce(v_dados -> chave, 'null'::jsonb)
             ) ORDER BY chave
           ), '{}'::jsonb)
      INTO v_deltas_decisao
      FROM (
        SELECT chave
          FROM jsonb_object_keys(coalesce(v_draft.dados_extraidos, '{}'::jsonb))
               AS chave
        UNION
        SELECT chave FROM jsonb_object_keys(v_dados_efetivos) AS chave
      ) AS chaves
     WHERE chave NOT IN (
       'versoes_revisao', 'status_confirmacao', 'decisao_versao'
     )
       AND v_draft.dados_extraidos -> chave
             IS DISTINCT FROM v_dados_efetivos -> chave;
    v_decisao_auditoria := jsonb_build_object(
      'modo', 'correcao_manual',
      'deltas_manuais', v_deltas_decisao
    );
  END IF;

  v_codigo := nullif(p_pedido ->> 'codigo_sugerido', '');
  SELECT array_agg(item #>> '{}') INTO v_campos
    FROM jsonb_array_elements(p_pedido -> 'campos_pendentes') item;
  UPDATE public.operation_drafts
     SET status = 'confirmado_telegram', codigo_sugerido = v_codigo,
         entidade_final_tipo = v_target,
         dados_extraidos = coalesce(v_draft.dados_extraidos, '{}'::jsonb)
           || v_dados || jsonb_build_object(
           'status_confirmacao', 'promocao_preparada'
         ),
         campos_pendentes = coalesce(v_campos, '{}'::text[]),
         inferencias = coalesce(v_draft.inferencias, '{}'::jsonb) || v_inferencias
   WHERE id = v_draft.id
  RETURNING atualizado_em INTO v_draft_decidido_em;

  -- A decisão humana muda o rascunho de propósito. Em vez de fingir uma nova
  -- investigação, registramos um atestado explícito que liga exatamente o
  -- retrato investigado, o novo retrato decidido e o hash do pedido mediado.
  SELECT coalesce(array_agg(investigacao.id ORDER BY investigacao.id), '{}'::uuid[])
    INTO v_investigacoes_atestadas
    FROM public.investigacoes_revisao investigacao
   WHERE investigacao.anexado_draft_id = v_draft.id
     AND investigacao.estado_execucao = 'concluida'
     AND investigacao.anexado_em IS NOT NULL
     AND investigacao.anexado_draft_atualizado_em
           IS NOT DISTINCT FROM v_draft.atualizado_em
     AND (
       cardinality(v_ids_atual) = 0
       OR (
         investigacao.negocio_candidato_ids = v_ids_atual
         AND public.investigacao_snapshot_candidatos_atual(
               investigacao.negocio_candidato_ids,
               investigacao.source_candidatos_atualizados_em
             ) IS TRUE
       )
     );
  IF cardinality(v_investigacoes_atestadas) = 0 THEN
    RAISE EXCEPTION 'A decisão não pôde ser ligada à investigação anexada atual';
  END IF;
  IF v_investigacao_decisao_id IS NOT NULL
     AND NOT (v_investigacao_decisao_id = ANY(v_investigacoes_atestadas)) THEN
    RAISE EXCEPTION 'A escolha deixou de pertencer ao retrato atestado';
  END IF;
  IF v_investigacao_decisao_id IS NULL THEN
    v_decisao_auditoria := v_decisao_auditoria || jsonb_build_object(
      'investigacoes_ref', (
        SELECT coalesce(jsonb_agg(
          encode(extensions.digest(convert_to(
            'investigacao:' || investigacao_id::text, 'UTF8'
          ), 'sha256'), 'hex') ORDER BY investigacao_id
        ), '[]'::jsonb)
          FROM unnest(v_investigacoes_atestadas) AS investigacao_id
      )
    );
  END IF;
  FOREACH v_id IN ARRAY v_investigacoes_atestadas LOOP
    v_atestacao_hash := encode(extensions.digest(convert_to(
      jsonb_build_object(
        'investigacao_id', v_id,
        'draft_atualizado_em', v_draft_decidido_em,
        'preparacao_hash', v_hash
      )::text, 'UTF8'
    ), 'sha256'), 'hex');
    INSERT INTO public.investigacao_autorizacoes_corretiva (
      txid, backend_pid, recurso, investigacao_id,
      operation_draft_id, pending_action_id, pedido_hash
    ) VALUES (
      txid_current(), pg_backend_pid(), 'atestar_decisao',
      v_id, v_draft.id, v_id, v_atestacao_hash
    );
    UPDATE public.investigacoes_revisao
       SET decisao_draft_atualizado_em = v_draft_decidido_em,
           decisao_preparacao_hash = v_hash
     WHERE id = v_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'A investigação anexada mudou durante a decisão';
    END IF;
    INSERT INTO public.investigacao_eventos (
      investigacao_id, chave_idempotencia, tipo, referencia_entidade,
      resumo_sanitizado
    ) VALUES (
      v_id, 'decisao-preparacao:' || v_id::text || ':' || v_hash,
      'decisao_revisao_atestada', v_draft.id::text,
      'A decisão revisada foi ligada ao retrato investigado antes da preparação.'
    );
  END LOOP;
  UPDATE public.pending_actions
     SET atualizado_em = now(), status = 'aprovado_confinex',
         entidade_tipo = v_target, entidade_codigo = v_codigo,
         payload = coalesce(v_acao_origem.payload, '{}'::jsonb)
           || jsonb_build_object(
             'dados_extraidos', v_dados,
             'campos_pendentes', p_pedido -> 'campos_pendentes',
             'inferencias', v_inferencias,
             'decisao_revisao', v_decisao_auditoria,
             'revisao_confinex', jsonb_build_object(
               'atualizado_em', now(), 'modo', 'promocao_preparada'
             )
           )
   WHERE id = v_acao_origem.id;

  INSERT INTO public.investigacao_autorizacoes_promocao (
    txid, backend_pid, pending_action_id, operacao,
    status_anterior, status_novo
  ) VALUES (
    txid_current(), pg_backend_pid(), v_promocao_id, 'INSERT',
    NULL, 'aguardando_confirmacao'
  );
  INSERT INTO public.pending_actions (
    id, agente, usuario_solicitante, canal, acao_tipo, entidade_tipo,
    entidade_id, entidade_codigo, resumo, payload, status, origem_canal,
    origem_conversa_id, origem_mensagem_id, contexto_canonico, contexto_nome,
    escopo, promocao_controle_version,
    promocao_preparacao_chave, promocao_preparacao_hash
  ) VALUES (
    v_promocao_id, 'confinex', 'pablo', 'confinex',
    'promover_revisao_operacional', v_target, v_draft.id, v_codigo,
    'Promover revisão aprovada para lançamento operacional',
    jsonb_build_object(
      'source_draft_id', v_draft.id,
      'source_pending_action_id', v_acao_origem.id,
      'origem_canal', v_draft.origem_canal,
      'origem_conversa_id', v_draft.origem_conversa_id,
      'origem_mensagem_id', v_draft.origem_mensagem_id,
      'target_table', v_target,
      'proposed_record', v_proposto,
      'dados_revisados', v_dados,
      'inferencias', v_inferencias,
      'decisao_revisao', v_decisao_auditoria,
      'campos_pendentes', p_pedido -> 'campos_pendentes',
      'origem', 'confinex_revisoes',
      'promovido_para_operacional', false
    ),
    'aguardando_confirmacao', v_draft.origem_canal,
    v_draft.origem_conversa_id, v_draft.origem_mensagem_id,
    v_draft.contexto_canonico, v_draft.contexto_nome, v_draft.escopo,
    'lease-v1', v_chave, v_hash
  );
  DELETE FROM public.investigacao_autorizacoes_promocao
   WHERE txid = txid_current() AND backend_pid = pg_backend_pid()
     AND pending_action_id = v_promocao_id AND operacao = 'INSERT';
  INSERT INTO public.eventos (
    id, tipo, agente, usuario, entidade_tipo, entidade_id, entidade_codigo,
    origem, origem_canal, origem_conversa_id, origem_mensagem_id,
    contexto_canonico, contexto_nome, escopo, status, dados, observacao
  ) VALUES (
    v_evento_id, 'promocao_operacional_preparada', 'confinex', 'pablo',
    'pending_action', v_promocao_id, v_codigo, 'confinex_revisoes',
    v_draft.origem_canal, v_draft.origem_conversa_id,
    v_draft.origem_mensagem_id, v_draft.contexto_canonico,
    v_draft.contexto_nome, v_draft.escopo, 'pendente',
    jsonb_build_object(
      'promotion_pending_action_id', v_promocao_id,
      'source_draft_id', v_draft.id,
      'target_table', v_target,
      'promovido_para_operacional', false,
      'preparacao_confirmada', true,
      'decisao_revisao', v_decisao_auditoria
    ),
    'Promoção preparada; aguardando confirmação antes de gravar o lançamento.'
  );
  RETURN jsonb_build_object(
    'preparada', true, 'estado', 'aguardando_confirmacao',
    'repeticao_idempotente', false
  );
END;
$$;

REVOKE ALL ON FUNCTION public.assumir_tarefa_investigacao(text, text, integer)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.decidir_pendencia_investigacao(uuid, text, uuid, timestamptz, text, text)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.decidir_revisao_corretiva(uuid, uuid, jsonb)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.assumir_promocao_operacional(uuid, text, text, text, text, integer)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.concluir_promocao_operacional(uuid, uuid, bigint, text, jsonb)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.reconciliar_promocao_em_execucao(uuid, bigint, text, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.substituir_investigacao_corretiva_stale(uuid, text, text, text, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.obter_contexto_replanejamento_corretiva_stale(uuid, text, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.replanejar_investigacao_corretiva_stale(uuid, text, text, text, jsonb, text, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.decidir_promocao_operacional(uuid, text, text, text, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.adiar_tarefa_investigacao(uuid, uuid, bigint, text, integer, text, text)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.publicar_resultado_tarefa_investigacao(uuid, uuid, bigint, text, text, jsonb, jsonb, text, text, text)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.concluir_tarefa_investigacao(uuid, uuid, bigint, text, text, text, text, text, jsonb)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.obsoletar_investigacao_por_mudanca_draft(uuid, timestamptz, jsonb)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.obsoletar_investigacao_por_mudanca_candidatos(uuid, jsonb, jsonb)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_fencing_snapshot(uuid)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_snapshot_candidatos_atual(uuid[], jsonb)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_alternativas_suportadas(uuid, uuid, uuid, bigint)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.vincular_investigacao_rascunho(uuid, uuid)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.anexar_investigacao_revisao(uuid)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.materializar_revisao_investigada(uuid, jsonb, jsonb, jsonb)
  FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION public.preparar_promocao_revisao_investigada(uuid, uuid, jsonb)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.exigir_investigacao_anexada_para_promocao(uuid, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.serializar_investigacao_revisao()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.proteger_pending_action_permanente()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.proteger_draft_corretivo_permanente()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.proteger_origem_investigacao_revisao()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.proteger_atestacao_decisao_investigacao()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.proteger_obsolescencia_investigacao()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.reativar_complementar_promocao_sem_gravacao()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.proteger_sucessao_promocao_terminal()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.consumir_sucessoes_promocao_terminal(uuid, text, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.obter_contexto_replanejamento_sucessoes_promocao_terminal(uuid, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.replanejar_sucessoes_promocao_terminal(uuid, text, text, jsonb, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.listar_sucessoes_promocao_terminal_pendentes(integer)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.saude_investigacoes_proativas()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_ids_candidatos_rascunho(jsonb, jsonb)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_jsonb_primeiro_valor(jsonb, text[])
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_provas_campos_validas(jsonb, jsonb, text, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_identidade_permitida_adaptador(text, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_campos_escopo_validos(text[])
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_instante_operacional(timestamptz)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_json_canonico(jsonb)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_hex_igual_constante(text, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_prova_cobertura_valida(uuid, uuid, uuid, bigint, text, text, jsonb, jsonb, text, text, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_proveniencia_registro(text, text, text, uuid)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_registro_corresponde_promocao(text, uuid, uuid, jsonb)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_snapshot_registro_promocao(text, uuid, uuid, jsonb)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.proteger_vinculo_promocao_operacional()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_evidencias_fontes_atuais(uuid)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.proteger_registro_adaptador_imutavel()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.proteger_config_adaptador()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.validar_janela_emissao_credencial()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.validar_revogacao_credencial()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.criar_entrega_evento_investigacao()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.validar_tarefa_no_plano_investigacao()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.bloquear_pending_action_com_investigacao()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.bloquear_draft_com_investigacao()
  FROM PUBLIC, anon, authenticated, service_role;
-- Helpers INVOKER chamados por RPCs DEFINER também integram a base confiável.
-- Todos ficam fechados ao owner, salvo o validador temporal puro usado pelos
-- CHECKs legados; CREATE OR REPLACE não pode conservar grants de um preseed.
REVOKE ALL ON FUNCTION public.investigacao_json_sanitizado(jsonb)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_json_publico_sanitizado(jsonb)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_texto_sanitizado(text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_texto_publico_sanitizado(text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_uuid_texto_seguro(text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_instante_texto_seguro(text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_uuid_array_unico(uuid[])
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_text_array_unico(text[])
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_campos_obrigatorios_validos(text[])
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_uuid_array_corresponde_objeto(uuid[], jsonb)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_snapshots_candidatos_validos(uuid[], jsonb, uuid, timestamptz)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_jsonb_objeto_tamanho(jsonb)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_json_possui_chave(jsonb, text[])
  FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.investigacao_json_possui_chave(jsonb, text[])
  TO CURRENT_USER;
REVOKE ALL ON FUNCTION public.investigacao_consulta_spec_valida(jsonb)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_confianca_campos_valida(jsonb)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_plano_tarefas_valido(jsonb)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_politica_campos(text, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_politica_schema_hash(text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_manifesto_adaptador_valido(text, text, text, text, text[], text[], text[], text[], text[])
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.proteger_consulta_tarefa_investigacao()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.atualizar_timestamp_investigacoes_revisao()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.atualizar_timestamp_staging_consolidacao()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.validar_fencing_resultado_investigacao()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.validar_fencing_ligacao_investigacao()
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_plano_materializado(uuid)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.investigacao_cobertura_sintese(uuid)
  FROM PUBLIC, anon, authenticated, service_role;
-- CHECKs e triggers SECURITY INVOKER das tabelas legadas avaliam este helper
-- puro; conceder apenas sua execução preserva o DML existente na fase sombra.
GRANT EXECUTE ON FUNCTION public.investigacao_instante_operacional(timestamptz)
  TO anon, authenticated, service_role;
-- Escritas diretas deliberadamente concedidas ao service_role avaliam CHECKs
-- como o papel chamador. Somente os helpers puros usados por esses CHECKs são
-- executáveis; parsers, correlatores e consultas internas continuam fechados.
GRANT EXECUTE ON FUNCTION public.investigacao_texto_sanitizado(text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.investigacao_texto_publico_sanitizado(text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.investigacao_json_sanitizado(jsonb)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.investigacao_json_publico_sanitizado(jsonb)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.investigacao_instante_texto_seguro(text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.investigacao_uuid_texto_seguro(text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.investigacao_uuid_array_unico(uuid[])
  TO service_role;
GRANT EXECUTE ON FUNCTION public.investigacao_campos_obrigatorios_validos(text[])
  TO service_role;
GRANT EXECUTE ON FUNCTION public.investigacao_uuid_array_corresponde_objeto(uuid[], jsonb)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.investigacao_snapshots_candidatos_validos(uuid[], jsonb, uuid, timestamptz)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.investigacao_jsonb_objeto_tamanho(jsonb)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.investigacao_consulta_spec_valida(jsonb)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.investigacao_plano_tarefas_valido(jsonb)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.investigacao_politica_campos(text, text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.investigacao_politica_schema_hash(text)
  TO service_role;
DO $$
BEGIN
  IF has_function_privilege(
       'anon',
       'public.reativar_complementar_promocao_sem_gravacao()', 'EXECUTE'
     )
     OR has_function_privilege(
       'authenticated',
       'public.reativar_complementar_promocao_sem_gravacao()', 'EXECUTE'
     )
     OR has_function_privilege(
       'service_role',
       'public.reativar_complementar_promocao_sem_gravacao()', 'EXECUTE'
     ) THEN
    RAISE EXCEPTION 'A função de trigger de reativação não pode ser executável diretamente';
  END IF;
END;
$$;
GRANT EXECUTE ON FUNCTION public.assumir_tarefa_investigacao(text, text, integer)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.decidir_pendencia_investigacao(uuid, text, uuid, timestamptz, text, text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.decidir_revisao_corretiva(uuid, uuid, jsonb)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.assumir_promocao_operacional(uuid, text, text, text, text, integer)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.concluir_promocao_operacional(uuid, uuid, bigint, text, jsonb)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.reconciliar_promocao_em_execucao(uuid, bigint, text, text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.substituir_investigacao_corretiva_stale(uuid, text, text, text, text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.obter_contexto_replanejamento_corretiva_stale(uuid, text, text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.replanejar_investigacao_corretiva_stale(uuid, text, text, text, jsonb, text, text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.consumir_sucessoes_promocao_terminal(uuid, text, text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.obter_contexto_replanejamento_sucessoes_promocao_terminal(uuid, text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.replanejar_sucessoes_promocao_terminal(uuid, text, text, jsonb, text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.listar_sucessoes_promocao_terminal_pendentes(integer)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.saude_investigacoes_proativas()
  TO service_role;
GRANT EXECUTE ON FUNCTION public.decidir_promocao_operacional(uuid, text, text, text, text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.adiar_tarefa_investigacao(uuid, uuid, bigint, text, integer, text, text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.publicar_resultado_tarefa_investigacao(uuid, uuid, bigint, text, text, jsonb, jsonb, text, text, text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.concluir_tarefa_investigacao(uuid, uuid, bigint, text, text, text, text, text, jsonb)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.obsoletar_investigacao_por_mudanca_draft(uuid, timestamptz, jsonb)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.obsoletar_investigacao_por_mudanca_candidatos(uuid, jsonb, jsonb)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.vincular_investigacao_rascunho(uuid, uuid)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.anexar_investigacao_revisao(uuid)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.materializar_revisao_investigada(uuid, jsonb, jsonb, jsonb)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.exigir_investigacao_anexada_para_promocao(uuid, text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.preparar_promocao_revisao_investigada(uuid, uuid, jsonb)
  TO service_role;

-- A fase sombra já expõe views e RPCs; portanto a própria fundação falha se
-- ALTER DEFAULT PRIVILEGES ou um grant legado deixar qualquer papel adicional
-- alcançar uma superfície SECURITY DEFINER. O gate 0002 repete a atestação na
-- janela de ativação, mas não é a primeira linha de defesa.
DO $$
DECLARE
  v_owner oid;
  v_anon oid;
  v_authenticated oid;
  v_service_role oid;
  v_objeto_owner oid;
  v_procedure regprocedure;
  v_view regclass;
  v_expostas_service regprocedure[] := ARRAY[
    'public.assumir_tarefa_investigacao(text,text,integer)'::regprocedure,
    'public.decidir_pendencia_investigacao(uuid,text,uuid,timestamptz,text,text)'::regprocedure,
    'public.assumir_promocao_operacional(uuid,text,text,text,text,integer)'::regprocedure,
    'public.concluir_promocao_operacional(uuid,uuid,bigint,text,jsonb)'::regprocedure,
    'public.reconciliar_promocao_em_execucao(uuid,bigint,text,text)'::regprocedure,
    'public.substituir_investigacao_corretiva_stale(uuid,text,text,text,text)'::regprocedure,
    'public.obter_contexto_replanejamento_corretiva_stale(uuid,text,text)'::regprocedure,
    'public.replanejar_investigacao_corretiva_stale(uuid,text,text,text,jsonb,text,text)'::regprocedure,
    'public.consumir_sucessoes_promocao_terminal(uuid,text,text)'::regprocedure,
    'public.obter_contexto_replanejamento_sucessoes_promocao_terminal(uuid,text)'::regprocedure,
    'public.replanejar_sucessoes_promocao_terminal(uuid,text,text,jsonb,text)'::regprocedure,
    'public.listar_sucessoes_promocao_terminal_pendentes(integer)'::regprocedure,
    'public.saude_investigacoes_proativas()'::regprocedure,
    'public.decidir_promocao_operacional(uuid,text,text,text,text)'::regprocedure,
    'public.decidir_revisao_corretiva(uuid,uuid,jsonb)'::regprocedure,
    'public.adiar_tarefa_investigacao(uuid,uuid,bigint,text,integer,text,text)'::regprocedure,
    'public.publicar_resultado_tarefa_investigacao(uuid,uuid,bigint,text,text,jsonb,jsonb,text,text,text)'::regprocedure,
    'public.concluir_tarefa_investigacao(uuid,uuid,bigint,text,text,text,text,text,jsonb)'::regprocedure,
    'public.obsoletar_investigacao_por_mudanca_draft(uuid,timestamptz,jsonb)'::regprocedure,
    'public.obsoletar_investigacao_por_mudanca_candidatos(uuid,jsonb,jsonb)'::regprocedure,
    'public.vincular_investigacao_rascunho(uuid,uuid)'::regprocedure,
    'public.anexar_investigacao_revisao(uuid)'::regprocedure,
    'public.materializar_revisao_investigada(uuid,jsonb,jsonb,jsonb)'::regprocedure,
    'public.exigir_investigacao_anexada_para_promocao(uuid,text)'::regprocedure,
    'public.preparar_promocao_revisao_investigada(uuid,uuid,jsonb)'::regprocedure
  ];
  v_internas_definer regprocedure[] := ARRAY[
    'public.serializar_investigacao_revisao()'::regprocedure,
    'public.proteger_origem_investigacao_revisao()'::regprocedure,
    'public.proteger_obsolescencia_investigacao()'::regprocedure,
    'public.reativar_complementar_promocao_sem_gravacao()'::regprocedure,
    'public.proteger_sucessao_promocao_terminal()'::regprocedure,
    'public.proteger_atestacao_decisao_investigacao()'::regprocedure,
    'public.investigacao_snapshot_candidatos_atual(uuid[],jsonb)'::regprocedure,
    'public.investigacao_prova_cobertura_valida(uuid,uuid,uuid,bigint,text,text,jsonb,jsonb,text,text,text)'::regprocedure,
    'public.investigacao_proveniencia_registro(text,text,text,uuid)'::regprocedure,
    'public.investigacao_registro_corresponde_promocao(text,uuid,uuid,jsonb)'::regprocedure,
    'public.investigacao_snapshot_registro_promocao(text,uuid,uuid,jsonb)'::regprocedure,
    'public.investigacao_evidencias_fontes_atuais(uuid)'::regprocedure,
    'public.proteger_registro_adaptador_imutavel()'::regprocedure,
    'public.proteger_config_adaptador()'::regprocedure,
    'public.validar_janela_emissao_credencial()'::regprocedure,
    'public.validar_revogacao_credencial()'::regprocedure,
    'public.criar_entrega_evento_investigacao()'::regprocedure,
    'public.validar_tarefa_no_plano_investigacao()'::regprocedure,
    'public.bloquear_pending_action_com_investigacao()'::regprocedure,
    'public.bloquear_draft_com_investigacao()'::regprocedure,
    'public.proteger_pending_action_permanente()'::regprocedure,
    'public.proteger_draft_corretivo_permanente()'::regprocedure,
    'public.proteger_vinculo_promocao_operacional()'::regprocedure
  ];
  v_views_authenticated regclass[] := ARRAY[
    'public.v_investigacoes_revisao'::regclass,
    'public.v_investigacoes_revisao_bloqueios'::regclass,
    'public.v_investigacao_alternativas'::regclass,
    'public.v_investigacao_evidencias'::regclass,
    'public.v_investigacao_pendencias'::regclass
  ];
BEGIN
  SELECT oid INTO v_owner FROM pg_roles WHERE rolname = current_user;
  SELECT oid INTO v_anon FROM pg_roles WHERE rolname = 'anon';
  SELECT oid INTO v_authenticated FROM pg_roles WHERE rolname = 'authenticated';
  SELECT oid INTO v_service_role FROM pg_roles WHERE rolname = 'service_role';
  IF v_owner IS NULL OR v_anon IS NULL OR v_authenticated IS NULL
     OR v_service_role IS NULL THEN
    RAISE EXCEPTION 'Papéis obrigatórios ausentes na atestação de superfícies';
  END IF;
  IF EXISTS (
    SELECT 1
      FROM pg_proc funcao
      JOIN pg_namespace esquema ON esquema.oid = funcao.pronamespace
     WHERE esquema.nspname = 'public'
       AND funcao.proname IN (
         'assumir_tarefa_investigacao', 'adiar_tarefa_investigacao',
         'decidir_pendencia_investigacao',
         'assumir_promocao_operacional',
         'concluir_promocao_operacional',
         'reconciliar_promocao_em_execucao',
         'substituir_investigacao_corretiva_stale',
         'obter_contexto_replanejamento_corretiva_stale',
         'replanejar_investigacao_corretiva_stale',
         'consumir_sucessoes_promocao_terminal',
         'obter_contexto_replanejamento_sucessoes_promocao_terminal',
         'replanejar_sucessoes_promocao_terminal',
         'listar_sucessoes_promocao_terminal_pendentes',
         'saude_investigacoes_proativas',
         'decidir_promocao_operacional',
         'decidir_revisao_corretiva',
         'publicar_resultado_tarefa_investigacao',
         'concluir_tarefa_investigacao',
         'obsoletar_investigacao_por_mudanca_draft',
         'obsoletar_investigacao_por_mudanca_candidatos',
         'vincular_investigacao_rascunho', 'anexar_investigacao_revisao',
         'materializar_revisao_investigada',
         'exigir_investigacao_anexada_para_promocao',
         'preparar_promocao_revisao_investigada'
       )
       AND NOT (funcao.oid::regprocedure = ANY(v_expostas_service))
  ) THEN
    RAISE EXCEPTION 'Overload legado de RPC permanece exposto; aplique somente em catálogo limpo/homologado';
  END IF;

  FOREACH v_procedure IN ARRAY v_expostas_service LOOP
    SELECT proowner INTO v_objeto_owner FROM pg_proc WHERE oid = v_procedure::oid;
    IF v_objeto_owner IS DISTINCT FROM v_owner
       OR NOT (SELECT prosecdef FROM pg_proc WHERE oid = v_procedure::oid)
       OR NOT EXISTS (
         SELECT 1 FROM pg_proc funcao
         JOIN pg_language linguagem ON linguagem.oid = funcao.prolang
          WHERE funcao.oid = v_procedure::oid
            AND linguagem.lanname = 'plpgsql'
            AND (
              (v_procedure IN (
                'public.listar_sucessoes_promocao_terminal_pendentes(integer)'::regprocedure,
                'public.saude_investigacoes_proativas()'::regprocedure
              ) AND funcao.provolatile = 's')
              OR
              (v_procedure NOT IN (
                'public.listar_sucessoes_promocao_terminal_pendentes(integer)'::regprocedure,
                'public.saude_investigacoes_proativas()'::regprocedure
              ) AND funcao.provolatile = 'v')
            )
            AND NOT funcao.proisstrict
            AND funcao.proconfig IS NOT DISTINCT FROM
                  ARRAY['search_path=pg_catalog, public']::text[]
       )
       OR has_function_privilege('anon', v_procedure::oid, 'EXECUTE')
       OR has_function_privilege('authenticated', v_procedure::oid, 'EXECUTE')
       OR NOT has_function_privilege('service_role', v_procedure::oid, 'EXECUTE')
       OR EXISTS (
         SELECT 1
           FROM pg_proc funcao
           CROSS JOIN LATERAL aclexplode(
             coalesce(funcao.proacl, acldefault('f', funcao.proowner))
           ) privilegio
          WHERE funcao.oid = v_procedure::oid
            AND (
              privilegio.privilege_type <> 'EXECUTE'
              OR privilegio.is_grantable
              OR privilegio.grantee NOT IN (v_owner, v_service_role)
            )
       ) THEN
      RAISE EXCEPTION 'Owner/ACL divergente na RPC de sombra %', v_procedure;
    END IF;
  END LOOP;

  FOREACH v_procedure IN ARRAY v_internas_definer LOOP
    SELECT proowner INTO v_objeto_owner FROM pg_proc WHERE oid = v_procedure::oid;
    IF v_objeto_owner IS DISTINCT FROM v_owner
       OR NOT (SELECT prosecdef FROM pg_proc WHERE oid = v_procedure::oid)
       OR NOT EXISTS (
         SELECT 1 FROM pg_proc funcao
         JOIN pg_language linguagem ON linguagem.oid = funcao.prolang
          WHERE funcao.oid = v_procedure::oid
            AND funcao.proconfig IS NOT DISTINCT FROM
                  ARRAY['search_path=pg_catalog, public']::text[]
            AND (
              (v_procedure = 'public.investigacao_snapshot_candidatos_atual(uuid[],jsonb)'::regprocedure
               AND linguagem.lanname = 'sql' AND funcao.provolatile = 's'
               AND funcao.proisstrict)
              OR
              (v_procedure = 'public.investigacao_proveniencia_registro(text,text,text,uuid)'::regprocedure
               AND linguagem.lanname = 'plpgsql' AND funcao.provolatile = 's'
               AND funcao.proisstrict)
              OR
              (v_procedure = 'public.investigacao_registro_corresponde_promocao(text,uuid,uuid,jsonb)'::regprocedure
               AND linguagem.lanname = 'sql' AND funcao.provolatile = 'v'
               AND funcao.proisstrict)
              OR
              (v_procedure = 'public.investigacao_snapshot_registro_promocao(text,uuid,uuid,jsonb)'::regprocedure
               AND linguagem.lanname = 'plpgsql' AND funcao.provolatile = 'v'
               AND funcao.proisstrict)
              OR
              (v_procedure = 'public.investigacao_evidencias_fontes_atuais(uuid)'::regprocedure
               AND linguagem.lanname = 'sql' AND funcao.provolatile = 's'
               AND funcao.proisstrict)
              OR
              (v_procedure NOT IN (
                 'public.investigacao_snapshot_candidatos_atual(uuid[],jsonb)'::regprocedure,
                 'public.investigacao_proveniencia_registro(text,text,text,uuid)'::regprocedure,
                 'public.investigacao_registro_corresponde_promocao(text,uuid,uuid,jsonb)'::regprocedure,
                 'public.investigacao_snapshot_registro_promocao(text,uuid,uuid,jsonb)'::regprocedure,
                 'public.investigacao_evidencias_fontes_atuais(uuid)'::regprocedure
               ) AND linguagem.lanname = 'plpgsql'
                 AND funcao.provolatile = 'v' AND NOT funcao.proisstrict)
            )
       )
       OR has_function_privilege('anon', v_procedure::oid, 'EXECUTE')
       OR has_function_privilege('authenticated', v_procedure::oid, 'EXECUTE')
       OR has_function_privilege('service_role', v_procedure::oid, 'EXECUTE')
       OR EXISTS (
         SELECT 1
           FROM pg_proc funcao
           CROSS JOIN LATERAL aclexplode(
             coalesce(funcao.proacl, acldefault('f', funcao.proowner))
           ) privilegio
          WHERE funcao.oid = v_procedure::oid
            AND (
              privilegio.privilege_type <> 'EXECUTE'
              OR privilegio.is_grantable
              OR privilegio.grantee <> v_owner
            )
       ) THEN
      RAISE EXCEPTION 'Owner/ACL divergente na função interna %', v_procedure;
    END IF;
  END LOOP;

  FOREACH v_view IN ARRAY v_views_authenticated LOOP
    SELECT relowner INTO v_objeto_owner FROM pg_class WHERE oid = v_view::oid;
    IF v_objeto_owner IS DISTINCT FROM v_owner
       OR NOT has_table_privilege('authenticated', v_view::oid, 'SELECT')
       OR has_table_privilege('anon', v_view::oid, 'SELECT')
       OR has_table_privilege('service_role', v_view::oid, 'SELECT')
       OR EXISTS (
         SELECT 1
           FROM pg_class classe
           CROSS JOIN LATERAL aclexplode(
             coalesce(classe.relacl, acldefault('r', classe.relowner))
           ) privilegio
          WHERE classe.oid = v_view::oid
            AND privilegio.grantee <> v_owner
            AND (
              privilegio.privilege_type <> 'SELECT'
              OR privilegio.is_grantable
              OR privilegio.grantee <> v_authenticated
            )
       ) THEN
      RAISE EXCEPTION 'Owner/ACL divergente na view de revisão %', v_view;
    END IF;
    IF EXISTS (
      SELECT 1
        FROM pg_attribute coluna
       WHERE coluna.attrelid = v_view::oid
         AND coluna.attnum > 0
         AND NOT coluna.attisdropped
         AND coluna.attacl IS NOT NULL
         AND cardinality(coluna.attacl) > 0
    ) THEN
      RAISE EXCEPTION 'Grant por coluna fora do inventário na view %', v_view;
    END IF;
  END LOOP;

  v_view := 'public.v_investigacoes_revisao_materializacao'::regclass;
  SELECT relowner INTO v_objeto_owner FROM pg_class WHERE oid = v_view::oid;
  IF v_objeto_owner IS DISTINCT FROM v_owner
     OR NOT has_table_privilege('service_role', v_view::oid, 'SELECT')
     OR has_table_privilege('anon', v_view::oid, 'SELECT')
     OR has_table_privilege('authenticated', v_view::oid, 'SELECT')
     OR EXISTS (
       SELECT 1
         FROM pg_class classe
         CROSS JOIN LATERAL aclexplode(
           coalesce(classe.relacl, acldefault('r', classe.relowner))
         ) privilegio
        WHERE classe.oid = v_view::oid
          AND privilegio.grantee <> v_owner
          AND (
            privilegio.privilege_type <> 'SELECT'
            OR privilegio.is_grantable
            OR privilegio.grantee <> v_service_role
          )
     ) THEN
    RAISE EXCEPTION 'Owner/ACL divergente na view privada de materialização';
  END IF;
  IF EXISTS (
    SELECT 1
      FROM pg_attribute coluna
     WHERE coluna.attrelid = v_view::oid
       AND coluna.attnum > 0
       AND NOT coluna.attisdropped
       AND coluna.attacl IS NOT NULL
       AND cardinality(coluna.attacl) > 0
  ) THEN
    RAISE EXCEPTION 'Grant por coluna fora do inventário na view privada';
  END IF;

  IF (SELECT count(*) FROM pg_trigger
       WHERE tgrelid = 'public.investigacao_tarefas'::regclass
         AND tgname = 'investigacao_tarefas_plano_imutavel'
         AND NOT tgisinternal AND tgenabled = 'O'
         AND tgfoid = 'public.validar_tarefa_no_plano_investigacao()'::regprocedure
         AND tgtype = 7 AND tgqual IS NULL AND tgattr::text = '') <> 1
     OR (SELECT count(*) FROM pg_trigger
       WHERE tgrelid = 'public.investigacao_adaptadores_config'::regclass
         AND tgname = 'investigacao_adaptadores_config_append_only'
         AND NOT tgisinternal AND tgenabled = 'O'
         AND tgfoid = 'public.proteger_config_adaptador()'::regprocedure
         AND tgtype = 27 AND tgqual IS NULL AND tgattr::text = '') <> 1
     OR (SELECT count(*) FROM pg_trigger
       WHERE tgrelid = 'public.investigacao_adaptador_credenciais'::regclass
         AND tgname = 'investigacao_adaptador_credenciais_janela'
         AND NOT tgisinternal AND tgenabled = 'O'
         AND tgfoid = 'public.validar_janela_emissao_credencial()'::regprocedure
         AND tgtype = 7 AND tgqual IS NULL AND tgattr::text = '') <> 1
     OR (SELECT count(*) FROM pg_trigger
       WHERE tgrelid = 'public.investigacao_adaptador_credenciais'::regclass
         AND tgname = 'investigacao_adaptador_credenciais_append_only'
         AND NOT tgisinternal AND tgenabled = 'O'
         AND tgfoid = 'public.proteger_registro_adaptador_imutavel()'::regprocedure
         AND tgtype = 27 AND tgqual IS NULL AND tgattr::text = '') <> 1
     OR (SELECT count(*) FROM pg_trigger
       WHERE tgrelid = 'public.investigacao_eventos'::regclass
         AND tgname = 'investigacao_eventos_cria_entrega'
         AND NOT tgisinternal AND tgenabled = 'O'
         AND tgfoid = 'public.criar_entrega_evento_investigacao()'::regprocedure
         AND tgtype = 5 AND tgqual IS NULL AND tgattr::text = '') <> 1
     OR (SELECT count(*) FROM pg_trigger
       WHERE tgrelid = 'public.investigacao_credenciais_revogadas'::regclass
         AND tgname = 'investigacao_credenciais_revogadas_valida'
         AND NOT tgisinternal AND tgenabled = 'O'
         AND tgfoid = 'public.validar_revogacao_credencial()'::regprocedure
         AND tgtype = 7 AND tgqual IS NULL AND tgattr::text = '') <> 1
     OR (SELECT count(*) FROM pg_trigger
       WHERE tgrelid = 'public.investigacao_credenciais_revogadas'::regclass
         AND tgname = 'investigacao_credenciais_revogadas_append_only'
         AND NOT tgisinternal AND tgenabled = 'O'
         AND tgfoid = 'public.proteger_registro_adaptador_imutavel()'::regprocedure
         AND tgtype = 27 AND tgqual IS NULL AND tgattr::text = '') <> 1 THEN
    RAISE EXCEPTION 'Catálogo dos guardiões de tarefa/configuração/credencial divergiu';
  END IF;
END;
$$;

-- Inventário exato dos helpers INVOKER alcançáveis por RPCs/guardas. Além
-- da assinatura, sela owner, linguagem, volatilidade, STRICT, search_path,
-- ACL e ausência de overload. Isso fecha o ataque de função preseed que um
-- simples CREATE OR REPLACE preservaria.
DO $$
DECLARE
  v_item record;
  v_funcao regprocedure;
  v_owner oid;
  v_anon oid;
  v_authenticated oid;
  v_service_role oid;
  v_nome text;
  v_helpers_check text[] := ARRAY[
    'investigacao_texto_sanitizado',
    'investigacao_texto_publico_sanitizado',
    'investigacao_json_sanitizado',
    'investigacao_json_publico_sanitizado',
    'investigacao_instante_texto_seguro',
    'investigacao_uuid_texto_seguro',
    'investigacao_uuid_array_unico',
    'investigacao_campos_obrigatorios_validos',
    'investigacao_uuid_array_corresponde_objeto',
    'investigacao_snapshots_candidatos_validos',
    'investigacao_jsonb_objeto_tamanho',
    'investigacao_consulta_spec_valida',
    'investigacao_plano_tarefas_valido',
    'investigacao_politica_campos',
    'investigacao_politica_schema_hash'
  ];
BEGIN
  SELECT oid INTO v_owner FROM pg_roles WHERE rolname = current_user;
  SELECT oid INTO v_anon FROM pg_roles WHERE rolname = 'anon';
  SELECT oid INTO v_authenticated FROM pg_roles WHERE rolname = 'authenticated';
  SELECT oid INTO v_service_role FROM pg_roles WHERE rolname = 'service_role';
  FOR v_item IN
    SELECT * FROM (VALUES
      ('public.investigacao_json_canonico(jsonb)', 'plpgsql', 'i', true, 'pg_catalog, public'),
      ('public.investigacao_hex_igual_constante(text,text)', 'sql', 'i', true, 'pg_catalog'),
      ('public.investigacao_json_sanitizado(jsonb)', 'plpgsql', 'i', true, 'pg_catalog, public'),
      ('public.investigacao_json_publico_sanitizado(jsonb)', 'plpgsql', 'i', true, 'pg_catalog, public'),
      ('public.investigacao_texto_sanitizado(text)', 'sql', 'i', true, 'pg_catalog, public'),
      ('public.investigacao_texto_publico_sanitizado(text)', 'sql', 'i', true, 'pg_catalog, public'),
      ('public.investigacao_uuid_texto_seguro(text)', 'plpgsql', 'i', true, 'pg_catalog'),
      ('public.investigacao_ids_candidatos_rascunho(jsonb,jsonb)', 'plpgsql', 'i', false, 'pg_catalog, public'),
      ('public.investigacao_jsonb_primeiro_valor(jsonb,text[])', 'plpgsql', 'i', true, 'pg_catalog'),
      ('public.investigacao_instante_operacional(timestamptz)', 'sql', 'i', true, 'pg_catalog'),
      ('public.investigacao_instante_texto_seguro(text)', 'plpgsql', 'i', true, 'pg_catalog'),
      ('public.investigacao_uuid_array_unico(uuid[])', 'sql', 'i', true, 'pg_catalog'),
      ('public.investigacao_text_array_unico(text[])', 'sql', 'i', true, 'pg_catalog'),
      ('public.investigacao_campos_obrigatorios_validos(text[])', 'sql', 'i', true, 'pg_catalog'),
      ('public.investigacao_uuid_array_corresponde_objeto(uuid[],jsonb)', 'sql', 'i', true, 'pg_catalog, public'),
      ('public.investigacao_snapshots_candidatos_validos(uuid[],jsonb,uuid,timestamptz)', 'plpgsql', 'i', false, 'pg_catalog, public'),
      ('public.investigacao_jsonb_objeto_tamanho(jsonb)', 'sql', 'i', true, 'pg_catalog'),
      ('public.investigacao_json_possui_chave(jsonb,text[])', 'sql', 'i', true, 'pg_catalog'),
      ('public.investigacao_consulta_spec_valida(jsonb)', 'sql', 'i', true, 'pg_catalog, public'),
      ('public.investigacao_confianca_campos_valida(jsonb)', 'plpgsql', 'i', true, 'pg_catalog, public'),
      ('public.investigacao_provas_campos_validas(jsonb,jsonb,text,text)', 'sql', 'i', true, 'pg_catalog, public'),
      ('public.investigacao_identidade_permitida_adaptador(text,text)', 'sql', 'i', false, 'pg_catalog'),
      ('public.investigacao_plano_tarefas_valido(jsonb)', 'plpgsql', 'i', true, 'pg_catalog, public'),
      ('public.investigacao_politica_campos(text,text)', 'sql', 'i', true, 'pg_catalog, public'),
      ('public.investigacao_politica_schema_hash(text)', 'sql', 'i', true, 'pg_catalog, public'),
      ('public.investigacao_manifesto_adaptador_valido(text,text,text,text,text[],text[],text[],text[],text[])', 'sql', 'i', true, 'pg_catalog'),
      ('public.investigacao_campos_escopo_validos(text[])', 'sql', 'i', true, 'pg_catalog, public'),
      ('public.investigacao_alternativas_suportadas(uuid,uuid,uuid,bigint)', 'sql', 's', true, 'pg_catalog, public'),
      ('public.proteger_consulta_tarefa_investigacao()', 'plpgsql', 'v', false, 'pg_catalog, public'),
      ('public.atualizar_timestamp_investigacoes_revisao()', 'plpgsql', 'v', false, 'pg_catalog, public'),
      ('public.atualizar_timestamp_staging_consolidacao()', 'plpgsql', 'v', false, 'pg_catalog, public'),
      ('public.validar_fencing_resultado_investigacao()', 'plpgsql', 'v', false, 'pg_catalog, public'),
      ('public.validar_fencing_ligacao_investigacao()', 'plpgsql', 'v', false, 'pg_catalog, public'),
      ('public.investigacao_plano_materializado(uuid)', 'sql', 's', true, 'pg_catalog, public'),
      ('public.investigacao_cobertura_sintese(uuid)', 'sql', 's', true, 'pg_catalog, public'),
      ('public.investigacao_fencing_snapshot(uuid)', 'sql', 's', true, 'pg_catalog, public')
    ) AS esperado(assinatura, linguagem, volatilidade, estrita, caminho)
  LOOP
    v_funcao := to_regprocedure(v_item.assinatura);
    IF v_funcao IS NULL THEN
      RAISE EXCEPTION 'Helper TCB ausente: %', v_item.assinatura;
    END IF;
    SELECT funcao.proname INTO v_nome
      FROM pg_proc funcao WHERE funcao.oid = v_funcao::oid;
    IF (SELECT count(*)
          FROM pg_proc funcao
          JOIN pg_namespace esquema ON esquema.oid = funcao.pronamespace
         WHERE esquema.nspname = 'public' AND funcao.proname = v_nome) <> 1
       OR NOT EXISTS (
         SELECT 1
           FROM pg_proc funcao
           JOIN pg_language linguagem ON linguagem.oid = funcao.prolang
          WHERE funcao.oid = v_funcao::oid
            AND funcao.proowner = v_owner
            AND NOT funcao.prosecdef
            AND linguagem.lanname = v_item.linguagem
            AND funcao.provolatile = v_item.volatilidade::"char"
            AND funcao.proisstrict = v_item.estrita
            AND funcao.proconfig IS NOT DISTINCT FROM
                  ARRAY['search_path=' || v_item.caminho]::text[]
       ) OR EXISTS (
         SELECT 1
           FROM pg_proc funcao
           CROSS JOIN LATERAL aclexplode(
             coalesce(funcao.proacl, acldefault('f', funcao.proowner))
           ) privilegio
          WHERE funcao.oid = v_funcao::oid
            AND (
              privilegio.privilege_type <> 'EXECUTE'
              OR privilegio.is_grantable
              OR (
                v_nome = 'investigacao_instante_operacional'
                AND privilegio.grantee NOT IN (
                  v_owner, v_anon, v_authenticated, v_service_role
                )
              )
              OR (
                v_nome <> 'investigacao_instante_operacional'
                AND NOT (v_nome = ANY(v_helpers_check)
                  AND privilegio.grantee IN (v_owner, v_service_role))
                AND v_nome <> ALL(v_helpers_check)
                AND privilegio.grantee <> v_owner
              )
              OR (
                v_nome = ANY(v_helpers_check)
                AND privilegio.grantee NOT IN (v_owner, v_service_role)
              )
            )
       ) THEN
      RAISE EXCEPTION 'Catálogo TCB divergente no helper %', v_item.assinatura;
    END IF;
  END LOOP;
END;
$$;

COMMENT ON TABLE public.investigacoes_revisao IS
  'Plano de controle de investigação; não representa dado ou promoção operacional.';
COMMENT ON TABLE public.investigacao_evidencias IS
  'Fatos normalizados e referências opacas; conteúdo bruto permanece na fonte privada.';
COMMENT ON TABLE public.investigacao_eventos IS
  'Trilha técnica append-only; estado de entrega fica em investigacao_entregas.';
COMMENT ON FUNCTION public.investigacao_json_sanitizado(jsonb) IS
  'Bloqueia chaves e valores com credenciais, contatos, documentos ou conteúdo bruto.';
COMMENT ON FUNCTION public.publicar_resultado_tarefa_investigacao(uuid, uuid, bigint, text, text, jsonb, jsonb, text, text, text) IS
  'Publica todo o bundle e aceita a tentativa em uma única transação com lease e fencing.';
COMMENT ON FUNCTION public.adiar_tarefa_investigacao(uuid, uuid, bigint, text, integer, text, text) IS
  'Devolve uma tarefa para retentativa com backoff sob o lease atual, sem criar evidência nem resultado conclusivo.';
COMMENT ON FUNCTION public.obsoletar_investigacao_por_mudanca_draft(uuid, timestamptz, jsonb) IS
  'Libera de forma auditável uma revisão editada, exigindo snapshot e mapa completo de fencing; não altera dado operacional.';
COMMENT ON FUNCTION public.obsoletar_investigacao_por_mudanca_candidatos(uuid, jsonb, jsonb) IS
  'Obsoleta uma rodada pré-rascunho após provar, sob locks ordenados, mudança dos candidatos e o mapa completo de fencing.';
COMMENT ON FUNCTION public.vincular_investigacao_rascunho(uuid, uuid) IS
  'Liga candidato investigado ao rascunho criado pelo materializador canônico, conferindo a origem.';
COMMENT ON FUNCTION public.anexar_investigacao_revisao(uuid) IS
  'Anexa versões a um rascunho existente; a tripla continua exclusiva do materializador de staging.';
COMMENT ON FUNCTION public.materializar_revisao_investigada(uuid, jsonb, jsonb, jsonb) IS
  'Cria rascunho, pendência de revisão, evento e anexo em uma transação após investigação concluída; nunca escreve em tabela operacional.';
COMMENT ON FUNCTION public.preparar_promocao_revisao_investigada(uuid, uuid, jsonb) IS
  'Prepara promoção e auditoria em uma transação idempotente para mediador service_role; não grava lançamento operacional.';
COMMENT ON FUNCTION public.exigir_investigacao_anexada_para_promocao(uuid, text) IS
  'Invariante transacional que bloqueia promoção enquanto investigação relacionada estiver ativa ou ainda não anexada.';

COMMIT;
