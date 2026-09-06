#!/usr/bin/env python3
"""Runtime local descartável da migração de complemento de comissão.

Nunca conecta ao Supabase: usa exclusivamente psql/createdb/dropdb no cluster
local fornecido pelo CI. Todos os identificadores e valores são sintéticos.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import signal
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import complemento_comissao_juan as complemento
from test_migracao_postgres import (
    MIGRACAO_ATIVACAO,
    aplicar_migracao,
    executar,
    erro_comando,
    fixture_sql,
    preparar_gate_ativacao,
    psql,
    roles_existentes,
    sql_texto,
)


RAIZ = Path(__file__).resolve().parents[1]
MIGRACAO = RAIZ / "supabase/migrations/202609060001_complemento_comissao_rascunho.sql"
GRUPO = "-700001"
DRAFT = "11111111-1111-4111-8111-111111111111"
PENDING = "22222222-2222-4222-8222-222222222222"
AUTOR = "12345"
MENSAGEM = "67890"


def json_sql(valor: object) -> str:
    return sql_texto(json.dumps(valor, ensure_ascii=False, separators=(",", ":"))) + "::jsonb"


def identidade(mensagem: str = MENSAGEM) -> dict[str, object]:
    return {"canal": "telegram", "agente": "juan", "grupo_id": GRUPO,
            "autor_id": AUTOR, "mensagem_id": mensagem, "topico_id": None,
            "autor_bot": False, "encaminhada": False}


def preparar_fixture(banco: str, com_legado=False) -> None:
    sql = f"""
INSERT INTO public.operation_drafts
  (id, agente, status, tipo_operacao, dados_extraidos, campos_pendentes,
   pending_action_id, origem_canal, origem_conversa_id, contexto_canonico,
   contexto_nome, escopo)
VALUES ({sql_texto(DRAFT)}::uuid, 'juan', 'em_revisao', 'compra',
  '{{"valor_total":"1000.00","preco_arroba":"300.00","status_confirmacao":"pendente"}}'::jsonb,
  ARRAY['comissao'], {sql_texto(PENDING)}::uuid, 'telegram', {sql_texto(GRUPO)},
  {sql_texto('telegram:grupo:' + GRUPO)}, 'Grupo sintético', 'grupo');
INSERT INTO public.pending_actions
  (id, agente, canal, acao_tipo, entidade_tipo, entidade_id, payload, status,
   origem_canal, origem_conversa_id, contexto_canonico, contexto_nome, escopo)
VALUES ({sql_texto(PENDING)}::uuid, 'juan', 'telegram', 'revisar_compra',
  'operation_draft', {sql_texto(DRAFT)}::uuid,
  '{{"dados_extraidos":{{"valor_total":"1000.00","preco_arroba":"300.00","status_confirmacao":"pendente"}}}}'::jsonb,
  'aguardando_confirmacao', 'telegram', {sql_texto(GRUPO)},
  {sql_texto('telegram:grupo:' + GRUPO)}, 'Grupo sintético', 'grupo');
INSERT INTO public.compras (operacao_id, data, quantidade, valor_total)
VALUES ('OPERACAO-NAO-TOCADA', DATE '2026-09-06', 7, 777.77);
INSERT INTO public.vendas (operacao_id, data, quantidade, valor_total)
VALUES ('VENDA-NAO-TOCADA', DATE '2026-09-06', 3, 333.33);
-- Retrato legado anterior à fundação: promoção já preparada ligada a outro par.
INSERT INTO public.operation_drafts(id,agente,status,tipo_operacao,dados_extraidos,pending_action_id,
 origem_canal,origem_conversa_id,contexto_canonico,contexto_nome,escopo)
 SELECT '33333333-3333-4333-8333-333333333333',agente,status,tipo_operacao,dados_extraidos,
 '44444444-4444-4444-8444-444444444444',origem_canal,origem_conversa_id,contexto_canonico,contexto_nome,escopo
 FROM public.operation_drafts WHERE id='{DRAFT}';
