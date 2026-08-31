-- Gate de ativação do mediador de promoções investigadas.
--
-- NÃO aplicar junto da fundação por padrão. Antes desta migração, o mediador
-- service_role precisa estar implantado e validado sem tráfego. A aplicação
-- deve ocorrer na mesma janela em que a rota protegida for ativada no frontend.
-- A migração não altera dados; apenas fecha o DML direto de promoções para o
-- papel authenticated.

BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '2min';

-- Revalida a identidade das primitivas criptográficas no próprio cutover.
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
    RAISE EXCEPTION 'Gate de ativação: pgcrypto confiável ausente';
  END IF;
  FOREACH v_funcao IN ARRAY ARRAY[
    'extensions.digest(bytea,text)'::regprocedure,
    'extensions.hmac(bytea,bytea,text)'::regprocedure,
    'extensions.gen_random_bytes(integer)'::regprocedure
  ] LOOP
    IF NOT EXISTS (
      SELECT 1 FROM pg_proc funcao
      JOIN pg_depend dependencia
        ON dependencia.classid = 'pg_proc'::regclass
       AND dependencia.objid = funcao.oid
       AND dependencia.refclassid = 'pg_extension'::regclass
       AND dependencia.refobjid = v_extensao
       AND dependencia.deptype = 'e'
      WHERE funcao.oid = v_funcao::oid
        AND funcao.proowner = v_extensao_owner
    ) THEN
      RAISE EXCEPTION 'Gate de ativação: primitiva não pertence ao pgcrypto: %', v_funcao;
    END IF;
  END LOOP;
  FOR v_papel IN SELECT oid FROM pg_roles
    WHERE rolname IN ('anon', 'authenticated', 'service_role')
  LOOP
    IF has_schema_privilege(v_papel, 'extensions', 'CREATE')
       OR pg_has_role(v_papel, v_extensao_owner, 'MEMBER')
       OR (SELECT rolsuper FROM pg_roles WHERE oid = v_papel) THEN
      RAISE EXCEPTION 'Gate de ativação: papel da aplicação pode substituir pgcrypto';
    END IF;
  END LOOP;
END;
$$;

-- A ativação troca policies permissivas, portanto qualquer policy DML não
-- inventariada poderia recompor acesso por OR. Congelamos a tabela e aceitamos
-- somente o estado legado canônico ou uma reaplicação exata deste gate.
LOCK TABLE public.investigacao_adaptadores_config,
  public.investigacao_adaptador_credenciais,
  public.investigacao_credenciais_revogadas,
  public.investigacao_configuracao_ativacao IN ACCESS EXCLUSIVE MODE;
LOCK TABLE public.investigacao_tarefas IN ACCESS EXCLUSIVE MODE;
LOCK TABLE public.operation_drafts IN ACCESS EXCLUSIVE MODE;
LOCK TABLE public.negocios_candidatos IN SHARE MODE;
LOCK TABLE public.pending_actions IN ACCESS EXCLUSIVE MODE;
-- O cutover também depende dos vínculos duráveis usados para provar a
-- idempotência. Não aceitamos trocar policy enquanto uma alteração de catálogo
-- ou de vínculo operacional possa tornar o preseed diferente do atestado.
LOCK TABLE public.compras, public.vendas, public.pesagens_caderno,
  public.abates IN SHARE MODE;
LOCK TABLE public.investigacao_autorizacoes_promocao,
  public.investigacao_autorizacoes_corretiva,
  public.investigacao_sucessoes_pendentes IN ACCESS EXCLUSIVE MODE;

