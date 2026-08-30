-- Reversão operacional fail-closed e idempotente do gate 202608290002.
--
-- Não apaga dados nem a fundação 0001. Desliga o produtor de outbox junto com
-- o consumidor, preservando a tabela e todas as linhas concluídas para
-- auditoria. Aceita somente o estado ativado exato ou o estado já revertido
-- exato; qualquer catálogo parcial falha.

BEGIN;

SET LOCAL lock_timeout = '10s';
SET LOCAL statement_timeout = '2min';

-- Mesma ordem relativa usada no cutover 0002; operações de manutenção não
-- podem introduzir um ciclo de locks com o produtor/consumidor.
LOCK TABLE public.investigacao_tarefas IN ACCESS EXCLUSIVE MODE;
LOCK TABLE public.operation_drafts IN ACCESS EXCLUSIVE MODE;
LOCK TABLE public.pending_actions IN ACCESS EXCLUSIVE MODE;
LOCK TABLE public.compras, public.vendas, public.pesagens_caderno,
  public.abates IN SHARE MODE;
LOCK TABLE public.investigacao_autorizacoes_promocao,
  public.investigacao_autorizacoes_corretiva,
  public.investigacao_sucessoes_pendentes IN ACCESS EXCLUSIVE MODE;

-- Fecha primeiro todas as superfícies que poderiam criar ou consumir trabalho.
-- As funções e o histórico permanecem no catálogo para auditoria; apenas o
-- papel da aplicação perde EXECUTE antes de os guardiões serem retirados.
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