INSERT INTO public.pending_actions(id,agente,canal,acao_tipo,entidade_tipo,entidade_id,payload,status,
 origem_canal,origem_conversa_id,contexto_canonico,contexto_nome,escopo)
 SELECT '44444444-4444-4444-8444-444444444444',agente,canal,acao_tipo,entidade_tipo,
 '33333333-3333-4333-8333-333333333333',payload,status,
 origem_canal,origem_conversa_id,contexto_canonico,contexto_nome,escopo FROM public.pending_actions WHERE id='{PENDING}';
INSERT INTO public.pending_actions(id,acao_tipo,entidade_tipo,entidade_id,payload,status)
 VALUES('55555555-5555-4555-8555-555555555555','promover_revisao_operacional','operation_draft',
 '33333333-3333-4333-8333-333333333333',
 '{{"source_draft_id":"33333333-3333-4333-8333-333333333333","source_pending_action_id":"44444444-4444-4444-8444-444444444444"}}','preparada');
"""
    if not com_legado:
        sql = sql.split('-- Retrato legado', 1)[0]
    resultado = psql(banco, sql)
    if resultado.returncode:
        raise RuntimeError(f"falha na fixture sintética: {erro_comando(resultado)}")


def snapshot(banco: str, tabela: str) -> list[dict[str, object]]:
    resultado = psql(banco, f"SELECT coalesce(json_agg(row_to_json(x) ORDER BY x.id), '[]'::json) FROM (SELECT * FROM public.{tabela}) x")
    if resultado.returncode:
        raise RuntimeError(f"snapshot indisponível: {tabela}: {erro_comando(resultado)}")
    return json.loads(resultado.stdout.strip() or "[]")


def snapshots_operacionais(banco: str) -> dict[str, list[dict[str, object]]]:
    return {tabela: snapshot(banco, tabela) for tabela in ("compras", "vendas", "pesagens_caderno", "abates")}


def snapshots_auditados(banco):
    return {t: snapshot(banco,t) for t in ('operation_drafts','pending_actions','eventos','compras','vendas','pesagens_caderno','abates')}


def resposta_json(resultado):
    return json.loads(next(linha for linha in resultado.stdout.splitlines() if linha.startswith('{')))


def snapshot_funcoes_publicas(banco: str) -> dict[str, str]:
    """Guarda definições já existentes para detectar mutação da fundação."""
    resultado = psql(banco, """
SELECT coalesce(json_object_agg(nome, definicao), '{}'::json)
FROM (
  SELECT p.oid::regprocedure::text AS nome, pg_get_functiondef(p.oid) AS definicao
  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
  WHERE n.nspname='public'
) f
""")
    if resultado.returncode:
        raise RuntimeError(f"snapshot de funções indisponível: {erro_comando(resultado)}")
    return json.loads(resultado.stdout.strip() or "{}")


def snapshot_rls(banco: str) -> dict[str, bool]:
    resultado = psql(banco, """
SELECT coalesce(json_object_agg(c.relname, c.relrowsecurity), '{}'::json)
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relkind='r'
""")
    if resultado.returncode:
        raise RuntimeError(f"snapshot de RLS indisponível: {erro_comando(resultado)}")
    return json.loads(resultado.stdout.strip() or "{}")


def assert_privilegios_fechados(banco: str) -> None:
    resultado = psql(banco, """
SELECT json_build_object(
  'anon', has_function_privilege('anon','public.confirmar_comissao_rascunho_juan(jsonb,jsonb)','EXECUTE'),
  'authenticated', has_function_privilege('authenticated','public.confirmar_comissao_rascunho_juan(jsonb,jsonb)','EXECUTE'),
  'service_role', has_function_privilege('service_role','public.confirmar_comissao_rascunho_juan(jsonb,jsonb)','EXECUTE'),
  'rls_draft', (SELECT relrowsecurity FROM pg_class WHERE oid='public.operation_drafts'::regclass),
  'rls_pending', (SELECT relrowsecurity FROM pg_class WHERE oid='public.pending_actions'::regclass))
