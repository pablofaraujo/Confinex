-- PROPOSTA NÃO APLICADA. Exige autorização própria e mediador autenticado.
-- Não altera dados ou RLS existente ao instalar. Adiciona guardas fail-closed.
-- Não habilitar no Juan
-- antes da homologação do mediador: service_role não autentica uma pessoa Telegram.
BEGIN;

-- Capacidades internas de uso único, criadas/consumidas na mesma transação.
-- Não são propostas persistentes e não ficam acessíveis pela API pública.
CREATE SCHEMA juan_comissao_privado;
REVOKE ALL ON SCHEMA juan_comissao_privado FROM PUBLIC, anon, authenticated, service_role;
CREATE TABLE juan_comissao_privado.autorizacoes (
  txid bigint NOT NULL,
  backend_pid integer NOT NULL,
  recurso text NOT NULL CHECK (recurso IN ('operation_drafts','pending_actions')),
  registro_id uuid NOT NULL,
  retrato_autorizado jsonb NOT NULL,
  PRIMARY KEY (txid, backend_pid, recurso, registro_id)
);
REVOKE ALL ON juan_comissao_privado.autorizacoes FROM PUBLIC, anon, authenticated, service_role;

CREATE FUNCTION public.proteger_comissao_confirmada_juan()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $$
DECLARE
  antes jsonb; depois jsonb; anterior jsonb; proxima jsonb;
BEGIN
  IF TG_OP <> 'INSERT' THEN antes:=to_jsonb(OLD); END IF;
  IF TG_OP <> 'DELETE' THEN depois:=to_jsonb(NEW); END IF;
  IF TG_TABLE_NAME='operation_drafts' THEN
    anterior:=antes->'dados_extraidos'->'comissao'; proxima:=depois->'dados_extraidos'->'comissao';
  ELSE
    anterior:=antes#>'{payload,dados_extraidos,comissao}'; proxima:=depois#>'{payload,dados_extraidos,comissao}';
  END IF;
  -- Só protege o novo contrato; comissões legadas continuam sem alteração.
  IF anterior->>'contrato' IS DISTINCT FROM 'comissao-juan-v1'
     AND proxima->>'contrato' IS DISTINCT FROM 'comissao-juan-v1' THEN
    IF TG_OP='DELETE' THEN RETURN OLD; ELSE RETURN NEW; END IF;
  END IF;
  -- Nenhum writer legado pode apagar, editar ou promover esse retrato congelado.
  -- Repetição byte-a-byte da linha (exceto relógio) é um no-op permitido.
  IF TG_OP='UPDATE' AND antes-'atualizado_em' = depois-'atualizado_em' THEN RETURN NEW; END IF;
  IF TG_OP<>'UPDATE' THEN RAISE EXCEPTION 'Comissão confirmada exige revisão auditada'; END IF;
  DELETE FROM juan_comissao_privado.autorizacoes
   WHERE txid=txid_current() AND backend_pid=pg_backend_pid()
     AND recurso=TG_TABLE_NAME AND registro_id=NEW.id
     AND autorizacoes.retrato_autorizado = depois-'atualizado_em';
  IF NOT FOUND THEN RAISE EXCEPTION 'Comissão confirmada exige revisão auditada'; END IF;
  RETURN NEW;
END;
$$;

-- AFTER confere a linha final, inclusive quaisquer alterações de triggers legados.
CREATE TRIGGER zz_juan_comissao_preservada
AFTER INSERT OR UPDATE OR DELETE ON public.operation_drafts
FOR EACH ROW EXECUTE FUNCTION public.proteger_comissao_confirmada_juan();
CREATE TRIGGER zz_juan_comissao_preservada
AFTER INSERT OR UPDATE OR DELETE ON public.pending_actions
FOR EACH ROW EXECUTE FUNCTION public.proteger_comissao_confirmada_juan();