DO $$
DECLARE
  v_authenticated oid;
  v_ativado_exato boolean;
  v_revertido_exato boolean;
  v_observador_exato boolean;
  v_guardioes_permanentes_exatos boolean;
  v_guardioes_operacionais_ativos boolean;
  v_guardioes_operacionais_ausentes boolean;
  v_draft_acl_exato boolean;
  v_acao_acl_exato boolean;
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
  SELECT oid INTO v_authenticated
    FROM pg_roles WHERE rolname = 'authenticated';
  IF v_authenticated IS NULL THEN
    RAISE EXCEPTION 'Rollback: papel authenticated ausente';
  END IF;

  v_observador_exato := (
    SELECT count(*) = 1
      FROM pg_trigger
     WHERE tgrelid = 'public.pending_actions'::regclass
       AND tgname = 'pending_actions_reativa_complementar'
       AND NOT tgisinternal AND tgenabled = 'O'
       AND tgfoid =
             'public.reativar_complementar_promocao_sem_gravacao()'::regprocedure
       AND tgtype = 17 AND tgqual IS NULL
       AND tgattr::text = (
         SELECT attnum::text FROM pg_attribute
          WHERE attrelid = 'public.pending_actions'::regclass
            AND attname = 'status' AND NOT attisdropped
       )
  );

  -- Apenas estes dois guards pertencem à fundação 0001 e precisam sobreviver
  -- tanto à ativação quanto à reversão.
  v_guardioes_permanentes_exatos := NOT EXISTS (
    WITH esperados(tabela, gatilho, funcao) AS (VALUES
      ('public.pending_actions'::regclass,
       'pending_actions_protecao_permanente',
       'public.proteger_pending_action_permanente()'::regprocedure),
      ('public.operation_drafts'::regclass,
       'operation_drafts_protecao_corretiva_permanente',
       'public.proteger_draft_corretivo_permanente()'::regprocedure)
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
  );
  IF v_guardioes_permanentes_exatos IS NOT TRUE THEN
    RAISE EXCEPTION 'Rollback: guardião permanente ausente ou divergente';
  END IF;

  -- Os quatro guardiões operacionais são parte do cutover 0002. A entrada do
  -- rollback aceita somente o conjunto completo ou a ausência completa; um
  -- estado parcial falha antes de qualquer alteração de catálogo.
  v_guardioes_operacionais_ativos := NOT EXISTS (
    WITH esperados(tabela, gatilho, funcao) AS (VALUES
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
  );
  v_guardioes_operacionais_ausentes := NOT EXISTS (
    SELECT 1 FROM pg_trigger gatilho
     WHERE NOT gatilho.tgisinternal
       AND (
         (gatilho.tgrelid = 'public.compras'::regclass
           AND gatilho.tgname = 'compras_vinculo_promocao_protegido')
         OR (gatilho.tgrelid = 'public.vendas'::regclass
           AND gatilho.tgname = 'vendas_vinculo_promocao_protegido')
         OR (gatilho.tgrelid = 'public.pesagens_caderno'::regclass
           AND gatilho.tgname = 'pesagens_vinculo_promocao_protegido')
         OR (gatilho.tgrelid = 'public.abates'::regclass
           AND gatilho.tgname = 'abates_vinculo_promocao_protegido')
       )
  );
  IF v_guardioes_operacionais_ativos = v_guardioes_operacionais_ausentes THEN
    RAISE EXCEPTION 'Rollback: guardiões operacionais em estado parcial ou ambíguo';
  END IF;

  -- O rollback preserva a projeção de colunas instalada pelo cutover. Isso
  -- mantém o formulário legado funcional sem reabrir os vínculos internos;
  -- qualquer matriz parcial, grant amplo ou ACL para outro papel é estado
  -- desconhecido e deve abortar antes de trocar as policies.
  v_draft_acl_exato := NOT has_table_privilege(
      'authenticated', 'public.operation_drafts', 'SELECT, INSERT, UPDATE')
    AND NOT EXISTS (
      SELECT 1
        FROM pg_attribute coluna
        CROSS JOIN LATERAL aclexplode(coluna.attacl) privilegio
       WHERE coluna.attrelid = 'public.operation_drafts'::regclass
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
    AND NOT EXISTS (
      SELECT 1
        FROM (
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
         'authenticated', 'public.operation_drafts',
         esperado.coluna, esperado.privilegio
       )
    );
  v_acao_acl_exato := NOT has_table_privilege(
      'authenticated', 'public.pending_actions', 'SELECT, INSERT, UPDATE')
    AND NOT EXISTS (
      SELECT 1
        FROM pg_attribute coluna
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
    AND NOT EXISTS (
      SELECT 1
        FROM (
          SELECT coluna, 'SELECT'::text AS privilegio
            FROM unnest(v_acao_select) coluna
          UNION ALL
          SELECT coluna, 'INSERT'::text
            FROM unnest(v_acao_insert) coluna
          UNION ALL
          SELECT coluna, 'UPDATE'::text
            FROM unnest(v_acao_update) coluna
        ) esperado
       WHERE NOT has_column_privilege(
         'authenticated', 'public.pending_actions',
         esperado.coluna, esperado.privilegio
       )
    );

  v_ativado_exato := v_observador_exato AND v_guardioes_permanentes_exatos
    AND v_guardioes_operacionais_ativos
    AND (SELECT count(*) FROM pg_policy
          WHERE polrelid = 'public.pending_actions'::regclass) = 4
    AND (SELECT count(*) FROM pg_policy
          WHERE polrelid = 'public.pending_actions'::regclass
            AND polpermissive
            AND polroles = ARRAY[v_authenticated]::oid[]
            AND (
              (polname = 'pending_actions_authenticated_revisoes_select'
                AND polcmd = 'r' AND polwithcheck IS NULL
                AND regexp_replace(lower(pg_get_expr(polqual, polrelid)),
                  '[()[:space:]]', '', 'g') = 'true')
              OR (polname = 'pending_actions_authenticated_revisoes_insert'
                AND polcmd = 'a' AND polqual IS NULL
                AND regexp_replace(lower(pg_get_expr(polwithcheck, polrelid)),
                  '[()[:space:]]', '', 'g') = v_policy_restrita)
              OR (polname = 'pending_actions_authenticated_revisoes_update'
                AND polcmd = 'w'
                AND regexp_replace(lower(pg_get_expr(polqual, polrelid)),
                  '[()[:space:]]', '', 'g') = v_policy_restrita
                AND regexp_replace(lower(pg_get_expr(polwithcheck, polrelid)),
                  '[()[:space:]]', '', 'g') = v_policy_restrita)
              OR (polname = 'pending_actions_authenticated_revisoes_delete'
                AND polcmd = 'd' AND polwithcheck IS NULL
                AND regexp_replace(lower(pg_get_expr(polqual, polrelid)),
                  '[()[:space:]]', '', 'g') = v_policy_restrita)
            )) = 4
    AND (SELECT count(*) FROM pg_trigger
          WHERE tgrelid = 'public.pending_actions'::regclass
            AND tgname = 'pending_actions_bloqueia_investigacao'
            AND NOT tgisinternal AND tgenabled = 'O'
            AND tgfoid =
                  'public.bloquear_pending_action_com_investigacao()'::regprocedure
            AND tgtype = 21 AND tgqual IS NULL AND tgattr::text = '') = 1
    AND (SELECT count(*) FROM pg_trigger
          WHERE tgrelid = 'public.operation_drafts'::regclass
            AND tgname = 'operation_drafts_bloqueia_investigacao'
            AND NOT tgisinternal AND tgenabled = 'O'
            AND tgfoid =
                  'public.bloquear_draft_com_investigacao()'::regprocedure
            AND tgtype = 17 AND tgqual IS NULL AND tgattr::text = '') = 1;

  v_revertido_exato := NOT v_observador_exato AND v_guardioes_permanentes_exatos
    AND v_guardioes_operacionais_ausentes
    AND v_draft_acl_exato AND v_acao_acl_exato
    AND (SELECT count(*) FROM pg_policy
          WHERE polrelid = 'public.pending_actions'::regclass) = 1
    AND EXISTS (
      SELECT 1 FROM pg_policy
       WHERE polrelid = 'public.pending_actions'::regclass
         AND polname = 'pending_actions_authenticated_revisoes'
         AND polcmd = '*' AND polpermissive
         AND polroles = ARRAY[v_authenticated]::oid[]
         AND regexp_replace(lower(pg_get_expr(polqual, polrelid)),
           '[()[:space:]]', '', 'g') = 'true'
         AND regexp_replace(lower(pg_get_expr(polwithcheck, polrelid)),
           '[()[:space:]]', '', 'g') = 'true'
    )
    AND NOT EXISTS (
      SELECT 1 FROM pg_trigger
       WHERE NOT tgisinternal AND (
         (tgrelid = 'public.pending_actions'::regclass
           AND tgname = 'pending_actions_bloqueia_investigacao')
         OR (tgrelid = 'public.operation_drafts'::regclass
           AND tgname = 'operation_drafts_bloqueia_investigacao')
         OR (tgrelid = 'public.pending_actions'::regclass
           AND tgname = 'pending_actions_reativa_complementar')
       )
    );

  IF v_ativado_exato = v_revertido_exato THEN
    RAISE EXCEPTION 'Rollback: catálogo parcial, ambíguo ou desconhecido';
  END IF;

  IF v_ativado_exato THEN
    IF EXISTS (
      SELECT 1 FROM public.investigacao_tarefas
       WHERE (estado_execucao = ANY(ARRAY[
         'concluida', 'cancelada', 'obsoleta'
       ])) IS NOT TRUE
    ) OR EXISTS (
      SELECT 1 FROM public.pending_actions
       WHERE acao_tipo = 'promover_revisao_operacional'
         AND (status = ANY(ARRAY[
           'executado', 'erro_pos_gravacao', 'erro',
           'cancelado', 'rejeitado', 'expirado'
         ])) IS NOT TRUE
    ) OR EXISTS (
      SELECT 1 FROM public.investigacao_sucessoes_pendentes
       WHERE estado <> 'concluida'
    ) OR EXISTS (
      SELECT 1 FROM public.investigacao_autorizacoes_promocao
    ) OR EXISTS (
      SELECT 1 FROM public.investigacao_autorizacoes_corretiva
    ) OR EXISTS (
      SELECT 1 FROM public.investigacoes_revisao
       WHERE obsolescencia_motivo = 'complementar_promocao_ativa'
          OR promocao_ativa_id IS NOT NULL
    ) THEN
      RAISE EXCEPTION 'Rollback: drene tarefas, promoções, sucessões pendentes e capacidades órfãs antes de reabrir a policy legada';
    END IF;

    -- Histórico concluído não é apenas conservado: o mapa selado e sua
    -- linhagem precisam continuar coerentes. Uma saída sem filha é válida
    -- somente porque o gate anterior também provou que não restou pai elegível;
    -- quando existe filha, ela deve apontar para este outbox e para um pai
    -- consumido da mesma raiz, em geração imediatamente posterior.
    IF EXISTS (
      SELECT 1 FROM public.investigacao_sucessoes_pendentes outbox
      CROSS JOIN LATERAL (
        SELECT coalesce(jsonb_agg(jsonb_build_object(
                 'predecessora_id', filha.sucessora_de_id,
                 'sucessora_id', filha.id,
                 'sucessao_pedido_hash', filha.sucessao_pedido_hash
               ) ORDER BY filha.sucessora_de_id, filha.id), '[]'::jsonb)
                 AS filhas,
               count(*)::integer AS quantidade
          FROM public.investigacoes_revisao filha
         WHERE filha.sucessao_outbox_id = outbox.id
      ) mapa
       WHERE outbox.estado = 'concluida'
         AND (
           outbox.filhas_quantidade IS DISTINCT FROM mapa.quantidade
           OR outbox.filhas_mapa_hash IS DISTINCT FROM encode(
                extensions.digest(
                  convert_to(mapa.filhas::text, 'UTF8'), 'sha256'
                ), 'hex'
              )
           OR EXISTS (
             SELECT 1
               FROM public.investigacoes_revisao filha
               LEFT JOIN public.investigacoes_revisao pai
                 ON pai.id = filha.sucessora_de_id
              WHERE filha.sucessao_outbox_id = outbox.id
                AND (
                  pai.id IS NULL
                  OR filha.raiz_investigacao_id
                       IS DISTINCT FROM pai.raiz_investigacao_id
                  OR filha.geracao IS DISTINCT FROM pai.geracao + 1
                  OR filha.sucessao_pedido_hash IS NULL
                  OR pai.obsolescencia_motivo
                       IS DISTINCT FROM 'complementar_consumida'
                  OR pai.promocao_ativa_id IS NOT NULL
                )
           )
         )
    ) THEN
      RAISE EXCEPTION 'Rollback: histórico concluído do outbox possui linhagem divergente';
    END IF;

    EXECUTE 'DROP TRIGGER pending_actions_bloqueia_investigacao ON public.pending_actions';
    EXECUTE 'DROP TRIGGER pending_actions_reativa_complementar ON public.pending_actions';
    EXECUTE 'DROP TRIGGER operation_drafts_bloqueia_investigacao ON public.operation_drafts';
    EXECUTE 'DROP TRIGGER compras_vinculo_promocao_protegido ON public.compras';
    EXECUTE 'DROP TRIGGER vendas_vinculo_promocao_protegido ON public.vendas';
    EXECUTE 'DROP TRIGGER pesagens_vinculo_promocao_protegido ON public.pesagens_caderno';
    EXECUTE 'DROP TRIGGER abates_vinculo_promocao_protegido ON public.abates';
    EXECUTE 'DROP POLICY pending_actions_authenticated_revisoes_select ON public.pending_actions';
    EXECUTE 'DROP POLICY pending_actions_authenticated_revisoes_insert ON public.pending_actions';
    EXECUTE 'DROP POLICY pending_actions_authenticated_revisoes_update ON public.pending_actions';
    EXECUTE 'DROP POLICY pending_actions_authenticated_revisoes_delete ON public.pending_actions';
    EXECUTE 'CREATE POLICY pending_actions_authenticated_revisoes '
      || 'ON public.pending_actions FOR ALL TO authenticated '
      || 'USING (true) WITH CHECK (true)';
  END IF;
END;
$$;

DO $$
DECLARE
  v_authenticated oid;
  v_funcao regprocedure;
  v_owner oid;
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
  SELECT oid INTO v_authenticated
    FROM pg_roles WHERE rolname = 'authenticated';
  SELECT relowner INTO v_owner
    FROM pg_class WHERE oid = 'public.pending_actions'::regclass;
  IF EXISTS (
    WITH esperados(tabela, gatilho, funcao) AS (VALUES
      ('public.pending_actions'::regclass,
       'pending_actions_protecao_permanente',
       'public.proteger_pending_action_permanente()'::regprocedure),
      ('public.operation_drafts'::regclass,
       'operation_drafts_protecao_corretiva_permanente',
       'public.proteger_draft_corretivo_permanente()'::regprocedure)
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
    RAISE EXCEPTION 'Rollback: pós-condição perdeu guardião permanente';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_trigger gatilho
     WHERE NOT gatilho.tgisinternal
       AND (
         (gatilho.tgrelid = 'public.compras'::regclass
           AND gatilho.tgname = 'compras_vinculo_promocao_protegido')
         OR (gatilho.tgrelid = 'public.vendas'::regclass
           AND gatilho.tgname = 'vendas_vinculo_promocao_protegido')
         OR (gatilho.tgrelid = 'public.pesagens_caderno'::regclass
           AND gatilho.tgname = 'pesagens_vinculo_promocao_protegido')
         OR (gatilho.tgrelid = 'public.abates'::regclass
           AND gatilho.tgname = 'abates_vinculo_promocao_protegido')
       )
  ) THEN
    RAISE EXCEPTION 'Rollback: pós-condição manteve guardião operacional do cutover';
  END IF;
  IF (SELECT count(*) FROM pg_policy
       WHERE polrelid = 'public.pending_actions'::regclass) <> 1
     OR NOT EXISTS (
       SELECT 1 FROM pg_policy
        WHERE polrelid = 'public.pending_actions'::regclass
          AND polname = 'pending_actions_authenticated_revisoes'
          AND polcmd = '*' AND polpermissive
          AND polroles = ARRAY[v_authenticated]::oid[]
          AND regexp_replace(lower(pg_get_expr(polqual, polrelid)),
            '[()[:space:]]', '', 'g') = 'true'
          AND regexp_replace(lower(pg_get_expr(polwithcheck, polrelid)),
            '[()[:space:]]', '', 'g') = 'true'
     )
     OR EXISTS (
       SELECT 1 FROM pg_trigger
        WHERE NOT tgisinternal AND (
          (tgrelid = 'public.pending_actions'::regclass
            AND tgname = 'pending_actions_bloqueia_investigacao')
          OR (tgrelid = 'public.operation_drafts'::regclass
            AND tgname = 'operation_drafts_bloqueia_investigacao')
        )
     )
     OR EXISTS (SELECT 1 FROM pg_trigger
       WHERE tgrelid = 'public.pending_actions'::regclass
         AND tgname = 'pending_actions_reativa_complementar'
         AND NOT tgisinternal) THEN
    RAISE EXCEPTION 'Rollback: pós-condição exata não foi satisfeita';
  END IF;
  IF has_table_privilege(
       'authenticated', 'public.operation_drafts', 'SELECT, INSERT, UPDATE')
     OR has_table_privilege(
       'authenticated', 'public.pending_actions', 'SELECT, INSERT, UPDATE')
     OR EXISTS (
       SELECT 1
         FROM pg_attribute coluna
         CROSS JOIN LATERAL aclexplode(coluna.attacl) privilegio
        WHERE coluna.attrelid IN (
                'public.operation_drafts'::regclass,
                'public.pending_actions'::regclass
              )
          AND coluna.attnum > 0 AND NOT coluna.attisdropped
          AND (
            privilegio.grantee <> v_authenticated
            OR privilegio.is_grantable
            OR NOT (
              (coluna.attrelid = 'public.operation_drafts'::regclass
               AND privilegio.privilege_type = 'SELECT'
               AND coluna.attname = ANY(v_draft_select))
              OR (coluna.attrelid = 'public.operation_drafts'::regclass
                  AND privilegio.privilege_type = 'INSERT'
                  AND coluna.attname = ANY(v_draft_insert))
              OR (coluna.attrelid = 'public.operation_drafts'::regclass
                  AND privilegio.privilege_type = 'UPDATE'
                  AND coluna.attname = ANY(v_draft_update))
              OR (coluna.attrelid = 'public.pending_actions'::regclass
                  AND privilegio.privilege_type = 'SELECT'
                  AND coluna.attname = ANY(v_acao_select))
              OR (coluna.attrelid = 'public.pending_actions'::regclass
                  AND privilegio.privilege_type = 'INSERT'
                  AND coluna.attname = ANY(v_acao_insert))
              OR (coluna.attrelid = 'public.pending_actions'::regclass
                  AND privilegio.privilege_type = 'UPDATE'
                  AND coluna.attname = ANY(v_acao_update))
            )
          )
     )
     OR EXISTS (
       SELECT 1
         FROM (
           SELECT 'public.operation_drafts'::regclass AS tabela,
                  coluna, 'SELECT'::text AS privilegio
             FROM unnest(v_draft_select) coluna
           UNION ALL
           SELECT 'public.operation_drafts'::regclass,
                  coluna, 'INSERT'::text
             FROM unnest(v_draft_insert) coluna
           UNION ALL
           SELECT 'public.operation_drafts'::regclass,
                  coluna, 'UPDATE'::text
             FROM unnest(v_draft_update) coluna
           UNION ALL
           SELECT 'public.pending_actions'::regclass,
                  coluna, 'SELECT'::text
             FROM unnest(v_acao_select) coluna
           UNION ALL
           SELECT 'public.pending_actions'::regclass,
                  coluna, 'INSERT'::text
             FROM unnest(v_acao_insert) coluna
           UNION ALL
           SELECT 'public.pending_actions'::regclass,
                  coluna, 'UPDATE'::text
             FROM unnest(v_acao_update) coluna
         ) esperado
        WHERE NOT has_column_privilege(
          'authenticated', esperado.tabela::oid, esperado.coluna,
          esperado.privilegio
        )
     ) THEN
    RAISE EXCEPTION 'Rollback: pós-condição de ACL por coluna não foi satisfeita';
  END IF;
  FOREACH v_funcao IN ARRAY ARRAY[
    'public.consumir_sucessoes_promocao_terminal(uuid,text,text)'::regprocedure,
    'public.obter_contexto_replanejamento_sucessoes_promocao_terminal(uuid,text)'::regprocedure,
    'public.replanejar_sucessoes_promocao_terminal(uuid,text,text,jsonb,text)'::regprocedure,
    'public.obter_contexto_replanejamento_corretiva_stale(uuid,text,text)'::regprocedure,
    'public.replanejar_investigacao_corretiva_stale(uuid,text,text,text,jsonb,text,text)'::regprocedure,
    'public.listar_sucessoes_promocao_terminal_pendentes(integer)'::regprocedure,
    'public.saude_investigacoes_proativas()'::regprocedure
  ] LOOP
    IF (SELECT proowner FROM pg_proc WHERE oid = v_funcao::oid)
         IS DISTINCT FROM v_owner
       OR NOT (SELECT prosecdef FROM pg_proc WHERE oid = v_funcao::oid)
       OR (SELECT proconfig FROM pg_proc WHERE oid = v_funcao::oid)
            IS DISTINCT FROM ARRAY['search_path=pg_catalog, public']::text[]
       OR has_function_privilege('anon', v_funcao::oid, 'EXECUTE')
       OR has_function_privilege('authenticated', v_funcao::oid, 'EXECUTE')
       OR has_function_privilege('service_role', v_funcao::oid, 'EXECUTE') THEN
      RAISE EXCEPTION 'Rollback: RPC de investigação permaneceu exposta: %', v_funcao;
    END IF;
  END LOOP;
  IF (SELECT relowner FROM pg_class
       WHERE oid = 'public.investigacao_sucessoes_pendentes'::regclass)
         IS DISTINCT FROM v_owner
     OR NOT (SELECT relrowsecurity FROM pg_class
              WHERE oid = 'public.investigacao_sucessoes_pendentes'::regclass)
     OR (SELECT relforcerowsecurity FROM pg_class
          WHERE oid = 'public.investigacao_sucessoes_pendentes'::regclass)
     OR EXISTS (SELECT 1 FROM pg_policy
                 WHERE polrelid =
                       'public.investigacao_sucessoes_pendentes'::regclass)
     OR has_table_privilege(
          'anon', 'public.investigacao_sucessoes_pendentes',
          'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER'
        )
     OR has_table_privilege(
          'authenticated', 'public.investigacao_sucessoes_pendentes',
          'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER'
        )
     OR has_table_privilege(
          'service_role', 'public.investigacao_sucessoes_pendentes',
          'SELECT, INSERT, UPDATE, DELETE, TRUNCATE, REFERENCES, TRIGGER'
        )
     OR EXISTS (
       SELECT 1 FROM pg_attribute coluna
        WHERE coluna.attrelid =
              'public.investigacao_sucessoes_pendentes'::regclass
          AND coluna.attnum > 0 AND NOT coluna.attisdropped
          AND coluna.attacl IS NOT NULL AND cardinality(coluna.attacl) > 0
     ) THEN
    RAISE EXCEPTION 'Rollback: outbox histórico perdeu owner/RLS/ACL privado';
  END IF;
END;
$$;

COMMIT;