""")
    if resultado.returncode:
        raise RuntimeError(f"falha ao conferir privilégios: {erro_comando(resultado)}")
    valor = json.loads(resultado.stdout.strip())
    if valor != {"anon": False, "authenticated": False, "service_role": True, "rls_draft": True, "rls_pending": True}:
        raise RuntimeError(f"privilégios/RLS inesperados: {valor}")


def ler_par(banco: str, draft_id=DRAFT, pending_id=PENDING) -> tuple[dict, dict]:
    sql = f"SELECT json_build_object('draft',(SELECT row_to_json(d) FROM public.operation_drafts d WHERE d.id={sql_texto(draft_id)}::uuid),'pending',(SELECT row_to_json(p) FROM public.pending_actions p WHERE p.id={sql_texto(pending_id)}::uuid))"
    resultado = psql(banco, sql)
    if resultado.returncode:
        raise RuntimeError(f"snapshot da revisão indisponível: {erro_comando(resultado)}")
    objeto = json.loads(resultado.stdout.strip())
    return objeto["draft"], objeto["pending"]


def plano_confirmacao(banco: str, *, mensagem: str = MENSAGEM, agora: int | None = None) -> tuple[dict, dict]:
    draft, pending = ler_par(banco)
    agora = agora or time.time_ns() // 1_000_000_000
    identidade_atual = identidade(mensagem)
    plano = complemento.preparar_comissao(
        draft, pending, identidade=identidade_atual, percentual="2.0000",
        beneficiario="Corretor Sintético", agora=agora,
    )
    return plano, complemento.assinar_previa(plano, b"S" * 32)


def chamar_rpc(banco: str, envelope: dict, confirmacao: dict) -> subprocess.CompletedProcess[str]:
    sql = f"SET ROLE service_role; SELECT public.confirmar_comissao_rascunho_juan({json_sql(envelope['plano'])}, {json_sql(confirmacao)}); RESET ROLE;"
    return psql(banco, sql)


def exigir_falha(banco: str, envelope: dict, confirmacao: dict, rotulo: str, antes: dict[str, list[dict[str, object]]], esperado: str | None = None) -> None:
    total_antes = snapshots_auditados(banco)
    resultado = chamar_rpc(banco, envelope, confirmacao)
    if resultado.returncode == 0:
        raise RuntimeError(f"{rotulo}: RPC aceitou entrada inválida")
    if not (resultado.stderr.strip() or resultado.stdout.strip()):
        raise RuntimeError(f"{rotulo}: falha sem diagnóstico")
    if esperado and esperado.lower() not in (resultado.stderr + resultado.stdout).lower():
        raise RuntimeError(f"{rotulo}: erro não corresponde ao gate esperado")
    depois = snapshots_operacionais(banco)
    if depois != antes:
        raise RuntimeError(f"{rotulo}: entrada inválida alterou tabela operacional")
    if snapshots_auditados(banco) != total_antes:
        raise RuntimeError(f"{rotulo}: falha deixou escrita parcial na revisão/eventos")


def sql_ok(banco, sql):
    resultado = psql(banco, sql)
    if resultado.returncode:
        raise RuntimeError(erro_comando(resultado))
    return resultado


def sql_recusado_sem_escrita(banco, sql, erro):
    antes = snapshots_auditados(banco)
    resultado = psql(banco, sql)
    if resultado.returncode == 0 or erro not in resultado.stderr:
        raise RuntimeError(f'Falha esperada não comprovada: {erro}: {erro_comando(resultado)}')
    if snapshots_auditados(banco) != antes:
        raise RuntimeError('Falha deixou registros de teste parciais')
    if sql_ok(banco,'SELECT count(*) FROM juan_comissao_privado.autorizacoes').stdout.strip() != '0':
        raise RuntimeError('Capacidade privada não foi limpa')


def contrato_novo(banco, mensagem):
    plano, envelope = plano_confirmacao(banco)
    contrato = complemento.confirmar_previa(envelope, segredo=b'S'*32,
        identidade=identidade(mensagem), texto=complemento.frase_confirmacao(plano),
        agora=plano['criado_em_epoch'])
    return envelope, contrato['p_confirmacao']


def comando_rpc(envelope, confirmacao):
    return f"SET ROLE service_role; SELECT public.confirmar_comissao_rascunho_juan({json_sql(envelope['plano'])}, {json_sql(confirmacao)});"


def ensaiar_falhas_transacionais(banco, envelope, confirmacao, legado=False):
    # Promoção antiga foi inserida ANTES das migrações no fixture. Não desligar
    # guardas atuais para fabricar um estado que hoje só pode nascer por RPC.
    if legado:
        d,p = ler_par(banco,'33333333-3333-4333-8333-333333333333','44444444-4444-4444-8444-444444444444')
        plano = complemento.preparar_comissao(d,p,identidade=identidade(),percentual='2',beneficiario='AB',agora=int(time.time()))
        envelope_legado = complemento.assinar_previa(plano,b'S'*32)
        contrato = complemento.confirmar_previa(envelope_legado,segredo=b'S'*32,identidade=identidade('99998'),
            texto=complemento.frase_confirmacao(plano),agora=plano['criado_em_epoch'])
        sql_recusado_sem_escrita(banco,comando_rpc(envelope_legado,contrato['p_confirmacao']),'Promoção ou investigação vinculada')
    # O tipo/evento público sozinho não é recibo: origem/contexto devem coincidir.
    resultado = {'repeticao_idempotente':False,'rascunho_id':DRAFT,'pending_action_id':PENDING,
        'status':'em_revisao','operacionais_alterados':0}
    evento_sql = "md5('juan:comissao:telegram:" + GRUPO + ':' + confirmacao['mensagem_id'] + "')::uuid"
    falso_sql = f"""