CREATE FUNCTION public.bloquear_promocao_comissao_juan()
RETURNS trigger LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public
AS $$
DECLARE alvo uuid;
BEGIN
  IF NEW.acao_tipo IS DISTINCT FROM 'promover_revisao_operacional' THEN RETURN NEW; END IF;
  -- Todos os vínculos conhecidos são conferidos, sem escolher só o primeiro.
  FOR alvo IN SELECT d.id FROM public.operation_drafts d
    WHERE d.id=NEW.entidade_id OR d.id::text=NEW.payload->>'source_draft_id'
      OR d.id::text=NEW.payload->>'operation_draft_id'
      OR d.pending_action_id::text=NEW.payload->>'source_pending_action_id'
    ORDER BY d.id LOOP
    IF NOT pg_try_advisory_xact_lock(hashtextextended('investigacao-draft:'||alvo::text,0)) THEN
      RAISE EXCEPTION 'A revisão está sendo usada por outro processo';
    END IF;
    IF EXISTS (SELECT 1 FROM public.operation_drafts d WHERE d.id=alvo
      AND d.dados_extraidos#>>'{comissao,contrato}'='comissao-juan-v1') THEN
      RAISE EXCEPTION 'Comissão em revisão ainda não possui destino operacional homologado';
    END IF;
  END LOOP;
  RETURN NEW;
END;
$$;
CREATE TRIGGER aaa_juan_comissao_promocao_bloqueada
BEFORE INSERT OR UPDATE ON public.pending_actions
FOR EACH ROW EXECUTE FUNCTION public.bloquear_promocao_comissao_juan();
REVOKE ALL ON FUNCTION public.proteger_comissao_confirmada_juan(), public.bloquear_promocao_comissao_juan()
  FROM PUBLIC, anon, authenticated, service_role;

CREATE FUNCTION public.confirmar_comissao_rascunho_juan(p_plano jsonb, p_confirmacao jsonb)
RETURNS jsonb LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
DECLARE
  d public.operation_drafts%ROWTYPE;
  a public.pending_actions%ROWTYPE;
  e public.eventos%ROWTYPE;
  ds jsonb; ps jsonb; pedido jsonb; c jsonb; identidade jsonb;
  evento_id uuid; base numeric; pct numeric; valor numeric;
  novos_dados jsonb; nova_comissao jsonb; resposta jsonb;
  atual timestamptz := clock_timestamp();
  grupo text; autor text; mensagem text;
BEGIN
  IF jsonb_typeof(p_plano) IS DISTINCT FROM 'object'
     OR jsonb_typeof(p_confirmacao) IS DISTINCT FROM 'object'
     OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p_plano) k)
        IS DISTINCT FROM ARRAY['acao','comissao','criado_em_epoch','expira_em_epoch',
                              'pedido','pendencia','plano_id','rascunho','versao']::text[]
     OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(p_confirmacao) k)
        IS DISTINCT FROM ARRAY['agente','autor_bot','autor_id','canal','confirmado_em_epoch',
                              'encaminhada','grupo_id','mensagem_id','plano_id','texto','topico_id']::text[]
     OR p_plano->>'versao' IS DISTINCT FROM '1'
     OR p_plano->>'acao' IS DISTINCT FROM 'definir_comissao'
     OR coalesce(p_plano->>'plano_id','') !~ '^[0-9a-f]{64}$' THEN
    RAISE EXCEPTION 'Contrato de complemento inválido';
  END IF;
  pedido := p_plano->'pedido'; ds := p_plano->'rascunho'; ps := p_plano->'pendencia';
  c := p_plano->'comissao';
  IF jsonb_typeof(pedido) IS DISTINCT FROM 'object'
     OR jsonb_typeof(ds) IS DISTINCT FROM 'object' OR jsonb_typeof(ps) IS DISTINCT FROM 'object'
     OR jsonb_typeof(c) IS DISTINCT FROM 'object'
     OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(pedido) k)
        IS DISTINCT FROM ARRAY['agente','autor_bot','autor_id','canal','encaminhada','grupo_id','mensagem_id','topico_id']::text[]
     OR (SELECT array_agg(k ORDER BY k) FROM jsonb_object_keys(c) k)
        IS DISTINCT FROM ARRAY['base_vendedor','beneficiario','percentual','valor']::text[] THEN
    RAISE EXCEPTION 'Prévia incompleta';
  END IF;
  grupo := p_confirmacao->>'grupo_id'; autor := p_confirmacao->>'autor_id';
  mensagem := p_confirmacao->>'mensagem_id';
  FOREACH identidade IN ARRAY ARRAY[pedido, p_confirmacao] LOOP
    IF identidade->>'agente' IS DISTINCT FROM 'juan' OR identidade->>'canal' IS DISTINCT FROM 'telegram'
       OR identidade->'autor_bot' IS DISTINCT FROM 'false'::jsonb
       OR identidade->'encaminhada' IS DISTINCT FROM 'false'::jsonb
       OR identidade->'topico_id' IS DISTINCT FROM 'null'::jsonb
       OR jsonb_typeof(identidade->'grupo_id') IS DISTINCT FROM 'string'
       OR jsonb_typeof(identidade->'autor_id') IS DISTINCT FROM 'string'
       OR jsonb_typeof(identidade->'mensagem_id') IS DISTINCT FROM 'string'
       OR coalesce(identidade->>'grupo_id','') !~ '^-[1-9][0-9]{0,19}$'
       OR coalesce(identidade->>'autor_id','') !~ '^[1-9][0-9]{0,19}$'
       OR coalesce(identidade->>'mensagem_id','') !~ '^[1-9][0-9]{0,19}$' THEN
      RAISE EXCEPTION 'Origem da confirmação inválida';
    END IF;
  END LOOP;
  IF pedido->>'grupo_id' IS DISTINCT FROM grupo OR pedido->>'autor_id' IS DISTINCT FROM autor
     OR pedido->>'mensagem_id' IS NOT DISTINCT FROM mensagem
     OR p_confirmacao->>'plano_id' IS DISTINCT FROM p_plano->>'plano_id'
     OR p_confirmacao->>'texto' IS DISTINCT FROM 'CONFIRMAR COMISSAO ' || left(p_plano->>'plano_id',12) THEN
    RAISE EXCEPTION 'Confirmação não corresponde à prévia';
  END IF;
  -- Mesmo envelope reutilizado após timeout retorna o resultado antes do CAS.
  -- Uma mensagem Telegram só pode confirmar um plano, inclusive entre rascunhos.
  evento_id := md5('juan:comissao:telegram:' || grupo || ':' || mensagem)::uuid;
  PERFORM pg_advisory_xact_lock(hashtextextended('juan-comissao:' || evento_id::text,0));
  SELECT * INTO e FROM public.eventos WHERE id=evento_id;
  IF FOUND THEN
    IF e.tipo IS DISTINCT FROM 'comissao_rascunho_confirmada'
       OR e.origem IS DISTINCT FROM 'telegram' OR e.origem_canal IS DISTINCT FROM 'telegram'
       OR e.agente IS DISTINCT FROM 'juan' OR e.usuario IS DISTINCT FROM autor
       OR e.origem_conversa_id IS DISTINCT FROM grupo OR e.origem_mensagem_id IS DISTINCT FROM mensagem
       OR e.contexto_canonico IS DISTINCT FROM 'telegram:grupo:'||grupo
       OR e.contexto_nome IS DISTINCT FROM ds->>'contexto_nome' OR e.escopo IS DISTINCT FROM 'grupo'
       OR e.status IS DISTINCT FROM 'registrado' OR e.entidade_tipo IS DISTINCT FROM 'operation_draft'
       OR e.entidade_id::text IS DISTINCT FROM ds->>'id'
       OR e.dados->'resultado' IS DISTINCT FROM jsonb_build_object(
         'repeticao_idempotente',false,'rascunho_id',ds->>'id','pending_action_id',ps->>'id',
         'evento_id',evento_id,'status','em_revisao','operacionais_alterados',0)
       OR e.dados->'plano' IS DISTINCT FROM p_plano
       OR e.dados->'confirmacao' IS DISTINCT FROM p_confirmacao THEN
      RAISE EXCEPTION 'Mensagem de confirmação já usada com outro conteúdo';
    END IF;
    RETURN (e.dados->'resultado') || jsonb_build_object('repeticao_idempotente',true);
  END IF;
  IF coalesce(p_plano->>'criado_em_epoch','') !~ '^[0-9]{1,12}$'
     OR coalesce(p_plano->>'expira_em_epoch','') !~ '^[0-9]{1,12}$'
     OR coalesce(p_confirmacao->>'confirmado_em_epoch','') !~ '^[0-9]{1,12}$' THEN
    RAISE EXCEPTION 'Horários da confirmação inválidos';
  END IF;
  IF (p_plano->>'expira_em_epoch')::bigint - (p_plano->>'criado_em_epoch')::bigint <> 900
     OR extract(epoch FROM atual) NOT BETWEEN (p_plano->>'criado_em_epoch')::bigint AND (p_plano->>'expira_em_epoch')::bigint
     OR (p_confirmacao->>'confirmado_em_epoch')::bigint NOT BETWEEN (p_plano->>'criado_em_epoch')::bigint AND extract(epoch FROM atual) THEN
    RAISE EXCEPTION 'Prévia vencida ou confirmação fora da janela';
  END IF;
  -- Mesma ordem de coordenação das investigações. Nunca esperar por locks de linha.
  IF NOT pg_try_advisory_xact_lock(hashtextextended('investigacao-draft:' || (ds->>'id'),0)) THEN
    RAISE EXCEPTION 'A revisão está sendo usada por outro processo';
  END IF;
  SELECT * INTO d FROM public.operation_drafts WHERE id=(ds->>'id')::uuid FOR UPDATE NOWAIT;
  IF NOT FOUND THEN RAISE EXCEPTION 'Rascunho não encontrado'; END IF;
  SELECT * INTO a FROM public.pending_actions WHERE id=(ps->>'id')::uuid FOR UPDATE NOWAIT;
  IF NOT FOUND THEN RAISE EXCEPTION 'Pendência não encontrada'; END IF;
  -- Timestamps são comparados tipados, pois REST e PostgreSQL formatam UTC de formas distintas.
  IF (to_jsonb(d)-'atualizado_em'-'criado_em') IS DISTINCT FROM (ds-'atualizado_em'-'criado_em')
     OR (to_jsonb(a)-'atualizado_em'-'criado_em') IS DISTINCT FROM (ps-'atualizado_em'-'criado_em')
     OR d.atualizado_em IS NULL OR a.atualizado_em IS NULL
     OR d.atualizado_em IS DISTINCT FROM (ds->>'atualizado_em')::timestamptz
     OR a.atualizado_em IS DISTINCT FROM (ps->>'atualizado_em')::timestamptz
     OR d.criado_em IS DISTINCT FROM (ds->>'criado_em')::timestamptz
     OR a.criado_em IS DISTINCT FROM (ps->>'criado_em')::timestamptz THEN
    RAISE EXCEPTION 'A revisão mudou; gere outra prévia';
  END IF;
  IF d.pending_action_id IS DISTINCT FROM a.id
     OR a.entidade_tipo IS DISTINCT FROM 'operation_draft' OR a.entidade_id IS DISTINCT FROM d.id
     OR d.tipo_operacao IS DISTINCT FROM 'compra' OR d.entidade_final_id IS NOT NULL
     OR d.revisao_tipo IS DISTINCT FROM 'pre_revisao'
     OR (d.status IN ('rascunho','em_revisao','aguardando_confirmacao')) IS NOT TRUE
     OR (a.status IN ('rascunho','em_revisao','aguardando_confirmacao')) IS NOT TRUE
     OR (a.acao_tipo IN ('revisar_compra','revisar_documento','revisar_consolidacao_negocio')) IS NOT TRUE
     OR d.origem_canal IS DISTINCT FROM 'telegram' OR a.origem_canal IS DISTINCT FROM 'telegram'
     OR d.origem_conversa_id IS DISTINCT FROM grupo OR a.origem_conversa_id IS DISTINCT FROM grupo
     OR d.contexto_canonico IS DISTINCT FROM 'telegram:grupo:' || grupo
     OR a.contexto_canonico IS DISTINCT FROM d.contexto_canonico
     OR d.escopo IS DISTINCT FROM 'grupo' OR a.escopo IS DISTINCT FROM 'grupo'
     OR mensagem IN (d.origem_mensagem_id, a.origem_mensagem_id)
     OR (a.canal IS NOT NULL AND a.canal <> 'telegram')
     OR (to_jsonb(a)->>'conversa_id' IS NOT NULL AND to_jsonb(a)->>'conversa_id' <> grupo)
     OR jsonb_typeof(d.dados_extraidos) IS DISTINCT FROM 'object'
     OR a.payload->'dados_extraidos' IS DISTINCT FROM d.dados_extraidos
     OR (a.payload ? 'source_draft_id' AND a.payload->>'source_draft_id' IS DISTINCT FROM d.id::text)
     OR (a.payload ? 'operation_draft_id' AND a.payload->>'operation_draft_id' IS DISTINCT FROM d.id::text)
     OR a.payload ?| ARRAY['target_table','proposed_record']
     OR d.dados_extraidos->>'status_confirmacao' IN ('promocao_preparada','aprovado_confinex') THEN
    RAISE EXCEPTION 'Revisão não permite complemento de comissão';
  END IF;
  IF EXISTS (SELECT 1 FROM public.pending_actions p WHERE p.acao_tipo='promover_revisao_operacional'
      AND (p.entidade_id=d.id OR p.payload->>'source_draft_id'=d.id::text
        OR p.payload->>'operation_draft_id'=d.id::text OR p.payload->>'source_pending_action_id'=a.id::text)
      AND p.status IN ('preparada','aprovado_confinex','aguardando_confirmacao','em_execucao','executado','erro_pos_gravacao'))
     OR EXISTS (SELECT 1 FROM public.investigacoes_revisao i WHERE i.source_draft_id=d.id) THEN
    RAISE EXCEPTION 'Promoção ou investigação vinculada exige conferência separada';
  END IF;
  IF coalesce(d.dados_extraidos->>'valor_total','') !~ '^[0-9]+(\.[0-9]+)?$'
     OR length(d.dados_extraidos->>'valor_total')>40
     OR coalesce(c->>'percentual','') !~ '^[0-9]+(\.[0-9]{1,4})?$'
     OR coalesce(c->>'base_vendedor','') !~ '^[0-9]+\.[0-9]{2}$'
     OR coalesce(c->>'valor','') !~ '^[0-9]+\.[0-9]{2}$'
     OR length(btrim(coalesce(c->>'beneficiario',''))) NOT BETWEEN 2 AND 120
     OR c->>'beneficiario' ~ '[[:cntrl:]<>@/\\]'
     OR (d.dados_extraidos ? 'comissao' AND jsonb_typeof(d.dados_extraidos->'comissao') IS DISTINCT FROM 'object') THEN
    RAISE EXCEPTION 'Valores ou beneficiário inválidos';
  END IF;
  base := (d.dados_extraidos->>'valor_total')::numeric;
  pct := (c->>'percentual')::numeric; valor := round(base*pct/100,2);
  IF base <= 0 OR base > 999999999999 OR base<>round(base,2) OR pct <= 0 OR pct > 100
     OR (c->>'base_vendedor')::numeric <> base OR (c->>'valor')::numeric <> valor THEN
    RAISE EXCEPTION 'A comissão não corresponde à base conferida';
  END IF;
  nova_comissao := c || jsonb_build_object('contrato','comissao-juan-v1','status_confirmacao','confirmado_telegram',
    'autor_id',autor,'origem_canal','telegram','origem_conversa_id',grupo,
    'origem_mensagem_id',mensagem,'plano_id',p_plano->>'plano_id');
  novos_dados := d.dados_extraidos || jsonb_build_object('comissao',nova_comissao);
  INSERT INTO juan_comissao_privado.autorizacoes(txid,backend_pid,recurso,registro_id,retrato_autorizado)
  VALUES
    (txid_current(),pg_backend_pid(),'operation_drafts',d.id,
      (to_jsonb(d)||jsonb_build_object('dados_extraidos',novos_dados,'status','em_revisao'))-'atualizado_em'),
    (txid_current(),pg_backend_pid(),'pending_actions',a.id,
      (to_jsonb(a)||jsonb_build_object('payload',jsonb_set(a.payload,'{dados_extraidos}',novos_dados),
        'status','em_revisao'))-'atualizado_em');
  UPDATE public.operation_drafts SET dados_extraidos=novos_dados, status='em_revisao', atualizado_em=atual WHERE id=d.id;
  UPDATE public.pending_actions SET payload=jsonb_set(a.payload,'{dados_extraidos}',novos_dados),
    status='em_revisao', atualizado_em=atual WHERE id=a.id;
  IF EXISTS (SELECT 1 FROM juan_comissao_privado.autorizacoes
    WHERE txid=txid_current() AND backend_pid=pg_backend_pid()) THEN
    RAISE EXCEPTION 'A proteção transacional não foi consumida integralmente';
  END IF;
  resposta := jsonb_build_object('repeticao_idempotente',false,'rascunho_id',d.id,
    'pending_action_id',a.id,'evento_id',evento_id,'status','em_revisao','operacionais_alterados',0);
  INSERT INTO public.eventos(id,tipo,agente,usuario,entidade_tipo,entidade_id,origem,
    origem_canal,origem_conversa_id,origem_mensagem_id,contexto_canonico,contexto_nome,escopo,status,dados,observacao)
  VALUES(evento_id,'comissao_rascunho_confirmada','juan',autor,'operation_draft',d.id,'telegram',
    'telegram',grupo,mensagem,d.contexto_canonico,d.contexto_nome,'grupo','registrado',
    jsonb_build_object('plano',p_plano,'confirmacao',p_confirmacao,'resultado',resposta,
      'rascunho_antes',to_jsonb(d),'pendencia_antes',to_jsonb(a),
      'rascunho_depois',(SELECT to_jsonb(r) FROM public.operation_drafts r WHERE r.id=d.id),
      'pendencia_depois',(SELECT to_jsonb(p) FROM public.pending_actions p WHERE p.id=a.id)),
    'Comissão confirmada na revisão; valor do vendedor preservado. Nenhum lançamento operacional foi criado.');
  RETURN resposta;
END;
$$;

COMMENT ON FUNCTION public.confirmar_comissao_rascunho_juan(jsonb,jsonb) IS
  'Complemento transacional somente de comissão em rascunho. Exige mediador que autentique Telegram e prévia HMAC; não expor ao modelo.';
REVOKE ALL ON FUNCTION public.confirmar_comissao_rascunho_juan(jsonb,jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.confirmar_comissao_rascunho_juan(jsonb,jsonb) TO service_role;
COMMIT;