-- Estas superfícies são o único acesso do worker ao outbox. A tabela
-- continua owner-only. O REVOKE + GRANT é repetível e também permite reaplicar
-- o gate depois de um rollback, sem abrir acesso ao navegador nem ao anon.
REVOKE ALL ON FUNCTION public.consumir_sucessoes_promocao_terminal(uuid, text, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.obter_contexto_replanejamento_sucessoes_promocao_terminal(uuid, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.replanejar_sucessoes_promocao_terminal(uuid, text, text, jsonb, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.obter_contexto_replanejamento_corretiva_stale(uuid, text, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.replanejar_investigacao_corretiva_stale(uuid, text, text, text, jsonb, text, text)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.listar_sucessoes_promocao_terminal_pendentes(integer)
  FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.saude_investigacoes_proativas()
  FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.consumir_sucessoes_promocao_terminal(uuid, text, text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.obter_contexto_replanejamento_sucessoes_promocao_terminal(uuid, text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.replanejar_sucessoes_promocao_terminal(uuid, text, text, jsonb, text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.obter_contexto_replanejamento_corretiva_stale(uuid, text, text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.replanejar_investigacao_corretiva_stale(uuid, text, text, text, jsonb, text, text)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.listar_sucessoes_promocao_terminal_pendentes(integer)
  TO service_role;
GRANT EXECUTE ON FUNCTION public.saude_investigacoes_proativas()
  TO service_role;

DO $$
DECLARE
  v_authenticated oid;
  v_anon oid;
  v_service_role oid;
  v_owner oid;
  v_objeto_owner oid;
  v_guardioes_operacionais_ausentes boolean;
  v_guardioes_operacionais_exatos boolean;
  v_procedure regprocedure;
  v_view regclass;
  v_tabela regclass;
  v_expostas_service regprocedure[] := ARRAY[
    'public.assumir_tarefa_investigacao(text,text,integer)'::regprocedure,
    'public.decidir_pendencia_investigacao(uuid,text,uuid,timestamptz,text,text)'::regprocedure,
    'public.adiar_tarefa_investigacao(uuid,uuid,bigint,text,integer,text,text)'::regprocedure,
    'public.publicar_resultado_tarefa_investigacao(uuid,uuid,bigint,text,text,jsonb,jsonb,text,text,text)'::regprocedure,
    'public.concluir_tarefa_investigacao(uuid,uuid,bigint,text,text,text,text,text,jsonb)'::regprocedure,
    'public.obsoletar_investigacao_por_mudanca_draft(uuid,timestamptz,jsonb)'::regprocedure,
    'public.obsoletar_investigacao_por_mudanca_candidatos(uuid,jsonb,jsonb)'::regprocedure,
    'public.vincular_investigacao_rascunho(uuid,uuid)'::regprocedure,
    'public.anexar_investigacao_revisao(uuid)'::regprocedure,
    'public.materializar_revisao_investigada(uuid,jsonb,jsonb,jsonb)'::regprocedure,
    'public.exigir_investigacao_anexada_para_promocao(uuid,text)'::regprocedure,
    'public.preparar_promocao_revisao_investigada(uuid,uuid,jsonb)'::regprocedure,
    'public.assumir_promocao_operacional(uuid,text,text,text,text,integer)'::regprocedure,
    'public.concluir_promocao_operacional(uuid,uuid,bigint,text,jsonb)'::regprocedure,
    'public.reconciliar_promocao_em_execucao(uuid,bigint,text,text)'::regprocedure,
    'public.consumir_sucessoes_promocao_terminal(uuid,text,text)'::regprocedure,
    'public.obter_contexto_replanejamento_sucessoes_promocao_terminal(uuid,text)'::regprocedure,
    'public.replanejar_sucessoes_promocao_terminal(uuid,text,text,jsonb,text)'::regprocedure,
    'public.listar_sucessoes_promocao_terminal_pendentes(integer)'::regprocedure,
    'public.saude_investigacoes_proativas()'::regprocedure,
    'public.substituir_investigacao_corretiva_stale(uuid,text,text,text,text)'::regprocedure,
    'public.obter_contexto_replanejamento_corretiva_stale(uuid,text,text)'::regprocedure,
    'public.replanejar_investigacao_corretiva_stale(uuid,text,text,text,jsonb,text,text)'::regprocedure,
    'public.decidir_promocao_operacional(uuid,text,text,text,text)'::regprocedure,
    'public.decidir_revisao_corretiva(uuid,uuid,jsonb)'::regprocedure
  ];
  v_internas_definer regprocedure[] := ARRAY[
    'public.serializar_investigacao_revisao()'::regprocedure,
    'public.proteger_origem_investigacao_revisao()'::regprocedure,
    'public.proteger_obsolescencia_investigacao()'::regprocedure,
    'public.reativar_complementar_promocao_sem_gravacao()'::regprocedure,
    'public.proteger_sucessao_promocao_terminal()'::regprocedure,
    'public.proteger_atestacao_decisao_investigacao()'::regprocedure,
    'public.investigacao_snapshot_candidatos_atual(uuid[],jsonb)'::regprocedure,
    'public.investigacao_proveniencia_registro(text,text,text,uuid)'::regprocedure,
    'public.investigacao_registro_corresponde_promocao(text,uuid,uuid,jsonb)'::regprocedure,
    'public.investigacao_snapshot_registro_promocao(text,uuid,uuid,jsonb)'::regprocedure,
    'public.investigacao_evidencias_fontes_atuais(uuid)'::regprocedure,
    'public.investigacao_prova_cobertura_valida(uuid,uuid,uuid,bigint,text,text,jsonb,jsonb,text,text,text)'::regprocedure,
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
  v_tabelas_privadas regclass[] := ARRAY[
    'public.investigacoes_revisao'::regclass,
    'public.investigacao_tarefas'::regclass,
    'public.investigacao_evidencias'::regclass,
    'public.investigacao_alternativas'::regclass,
    'public.investigacao_alternativa_evidencias'::regclass,
    'public.investigacao_pendencias'::regclass,
    'public.investigacao_eventos'::regclass,
    'public.investigacao_entregas'::regclass,
    'public.investigacao_adaptadores_config'::regclass,
    'public.investigacao_adaptador_credenciais'::regclass,
    'public.investigacao_credenciais_revogadas'::regclass,
    'public.investigacao_configuracao_ativacao'::regclass,
    'public.investigacao_autorizacoes_promocao'::regclass,
    'public.investigacao_autorizacoes_corretiva'::regclass,
    'public.investigacao_sucessoes_pendentes'::regclass
  ];
  v_total_policies integer;
  v_legado_exato boolean;
  v_ativado_exato boolean;
  v_policy_restrita text :=
    'acao_tipoisdistinctfrom''promover_revisao_operacional''::text'
    || 'andacao_tipoisdistinctfrom''revisar_correcao_pos_gravacao''::text';
  v_draft_select text[] := ARRAY[
    'id', 'criado_em', 'atualizado_em', 'status', 'tipo_operacao',
    'codigo_sugerido', 'entidade_final_tipo', 'dados_extraidos',
    'campos_pendentes', 'inferencias', 'pending_action_id', 'agente',
    'origem_canal', 'origem_conversa_id', 'origem_mensagem_id',
    'contexto_canonico', 'contexto_nome', 'escopo', 'revisao_tipo'
  ];
  v_draft_insert text[] := ARRAY[
    'agente', 'status', 'tipo_operacao', 'entidade_final_tipo',
    'codigo_sugerido', 'dados_extraidos', 'campos_pendentes', 'inferencias',
    'pending_action_id', 'origem_canal', 'origem_conversa_id',
    'origem_mensagem_id', 'contexto_canonico', 'contexto_nome', 'escopo'
  ];
  v_draft_update text[] := ARRAY[
    'atualizado_em', 'status', 'codigo_sugerido', 'entidade_final_tipo',
    'dados_extraidos', 'campos_pendentes', 'inferencias', 'agente',
    'origem_canal', 'origem_conversa_id', 'origem_mensagem_id',
    'contexto_canonico', 'contexto_nome', 'escopo'
  ];
  v_acao_select text[] := ARRAY[
    'id', 'criado_em', 'atualizado_em', 'status', 'acao_tipo',
    'entidade_tipo', 'entidade_codigo', 'resumo', 'payload', 'resultado',
    'erro', 'agente', 'usuario_solicitante', 'canal', 'origem_canal',
    'origem_conversa_id', 'origem_mensagem_id', 'contexto_canonico',
    'contexto_nome', 'escopo', 'confirmado_em', 'confirmado_por', 'executavel'
  ];
  v_acao_insert text[] := ARRAY[
    'agente', 'usuario_solicitante', 'canal', 'acao_tipo', 'entidade_tipo',
    'entidade_codigo', 'resumo', 'payload', 'resultado', 'status',
    'origem_canal', 'origem_conversa_id', 'origem_mensagem_id',
    'contexto_canonico', 'contexto_nome', 'escopo'
  ];
  v_acao_update text[] := ARRAY[
    'atualizado_em', 'status', 'entidade_tipo', 'entidade_codigo', 'resumo',
    'payload', 'erro', 'agente', 'usuario_solicitante', 'canal',
    'origem_canal', 'origem_conversa_id', 'origem_mensagem_id',
    'contexto_canonico', 'contexto_nome', 'escopo'
  ];
BEGIN
  SELECT oid INTO v_authenticated FROM pg_roles WHERE rolname = 'authenticated';
  SELECT oid INTO v_anon FROM pg_roles WHERE rolname = 'anon';
  SELECT oid INTO v_service_role FROM pg_roles WHERE rolname = 'service_role';
  IF v_authenticated IS NULL OR v_anon IS NULL OR v_service_role IS NULL THEN
    RAISE EXCEPTION 'Gate de ativação: papéis anon/authenticated/service_role ausentes';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_roles
     WHERE oid IN (v_authenticated, v_anon)
       AND (rolsuper OR rolbypassrls)
  ) OR EXISTS (
    SELECT 1
      FROM pg_roles papel_privilegiado
     WHERE (papel_privilegiado.rolsuper OR papel_privilegiado.rolbypassrls)
       AND (
         pg_has_role(v_authenticated, papel_privilegiado.oid, 'MEMBER')
         OR pg_has_role(v_anon, papel_privilegiado.oid, 'MEMBER')
       )
  ) OR pg_has_role(v_authenticated, v_service_role, 'MEMBER')
     OR pg_has_role(v_anon, v_service_role, 'MEMBER') THEN
    RAISE EXCEPTION 'Gate de ativação: papel público possui superusuário, BYPASSRLS ou herança de service_role';
  END IF;
  IF has_schema_privilege(v_anon, 'public', 'CREATE')
     OR has_schema_privilege(v_authenticated, 'public', 'CREATE')
     OR has_schema_privilege(v_service_role, 'public', 'CREATE')
     OR EXISTS (
       SELECT 1
         FROM pg_namespace esquema
         CROSS JOIN LATERAL aclexplode(
           coalesce(esquema.nspacl, acldefault('n', esquema.nspowner))
         ) privilegio
        WHERE esquema.nspname = 'public'
          AND privilegio.privilege_type = 'CREATE'
          AND privilegio.grantee IN (0, v_anon, v_authenticated, v_service_role)
     ) THEN
    RAISE EXCEPTION 'Gate de ativação: papel da aplicação pode criar objeto no schema public';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_roles
     WHERE oid = v_service_role AND rolbypassrls AND NOT rolsuper
  ) THEN
    RAISE EXCEPTION 'Gate de ativação: service_role não corresponde ao mediador BYPASSRLS esperado';
  END IF;
  IF EXISTS (
    SELECT 1
      FROM pg_proc funcao
      JOIN pg_namespace esquema ON esquema.oid = funcao.pronamespace
     WHERE esquema.nspname = 'public'
       AND funcao.proname IN (
         'assumir_tarefa_investigacao', 'adiar_tarefa_investigacao',
         'decidir_pendencia_investigacao',
         'publicar_resultado_tarefa_investigacao',
         'concluir_tarefa_investigacao',
         'obsoletar_investigacao_por_mudanca_draft',
         'obsoletar_investigacao_por_mudanca_candidatos',
         'vincular_investigacao_rascunho', 'anexar_investigacao_revisao',
         'materializar_revisao_investigada',
         'exigir_investigacao_anexada_para_promocao',
         'preparar_promocao_revisao_investigada',
         'assumir_promocao_operacional',
         'concluir_promocao_operacional',
        'reconciliar_promocao_em_execucao',
        'consumir_sucessoes_promocao_terminal',
        'obter_contexto_replanejamento_sucessoes_promocao_terminal',
        'replanejar_sucessoes_promocao_terminal',
        'listar_sucessoes_promocao_terminal_pendentes',
        'saude_investigacoes_proativas',
        'substituir_investigacao_corretiva_stale',
        'obter_contexto_replanejamento_corretiva_stale',
        'replanejar_investigacao_corretiva_stale',
         'decidir_promocao_operacional',
         'decidir_revisao_corretiva'
       )
       AND NOT (funcao.oid::regprocedure = ANY(v_expostas_service))
  ) THEN
    RAISE EXCEPTION 'Gate de ativação: overload legado de RPC detectado';
  END IF;

  SELECT relowner INTO v_owner
    FROM pg_class
   WHERE oid = 'public.pending_actions'::regclass;
  IF v_owner IS NULL
     OR v_owner <> (SELECT oid FROM pg_roles WHERE rolname = current_user)
     OR v_owner IN (v_authenticated, v_anon, v_service_role)
     OR pg_has_role(v_authenticated, v_owner, 'MEMBER')
     OR pg_has_role(v_anon, v_owner, 'MEMBER')
     OR pg_has_role(v_service_role, v_owner, 'MEMBER') THEN
    RAISE EXCEPTION 'Gate de ativação: owner de pending_actions não é o executor confiável isolado';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_class
     WHERE oid = 'public.pending_actions'::regclass
       AND relrowsecurity
  ) THEN
    RAISE EXCEPTION 'Gate de ativação: RLS de pending_actions precisa estar habilitado';
  END IF;

  -- O estado já ativado possui grants por coluna criados por este próprio
  -- cutover. Reconhecemos esse estado somente pela policy exata antes de
  -- exigir a projeção fechada. A policy legada aceita tanto o ACL amplo
  -- original sem attacl quanto a mesma projeção exata preservada pelo
  -- rollback; qualquer matriz parcial ou coluna adicional continua falhando.
  SELECT count(*) = 4
    AND count(*) FILTER (
      WHERE polname = 'pending_actions_authenticated_revisoes_select'
        AND polcmd = 'r'
        AND regexp_replace(
              lower(pg_get_expr(polqual, polrelid)), '[()[:space:]]', '', 'g'
            ) = 'true'
        AND polwithcheck IS NULL
    ) = 1
    AND count(*) FILTER (
      WHERE polname = 'pending_actions_authenticated_revisoes_insert'
        AND polcmd = 'a' AND polqual IS NULL
        AND regexp_replace(
              lower(pg_get_expr(polwithcheck, polrelid)),
              '[()[:space:]]', '', 'g'
            ) = v_policy_restrita
    ) = 1
    AND count(*) FILTER (
      WHERE polname = 'pending_actions_authenticated_revisoes_update'
        AND polcmd = 'w'
        AND regexp_replace(
              lower(pg_get_expr(polqual, polrelid)), '[()[:space:]]', '', 'g'
            ) = v_policy_restrita
        AND regexp_replace(
              lower(pg_get_expr(polwithcheck, polrelid)),
              '[()[:space:]]', '', 'g'
            ) = v_policy_restrita
    ) = 1
    AND count(*) FILTER (
      WHERE polname = 'pending_actions_authenticated_revisoes_delete'
        AND polcmd = 'd'
        AND regexp_replace(
              lower(pg_get_expr(polqual, polrelid)), '[()[:space:]]', '', 'g'
            ) = v_policy_restrita
        AND polwithcheck IS NULL
    ) = 1
    AND bool_and(polpermissive)
    AND bool_and(polroles = ARRAY[v_authenticated]::oid[])
    INTO v_ativado_exato
    FROM pg_policy
   WHERE polrelid = 'public.pending_actions'::regclass;

  FOREACH v_tabela IN ARRAY ARRAY[
    'public.operation_drafts'::regclass,
    'public.negocios_candidatos'::regclass
  ] LOOP
    SELECT relowner INTO v_objeto_owner FROM pg_class WHERE oid = v_tabela::oid;
    IF v_objeto_owner IS DISTINCT FROM v_owner
       OR pg_has_role(v_authenticated, v_objeto_owner, 'MEMBER')
       OR pg_has_role(v_anon, v_objeto_owner, 'MEMBER')
       OR pg_has_role(v_service_role, v_objeto_owner, 'MEMBER')
       OR has_table_privilege('anon', v_tabela::oid, 'TRIGGER')
       OR has_table_privilege('authenticated', v_tabela::oid, 'TRIGGER')
       OR has_table_privilege('service_role', v_tabela::oid, 'TRIGGER')
       OR (
         v_tabela = 'public.negocios_candidatos'::regclass
         AND EXISTS (
           SELECT 1 FROM pg_attribute coluna
            WHERE coluna.attrelid = v_tabela::oid
              AND coluna.attnum > 0 AND NOT coluna.attisdropped
              AND coluna.attacl IS NOT NULL
              AND cardinality(coluna.attacl) > 0
         )
       )
       OR (
         v_tabela = 'public.operation_drafts'::regclass
         AND (
           (
             coalesce(v_ativado_exato, false)
             OR EXISTS (
               SELECT 1 FROM pg_attribute coluna
                WHERE coluna.attrelid = v_tabela::oid
                  AND coluna.attnum > 0 AND NOT coluna.attisdropped
                  AND coluna.attacl IS NOT NULL
                  AND cardinality(coluna.attacl) > 0
             )
           )
           AND (
               has_table_privilege(
                 'authenticated', v_tabela::oid, 'SELECT'
               )
               OR has_table_privilege(
                 'authenticated', v_tabela::oid, 'INSERT'
               )
               OR has_table_privilege(
                 'authenticated', v_tabela::oid, 'UPDATE'
               )
               OR EXISTS (
                 SELECT 1 FROM pg_attribute coluna
                 CROSS JOIN LATERAL aclexplode(coluna.attacl) privilegio
                  WHERE coluna.attrelid = v_tabela::oid
                    AND coluna.attnum > 0 AND NOT coluna.attisdropped
                    AND (
                      privilegio.grantee <> v_authenticated
                      OR privilegio.is_grantable
                      OR NOT (
                        privilegio.privilege_type = 'SELECT'
                          AND coluna.attname = ANY(v_draft_select)
                        OR privilegio.privilege_type = 'INSERT'
                          AND coluna.attname = ANY(v_draft_insert)
                        OR privilegio.privilege_type = 'UPDATE'
                          AND coluna.attname = ANY(v_draft_update)
                      )
                    )
               )
               OR EXISTS (
                 SELECT 1 FROM (
                   SELECT coluna, 'SELECT'::text AS privilegio
                     FROM unnest(v_draft_select) coluna
                   UNION ALL
                   SELECT coluna, 'INSERT'::text
                     FROM unnest(v_draft_insert) coluna
                   UNION ALL
                   SELECT coluna, 'UPDATE'::text
                     FROM unnest(v_draft_update) coluna
                 ) esperado
                  WHERE NOT has_column_privilege(
                    'authenticated', v_tabela::oid,
                    esperado.coluna, esperado.privilegio
                 )
               )
           )
         )
       )
       OR EXISTS (
         SELECT 1
           FROM pg_class classe
           CROSS JOIN LATERAL aclexplode(
             coalesce(classe.relacl, acldefault('r', classe.relowner))
           ) privilegio
          WHERE classe.oid = v_tabela::oid
            AND privilegio.grantee <> v_owner
            AND privilegio.privilege_type = 'TRIGGER'
       ) THEN
      RAISE EXCEPTION 'Gate de ativação: owner/ACL de guardião legado divergente em %', v_tabela;
    END IF;
  END LOOP;
  IF (SELECT count(*) FROM pg_trigger
       WHERE tgrelid = 'public.negocios_candidatos'::regclass
         AND tgname = 'negocios_candidatos_atualizado_em'
         AND NOT tgisinternal AND tgenabled = 'O'
         AND tgfoid =
               'public.atualizar_timestamp_staging_consolidacao()'::regprocedure
         AND tgtype = 19 AND tgqual IS NULL AND tgattr::text = '') <> 1 THEN
    RAISE EXCEPTION 'Gate de ativação: snapshot de negócios candidatos não está protegido';
  END IF;

  -- O resultado de uma promoção lease-v1 só é comprovável pelos vínculos
  -- físicos abaixo. ADD COLUMN/INDEX IF NOT EXISTS na fundação não é prova de
  -- identidade: uma coluna, FK ou índice homônimo divergente deve bloquear o
  -- cutover antes de qualquer policy ser reaberta.
  IF EXISTS (
    WITH esperados(tabela, coluna, tipo, indice, fk_nome) AS (VALUES
      ('public.compras'::regclass, 'idempotency_key', 'text'::regtype,
       'compras_idempotency_key_unique', NULL::text),
      ('public.vendas'::regclass, 'promocao_origem_id', 'uuid'::regtype,
       'vendas_promocao_origem_id_unica', 'vendas_promocao_origem_id_fkey'),
      ('public.pesagens_caderno'::regclass, 'promocao_origem_id', 'uuid'::regtype,
       'pesagens_promocao_origem_id_unica', 'pesagens_caderno_promocao_origem_id_fkey'),
      ('public.abates'::regclass, 'promocao_origem_id', 'uuid'::regtype,
       'abates_promocao_origem_id_unica', 'abates_promocao_origem_id_fkey')
    )
    SELECT 1
      FROM esperados esperado
      LEFT JOIN pg_attribute coluna
        ON coluna.attrelid = esperado.tabela
       AND coluna.attname = esperado.coluna
       AND coluna.attnum > 0
       AND NOT coluna.attisdropped
      LEFT JOIN pg_index indice
        ON indice.indexrelid = to_regclass('public.' || esperado.indice)
       AND indice.indrelid = esperado.tabela
      LEFT JOIN pg_constraint fk
        ON fk.conrelid = esperado.tabela
       AND fk.conname = esperado.fk_nome
     WHERE coluna.attnum IS NULL
        OR coluna.atttypid <> esperado.tipo
        OR coluna.attnotnull
        OR coluna.atthasdef
        OR indice.indexrelid IS NULL
        OR NOT indice.indisunique
        OR indice.indisprimary
        OR NOT indice.indisvalid
        OR NOT indice.indisready
        OR NOT indice.indislive
        OR indice.indnkeyatts <> 1
        OR indice.indnatts <> 1
        OR indice.indkey::text <> coluna.attnum::text
        OR regexp_replace(
             lower(coalesce(pg_get_expr(indice.indpred, indice.indrelid), '')),
             '[()[:space:]]', '', 'g'
           ) <> (esperado.coluna || 'isnotnull')
        OR (
          esperado.fk_nome IS NULL
          AND EXISTS (
            SELECT 1 FROM pg_constraint fk_extra
             WHERE fk_extra.conrelid = esperado.tabela
               AND fk_extra.contype = 'f'
               AND fk_extra.conkey = ARRAY[coluna.attnum]::smallint[]
          )
        )
        OR (
          esperado.fk_nome IS NOT NULL
          AND (
            fk.oid IS NULL
            OR fk.contype <> 'f'
            OR fk.confrelid <> 'public.pending_actions'::regclass
            OR fk.conkey <> ARRAY[coluna.attnum]::smallint[]
            OR fk.confkey <> ARRAY[
                 (SELECT attnum FROM pg_attribute
                   WHERE attrelid = 'public.pending_actions'::regclass
                     AND attname = 'id' AND NOT attisdropped)
               ]::smallint[]
            OR fk.confupdtype <> 'a'
            OR fk.confdeltype <> 'a'
            OR fk.confmatchtype <> 's'
            OR fk.condeferrable
            OR fk.condeferred
            OR NOT fk.convalidated
            OR (SELECT count(*) FROM pg_constraint fk_extra
                 WHERE fk_extra.conrelid = esperado.tabela
                   AND fk_extra.contype = 'f'
                   AND fk_extra.conkey = ARRAY[coluna.attnum]::smallint[]) <> 1
          )
        )
  ) THEN
    RAISE EXCEPTION 'Gate de ativação: coluna, FK ou índice de vínculo operacional diverge do contrato lease-v1';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint restricao
     WHERE restricao.conrelid = 'public.compras'::regclass
       AND restricao.conname = 'compras_idempotency_key_nao_vazia'
       AND restricao.contype = 'c'
       AND restricao.convalidated
       AND NOT restricao.condeferrable
       AND NOT restricao.condeferred
       AND regexp_replace(
             lower(pg_get_constraintdef(restricao.oid, true)),
             '[()[:space:]]', '', 'g'
           ) LIKE '%idempotency_keyisnull%'
       AND regexp_replace(
             lower(pg_get_constraintdef(restricao.oid, true)),
             '[()[:space:]]', '', 'g'
           ) LIKE '%btrimidempotency_key<>''''%'
       AND regexp_replace(
             lower(pg_get_constraintdef(restricao.oid, true)),
             '[()[:space:]]', '', 'g'
           ) LIKE '%lengthidempotency_key<=200%'
  ) THEN
    RAISE EXCEPTION 'Gate de ativação: contrato de idempotência de compras diverge';
  END IF;

  -- A fundação em sombra não pode interceptar o executor legado. Os quatro
  -- guardiões operacionais pertencem ao cutover reversível: aceitamos somente
  -- ausência total (primeiro cutover) ou presença exata (reaplicação).
  WITH esperados(tabela, gatilho) AS (VALUES
    ('public.compras'::regclass, 'compras_vinculo_promocao_protegido'),
    ('public.vendas'::regclass, 'vendas_vinculo_promocao_protegido'),
    ('public.pesagens_caderno'::regclass, 'pesagens_vinculo_promocao_protegido'),
    ('public.abates'::regclass, 'abates_vinculo_promocao_protegido')
  )
  SELECT
    bool_and(NOT EXISTS (SELECT 1 FROM pg_trigger gatilho
      WHERE gatilho.tgrelid=esperado.tabela
        AND gatilho.tgname=esperado.gatilho AND NOT gatilho.tgisinternal)),
    bool_and((SELECT count(*) FROM pg_trigger gatilho
      WHERE gatilho.tgrelid=esperado.tabela
        AND gatilho.tgname=esperado.gatilho
        AND NOT gatilho.tgisinternal AND gatilho.tgenabled='O'
        AND gatilho.tgfoid=
          'public.proteger_vinculo_promocao_operacional()'::regprocedure
        AND gatilho.tgtype=31 AND gatilho.tgqual IS NULL
        AND gatilho.tgnargs=0 AND octet_length(gatilho.tgargs)=0
        AND gatilho.tgconstraint=0 AND NOT gatilho.tgdeferrable
        AND NOT gatilho.tginitdeferred AND gatilho.tgattr::text='')=1)
    INTO v_guardioes_operacionais_ausentes, v_guardioes_operacionais_exatos
    FROM esperados esperado;
  IF NOT coalesce(v_guardioes_operacionais_ausentes,false)
     AND NOT coalesce(v_guardioes_operacionais_exatos,false) THEN
    RAISE EXCEPTION 'Gate de ativação: guardiões operacionais estão parciais ou divergentes';
  END IF;
  -- As quatro tabelas continuam com os seus DML legados, mas nenhum papel da
  -- aplicação pode instalar gatilho, delegar privilégios ou contornar o
  -- guardião por ACL de coluna. O owner precisa ser o mesmo TCB de
  -- pending_actions.
  FOREACH v_tabela IN ARRAY ARRAY[
    'public.compras'::regclass,
    'public.vendas'::regclass,
    'public.pesagens_caderno'::regclass,
    'public.abates'::regclass
  ] LOOP
    SELECT relowner INTO v_objeto_owner FROM pg_class WHERE oid = v_tabela::oid;
    IF v_objeto_owner IS DISTINCT FROM v_owner
       OR has_table_privilege('anon', v_tabela::oid, 'TRIGGER')
       OR has_table_privilege('authenticated', v_tabela::oid, 'TRIGGER')
       OR has_table_privilege('service_role', v_tabela::oid, 'TRIGGER')
       OR EXISTS (
         SELECT 1
           FROM pg_class classe
           CROSS JOIN LATERAL aclexplode(
             coalesce(classe.relacl, acldefault('r', classe.relowner))
           ) privilegio
          WHERE classe.oid = v_tabela::oid
            AND (
              (privilegio.privilege_type = 'TRIGGER'
                AND privilegio.grantee <> v_owner)
              OR (privilegio.grantee IN (v_anon, v_authenticated, v_service_role)
                AND privilegio.is_grantable)
            )
       )
       OR EXISTS (
         SELECT 1 FROM pg_attribute coluna
          WHERE coluna.attrelid = v_tabela::oid
            AND coluna.attnum > 0 AND NOT coluna.attisdropped
            AND coluna.attacl IS NOT NULL AND cardinality(coluna.attacl) > 0
       ) THEN
      RAISE EXCEPTION 'Gate de ativação: owner/ACL/TRIGGER operacional divergente em %', v_tabela;
    END IF;
  END LOOP;
  IF EXISTS (
    WITH permitidos(tabela, gatilho) AS (VALUES
      ('public.compras'::regclass, 'compras_vinculo_promocao_protegido'),
      ('public.vendas'::regclass, 'vendas_vinculo_promocao_protegido'),
      ('public.pesagens_caderno'::regclass, 'pesagens_vinculo_promocao_protegido'),
      ('public.abates'::regclass, 'abates_vinculo_promocao_protegido')
    ),
    -- Triggers legados de manutenção de updated_at, pré-existentes na
    -- produção antes desta migração. A exceção exige identidade completa:
    -- um homônimo com outra função, outro evento, WHEN, argumentos ou
    -- função fora do dono confiável NÃO é excusado e bloqueia o gate.
    legados(tabela, gatilho, funcao, tipo) AS (VALUES
      ('public.compras'::regclass, 'trg_upd_compras',
       to_regprocedure('public.set_updated_at()'), 19),
      ('public.vendas'::regclass, 'trg_upd_vendas',
       to_regprocedure('public.set_updated_at()'), 19)
    )
    SELECT 1 FROM pg_trigger gatilho
     WHERE NOT gatilho.tgisinternal
       AND gatilho.tgrelid = ANY(ARRAY[
         'public.compras'::regclass,
         'public.vendas'::regclass,
         'public.pesagens_caderno'::regclass,
         'public.abates'::regclass
       ])
       -- BEFORE + FOR EACH ROW. Um gatilho extra nessa posição poderia
       -- reescrever NEW antes do guardião de vínculo durável.
       AND (gatilho.tgtype & 3) = 3
       AND NOT EXISTS (
         SELECT 1 FROM permitidos permitido
          WHERE permitido.tabela = gatilho.tgrelid
            AND permitido.gatilho = gatilho.tgname
       )
       AND NOT EXISTS (
         SELECT 1 FROM legados legado
          WHERE legado.tabela = gatilho.tgrelid
            AND legado.gatilho = gatilho.tgname
            AND legado.funcao IS NOT NULL
            AND legado.funcao::oid = gatilho.tgfoid
            AND legado.tipo::int2 = gatilho.tgtype
            AND gatilho.tgenabled = 'O'
            AND gatilho.tgqual IS NULL
            AND gatilho.tgnargs = 0
            AND octet_length(gatilho.tgargs) = 0
            AND gatilho.tgconstraint = 0
            AND NOT gatilho.tgdeferrable
            AND NOT gatilho.tginitdeferred
            AND gatilho.tgattr::text = ''
            -- A função excusada precisa pertencer ao dono confiável, sem
            -- SECURITY DEFINER, em plpgsql e sem overload homônimo: só o
            -- owner pode trocar o corpo, e é nisso que a excusa se apoia.
            AND EXISTS (
              SELECT 1 FROM pg_proc funcao
                JOIN pg_language linguagem ON linguagem.oid = funcao.prolang
               WHERE funcao.oid = gatilho.tgfoid
                 AND funcao.proowner = v_owner
                 AND NOT funcao.prosecdef
                 AND linguagem.lanname = 'plpgsql'
                 AND (SELECT count(*) FROM pg_proc homonimo
                       JOIN pg_namespace esquema
                         ON esquema.oid = homonimo.pronamespace
                      WHERE esquema.nspname = 'public'
                        AND homonimo.proname = funcao.proname) = 1
            )
       )
  ) THEN
    RAISE EXCEPTION 'Gate de ativação: trigger BEFORE ROW operacional não allowlisted';
  END IF;

  -- O ramo corretivo e o lease são contratos físicos; nomes homônimos com
  -- definição diferente seriam uma aceitação silenciosa de preseed drift.
  IF EXISTS (
    WITH esperados(tabela, restricao, fragmentos) AS (VALUES
      ('public.pending_actions'::regclass,
       'pending_actions_promocao_lease_v1_valido',
       ARRAY['promocao_controle_version', 'lease-v1',
             'promocao_fencing_token', 'em_execucao']),
      ('public.pending_actions'::regclass,
       'pending_actions_promocao_preparacao_interna_valida',
       ARRAY['promocao_preparacao_chave', 'promocao_preparacao_hash',
             'lease-v1']),
      ('public.pending_actions'::regclass,
       'pending_actions_corretiva_nao_executavel',
       ARRAY['revisar_correcao_pos_gravacao', 'notexecutavel',
             'operation_draft', 'proposed_record']),
      ('public.operation_drafts'::regclass,
       'operation_drafts_revisao_tipo_valido',
       ARRAY['pre_revisao', 'corretiva_pos_gravacao',
             'investigacao_origem_id', 'promocao_origem_id'])
    )
    SELECT 1 FROM esperados esperado
     LEFT JOIN pg_constraint restricao
       ON restricao.conrelid = esperado.tabela
      AND restricao.conname = esperado.restricao
     WHERE restricao.oid IS NULL
        OR restricao.contype <> 'c'
        OR NOT restricao.convalidated
        OR restricao.condeferrable
        OR restricao.condeferred
        OR EXISTS (
          SELECT 1 FROM unnest(esperado.fragmentos) fragmento
           WHERE regexp_replace(
             lower(pg_get_constraintdef(restricao.oid, true)),
             '[()[:space:]]', '', 'g'
           ) NOT LIKE '%' || fragmento || '%'
        )
  ) THEN
    RAISE EXCEPTION 'Gate de ativação: check lease/corretivo diverge do contrato';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_index indice
    JOIN pg_attribute coluna
      ON coluna.attrelid = 'public.operation_drafts'::regclass
     AND coluna.attname = 'investigacao_origem_id'
     AND coluna.attnum > 0 AND NOT coluna.attisdropped
   WHERE indice.indexrelid =
           'public.operation_drafts_corretiva_investigacao_unica'::regclass
     AND indice.indrelid = 'public.operation_drafts'::regclass
     AND indice.indisunique AND NOT indice.indisprimary
     AND indice.indisvalid AND indice.indisready AND indice.indislive
     AND indice.indnkeyatts = 1 AND indice.indnatts = 1
     AND indice.indkey::text = coluna.attnum::text
     AND regexp_replace(
           lower(coalesce(pg_get_expr(indice.indpred, indice.indrelid), '')),
           '[()[:space:]]', '', 'g'
         ) = 'revisao_tipo=''corretiva_pos_gravacao''::text'
  ) THEN
    RAISE EXCEPTION 'Gate de ativação: índice único de rascunho corretivo diverge do contrato';
  END IF;

  FOREACH v_tabela IN ARRAY v_tabelas_privadas LOOP
    SELECT relowner INTO v_objeto_owner FROM pg_class WHERE oid = v_tabela::oid;
    IF v_objeto_owner IS DISTINCT FROM v_owner
       OR NOT (SELECT relrowsecurity FROM pg_class WHERE oid = v_tabela::oid)
       OR (SELECT relforcerowsecurity FROM pg_class WHERE oid = v_tabela::oid)
       OR EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = v_tabela::oid)
       OR has_table_privilege('anon', v_tabela::oid, 'SELECT')
       OR has_table_privilege('anon', v_tabela::oid, 'INSERT')
       OR has_table_privilege('anon', v_tabela::oid, 'UPDATE')
       OR has_table_privilege('anon', v_tabela::oid, 'DELETE')
       OR has_table_privilege('authenticated', v_tabela::oid, 'SELECT')
       OR has_table_privilege('authenticated', v_tabela::oid, 'INSERT')
       OR has_table_privilege('authenticated', v_tabela::oid, 'UPDATE')
       OR has_table_privilege('authenticated', v_tabela::oid, 'DELETE')
       OR has_table_privilege('service_role', v_tabela::oid, 'DELETE')
       OR has_table_privilege('service_role', v_tabela::oid, 'TRUNCATE')
       OR has_table_privilege('service_role', v_tabela::oid, 'TRIGGER')
       OR has_table_privilege('service_role', v_tabela::oid, 'REFERENCES')
       OR has_table_privilege('service_role', v_tabela::oid, 'SELECT')
            IS DISTINCT FROM (v_tabela = ANY(ARRAY[
              'public.investigacoes_revisao'::regclass,
              'public.investigacao_tarefas'::regclass,
              'public.investigacao_evidencias'::regclass,
              'public.investigacao_alternativas'::regclass,
              'public.investigacao_alternativa_evidencias'::regclass,
              'public.investigacao_pendencias'::regclass,
              'public.investigacao_eventos'::regclass,
              'public.investigacao_entregas'::regclass
            ]))
       OR has_table_privilege('service_role', v_tabela::oid, 'INSERT')
            IS DISTINCT FROM (v_tabela = ANY(ARRAY[
              'public.investigacoes_revisao'::regclass,
              'public.investigacao_tarefas'::regclass,
              'public.investigacao_eventos'::regclass,
              'public.investigacao_entregas'::regclass
            ]))
       OR has_table_privilege('service_role', v_tabela::oid, 'UPDATE')
            IS DISTINCT FROM (
              v_tabela = 'public.investigacao_entregas'::regclass
            )
       OR EXISTS (
         SELECT 1
           FROM pg_class classe
           CROSS JOIN LATERAL aclexplode(
             coalesce(classe.relacl, acldefault('r', classe.relowner))
          ) privilegio
          WHERE classe.oid = v_tabela::oid
            AND privilegio.grantee <> v_owner
            AND (
              privilegio.grantee <> v_service_role
              OR privilegio.is_grantable
              OR NOT (
                privilegio.privilege_type = 'SELECT'
                  AND v_tabela = ANY(ARRAY[
                    'public.investigacoes_revisao'::regclass,
                    'public.investigacao_tarefas'::regclass,
                    'public.investigacao_evidencias'::regclass,
                    'public.investigacao_alternativas'::regclass,
                    'public.investigacao_alternativa_evidencias'::regclass,
                    'public.investigacao_pendencias'::regclass,
                    'public.investigacao_eventos'::regclass,
                    'public.investigacao_entregas'::regclass
                  ])
                OR privilegio.privilege_type = 'INSERT'
                  AND v_tabela = ANY(ARRAY[
                    'public.investigacoes_revisao'::regclass,
                    'public.investigacao_tarefas'::regclass,
                    'public.investigacao_eventos'::regclass,
                    'public.investigacao_entregas'::regclass
                  ])
                OR privilegio.privilege_type = 'UPDATE'
                  AND v_tabela = 'public.investigacao_entregas'::regclass
              )
            )
       ) THEN
      RAISE EXCEPTION 'Gate de ativação: owner/RLS/ACL divergente na tabela %', v_tabela;
    END IF;
    IF EXISTS (
      SELECT 1
        FROM pg_attribute coluna
       WHERE coluna.attrelid = v_tabela::oid
         AND coluna.attnum > 0
         AND NOT coluna.attisdropped
         AND coluna.attacl IS NOT NULL
         AND cardinality(coluna.attacl) > 0
    ) THEN
      RAISE EXCEPTION 'Gate de ativação: grant por coluna em %', v_tabela;
    END IF;
  END LOOP;

  -- As capacidades são permissões efêmeras, não uma fila de trabalho nem uma
  -- API. Exigimos estrutura privada, sem policy/grant alternativo e vazia antes
  -- do cutover; qualquer resíduo pode ser uma capacidade reutilizável.
  FOREACH v_tabela IN ARRAY ARRAY[
    'public.investigacao_autorizacoes_promocao'::regclass,
    'public.investigacao_autorizacoes_corretiva'::regclass
  ] LOOP
    IF (SELECT relowner FROM pg_class WHERE oid = v_tabela::oid) IS DISTINCT FROM v_owner
       OR NOT (SELECT relrowsecurity FROM pg_class WHERE oid = v_tabela::oid)
       OR (SELECT relforcerowsecurity FROM pg_class WHERE oid = v_tabela::oid)
       OR EXISTS (SELECT 1 FROM pg_policy WHERE polrelid = v_tabela::oid)
       OR has_table_privilege('anon', v_tabela::oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
       OR has_table_privilege('authenticated', v_tabela::oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
       OR has_table_privilege('service_role', v_tabela::oid, 'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER')
       OR EXISTS (
         SELECT 1
           FROM pg_class classe
           CROSS JOIN LATERAL aclexplode(
             coalesce(classe.relacl, acldefault('r', classe.relowner))
           ) privilegio
          WHERE classe.oid = v_tabela::oid
            AND privilegio.grantee <> v_owner
       )
       OR EXISTS (
         SELECT 1 FROM pg_attribute coluna
          WHERE coluna.attrelid = v_tabela::oid
            AND coluna.attnum > 0 AND NOT coluna.attisdropped
            AND coluna.attacl IS NOT NULL AND cardinality(coluna.attacl) > 0
       ) THEN
      RAISE EXCEPTION 'Gate de ativação: capacidade privada possui owner/RLS/policy/ACL divergente em %', v_tabela;
    END IF;
  END LOOP;
  IF EXISTS (SELECT 1 FROM public.investigacao_autorizacoes_promocao)
     OR EXISTS (SELECT 1 FROM public.investigacao_autorizacoes_corretiva) THEN
    RAISE EXCEPTION 'Gate de ativação: capacidade transitória residual impede o cutover';
  END IF;
  -- Uma reaplicação do gate também funciona como preflight de saúde. Repetir
  -- a varredura não pode esconder a idade real do intent, por isso a janela é
  -- medida desde criado_em, não desde a última tentativa do heartbeat.
  IF EXISTS (
    SELECT 1 FROM public.investigacao_sucessoes_pendentes
     WHERE (
       estado IN (
         'pendente', 'aguardando_reconciliacao', 'aguardando_planejamento'
       )
       AND criado_em < clock_timestamp() - interval '15 minutes'
     ) OR estado = 'falha_permanente'
  ) THEN
    RAISE EXCEPTION 'Gate de ativação: outbox terminal antiga impede o cutover; drene ou reconcilie antes de ativar';
  END IF;

  IF (SELECT count(*) FROM public.investigacao_configuracao_ativacao) <> 1
     OR NOT EXISTS (
       SELECT 1 FROM public.investigacao_configuracao_ativacao
        WHERE adaptadores_isolados
          AND workers_sem_service_role
          AND public.investigacao_instante_operacional(atestado_em)
          AND atestado_em <= clock_timestamp()
          AND atestado_em >= clock_timestamp() - interval '15 minutes'
          AND atestado_por = session_user
          AND broker_version = nullif(
                current_setting('confinex.broker_version_esperada', true), ''
              )
          AND broker_artefato_hash = nullif(
                current_setting('confinex.broker_hash_esperado', true), ''
              )
          AND teste_capacidades_hash = nullif(
                current_setting('confinex.teste_capacidades_hash', true), ''
              )
     ) THEN
    RAISE EXCEPTION 'Gate de ativação: broker isolado ainda não foi atestado';
  END IF;
  IF (SELECT count(*) FROM public.investigacao_adaptadores_config
       WHERE habilitado) < 1
     OR EXISTS (
       SELECT adaptador
         FROM public.investigacao_adaptadores_config
        WHERE habilitado
        GROUP BY adaptador
       HAVING count(*) <> 1
     )
     OR EXISTS (
       SELECT config.adaptador, config.adaptador_version
         FROM public.investigacao_adaptadores_config config
        WHERE config.habilitado
          AND (
            NOT public.investigacao_manifesto_adaptador_valido(
              config.adaptador,
              config.adaptador_version,
              config.familia_fonte,
              config.autoridade_fonte,
              config.fontes_tipo_permitidas,
              config.tabelas_permitidas,
              config.tabelas_nativas,
              config.identidades_permitidas,
              config.capacidades
            )
            OR (
            SELECT count(*)
              FROM public.investigacao_adaptador_credenciais credencial
             WHERE credencial.adaptador = config.adaptador
               AND credencial.adaptador_version = config.adaptador_version
               AND clock_timestamp() >= credencial.valida_desde
               AND clock_timestamp() < credencial.emite_ate
               AND NOT EXISTS (
                 SELECT 1
                   FROM public.investigacao_credenciais_revogadas revogada
                  WHERE revogada.adaptador = credencial.adaptador
                    AND revogada.adaptador_version = credencial.adaptador_version
                    AND revogada.chave_id = credencial.chave_id
               )
            ) <> 1
          )
     )
     OR EXISTS (
       SELECT 1
         FROM public.investigacao_tarefas tarefa
        WHERE tarefa.adaptador <> 'sintese'
          AND tarefa.estado_execucao IN (
            'pendente', 'aguardando_retentativa', 'em_execucao'
          )
          AND NOT EXISTS (
            SELECT 1
              FROM public.investigacao_adaptadores_config config
             WHERE config.adaptador = tarefa.adaptador
               AND config.adaptador_version = tarefa.adaptador_version
               AND config.habilitado
          )
     ) THEN
    RAISE EXCEPTION 'Gate de ativação: adaptadores habilitados não possuem exatamente um emissor vigente';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.pending_actions
     WHERE acao_tipo = 'promover_revisao_operacional'
       AND (status = ANY(ARRAY[
         'preparada', 'aguardando_confirmacao', 'aprovado_confinex',
         'em_execucao', 'executado', 'erro_pos_gravacao', 'erro',
         'cancelado', 'rejeitado', 'expirado'
       ])) IS NOT TRUE
  ) THEN
    RAISE EXCEPTION 'Gate de ativação: promoção legada possui status nulo ou desconhecido';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.pending_actions
     WHERE acao_tipo = 'promover_revisao_operacional'
       AND status NOT IN (
         'executado', 'erro_pos_gravacao', 'erro',
         'cancelado', 'rejeitado', 'expirado'
       )
       AND promocao_controle_version IS DISTINCT FROM 'lease-v1'
  ) THEN
    RAISE EXCEPTION 'Gate de ativação: promoção legada não terminal precisa ser drenada ou preparada novamente pelo mediador lease-v1';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.investigacao_autorizacoes_promocao
  ) THEN
    RAISE EXCEPTION 'Gate de ativação: capacidade transitória de promoção permaneceu aberta';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.investigacao_tarefas
     WHERE estado_execucao = 'em_execucao'
  ) OR EXISTS (
    SELECT 1 FROM public.pending_actions
     WHERE acao_tipo = 'promover_revisao_operacional'
       AND status = 'em_execucao'
  ) THEN
    RAISE EXCEPTION 'Gate de ativação: tarefa ou promoção ainda está em execução; drene antes do cutover';
  END IF;
  IF has_table_privilege('anon', 'public.pending_actions', 'INSERT')
     OR has_table_privilege('anon', 'public.pending_actions', 'UPDATE')
     OR has_table_privilege('anon', 'public.pending_actions', 'DELETE')
     OR has_table_privilege('anon', 'public.pending_actions', 'TRUNCATE')
     OR has_table_privilege('anon', 'public.pending_actions', 'TRIGGER')
     OR has_table_privilege('authenticated', 'public.pending_actions', 'TRUNCATE')
     OR has_table_privilege('authenticated', 'public.pending_actions', 'TRIGGER')
     OR has_table_privilege('authenticated', 'public.pending_actions', 'REFERENCES')
     OR has_table_privilege('service_role', 'public.pending_actions', 'DELETE')
     OR has_table_privilege('service_role', 'public.pending_actions', 'TRUNCATE')
     OR has_table_privilege('service_role', 'public.pending_actions', 'TRIGGER')
     OR has_table_privilege('service_role', 'public.pending_actions', 'REFERENCES')
     OR EXISTS (
       SELECT 1
         FROM pg_class classe
         CROSS JOIN LATERAL aclexplode(
           coalesce(classe.relacl, acldefault('r', classe.relowner))
         ) privilegio
        WHERE classe.oid = 'public.pending_actions'::regclass
          AND privilegio.grantee = 0
          AND privilegio.privilege_type IN (
            'INSERT', 'UPDATE', 'DELETE', 'TRUNCATE', 'TRIGGER', 'REFERENCES'
          )
     ) THEN
    RAISE EXCEPTION 'Gate de ativação: privilégio destrutivo ou de trigger inesperado em pending_actions';
  END IF;
  IF NOT has_table_privilege('authenticated', 'public.pending_actions', 'DELETE')
     OR NOT has_table_privilege('service_role', 'public.pending_actions', 'SELECT')
     OR NOT has_table_privilege('service_role', 'public.pending_actions', 'INSERT')
     OR NOT has_table_privilege('service_role', 'public.pending_actions', 'UPDATE')
     OR (
       NOT coalesce(v_ativado_exato, false)
       AND NOT EXISTS (
         SELECT 1 FROM pg_attribute coluna
          WHERE coluna.attrelid = 'public.pending_actions'::regclass
            AND coluna.attnum > 0 AND NOT coluna.attisdropped
            AND coluna.attacl IS NOT NULL
            AND cardinality(coluna.attacl) > 0
       )
       AND (
         NOT has_table_privilege(
           'authenticated', 'public.pending_actions', 'SELECT'
         )
         OR NOT has_table_privilege(
           'authenticated', 'public.pending_actions', 'INSERT'
         )
         OR NOT has_table_privilege(
           'authenticated', 'public.pending_actions', 'UPDATE'
         )
       )
     )
     OR (
       (
         coalesce(v_ativado_exato, false)
         OR EXISTS (
           SELECT 1 FROM pg_attribute coluna
            WHERE coluna.attrelid = 'public.pending_actions'::regclass
              AND coluna.attnum > 0 AND NOT coluna.attisdropped
              AND coluna.attacl IS NOT NULL
              AND cardinality(coluna.attacl) > 0
         )
       )
       AND (
         has_table_privilege(
           'authenticated', 'public.pending_actions', 'SELECT'
         )
         OR has_table_privilege(
           'authenticated', 'public.pending_actions', 'INSERT'
         )
         OR has_table_privilege(
           'authenticated', 'public.pending_actions', 'UPDATE'
         )
         OR EXISTS (
           SELECT 1 FROM pg_attribute coluna
           CROSS JOIN LATERAL aclexplode(coluna.attacl) privilegio
            WHERE coluna.attrelid = 'public.pending_actions'::regclass
              AND coluna.attnum > 0 AND NOT coluna.attisdropped
              AND (
                privilegio.grantee <> v_authenticated
                OR privilegio.is_grantable
                OR NOT (
                  privilegio.privilege_type = 'SELECT'
                    AND coluna.attname = ANY(v_acao_select)
                  OR privilegio.privilege_type = 'INSERT'
                    AND coluna.attname = ANY(v_acao_insert)
                  OR privilegio.privilege_type = 'UPDATE'
                    AND coluna.attname = ANY(v_acao_update)
                )
              )
         )
         OR EXISTS (
           SELECT 1 FROM (
             SELECT coluna, 'SELECT'::text AS privilegio
               FROM unnest(v_acao_select) coluna
             UNION ALL
             SELECT coluna, 'INSERT'::text FROM unnest(v_acao_insert) coluna
             UNION ALL
             SELECT coluna, 'UPDATE'::text FROM unnest(v_acao_update) coluna
           ) esperado
            WHERE NOT has_column_privilege(
              'authenticated', 'public.pending_actions',
              esperado.coluna, esperado.privilegio
            )
         )
       )
     )
     OR EXISTS (
       SELECT 1
         FROM pg_class classe
         CROSS JOIN LATERAL aclexplode(
           coalesce(classe.relacl, acldefault('r', classe.relowner))
         ) privilegio
        WHERE classe.oid = 'public.pending_actions'::regclass
          AND privilegio.grantee <> v_owner
          AND (
            privilegio.is_grantable
            OR NOT (
              privilegio.grantee = v_authenticated
                AND (
                  privilegio.privilege_type = 'DELETE'
                  OR NOT coalesce(v_ativado_exato, false)
                    AND privilegio.privilege_type IN (
                      'SELECT', 'INSERT', 'UPDATE'
                    )
                )
              OR privilegio.grantee = v_service_role
                AND privilegio.privilege_type IN (
                  'SELECT', 'INSERT', 'UPDATE'
                )
            )
          )
     ) THEN
    RAISE EXCEPTION 'Gate de ativação: privilégios esperados do frontend/mediador estão incompletos';
  END IF;

  -- Catálogo fechado: além de provar cada guardião obrigatório, rejeita todo
  -- trigger preseed/legado não inventariado. O guardião terminal é obrigatório
  -- também na fase sombra; numa reaplicação todos precisam ter a identidade
  -- exata declarada na allowlist.
  IF EXISTS (
    WITH permitidos(tabela, gatilho, funcao, tipo) AS (VALUES
      ('public.operation_drafts', 'operation_drafts_contexto_canonico',
       'public.preencher_contexto_canonico()', 23),
      ('public.operation_drafts', 'operation_drafts_investigacao_atualizado_em',
       'public.atualizar_timestamp_investigacoes_revisao()', 19),
      ('public.operation_drafts', 'operation_drafts_protecao_corretiva_permanente',
       'public.proteger_draft_corretivo_permanente()', 31),
      ('public.operation_drafts', 'operation_drafts_bloqueia_investigacao',
       'public.bloquear_draft_com_investigacao()', 17),
      ('public.pending_actions', 'pending_actions_contexto_canonico',
       'public.preencher_contexto_canonico()', 23),
      ('public.pending_actions', 'pending_actions_investigacao_atualizado_em',
       'public.atualizar_timestamp_investigacoes_revisao()', 19),
      ('public.pending_actions', 'pending_actions_protecao_permanente',
       'public.proteger_pending_action_permanente()', 31),
      ('public.pending_actions', 'pending_actions_bloqueia_investigacao',
       'public.bloquear_pending_action_com_investigacao()', 21),
      ('public.pending_actions', 'pending_actions_reativa_complementar',
       'public.reativar_complementar_promocao_sem_gravacao()', 17),
      ('public.negocios_candidatos', 'negocios_candidatos_atualizado_em',
       'public.atualizar_timestamp_staging_consolidacao()', 19),
      ('public.investigacao_adaptadores_config', 'investigacao_adaptadores_config_append_only',
       'public.proteger_config_adaptador()', 27),
      ('public.investigacao_adaptador_credenciais', 'investigacao_adaptador_credenciais_janela',
       'public.validar_janela_emissao_credencial()', 7),
      ('public.investigacao_adaptador_credenciais', 'investigacao_adaptador_credenciais_append_only',
       'public.proteger_registro_adaptador_imutavel()', 27),
      ('public.investigacao_credenciais_revogadas', 'investigacao_credenciais_revogadas_valida',
       'public.validar_revogacao_credencial()', 7),
      ('public.investigacao_credenciais_revogadas', 'investigacao_credenciais_revogadas_append_only',
       'public.proteger_registro_adaptador_imutavel()', 27),
      ('public.investigacao_eventos', 'investigacao_eventos_cria_entrega',
       'public.criar_entrega_evento_investigacao()', 5),
      ('public.investigacao_tarefas', 'investigacao_tarefas_plano_imutavel',
       'public.validar_tarefa_no_plano_investigacao()', 7),
      ('public.investigacao_tarefas', 'investigacao_tarefas_consulta_imutavel',
       'public.proteger_consulta_tarefa_investigacao()', 19),
      ('public.investigacoes_revisao', 'investigacoes_revisao_atualizado_em',
       'public.atualizar_timestamp_investigacoes_revisao()', 19),
      ('public.investigacoes_revisao', 'investigacoes_revisao_serializacao',
       'public.serializar_investigacao_revisao()', 7),
      ('public.investigacoes_revisao', 'investigacoes_revisao_origem_imutavel',
       'public.proteger_origem_investigacao_revisao()', 19),
      ('public.investigacoes_revisao', 'investigacoes_revisao_obsolescencia_protegida',
       'public.proteger_obsolescencia_investigacao()', 19),
      ('public.investigacoes_revisao', 'investigacoes_revisao_atestacao_protegida',
       'public.proteger_atestacao_decisao_investigacao()', 19),
      ('public.investigacao_entregas', 'investigacao_entregas_atualizado_em',
       'public.atualizar_timestamp_investigacoes_revisao()', 19),
      ('public.investigacao_evidencias', 'investigacao_evidencias_fencing',
       'public.validar_fencing_resultado_investigacao()', 7),
      ('public.investigacao_alternativas', 'investigacao_alternativas_fencing',
       'public.validar_fencing_resultado_investigacao()', 7),
      ('public.investigacao_pendencias', 'investigacao_pendencias_fencing',
       'public.validar_fencing_resultado_investigacao()', 7),
      ('public.investigacao_alternativa_evidencias', 'investigacao_alternativa_evidencias_fencing',
       'public.validar_fencing_ligacao_investigacao()', 7),
      ('public.investigacao_sucessoes_pendentes',
       'investigacao_sucessoes_pendentes_imutavel',
       'public.proteger_sucessao_promocao_terminal()', 27)
    ), tabelas(nome) AS (VALUES
      ('public.operation_drafts'), ('public.pending_actions'),
      ('public.negocios_candidatos'), ('public.investigacoes_revisao'),
      ('public.investigacao_tarefas'), ('public.investigacao_evidencias'),
      ('public.investigacao_alternativas'),
      ('public.investigacao_alternativa_evidencias'),
      ('public.investigacao_pendencias'), ('public.investigacao_eventos'),
      ('public.investigacao_entregas'),
      ('public.investigacao_adaptadores_config'),
      ('public.investigacao_adaptador_credenciais'),
      ('public.investigacao_credenciais_revogadas'),
      ('public.investigacao_configuracao_ativacao'),
      ('public.investigacao_autorizacoes_promocao'),
      ('public.investigacao_autorizacoes_corretiva'),
      ('public.investigacao_sucessoes_pendentes')
    )
    SELECT 1
      FROM pg_trigger gatilho
      JOIN tabelas ON gatilho.tgrelid = tabelas.nome::regclass
      LEFT JOIN permitidos permitido
        ON permitido.tabela::regclass = gatilho.tgrelid
       AND permitido.gatilho = gatilho.tgname
       AND permitido.funcao::regprocedure = gatilho.tgfoid
       AND permitido.tipo = gatilho.tgtype
     WHERE NOT gatilho.tgisinternal
       AND (
         permitido.gatilho IS NULL OR gatilho.tgenabled <> 'O'
         OR gatilho.tgqual IS NOT NULL
         OR gatilho.tgnargs <> 0
         OR octet_length(gatilho.tgargs) <> 0
         OR gatilho.tgconstraint <> 0
         OR gatilho.tgdeferrable
         OR gatilho.tginitdeferred
         OR NOT (
           gatilho.tgattr::text = ''
           OR (
             gatilho.tgrelid = 'public.pending_actions'::regclass
             AND gatilho.tgname = 'pending_actions_reativa_complementar'
             AND gatilho.tgattr::text = (
               SELECT attnum::text FROM pg_attribute
                WHERE attrelid = 'public.pending_actions'::regclass
                  AND attname = 'status' AND NOT attisdropped
             )
           )
           OR (
             gatilho.tgrelid = 'public.investigacao_tarefas'::regclass
             AND gatilho.tgname = 'investigacao_tarefas_consulta_imutavel'
             AND gatilho.tgattr::text = (
               SELECT string_agg(coluna.attnum::text, ' ' ORDER BY nome.ordem)
                 FROM unnest(ARRAY[
                   'plano_item_ref', 'consulta_ref',
                   'consulta_schema_version', 'consulta_spec',
                   'consulta_canonico', 'consulta_hash',
                   'adaptador', 'adaptador_version'
                 ]::text[]) WITH ORDINALITY AS nome(attname, ordem)
                 JOIN pg_attribute coluna
                   ON coluna.attrelid = 'public.investigacao_tarefas'::regclass
                  AND coluna.attname = nome.attname
                  AND coluna.attnum > 0 AND NOT coluna.attisdropped
             )
           )
           OR (
             gatilho.tgrelid = 'public.investigacoes_revisao'::regclass
             AND gatilho.tgname = 'investigacoes_revisao_origem_imutavel'
             AND gatilho.tgattr::text = (
               SELECT string_agg(coluna.attnum::text, ' ' ORDER BY nome.ordem)
                 FROM unnest(ARRAY[
                   'source_draft_id', 'source_draft_atualizado_em',
                   'raiz_investigacao_id', 'sucessora_de_id', 'geracao',
                   'sucessao_pedido_hash', 'sucessao_outbox_id',
                   'negocio_candidato_id', 'negocio_candidato_ids',
                   'source_candidato_atualizado_em',
                   'source_candidatos_atualizados_em', 'fingerprint_base',
                   'policy_version', 'policy_schema_hash', 'plano_hash',
                   'plano_canonico', 'plano_tarefas', 'campos_obrigatorios',
                   'fluxo_tipo', 'promocao_origem_id',
                   'draft_operacional_origem_id',
                   'destino_operacional_origem',
                   'registro_operacional_origem_id',
                   'registro_operacional_origem_snapshot_ref',
                   'vinculo_operacional_estado'
                 ]::text[]) WITH ORDINALITY AS nome(attname, ordem)
                 JOIN pg_attribute coluna
                   ON coluna.attrelid = 'public.investigacoes_revisao'::regclass
                  AND coluna.attname = nome.attname
                  AND coluna.attnum > 0 AND NOT coluna.attisdropped
             )
           )
           OR (
             gatilho.tgrelid = 'public.investigacoes_revisao'::regclass
             AND gatilho.tgname =
                   'investigacoes_revisao_obsolescencia_protegida'
             AND gatilho.tgattr::text = (
               SELECT string_agg(coluna.attnum::text, ' ' ORDER BY nome.ordem)
                 FROM unnest(ARRAY[
                   'obsolescencia_motivo', 'promocao_ativa_id'
                 ]::text[]) WITH ORDINALITY AS nome(attname, ordem)
                 JOIN pg_attribute coluna
                   ON coluna.attrelid = 'public.investigacoes_revisao'::regclass
                  AND coluna.attname = nome.attname
                  AND coluna.attnum > 0 AND NOT coluna.attisdropped
             )
           )
           OR (
             gatilho.tgrelid = 'public.investigacoes_revisao'::regclass
             AND gatilho.tgname = 'investigacoes_revisao_atestacao_protegida'
             AND gatilho.tgattr::text = (
               SELECT string_agg(coluna.attnum::text, ' ' ORDER BY nome.ordem)
                 FROM unnest(ARRAY[
                   'decisao_draft_atualizado_em',
                   'decisao_preparacao_hash'
                 ]::text[]) WITH ORDINALITY AS nome(attname, ordem)
                 JOIN pg_attribute coluna
                   ON coluna.attrelid = 'public.investigacoes_revisao'::regclass
                  AND coluna.attname = nome.attname
                  AND coluna.attnum > 0 AND NOT coluna.attisdropped
             )
           )
         )
       )
  ) THEN
    RAISE EXCEPTION 'Gate de ativação: trigger extra ou identidade divergente no catálogo protegido';
  END IF;
  IF EXISTS (
    WITH obrigatorios(tabela, gatilho, funcao, tipo) AS (VALUES
      ('public.operation_drafts', 'operation_drafts_contexto_canonico',
       'public.preencher_contexto_canonico()', 23),
      ('public.operation_drafts', 'operation_drafts_investigacao_atualizado_em',
       'public.atualizar_timestamp_investigacoes_revisao()', 19),
      ('public.operation_drafts', 'operation_drafts_protecao_corretiva_permanente',
       'public.proteger_draft_corretivo_permanente()', 31),
      ('public.pending_actions', 'pending_actions_contexto_canonico',
       'public.preencher_contexto_canonico()', 23),
      ('public.pending_actions', 'pending_actions_investigacao_atualizado_em',
       'public.atualizar_timestamp_investigacoes_revisao()', 19),
      ('public.pending_actions', 'pending_actions_protecao_permanente',
       'public.proteger_pending_action_permanente()', 31),
      ('public.negocios_candidatos', 'negocios_candidatos_atualizado_em',
       'public.atualizar_timestamp_staging_consolidacao()', 19),
      ('public.investigacao_adaptadores_config', 'investigacao_adaptadores_config_append_only',
       'public.proteger_config_adaptador()', 27),
      ('public.investigacao_adaptador_credenciais', 'investigacao_adaptador_credenciais_janela',
       'public.validar_janela_emissao_credencial()', 7),
      ('public.investigacao_adaptador_credenciais', 'investigacao_adaptador_credenciais_append_only',
       'public.proteger_registro_adaptador_imutavel()', 27),
      ('public.investigacao_credenciais_revogadas', 'investigacao_credenciais_revogadas_valida',
       'public.validar_revogacao_credencial()', 7),
      ('public.investigacao_credenciais_revogadas', 'investigacao_credenciais_revogadas_append_only',
       'public.proteger_registro_adaptador_imutavel()', 27),
      ('public.investigacao_eventos', 'investigacao_eventos_cria_entrega',
       'public.criar_entrega_evento_investigacao()', 5),
      ('public.investigacao_tarefas', 'investigacao_tarefas_plano_imutavel',
       'public.validar_tarefa_no_plano_investigacao()', 7),
      ('public.investigacao_tarefas', 'investigacao_tarefas_consulta_imutavel',
       'public.proteger_consulta_tarefa_investigacao()', 19),
      ('public.investigacoes_revisao', 'investigacoes_revisao_atualizado_em',
       'public.atualizar_timestamp_investigacoes_revisao()', 19),
      ('public.investigacoes_revisao', 'investigacoes_revisao_serializacao',
       'public.serializar_investigacao_revisao()', 7),
      ('public.investigacoes_revisao', 'investigacoes_revisao_origem_imutavel',
       'public.proteger_origem_investigacao_revisao()', 19),
      ('public.investigacoes_revisao', 'investigacoes_revisao_obsolescencia_protegida',
       'public.proteger_obsolescencia_investigacao()', 19),
      ('public.investigacoes_revisao', 'investigacoes_revisao_atestacao_protegida',
       'public.proteger_atestacao_decisao_investigacao()', 19),
      ('public.investigacao_entregas', 'investigacao_entregas_atualizado_em',
       'public.atualizar_timestamp_investigacoes_revisao()', 19),
      ('public.investigacao_evidencias', 'investigacao_evidencias_fencing',
       'public.validar_fencing_resultado_investigacao()', 7),
      ('public.investigacao_alternativas', 'investigacao_alternativas_fencing',
       'public.validar_fencing_resultado_investigacao()', 7),
      ('public.investigacao_pendencias', 'investigacao_pendencias_fencing',
       'public.validar_fencing_resultado_investigacao()', 7),
      ('public.investigacao_alternativa_evidencias', 'investigacao_alternativa_evidencias_fencing',
       'public.validar_fencing_ligacao_investigacao()', 7),
      ('public.investigacao_sucessoes_pendentes',
       'investigacao_sucessoes_pendentes_imutavel',
       'public.proteger_sucessao_promocao_terminal()', 27)
    )
    SELECT 1 FROM obrigatorios esperado
     WHERE NOT EXISTS (
       SELECT 1 FROM pg_trigger gatilho
        WHERE gatilho.tgrelid = esperado.tabela::regclass
          AND gatilho.tgname = esperado.gatilho
          AND gatilho.tgfoid = esperado.funcao::regprocedure
          AND gatilho.tgtype = esperado.tipo
          AND NOT gatilho.tgisinternal AND gatilho.tgenabled = 'O'
          AND gatilho.tgqual IS NULL
          AND gatilho.tgnargs = 0
          AND octet_length(gatilho.tgargs) = 0
          AND gatilho.tgconstraint = 0
          AND NOT gatilho.tgdeferrable
          AND NOT gatilho.tginitdeferred
          AND gatilho.tgattr::text = CASE esperado.gatilho
            WHEN 'investigacao_tarefas_consulta_imutavel' THEN (
              SELECT string_agg(coluna.attnum::text, ' ' ORDER BY nome.ordem)
                FROM unnest(ARRAY[
                  'plano_item_ref', 'consulta_ref',
                  'consulta_schema_version', 'consulta_spec',
                  'consulta_canonico', 'consulta_hash',
                  'adaptador', 'adaptador_version'
                ]::text[]) WITH ORDINALITY AS nome(attname, ordem)
                JOIN pg_attribute coluna
                  ON coluna.attrelid = 'public.investigacao_tarefas'::regclass
                 AND coluna.attname = nome.attname
                 AND coluna.attnum > 0 AND NOT coluna.attisdropped
            )
            WHEN 'investigacoes_revisao_origem_imutavel' THEN (
              SELECT string_agg(coluna.attnum::text, ' ' ORDER BY nome.ordem)
                FROM unnest(ARRAY[
                  'source_draft_id', 'source_draft_atualizado_em',
                  'raiz_investigacao_id', 'sucessora_de_id', 'geracao',
                  'sucessao_pedido_hash', 'sucessao_outbox_id',
                  'negocio_candidato_id', 'negocio_candidato_ids',
                  'source_candidato_atualizado_em',
                  'source_candidatos_atualizados_em', 'fingerprint_base',
                  'policy_version', 'policy_schema_hash', 'plano_hash',
                  'plano_canonico', 'plano_tarefas', 'campos_obrigatorios',
                  'fluxo_tipo', 'promocao_origem_id',
                  'draft_operacional_origem_id',
                  'destino_operacional_origem',
                  'registro_operacional_origem_id',
                  'registro_operacional_origem_snapshot_ref',
                  'vinculo_operacional_estado'
                ]::text[]) WITH ORDINALITY AS nome(attname, ordem)
                JOIN pg_attribute coluna
                  ON coluna.attrelid = 'public.investigacoes_revisao'::regclass
                 AND coluna.attname = nome.attname
                 AND coluna.attnum > 0 AND NOT coluna.attisdropped
            )
            WHEN 'investigacoes_revisao_obsolescencia_protegida' THEN (
              SELECT string_agg(coluna.attnum::text, ' ' ORDER BY nome.ordem)
                FROM unnest(ARRAY[
                  'obsolescencia_motivo', 'promocao_ativa_id'
                ]::text[]) WITH ORDINALITY AS nome(attname, ordem)
                JOIN pg_attribute coluna
                  ON coluna.attrelid = 'public.investigacoes_revisao'::regclass
                 AND coluna.attname = nome.attname
                 AND coluna.attnum > 0 AND NOT coluna.attisdropped
            )
            WHEN 'investigacoes_revisao_atestacao_protegida' THEN (
              SELECT string_agg(coluna.attnum::text, ' ' ORDER BY nome.ordem)
                FROM unnest(ARRAY[
                  'decisao_draft_atualizado_em',
                  'decisao_preparacao_hash'
                ]::text[]) WITH ORDINALITY AS nome(attname, ordem)
                JOIN pg_attribute coluna
                  ON coluna.attrelid = 'public.investigacoes_revisao'::regclass
                 AND coluna.attname = nome.attname
                 AND coluna.attnum > 0 AND NOT coluna.attisdropped
            )
            ELSE ''
          END
     )
  ) THEN
    RAISE EXCEPTION 'Gate de ativação: trigger obrigatório ausente no catálogo protegido';
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
    RAISE EXCEPTION 'Gate de ativação: guardiões de tarefa/configuração/credencial divergiram do catálogo exato';
  END IF;

  -- CREATE OR REPLACE preserva owner e ACL preexistentes. A ativação atesta
  -- cada superfície SECURITY DEFINER e cada view antes de fechar a rota antiga.
  FOREACH v_procedure IN ARRAY v_expostas_service LOOP
    SELECT proowner INTO v_objeto_owner
      FROM pg_proc WHERE oid = v_procedure::oid;
    IF v_objeto_owner IS DISTINCT FROM v_owner
       OR NOT (SELECT prosecdef FROM pg_proc WHERE oid = v_procedure::oid)
       OR NOT EXISTS (
         SELECT 1 FROM pg_proc funcao
         JOIN pg_language linguagem ON linguagem.oid = funcao.prolang
          WHERE funcao.oid = v_procedure::oid
            AND linguagem.lanname = 'plpgsql'
            AND NOT funcao.proisstrict
            AND funcao.proconfig IS NOT DISTINCT FROM
                  ARRAY['search_path=pg_catalog, public']::text[]
            AND (
              (v_procedure = ANY(ARRAY[
                'public.listar_sucessoes_promocao_terminal_pendentes(integer)'::regprocedure,
                'public.saude_investigacoes_proativas()'::regprocedure
              ]) AND funcao.provolatile = 's')
              OR
              (v_procedure <> ALL(ARRAY[
                'public.listar_sucessoes_promocao_terminal_pendentes(integer)'::regprocedure,
                'public.saude_investigacoes_proativas()'::regprocedure
              ]) AND funcao.provolatile = 'v')
            )
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
      RAISE EXCEPTION 'Gate de ativação: owner/ACL divergente na RPC %', v_procedure;
    END IF;
  END LOOP;

  FOREACH v_procedure IN ARRAY v_internas_definer LOOP
    SELECT proowner INTO v_objeto_owner
      FROM pg_proc WHERE oid = v_procedure::oid;
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
      RAISE EXCEPTION 'Gate de ativação: owner/ACL divergente na função interna %', v_procedure;
    END IF;
  END LOOP;

  FOREACH v_view IN ARRAY v_views_authenticated LOOP
    SELECT relowner INTO v_objeto_owner FROM pg_class WHERE oid = v_view::oid;
    IF v_objeto_owner IS DISTINCT FROM v_owner
       OR NOT has_table_privilege('authenticated', v_view::oid, 'SELECT')
       OR has_table_privilege('anon', v_view::oid, 'SELECT')
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
      RAISE EXCEPTION 'Gate de ativação: owner/ACL divergente na view %', v_view;
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
      RAISE EXCEPTION 'Gate de ativação: grant por coluna na view %', v_view;
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
    RAISE EXCEPTION 'Gate de ativação: owner/ACL divergente na view de materialização';
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
    RAISE EXCEPTION 'Gate de ativação: grant por coluna na view privada';
  END IF;

  SELECT count(*) INTO v_total_policies
    FROM pg_policy
   WHERE polrelid = 'public.pending_actions'::regclass;
  SELECT v_total_policies = 1 AND EXISTS (
    SELECT 1
      FROM pg_policy
     WHERE polrelid = 'public.pending_actions'::regclass
       AND polname = 'pending_actions_authenticated_revisoes'
       AND polcmd = '*' AND polpermissive
       AND polroles = ARRAY[v_authenticated]::oid[]
       AND regexp_replace(
             lower(pg_get_expr(polqual, polrelid)), '[()[:space:]]', '', 'g'
           ) = 'true'
       AND regexp_replace(
             lower(pg_get_expr(polwithcheck, polrelid)), '[()[:space:]]', '', 'g'
           ) = 'true'
  ) INTO v_legado_exato;
  SELECT v_total_policies = 4
    AND count(*) FILTER (
      WHERE polname = 'pending_actions_authenticated_revisoes_select'
        AND polcmd = 'r'
        AND regexp_replace(
              lower(pg_get_expr(polqual, polrelid)), '[()[:space:]]', '', 'g'
            ) = 'true'
        AND polwithcheck IS NULL
    ) = 1
    AND count(*) FILTER (
      WHERE polname = 'pending_actions_authenticated_revisoes_insert'
        AND polcmd = 'a' AND polqual IS NULL
        AND regexp_replace(
              lower(pg_get_expr(polwithcheck, polrelid)),
              '[()[:space:]]', '', 'g'
            ) = v_policy_restrita
    ) = 1
    AND count(*) FILTER (
      WHERE polname = 'pending_actions_authenticated_revisoes_update'
        AND polcmd = 'w'
        AND regexp_replace(
              lower(pg_get_expr(polqual, polrelid)), '[()[:space:]]', '', 'g'
            ) = v_policy_restrita
        AND regexp_replace(
              lower(pg_get_expr(polwithcheck, polrelid)),
              '[()[:space:]]', '', 'g'
            ) = v_policy_restrita
    ) = 1
    AND count(*) FILTER (
      WHERE polname = 'pending_actions_authenticated_revisoes_delete'
        AND polcmd = 'd'
        AND regexp_replace(
              lower(pg_get_expr(polqual, polrelid)), '[()[:space:]]', '', 'g'
            ) = v_policy_restrita
        AND polwithcheck IS NULL
    ) = 1
    AND bool_and(polpermissive)
    AND bool_and(polroles = ARRAY[v_authenticated]::oid[])
    INTO v_ativado_exato
    FROM pg_policy
   WHERE polrelid = 'public.pending_actions'::regclass;
  IF NOT coalesce(v_legado_exato, false)
     AND NOT coalesce(v_ativado_exato, false) THEN
    RAISE EXCEPTION 'Gate de ativação: inventário de policies de pending_actions diverge do contrato conhecido';
  END IF;
  IF (coalesce(v_legado_exato,false)
        AND NOT coalesce(v_guardioes_operacionais_ausentes,false))
     OR (coalesce(v_ativado_exato,false)
        AND NOT coalesce(v_guardioes_operacionais_exatos,false)) THEN
    RAISE EXCEPTION 'Gate de ativação: policies e guardiões operacionais pertencem a estados diferentes';
  END IF;
END;
$$;

-- Os guardiões das tabelas legadas só são instalados junto com o mediador. A
-- fundação 0001 pode, assim, permanecer em sombra sem interromper o fluxo já
-- publicado. O AFTER em pending_actions valida a linha final, inclusive após
-- triggers BEFORE legados que preencham ou reescrevam contexto.
DROP TRIGGER IF EXISTS pending_actions_bloqueia_investigacao
  ON public.pending_actions;
CREATE TRIGGER pending_actions_bloqueia_investigacao
AFTER INSERT OR UPDATE
ON public.pending_actions
FOR EACH ROW EXECUTE FUNCTION public.bloquear_pending_action_com_investigacao();

DROP TRIGGER IF EXISTS pending_actions_reativa_complementar
  ON public.pending_actions;
CREATE TRIGGER pending_actions_reativa_complementar
AFTER UPDATE OF status
ON public.pending_actions
FOR EACH ROW EXECUTE FUNCTION public.reativar_complementar_promocao_sem_gravacao();

DROP TRIGGER IF EXISTS operation_drafts_bloqueia_investigacao
  ON public.operation_drafts;
CREATE TRIGGER operation_drafts_bloqueia_investigacao
AFTER UPDATE ON public.operation_drafts
FOR EACH ROW EXECUTE FUNCTION public.bloquear_draft_com_investigacao();

DROP TRIGGER IF EXISTS compras_vinculo_promocao_protegido ON public.compras;
CREATE TRIGGER compras_vinculo_promocao_protegido
BEFORE INSERT OR UPDATE OR DELETE ON public.compras
FOR EACH ROW EXECUTE FUNCTION public.proteger_vinculo_promocao_operacional();

DROP TRIGGER IF EXISTS vendas_vinculo_promocao_protegido ON public.vendas;
CREATE TRIGGER vendas_vinculo_promocao_protegido
BEFORE INSERT OR UPDATE OR DELETE ON public.vendas
FOR EACH ROW EXECUTE FUNCTION public.proteger_vinculo_promocao_operacional();

DROP TRIGGER IF EXISTS pesagens_vinculo_promocao_protegido
  ON public.pesagens_caderno;
CREATE TRIGGER pesagens_vinculo_promocao_protegido
BEFORE INSERT OR UPDATE OR DELETE ON public.pesagens_caderno
FOR EACH ROW EXECUTE FUNCTION public.proteger_vinculo_promocao_operacional();

DROP TRIGGER IF EXISTS abates_vinculo_promocao_protegido ON public.abates;
CREATE TRIGGER abates_vinculo_promocao_protegido
BEFORE INSERT OR UPDATE OR DELETE ON public.abates
FOR EACH ROW EXECUTE FUNCTION public.proteger_vinculo_promocao_operacional();

DO $$
BEGIN
  IF (SELECT count(*) FROM pg_trigger
       WHERE tgrelid = 'public.pending_actions'::regclass
         AND tgname = 'pending_actions_bloqueia_investigacao'
         AND NOT tgisinternal AND tgenabled = 'O'
         AND tgfoid = 'public.bloquear_pending_action_com_investigacao()'::regprocedure
         AND tgtype = 21 AND tgqual IS NULL AND tgattr::text = '') <> 1
     OR (SELECT count(*) FROM pg_trigger
       WHERE tgrelid = 'public.pending_actions'::regclass
         AND tgname = 'pending_actions_reativa_complementar'
         AND NOT tgisinternal AND tgenabled = 'O'
         AND tgfoid = 'public.reativar_complementar_promocao_sem_gravacao()'::regprocedure
         AND tgtype = 17 AND tgqual IS NULL
         AND tgattr::text = (
           SELECT attnum::text FROM pg_attribute
            WHERE attrelid = 'public.pending_actions'::regclass
              AND attname = 'status' AND NOT attisdropped
         )) <> 1
     OR (SELECT count(*) FROM pg_trigger
       WHERE tgrelid = 'public.operation_drafts'::regclass
         AND tgname = 'operation_drafts_bloqueia_investigacao'
         AND NOT tgisinternal AND tgenabled = 'O'
         AND tgfoid = 'public.bloquear_draft_com_investigacao()'::regprocedure
         AND tgtype = 17 AND tgqual IS NULL AND tgattr::text = '') <> 1 THEN
    RAISE EXCEPTION 'Gate de ativação: catálogo dos triggers diverge do contrato exato';
  END IF;
END;
$$;

-- Repete as invariantes que não pertencem ao rollout reversível depois de
-- instalar os guards do mediador: esta etapa não pode enfraquecer os guards
-- corretivos nem os vínculos duráveis operacionais.
DO $$
BEGIN
  IF EXISTS (
    WITH esperados(tabela, gatilho, funcao) AS (VALUES
      ('public.pending_actions'::regclass,
       'pending_actions_protecao_permanente',
       'public.proteger_pending_action_permanente()'::regprocedure),
      ('public.operation_drafts'::regclass,
       'operation_drafts_protecao_corretiva_permanente',
       'public.proteger_draft_corretivo_permanente()'::regprocedure),
      ('public.compras'::regclass,
       'compras_vinculo_promocao_protegido',
       'public.proteger_vinculo_promocao_operacional()'::regprocedure),
      ('public.vendas'::regclass,
       'vendas_vinculo_promocao_protegido',
       'public.proteger_vinculo_promocao_operacional()'::regprocedure),
      ('public.pesagens_caderno'::regclass,
       'pesagens_vinculo_promocao_protegido',
       'public.proteger_vinculo_promocao_operacional()'::regprocedure),
      ('public.abates'::regclass,
       'abates_vinculo_promocao_protegido',
       'public.proteger_vinculo_promocao_operacional()'::regprocedure)
    )
    SELECT 1 FROM esperados esperado
     WHERE (SELECT count(*) FROM pg_trigger gatilho
             WHERE gatilho.tgrelid = esperado.tabela
               AND gatilho.tgname = esperado.gatilho
               AND gatilho.tgfoid = esperado.funcao
               AND NOT gatilho.tgisinternal
               AND gatilho.tgenabled = 'O'
               AND gatilho.tgtype = 31
               AND gatilho.tgqual IS NULL
               AND gatilho.tgnargs = 0
               AND octet_length(gatilho.tgargs) = 0
               AND gatilho.tgconstraint = 0
               AND NOT gatilho.tgdeferrable
               AND NOT gatilho.tginitdeferred
               AND gatilho.tgattr::text = '') <> 1
  ) THEN
    RAISE EXCEPTION 'Gate de ativação: guardião permanente ou operacional alterado durante o cutover';
  END IF;
END;
$$;

ALTER TABLE public.pending_actions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS pending_actions_authenticated_revisoes
  ON public.pending_actions;
DROP POLICY IF EXISTS pending_actions_authenticated_revisoes_select
  ON public.pending_actions;
DROP POLICY IF EXISTS pending_actions_authenticated_revisoes_insert
  ON public.pending_actions;
DROP POLICY IF EXISTS pending_actions_authenticated_revisoes_update
  ON public.pending_actions;
DROP POLICY IF EXISTS pending_actions_authenticated_revisoes_delete
  ON public.pending_actions;

CREATE POLICY pending_actions_authenticated_revisoes_select
ON public.pending_actions FOR SELECT TO authenticated
USING (true);

CREATE POLICY pending_actions_authenticated_revisoes_insert
ON public.pending_actions FOR INSERT TO authenticated
WITH CHECK (
  acao_tipo IS DISTINCT FROM 'promover_revisao_operacional'
  AND acao_tipo IS DISTINCT FROM 'revisar_correcao_pos_gravacao'
);

CREATE POLICY pending_actions_authenticated_revisoes_update
ON public.pending_actions FOR UPDATE TO authenticated
USING (
  acao_tipo IS DISTINCT FROM 'promover_revisao_operacional'
  AND acao_tipo IS DISTINCT FROM 'revisar_correcao_pos_gravacao'
)
WITH CHECK (
  acao_tipo IS DISTINCT FROM 'promover_revisao_operacional'
  AND acao_tipo IS DISTINCT FROM 'revisar_correcao_pos_gravacao'
);

CREATE POLICY pending_actions_authenticated_revisoes_delete
ON public.pending_actions FOR DELETE TO authenticated
USING (
  acao_tipo IS DISTINCT FROM 'promover_revisao_operacional'
  AND acao_tipo IS DISTINCT FROM 'revisar_correcao_pos_gravacao'
);

COMMENT ON POLICY pending_actions_authenticated_revisoes_insert
  ON public.pending_actions IS
  'Promoções são criadas somente pelo mediador service_role; demais pendências continuam disponíveis à interface autenticada.';

-- O navegador precisa somente da projeção humana de Revisões. Os tokens de
-- lease/fencing, hashes de pedido e vínculos corretivos são controle interno
-- do executor e não podem ser revelados ou alterados por uma sessão
-- authenticated. Revogar o privilégio de tabela é intencional: os GRANTs por
-- coluna abaixo mantêm os formulários legados de salvar/voltar/rejeitar sem
-- permitir que uma coluna nova entre na API por acidente.
REVOKE SELECT, INSERT, UPDATE ON TABLE public.operation_drafts
  FROM PUBLIC, anon, authenticated;
REVOKE SELECT, INSERT, UPDATE ON TABLE public.pending_actions
  FROM PUBLIC, anon, authenticated;

GRANT SELECT (
  id, criado_em, atualizado_em, status, tipo_operacao, codigo_sugerido,
  entidade_final_tipo, dados_extraidos, campos_pendentes, inferencias,
  pending_action_id, agente, origem_canal, origem_conversa_id,
  origem_mensagem_id, contexto_canonico, contexto_nome, escopo, revisao_tipo
) ON public.operation_drafts TO authenticated;
GRANT UPDATE (
  atualizado_em, status, codigo_sugerido, entidade_final_tipo,
  dados_extraidos, campos_pendentes, inferencias, agente, origem_canal,
  origem_conversa_id, origem_mensagem_id, contexto_canonico, contexto_nome,
  escopo
) ON public.operation_drafts TO authenticated;
GRANT INSERT (
  agente, status, tipo_operacao, entidade_final_tipo, codigo_sugerido,
  dados_extraidos, campos_pendentes, inferencias, pending_action_id,
  origem_canal, origem_conversa_id, origem_mensagem_id, contexto_canonico,
  contexto_nome, escopo
) ON public.operation_drafts TO authenticated;

GRANT SELECT (
  id, criado_em, atualizado_em, status, acao_tipo, entidade_tipo,
  entidade_codigo, resumo, payload, resultado, erro, agente,
  usuario_solicitante, canal, origem_canal, origem_conversa_id,
  origem_mensagem_id, contexto_canonico, contexto_nome, escopo,
  confirmado_em, confirmado_por, executavel
) ON public.pending_actions TO authenticated;
GRANT UPDATE (
  atualizado_em, status, entidade_tipo, entidade_codigo, resumo, payload,
  erro, agente, usuario_solicitante, canal, origem_canal,
  origem_conversa_id, origem_mensagem_id, contexto_canonico, contexto_nome,
  escopo
) ON public.pending_actions TO authenticated;
GRANT INSERT (
  agente, usuario_solicitante, canal, acao_tipo, entidade_tipo,
  entidade_codigo, resumo, payload, resultado, status, origem_canal,
  origem_conversa_id, origem_mensagem_id, contexto_canonico, contexto_nome,
  escopo
) ON public.pending_actions TO authenticated;

-- Pós-condição de ACL: além de não haver grants de tabela de leitura/escrita
-- para authenticated, cada coluna sensível precisa continuar inacessível. O
-- mediador usa service_role e permanece o único caminho para estes campos.
DO $$
DECLARE
  v_coluna text;
  v_sensiveis_draft text[] := ARRAY[
    'investigacao_origem_id', 'promocao_origem_id', 'entidade_final_id'
  ];
  v_sensiveis_acao text[] := ARRAY[
    'entidade_id', 'promocao_controle_version', 'promocao_lease_executor',
    'promocao_lease_token', 'promocao_lease_expira_em',
    'promocao_fencing_token', 'promocao_confirmacao_origem_conversa_id',
    'promocao_confirmacao_origem_mensagem_id',
    'promocao_preparacao_chave', 'promocao_preparacao_hash',
    'promocao_resultado_lease_token', 'promocao_resultado_fencing_token',
    'promocao_resultado_pedido_hash'
  ];
BEGIN
  IF has_table_privilege('authenticated', 'public.operation_drafts', 'SELECT')
     OR has_table_privilege('authenticated', 'public.operation_drafts', 'INSERT')
     OR has_table_privilege('authenticated', 'public.operation_drafts', 'UPDATE')
     OR has_table_privilege('authenticated', 'public.pending_actions', 'SELECT')
     OR has_table_privilege('authenticated', 'public.pending_actions', 'INSERT')
     OR has_table_privilege('authenticated', 'public.pending_actions', 'UPDATE') THEN
    RAISE EXCEPTION 'Gate de ativação: Revisões não pode manter grant amplo de tabela para authenticated';
  END IF;
  FOREACH v_coluna IN ARRAY v_sensiveis_draft LOOP
    IF has_column_privilege('authenticated', 'public.operation_drafts', v_coluna, 'SELECT')
       OR has_column_privilege('authenticated', 'public.operation_drafts', v_coluna, 'INSERT')
       OR has_column_privilege('authenticated', 'public.operation_drafts', v_coluna, 'UPDATE') THEN
      RAISE EXCEPTION 'Gate de ativação: coluna interna de rascunho exposta: %', v_coluna;
    END IF;
  END LOOP;
  FOREACH v_coluna IN ARRAY v_sensiveis_acao LOOP
    IF has_column_privilege('authenticated', 'public.pending_actions', v_coluna, 'SELECT')
       OR has_column_privilege('authenticated', 'public.pending_actions', v_coluna, 'INSERT')
       OR has_column_privilege('authenticated', 'public.pending_actions', v_coluna, 'UPDATE') THEN
      RAISE EXCEPTION 'Gate de ativação: coluna interna de ação exposta: %', v_coluna;
    END IF;
  END LOOP;
  IF NOT has_column_privilege('authenticated', 'public.operation_drafts', 'id', 'SELECT')
     OR NOT has_column_privilege('authenticated', 'public.operation_drafts', 'dados_extraidos', 'SELECT')
     OR NOT has_column_privilege('authenticated', 'public.operation_drafts', 'dados_extraidos', 'UPDATE')
     OR NOT has_column_privilege('authenticated', 'public.pending_actions', 'id', 'SELECT')
     OR NOT has_column_privilege('authenticated', 'public.pending_actions', 'payload', 'SELECT')
     OR NOT has_column_privilege('authenticated', 'public.pending_actions', 'payload', 'UPDATE')
     OR NOT has_table_privilege('service_role', 'public.pending_actions', 'SELECT')
     OR NOT has_table_privilege('service_role', 'public.pending_actions', 'INSERT')
     OR NOT has_table_privilege('service_role', 'public.pending_actions', 'UPDATE') THEN
    RAISE EXCEPTION 'Gate de ativação: projeção mínima de Revisões ou acesso do mediador está incompleta';
  END IF;
END;
$$;

-- Pós-condição: nenhuma policy adicional pode combinar permissivamente com o
-- conjunto recém-instalado e todas continuam restritas ao papel autenticado.
DO $$
DECLARE
  v_authenticated oid;
BEGIN
  SELECT oid INTO v_authenticated FROM pg_roles WHERE rolname = 'authenticated';
  IF (SELECT count(*) FROM pg_policy
       WHERE polrelid = 'public.pending_actions'::regclass) <> 4
     OR EXISTS (
       SELECT 1 FROM pg_policy
        WHERE polrelid = 'public.pending_actions'::regclass
          AND (
            NOT polpermissive
            OR polroles IS DISTINCT FROM ARRAY[v_authenticated]::oid[]
            OR polname NOT IN (
              'pending_actions_authenticated_revisoes_select',
              'pending_actions_authenticated_revisoes_insert',
              'pending_actions_authenticated_revisoes_update',
              'pending_actions_authenticated_revisoes_delete'
            )
          )
     ) THEN
    RAISE EXCEPTION 'Gate de ativação: pós-condição de policies não foi satisfeita';
  END IF;
END;
$$;

COMMIT;