BEGIN;
INSERT INTO public.eventos(id,tipo,origem,status,entidade_tipo,entidade_id,dados)
VALUES({evento_sql},'comissao_rascunho_confirmada','confinex_revisoes','registrado',
 'operation_draft','{DRAFT}',jsonb_build_object('plano',{json_sql(envelope['plano'])},
 'confirmacao',{json_sql(confirmacao)},'resultado',{json_sql(resultado)}||jsonb_build_object('evento_id',{evento_sql})));
""" + comando_rpc(envelope,confirmacao) + 'COMMIT;'
    sql_recusado_sem_escrita(banco, falso_sql, 'outro conteúdo')
    # Segundo UPDATE falha: o primeiro e as capacidades devem ser revertidos.
    sql_ok(banco, f"""
CREATE FUNCTION public.teste_falha_comissao() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN IF NEW.id='{PENDING}'::uuid THEN RAISE EXCEPTION 'falha_sintetica_pending'; END IF; RETURN NEW; END; $$;
CREATE TRIGGER teste_falha_comissao BEFORE UPDATE ON public.pending_actions
FOR EACH ROW EXECUTE FUNCTION public.teste_falha_comissao();
""")
    try:
        sql_recusado_sem_escrita(banco, comando_rpc(envelope,confirmacao),'falha_sintetica_pending')
    finally:
        sql_ok(banco, 'DROP TRIGGER teste_falha_comissao ON public.pending_actions; DROP FUNCTION public.teste_falha_comissao();')
    # Timeout do servidor depois do primeiro UPDATE também precisa reverter tudo.
    sql_ok(banco, f"""
CREATE FUNCTION public.teste_atraso_comissao() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN IF NEW.id='{PENDING}'::uuid THEN PERFORM pg_sleep(1); END IF; RETURN NEW; END; $$;
CREATE TRIGGER teste_atraso_comissao BEFORE UPDATE ON public.pending_actions
FOR EACH ROW EXECUTE FUNCTION public.teste_atraso_comissao();
""")
    try:
        sql_recusado_sem_escrita(banco, "SET statement_timeout='100ms';"+comando_rpc(envelope,confirmacao), 'statement timeout')
    finally:
        sql_ok(banco, 'DROP TRIGGER teste_atraso_comissao ON public.pending_actions; DROP FUNCTION public.teste_atraso_comissao();')


def ensaiar_guardas_e_concorrencia(banco, envelope_inicial, confirmacao_inicial):
    # Escritores antigos não podem apagar o complemento, mudar a base ou os vínculos.
    antigo = envelope_inicial['plano']
    for sql in (
        f"UPDATE public.operation_drafts SET dados_extraidos={json_sql(antigo['rascunho']['dados_extraidos'])} WHERE id='{DRAFT}';",
        f"UPDATE public.pending_actions SET payload={json_sql(antigo['pendencia']['payload'])} WHERE id='{PENDING}';",
        f"UPDATE public.operation_drafts SET dados_extraidos=jsonb_set(dados_extraidos,'{{valor_total}}','2000') WHERE id='{DRAFT}';",
        f"DELETE FROM public.operation_drafts WHERE id='{DRAFT}';",
        f"UPDATE public.pending_actions SET entidade_id=null WHERE id='{PENDING}';",
    ):
        papel = '' if sql.startswith('DELETE') else 'SET ROLE service_role;'
        sql_recusado_sem_escrita(banco,papel+sql,'revisão auditada')
    for papel in ('anon','authenticated','service_role'):
        sql_recusado_sem_escrita(banco,f'SET ROLE {papel}; SELECT * FROM juan_comissao_privado.autorizacoes;', 'permission denied')
    for papel in ('anon','authenticated'):
        sql_recusado_sem_escrita(banco,comando_rpc(envelope_inicial,confirmacao_inicial).replace('SET ROLE service_role', f'SET ROLE {papel}'),'permission denied')
    # Cliente tentando preparar uma promoção sem comissão não contorna o vínculo.
    for campo, valor in (('source_draft_id',DRAFT), ('operation_draft_id',DRAFT), ('source_pending_action_id',PENDING)):
        sql = "INSERT INTO public.pending_actions(acao_tipo,payload,status) VALUES('promover_revisao_operacional'," + json_sql({campo:valor}) + ",'aguardando_confirmacao');"
        sql_recusado_sem_escrita(banco,sql,'destino operacional homologado')
    for mesma_mensagem in (True,False):
        envelope, confirmacao = contrato_novo(banco,'100001' if mesma_mensagem else '100002')
        segunda = dict(confirmacao) if mesma_mensagem else {**confirmacao,'mensagem_id':'100003'}
        antes = snapshots_auditados(banco)
        # Barreira real: uma conexão segura o lock do evento ou do draft
        # durante a tentativa da segunda; ambas são conexões PostgreSQL distintas.
        sql_ok(banco, f"""
CREATE FUNCTION public.teste_concorrencia_comissao() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN IF NEW.id='{PENDING}'::uuid THEN PERFORM pg_sleep(0.3); END IF; RETURN NEW; END; $$;
CREATE TRIGGER teste_concorrencia_comissao BEFORE UPDATE ON public.pending_actions
FOR EACH ROW EXECUTE FUNCTION public.teste_concorrencia_comissao();
""")
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                r1 = pool.submit(chamar_rpc,banco,envelope,confirmacao)
                r2 = pool.submit(chamar_rpc,banco,envelope,segunda)
                resultados = [r1.result(timeout=20),r2.result(timeout=20)]
        finally:
            sql_ok(banco,'DROP TRIGGER teste_concorrencia_comissao ON public.pending_actions; DROP FUNCTION public.teste_concorrencia_comissao();')
        sucessos = [resposta_json(r) for r in resultados if r.returncode==0]
        if mesma_mensagem:
            if len(sucessos)!=2 or sorted(r['repeticao_idempotente'] for r in sucessos) != [False,True]:
                raise RuntimeError('Confirmação concorrente repetida não foi idempotente')
        elif len(sucessos)!=1 or sucessos[0]['repeticao_idempotente']:
            raise RuntimeError('Duas confirmações divergentes passaram juntas')
        if not mesma_mensagem and not any('outro processo' in r.stderr or 'revisão mudou' in r.stderr for r in resultados):
            raise RuntimeError('Concorrência falhou por causa diferente do gate esperado')
        depois = snapshots_auditados(banco)
        if len(depois['eventos']) != len(antes['eventos'])+1:
            raise RuntimeError('Concorrência duplicou evento')
        for tabela in ('compras','vendas','pesagens_caderno','abates'):
            if depois[tabela]!=antes[tabela]:
                raise RuntimeError('Concorrência alterou operação')
    # Prova de limpeza das capacidades consumidas mesmo sob concorrência.
    if sql_ok(banco,'SELECT count(*) FROM juan_comissao_privado.autorizacoes').stdout.strip()!='0':
        raise RuntimeError('Sobrou capacidade transitória')


def executar_teste(obrigatorio: bool, legado=False) -> int:
    host = os.environ.get("PGHOST", "")
    if host and host not in {"localhost", "127.0.0.1", "::1"} and not host.startswith("/"):
        raise RuntimeError("PGHOST remoto bloqueado; este runner aceita somente PostgreSQL local")
    if os.environ.get('PGHOSTADDR','') not in ('','127.0.0.1','::1') or os.environ.get('PGSERVICE'):
        raise RuntimeError('Roteamento PostgreSQL alternativo bloqueado no teste local')
    os.environ['PGCONNECT_TIMEOUT']='5'
    if not MIGRACAO.exists() or not MIGRACAO_ATIVACAO.exists():
        raise RuntimeError("migração de complemento/ativação ausente")
    if not all(shutil.which(comando) for comando in ("psql", "createdb", "dropdb")):
        mensagem = "PostgreSQL CLI não disponível; runtime será executado no CI"
        if obrigatorio:
            raise RuntimeError(mensagem)
        print(f"SKIP_RUNTIME_POSTGRES: {mensagem}")
        return 0
    banco = f"confinex_comissao_rt_{secrets.token_hex(6)}"
    roles_antes = roles_existentes()
    criado = False
    try:
        criado_resultado = executar(["createdb", banco])
        if criado_resultado.returncode:
            raise RuntimeError(f"não foi possível criar banco: {erro_comando(criado_resultado)}")
        criado = True
        base = psql(banco, fixture_sql())
        if base.returncode:
            raise RuntimeError(f"fixture base falhou: {erro_comando(base)}")
        preparar_fixture(banco, com_legado=legado)
        # 0001/0002 são a fundação exigida pela migração de complemento.
        aplicar_migracao(banco, RAIZ / "supabase/migrations/202608290001_investigacoes_revisao.sql")
        if not legado:
            preparar_gate_ativacao(banco)
            aplicar_migracao(banco, MIGRACAO_ATIVACAO)
        # O gate de ativação pode criar a fundação; a comparação da migração
        # alvo começa somente depois dele.
        antes_instalacao = snapshots_auditados(banco)
        funcoes_antes = snapshot_funcoes_publicas(banco)
        rls_antes = snapshot_rls(banco)
        aplicar_migracao(banco, MIGRACAO)
        depois_instalacao = snapshots_auditados(banco)
        if depois_instalacao != antes_instalacao:
            raise RuntimeError("instalação alterou dados operacionais preexistentes")
        funcoes_depois = snapshot_funcoes_publicas(banco)
        if any(funcoes_depois.get(nome) != definicao for nome, definicao in funcoes_antes.items()):
            raise RuntimeError("instalação alterou definição de função preexistente")
        if snapshot_rls(banco) != rls_antes:
            raise RuntimeError("instalação alterou RLS preexistente")
        assert_privilegios_fechados(banco)
        antes_operacionais = snapshots_operacionais(banco)
        plano, envelope = plano_confirmacao(banco)
        texto = complemento.frase_confirmacao(plano)
        confirmacao = {**identidade("99999"), "texto": texto, "plano_id": plano["plano_id"], "confirmado_em_epoch": plano["criado_em_epoch"]}
        stale_antes = snapshots_operacionais(banco)
        stale = psql(banco, f"UPDATE public.operation_drafts SET status='em_revisao' WHERE id='{DRAFT}'::uuid")
        if stale.returncode:
            raise RuntimeError(f"falha ao preparar stale sintético: {erro_comando(stale)}")
        exigir_falha(banco, envelope, confirmacao, "CAS stale", stale_antes, "revis")
        # A prévia nova captura os timestamps alterados e é a única autorizada.
        plano, envelope = plano_confirmacao(banco)
        texto = complemento.frase_confirmacao(plano)
        confirmacao = {**identidade("99999"), "texto": texto, "plano_id": plano["plano_id"], "confirmado_em_epoch": plano["criado_em_epoch"]}
        ensaiar_falhas_transacionais(banco, envelope, confirmacao, legado=legado)
        resultado = chamar_rpc(banco, envelope, confirmacao)
        if resultado.returncode:
            raise RuntimeError(f"confirmação sintética falhou: {erro_comando(resultado)}")
        depois_operacionais = snapshots_operacionais(banco)
        if depois_operacionais != antes_operacionais:
            raise RuntimeError("confirmação alterou tabelas operacionais")
        status = psql(banco, f"SELECT status FROM public.operation_drafts WHERE id='{DRAFT}'::uuid UNION ALL SELECT status FROM public.pending_actions WHERE id='{PENDING}'::uuid")
        if status.stdout.split() != ["em_revisao", "em_revisao"]:
            raise RuntimeError("status do par não foi atualizado exatamente")
        eventos = psql(banco, "SELECT count(*) FROM public.eventos WHERE tipo='comissao_rascunho_confirmada'")
        if eventos.stdout.strip() != "1":
            raise RuntimeError("evento de confirmação não foi criado uma única vez")
        d,p = ler_par(banco)
        for atual,original,chaves in ((d,plano['rascunho'],('dados_extraidos','status','atualizado_em')), (p,plano['pendencia'],('payload','status','atualizado_em'))):
            if {k:v for k,v in atual.items() if k not in chaves}!={k:v for k,v in original.items() if k not in chaves}:
                raise RuntimeError('Complemento modificou campos fora do contrato')
        if {k:v for k,v in d['dados_extraidos'].items() if k!='comissao'}!=plano['rascunho']['dados_extraidos']:
            raise RuntimeError('Preço/base/origem extraída foi alterada')
        if p['payload']['dados_extraidos']!=d['dados_extraidos'] or d['dados_extraidos']['comissao']['valor']!='20.00':
            raise RuntimeError('Espelho ou valor da comissão divergente')
        repeticao = chamar_rpc(banco, envelope, confirmacao)
        if repeticao.returncode:
            raise RuntimeError("repetição do mesmo envelope não foi idempotente")
        repeticao_json = resposta_json(repeticao)
        if repeticao_json.get("repeticao_idempotente") is not True:
            raise RuntimeError("repetição não retornou repeticao_idempotente=true")
        primeira_json = resposta_json(resultado)
        if primeira_json.get("repeticao_idempotente") is not False:
            raise RuntimeError("primeira confirmação não retornou repeticao_idempotente=false")
        alterado = json.loads(json.dumps(envelope))
        alterado["plano"]["comissao"]["valor"] = "999.99"
        # Chamada direta, impossível no mediador correto: prova defesa do SQL,
        # não autenticação HMAC (que é conferida antes, exclusivamente no Python).
        exigir_falha(banco, alterado, confirmacao, "conteúdo alterado", depois_operacionais, "mensagem")
        for campo, valor in (("grupo_id", "-700002"), ("autor_id", "99999"), ("texto", "CONFIRMAR COMISSAO 000000000000")):
            tentativa = dict(confirmacao, **{campo: valor})
            esperado = "confirmação"
            exigir_falha(banco, envelope, tentativa, f"confirmação inválida: {campo}", depois_operacionais, esperado)
        ensaiar_guardas_e_concorrencia(banco,envelope,confirmacao)
        modo = 'sombra com legado' if legado else 'guardas operacionais ativas'
        print(f"RUNTIME_POSTGRES_OK ({modo}): instalação aditiva, privilégio restrito, CAS, rollback, timeout, concorrência, cliente stale, promoção bloqueada e limpeza de capacidades passaram")
        return 0
    finally:
        if criado:
            executar(["dropdb", "--if-exists", banco])
        for papel in ("anon", "authenticated", "service_role"):
            if papel not in roles_antes:
                executar(["dropuser", "--if-exists", papel])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--obrigatorio", action="store_true")
    args = parser.parse_args()
    def timeout_handler(signum: int, frame: object) -> None:
        raise TimeoutError("runtime PostgreSQL excedeu o prazo local")
    signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(180)
    try:
        executar_teste(args.obrigatorio, legado=True)
        return executar_teste(args.obrigatorio)
    except (RuntimeError, subprocess.TimeoutExpired, TimeoutError) as erro:
        print(f"RUNTIME_POSTGRES_FALHOU: {erro}", file=sys.stderr)
        return 1
    finally:
        signal.alarm(0)


if __name__ == "__main__":
    raise SystemExit(main())
