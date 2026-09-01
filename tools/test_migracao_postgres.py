#!/usr/bin/env python3
"""Teste de runtime da migração de investigações em PostgreSQL descartável.

O teste não usa o Supabase. Ele cria um banco efêmero em um PostgreSQL local
(ou no serviço PostgreSQL do CI), aplica a migração exata e destrói o banco ao
final. Todos os dados usados são sintéticos.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile

import planejador_investigacoes as planejador_modulo
from investigacoes_revisao import (
    contrato_consulta,
    normalizar_consulta,
    planejar_investigacao,
    selar_fonte_adaptador,
)


RAIZ = Path(__file__).resolve().parents[1]
MIGRACAO = RAIZ / "supabase/migrations/202608290001_investigacoes_revisao.sql"
MIGRACAO_ATIVACAO = (
    RAIZ / "supabase/migrations/202608290002_ativar_mediador_investigacoes.sql"
)
ROLLBACK_ATIVACAO = (
    RAIZ / "supabase/rollbacks/202608290002_desativar_mediador_investigacoes.sql"
)
POLITICA_SCHEMA_HASH = "67cbdc991384e6cb6f7c65ce120e59b481aca204b0b8b316fb723365aa08220e"


def json_canonico(valor: object) -> str:
    return json.dumps(
        valor, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def contrato_consulta(spec: dict[str, object]) -> dict[str, object]:
    canonico = json_canonico(spec)
    digest = hashlib.sha256(canonico.encode("utf-8")).hexdigest()
    return {
        "consulta_ref": f"qref_{digest[:32]}",
        "consulta_schema_version": "consulta-v1",
        "consulta_spec": spec,
        "consulta_canonico": canonico,
        "consulta_hash": digest,
    }


def contrato_plano(
    *, fonte_ref: str, sintese_ref: str, pergunta: str,
    campos_obrigatorios: list[str] | None = None,
) -> tuple[str, str, list[dict[str, object]]]:
    base = {
        "tipo": "busca", "pergunta": pergunta, "termos": [], "campos": [],
        "janela_inicio": "", "janela_fim": "", "limite": 10,
        "paginacao": "inicio", "cobertura_esperada": "completa",
    }
    sintese = {
        "tipo": "sintese", "pergunta": "sintetizar evidencias aceitas",
        "termos": [], "campos": [], "janela_inicio": "", "janela_fim": "",
        "limite": 100, "paginacao": "inicio",
        "cobertura_esperada": "fontes_planejadas",
    }
    tarefas = [
        {
            "plano_item_ref": fonte_ref, "adaptador": "outro",
            "adaptador_version": "v1", **contrato_consulta(base),
        },
        {
            "plano_item_ref": sintese_ref, "adaptador": "sintese",
            "adaptador_version": "investigacao-v1", **contrato_consulta(sintese),
        },
    ]
    tarefas.sort(key=lambda item: str(item["plano_item_ref"]))
    canonico = json_canonico({
        "campos_obrigatorios": campos_obrigatorios or [],
        "policy_schema_hash": POLITICA_SCHEMA_HASH,
        "tarefas": tarefas,
    })
    return canonico, hashlib.sha256(canonico.encode("utf-8")).hexdigest(), tarefas


def sql_texto(valor: str) -> str:
    return "'" + valor.replace("'", "''") + "'"


def executar(cmd: list[str], *, captura: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        check=False,
        text=True,
        capture_output=captura,
        env=os.environ.copy(),
    )


def psql(banco: str, sql: str) -> subprocess.CompletedProcess[str]:
    return executar(
        ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-At", "-d", banco, "-c", sql]
    )


def erro_comando(resultado: subprocess.CompletedProcess[str]) -> str:
    texto = (resultado.stderr or resultado.stdout or "").strip().splitlines()
    return "\n".join(texto[-100:]) if texto else f"exit={resultado.returncode}"


def roles_existentes() -> set[str]:
    resultado = psql(
        "postgres",
        "SELECT rolname FROM pg_roles WHERE rolname IN ('anon','authenticated','service_role') ORDER BY 1",
    )
    if resultado.returncode:
        return set()
    return {linha.strip() for linha in resultado.stdout.splitlines() if linha.strip()}


def fixture_sql() -> str:
    return r"""
CREATE SCHEMA IF NOT EXISTS extensions;
CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA extensions;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'anon') THEN CREATE ROLE anon NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'authenticated') THEN CREATE ROLE authenticated NOLOGIN; END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'service_role') THEN CREATE ROLE service_role NOLOGIN BYPASSRLS; END IF;
END $$;
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;

CREATE TABLE public.operation_drafts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  agente text, status text, tipo_operacao text, entidade_final_tipo text,
  entidade_final_id uuid, confianca numeric,
  dados_extraidos jsonb NOT NULL DEFAULT '{}'::jsonb,
  campos_pendentes text[] NOT NULL DEFAULT '{}'::text[],
  inferencias jsonb NOT NULL DEFAULT '{}'::jsonb,
  pending_action_id uuid, origem_canal text, origem_conversa_id text,
  origem_mensagem_id text, contexto_canonico text, contexto_nome text,
  escopo text, criado_em timestamptz NOT NULL DEFAULT now(),
  -- Sem NOT NULL: o schema real de `operation_drafts` não é versionado aqui e
  -- uma linha legada com `atualizado_em` NULL pode existir em produção. O
  -- fixture precisa permitir esse estado para provar que o trigger da 0001
  -- não bloqueia o executor legado nesse caso.
  atualizado_em timestamptz DEFAULT now(),
  codigo_sugerido text
);
CREATE TABLE public.pending_actions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  agente text, usuario_solicitante text, canal text, acao_tipo text,
  entidade_tipo text, entidade_id uuid, entidade_codigo text, resumo text,
  payload jsonb NOT NULL DEFAULT '{}'::jsonb, resultado jsonb, erro text,
  status text, origem_canal text, origem_conversa_id text,
  origem_mensagem_id text, contexto_canonico text, contexto_nome text,
  escopo text, confirmado_em timestamptz, confirmado_por text,
  criado_em timestamptz NOT NULL DEFAULT now(),
  -- Idem `operation_drafts`: sem NOT NULL para permitir simular a linha
  -- legada com `atualizado_em` NULL que pode existir em produção.
  atualizado_em timestamptz DEFAULT now()
);
CREATE TABLE public.eventos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), tipo text, agente text,
  usuario text, entidade_tipo text, entidade_id uuid, entidade_codigo text,
  origem text, origem_canal text, origem_conversa_id text,
  origem_mensagem_id text, contexto_canonico text, contexto_nome text,
  escopo text, status text, fonte_ref text, confianca numeric,
  dados jsonb NOT NULL DEFAULT '{}'::jsonb, observacao text,
  criado_em timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE public.negocios_candidatos (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  atualizado_em timestamptz NOT NULL DEFAULT now()
);
CREATE OR REPLACE FUNCTION public.atualizar_timestamp_staging_consolidacao()
RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path = public AS $$
BEGIN
  NEW.atualizado_em := now();
  RETURN NEW;
END;
$$;
CREATE TRIGGER negocios_candidatos_atualizado_em
BEFORE UPDATE ON public.negocios_candidatos
FOR EACH ROW EXECUTE FUNCTION public.atualizar_timestamp_staging_consolidacao();
-- Estado mínimo deixado pelas migrações operacionais anteriores. Os campos
-- comuns permitem provar que o retrato selado da promoção é comparado com a
-- linha realmente inserida, sem transformar este fixture numa cópia do
-- schema de produção.
CREATE TABLE public.compras (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operacao_id text, data date, quantidade integer, valor_total numeric,
  idempotency_key text, nota_runtime text,
  updated_at timestamptz,
  CONSTRAINT compras_idempotency_key_nao_vazia CHECK (
    idempotency_key IS NULL OR (
      btrim(idempotency_key) <> '' AND length(idempotency_key) <= 200
    )
  )
);
CREATE UNIQUE INDEX compras_idempotency_key_unique
  ON public.compras (idempotency_key)
  WHERE idempotency_key IS NOT NULL;
CREATE TABLE public.vendas (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operacao_id text, data date, quantidade integer, valor_total numeric,
  updated_at timestamptz
);
-- Réplica fiel dos triggers legados de produção (trg_upd_compras e
-- trg_upd_vendas → public.set_updated_at). Eles são BEFORE UPDATE FOR EACH
-- ROW nas tabelas operacionais e a 0002 precisa allowlistá-los por
-- identidade completa em vez de rejeitar todo BEFORE ROW não guardião.
CREATE OR REPLACE FUNCTION public.set_updated_at()
RETURNS trigger LANGUAGE plpgsql AS $$
begin new.updated_at = now(); return new; end $$;
CREATE TRIGGER trg_upd_compras
BEFORE UPDATE ON public.compras
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TRIGGER trg_upd_vendas
BEFORE UPDATE ON public.vendas
FOR EACH ROW EXECUTE FUNCTION public.set_updated_at();
CREATE TABLE public.pesagens_caderno (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operacao_id text, data date, quantidade integer, valor_total numeric
);
CREATE TABLE public.abates (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  operacao_id text, data date, quantidade integer, valor_total numeric
);
GRANT SELECT, INSERT, UPDATE ON public.operation_drafts TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.pending_actions TO service_role;
GRANT SELECT, INSERT ON public.eventos TO service_role;
GRANT SELECT, INSERT, UPDATE ON public.negocios_candidatos TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE
  ON public.compras, public.vendas, public.pesagens_caderno, public.abates
  TO service_role;
ALTER TABLE public.operation_drafts ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.operation_drafts TO authenticated;
CREATE POLICY operation_drafts_authenticated_revisoes
ON public.operation_drafts FOR ALL TO authenticated
USING (true) WITH CHECK (true);
ALTER TABLE public.pending_actions ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.pending_actions TO authenticated;
CREATE POLICY pending_actions_authenticated_revisoes
ON public.pending_actions FOR ALL TO authenticated
USING (true) WITH CHECK (true);
CREATE OR REPLACE FUNCTION public.preencher_contexto_canonico()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.contexto_canonico = 'forcar_reescrita' THEN
    NEW.origem_mensagem_id := 'reescrito-pelo-trigger-legado';
  END IF;
  RETURN NEW;
END;
$$;
CREATE TRIGGER pending_actions_contexto_canonico
BEFORE INSERT OR UPDATE ON public.pending_actions
FOR EACH ROW EXECUTE FUNCTION public.preencher_contexto_canonico();
CREATE TRIGGER operation_drafts_contexto_canonico
BEFORE INSERT OR UPDATE ON public.operation_drafts
FOR EACH ROW EXECUTE FUNCTION public.preencher_contexto_canonico();
INSERT INTO public.pending_actions (
  id, agente, canal, acao_tipo, entidade_tipo, resumo, payload, status, escopo
) VALUES
  ('80810000-0000-4000-8000-000000000001', 'executor-legado', 'teste',
   'promover_revisao_operacional', 'compras', 'Promoção sombra antes do cutover',
   '{"target_table":"compras","proposed_record":{"operacao_id":"LEGADO-SOMBRA","data":"2026-08-29","quantidade":1,"valor_total":10}}',
   'executado', 'teste_executor_legado'),
  ('80810000-0000-4000-8000-000000000002', 'executor-legado', 'teste',
   'promover_revisao_operacional', 'compras', 'Promoção após rollback',
   '{"target_table":"compras","proposed_record":{"operacao_id":"LEGADO-ROLLBACK","data":"2026-08-29","quantidade":1,"valor_total":20}}',
   'executado', 'teste_executor_legado'),
  ('80810000-0000-4000-8000-000000000003', 'executor-legado', 'teste',
   'promover_revisao_operacional', 'compras', 'Promoção bloqueada no cutover',
   '{"target_table":"compras","proposed_record":{"operacao_id":"LEGADO-ATIVO","data":"2026-08-29","quantidade":1,"valor_total":30}}',
   'executado', 'teste_executor_legado'),
  ('80810000-0000-4000-8000-000000000011', 'executor-legado', 'teste',
   'promover_revisao_operacional', 'vendas', 'Venda sombra antes do cutover',
   '{"target_table":"vendas","proposed_record":{"operacao_id":"VENDA-SOMBRA","data":"2026-08-29","quantidade":1,"valor_total":11}}',
   'executado', 'teste_executor_legado'),
  ('80810000-0000-4000-8000-000000000012', 'executor-legado', 'teste',
   'promover_revisao_operacional', 'vendas', 'Venda após rollback',
   '{"target_table":"vendas","proposed_record":{"operacao_id":"VENDA-ROLLBACK","data":"2026-08-29","quantidade":1,"valor_total":21}}',
   'executado', 'teste_executor_legado'),
  ('80810000-0000-4000-8000-000000000021', 'executor-legado', 'teste',
   'promover_revisao_operacional', 'pesagens_caderno', 'Pesagem sombra antes do cutover',
   '{"target_table":"pesagens_caderno","proposed_record":{"operacao_id":"PESAGEM-SOMBRA","data":"2026-08-29","quantidade":1,"valor_total":12}}',
   'executado', 'teste_executor_legado'),
  ('80810000-0000-4000-8000-000000000022', 'executor-legado', 'teste',
   'promover_revisao_operacional', 'pesagens_caderno', 'Pesagem após rollback',
   '{"target_table":"pesagens_caderno","proposed_record":{"operacao_id":"PESAGEM-ROLLBACK","data":"2026-08-29","quantidade":1,"valor_total":22}}',
   'executado', 'teste_executor_legado'),
  ('80810000-0000-4000-8000-000000000031', 'executor-legado', 'teste',
   'promover_revisao_operacional', 'abates', 'Abate sombra antes do cutover',
   '{"target_table":"abates","proposed_record":{"operacao_id":"ABATE-SOMBRA","data":"2026-08-29","quantidade":1,"valor_total":13}}',
   'executado', 'teste_executor_legado'),
  ('80810000-0000-4000-8000-000000000032', 'executor-legado', 'teste',
   'promover_revisao_operacional', 'abates', 'Abate após rollback',
   '{"target_table":"abates","proposed_record":{"operacao_id":"ABATE-ROLLBACK","data":"2026-08-29","quantidade":1,"valor_total":23}}',
   'executado', 'teste_executor_legado');
"""


def aplicar_migracao(
    banco: str, caminho: Path, *, segunda_vez: bool = False,
) -> None:
    with tempfile.NamedTemporaryFile("w", suffix=".sql", encoding="utf-8") as arquivo:
        arquivo.write(f"\\i {caminho}\n")
        arquivo.flush()
        resultado = executar(
            ["psql", "-X", "-v", "ON_ERROR_STOP=1", "-d", banco, "-f", arquivo.name]
        )
    if resultado.returncode:
        vez = "segunda aplicação" if segunda_vez else "aplicação"
        raise RuntimeError(
            f"Falha na {vez} da migração {caminho.name}: {erro_comando(resultado)}"
        )


def validar_catalogo(banco: str) -> None:
    sql = r"""
DO $$
DECLARE
  esperado text[] := ARRAY[
    'investigacoes_revisao','investigacao_tarefas','investigacao_evidencias',
    'investigacao_alternativas','investigacao_alternativa_evidencias',
    'investigacao_pendencias','investigacao_eventos','investigacao_entregas'
  ];
  nome text;
BEGIN
  IF NOT EXISTS (
    SELECT 1
      FROM pg_extension extensao
      JOIN pg_namespace esquema ON esquema.oid = extensao.extnamespace
     WHERE extensao.extname = 'pgcrypto' AND esquema.nspname = 'extensions'
  ) THEN
    RAISE EXCEPTION 'pgcrypto não está no schema extensions';
  END IF;
  FOREACH nome IN ARRAY esperado LOOP
    IF to_regclass('public.' || nome) IS NULL THEN
      RAISE EXCEPTION 'objeto ausente: %', nome;
    END IF;
    IF NOT (SELECT relrowsecurity FROM pg_class WHERE oid = ('public.' || nome)::regclass) THEN
      RAISE EXCEPTION 'RLS ausente: %', nome;
    END IF;
  END LOOP;
  IF has_table_privilege('authenticated', 'public.investigacoes_revisao', 'SELECT')
     OR has_table_privilege('authenticated', 'public.investigacao_eventos', 'SELECT') THEN
    RAISE EXCEPTION 'authenticated recebeu acesso direto a tabela privada';
  END IF;
  IF NOT has_table_privilege('service_role', 'public.investigacoes_revisao', 'INSERT') THEN
    RAISE EXCEPTION 'service_role não pode criar investigação';
  END IF;
  IF has_table_privilege('service_role', 'public.investigacoes_revisao', 'UPDATE')
     OR has_table_privilege('service_role', 'public.investigacao_tarefas', 'UPDATE')
     OR has_table_privilege('service_role', 'public.investigacao_pendencias', 'UPDATE') THEN
    RAISE EXCEPTION 'service_role recebeu UPDATE direto no plano de controle';
  END IF;
  IF has_table_privilege('service_role', 'public.investigacao_evidencias', 'INSERT') THEN
    RAISE EXCEPTION 'evidência pode ser inserida fora da RPC';
  END IF;
  IF has_table_privilege('service_role', 'public.investigacao_eventos', 'UPDATE')
     OR has_table_privilege('service_role', 'public.investigacao_eventos', 'DELETE') THEN
    RAISE EXCEPTION 'trilha técnica não está append-only';
  END IF;
  IF has_function_privilege('authenticated', 'public.assumir_tarefa_investigacao(text,text,integer)', 'EXECUTE')
     OR has_function_privilege('authenticated', 'public.preparar_promocao_revisao_investigada(uuid,uuid,jsonb)', 'EXECUTE')
     OR has_function_privilege('authenticated', 'public.obsoletar_investigacao_por_mudanca_draft(uuid,timestamptz,jsonb)', 'EXECUTE') THEN
    RAISE EXCEPTION 'authenticated recebeu RPC de worker/promoção';
  END IF;
  IF NOT has_function_privilege('service_role', 'public.assumir_tarefa_investigacao(text,text,integer)', 'EXECUTE') THEN
    RAISE EXCEPTION 'service_role não recebeu RPC de worker';
  END IF;
  IF NOT has_function_privilege(
       'service_role',
       'public.obsoletar_investigacao_por_mudanca_draft(uuid,timestamptz,jsonb)',
       'EXECUTE'
     ) THEN
    RAISE EXCEPTION 'service_role não recebeu RPC auditável de obsolescência';
  END IF;
  IF NOT has_schema_privilege('service_role', 'extensions', 'USAGE')
     OR NOT has_function_privilege(
       'service_role', 'extensions.digest(bytea,text)', 'EXECUTE'
     ) THEN
    RAISE EXCEPTION 'service_role não recebeu acesso mínimo a pgcrypto';
  END IF;
  IF NOT EXISTS (
       SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'public.pending_actions'::regclass
          AND tgname = 'pending_actions_bloqueia_investigacao'
          AND NOT tgisinternal
     ) OR NOT EXISTS (
       SELECT 1 FROM pg_trigger
        WHERE tgrelid = 'public.operation_drafts'::regclass
          AND tgname = 'operation_drafts_bloqueia_investigacao'
          AND NOT tgisinternal
     ) THEN
    RAISE EXCEPTION 'ativação não instalou os guardiões do mediador';
  END IF;
  IF NOT public.investigacao_json_sanitizado('{"ok":"valor sintético"}'::jsonb)
     OR public.investigacao_json_sanitizado('{"password":"não deve passar"}'::jsonb)
     OR public.investigacao_json_sanitizado('{"sênha":"não deve passar"}'::jsonb)
     OR public.investigacao_json_sanitizado('{"documento":12345678901}'::jsonb)
     OR public.investigacao_json_sanitizado(
       '{"chave_nfe":"12345678901234567890123456789012345678901234"}'::jsonb
     )
     OR public.investigacao_json_sanitizado(
       '{"documento":"NFe12345678901234567890123456789012345678901234"}'::jsonb
     )
     OR public.investigacao_json_sanitizado(
       '{"aninhado":{"documento":"NFe 12345678901234567890123456789012345678901234"}}'::jsonb
     )
     OR public.investigacao_json_sanitizado(
       '{"chave_nfe":12345678901234567890123456789012345678901234}'::jsonb
     )
     OR public.investigacao_json_sanitizado('{"x-api-key":"não deve passar"}'::jsonb)
     OR public.investigacao_json_sanitizado('{"resumo":"CPF 123.456.789-00"}'::jsonb)
     OR public.investigacao_json_publico_sanitizado(
       '{"operacao_id":"123e4567-e89b-42d3-a456-426614174000"}'::jsonb
     ) OR public.investigacao_json_publico_sanitizado(
       '{"resumo":"019535d9-3df7-7d2b-8c4d-ffeeddccbbaa"}'::jsonb
     ) OR public.investigacao_json_publico_sanitizado(
       '{"resumo":"00000000-0000-0000-0000-000000000000"}'::jsonb
     ) OR public.investigacao_json_publico_sanitizado(
       '{"resumo":"01234567-89ab-cdef-0123-456789abcdef"}'::jsonb
     ) THEN
    RAISE EXCEPTION 'sanitizador não bloqueou/aceitou conforme contrato';
  END IF;
  IF public.investigacao_uuid_texto_seguro('nao-e-uuid') IS NOT NULL THEN
    RAISE EXCEPTION 'conversão segura aceitou UUID legado inválido';
  END IF;
  IF NOT public.investigacao_campos_obrigatorios_validos('{}'::text[])
     OR NOT public.investigacao_campos_obrigatorios_validos(
       ARRAY['gta', 'valor_total']
     )
     OR public.investigacao_campos_obrigatorios_validos(ARRAY['']::text[])
     OR public.investigacao_campos_obrigatorios_validos(ARRAY[NULL]::text[])
     OR public.investigacao_campos_obrigatorios_validos(
       ARRAY['gta', 'gta']
     ) THEN
    RAISE EXCEPTION 'campos obrigatórios aceitaram forma vazia, nula ou duplicada';
  END IF;
  IF NOT public.investigacao_snapshots_candidatos_validos(
       '{}'::uuid[], '{}'::jsonb, NULL, NULL
     )
     OR public.investigacao_snapshots_candidatos_validos(
       ARRAY['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid],
       '{"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa":"instante-invalido"}'::jsonb,
       'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid, now()
     )
     OR public.investigacao_uuid_array_corresponde_objeto(
       ARRAY['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid],
       '{"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA":null}'::jsonb
     ) THEN
    RAISE EXCEPTION 'snapshots de candidatos aceitaram instante inválido';
  END IF;
END $$;
"""
    resultado = psql(banco, sql)
    if resultado.returncode:
        raise RuntimeError(f"Falha nas asserções de catálogo/RLS: {erro_comando(resultado)}")


def validar_compatibilidade_sombra(banco: str) -> None:
    """Prova que a fundação 0001 não interrompe o fluxo legado autenticado."""
    sql = r"""
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_trigger
     WHERE tgrelid IN (
       'public.pending_actions'::regclass,
       'public.operation_drafts'::regclass,
       'public.compras'::regclass, 'public.vendas'::regclass,
       'public.pesagens_caderno'::regclass, 'public.abates'::regclass
     )
       AND tgname IN (
         'pending_actions_bloqueia_investigacao',
         'operation_drafts_bloqueia_investigacao',
         'compras_vinculo_promocao_protegido',
         'vendas_vinculo_promocao_protegido',
         'pesagens_vinculo_promocao_protegido',
         'abates_vinculo_promocao_protegido'
       )
       AND NOT tgisinternal
  ) THEN
    RAISE EXCEPTION 'a fundação em sombra ativou guardião do mediador';
  END IF;
END $$;
INSERT INTO public.operation_drafts
  (id, agente, status, tipo_operacao, entidade_final_tipo, dados_extraidos,
   campos_pendentes, inferencias, escopo)
VALUES ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1', 'teste', 'em_revisao',
  'compra', 'compras', '{}', '{}', '{}', 'teste_sombra');
SET ROLE authenticated;
INSERT INTO public.pending_actions
  (agente, canal, acao_tipo, entidade_tipo, resumo,
   payload, status, escopo)
VALUES ('teste', 'teste', 'revisar_consolidacao_negocio', 'operation_draft',
  'fluxo legado sintético', '{}',
  'aguardando_confirmacao', 'teste_sombra');
RESET ROLE;
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.pending_actions
     WHERE escopo = 'teste_sombra'
  ) THEN
    RAISE EXCEPTION 'a fundação 0001 interrompeu o fluxo legado';
  END IF;
END $$;
DELETE FROM public.pending_actions
 WHERE escopo = 'teste_sombra';
DELETE FROM public.operation_drafts
 WHERE id = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1';
"""
    resultado = psql(banco, sql)
    if resultado.returncode:
        raise RuntimeError(
            f"Falha na compatibilidade da fase sombra: {erro_comando(resultado)}"
        )


def testar_backfill_atualizado_em_nulo_legado(banco: str) -> None:
    """`operation_drafts`/`pending_actions` são tabelas pré-existentes cujo
    schema real não é versionado nesta migração: uma linha legada com
    `atualizado_em` NULL pode existir em produção. Prova que, somente com a
    0001 aplicada, um UPDATE legado nessa linha continua funcionando (o
    trigger faz backfill com `clock_timestamp()`), e que um timestamp
    NÃO NULO fora da janela operacional (ano 1990) continua sendo rejeitado.
    """
    sql = r"""
INSERT INTO public.operation_drafts
  (id, agente, status, tipo_operacao, entidade_final_tipo, dados_extraidos,
   campos_pendentes, inferencias, escopo, atualizado_em)
VALUES ('cccccccc-cccc-4ccc-8ccc-ccccccccccc1', 'teste', 'em_revisao',
  'compra', 'compras', '{}', '{}', '{}', 'teste_backfill_atualizado_em_nulo',
  NULL);
INSERT INTO public.pending_actions
  (id, agente, canal, acao_tipo, entidade_tipo, resumo, payload, status,
   escopo, atualizado_em)
VALUES ('cccccccc-cccc-4ccc-8ccc-ccccccccccc2', 'teste', 'teste',
  'revisar_consolidacao_negocio', 'operation_draft',
  'fluxo legado sintético com atualizado_em nulo', '{}',
  'aguardando_confirmacao', 'teste_backfill_atualizado_em_nulo', NULL);
SET ROLE authenticated;
UPDATE public.operation_drafts SET status = 'confirmado'
 WHERE id = 'cccccccc-cccc-4ccc-8ccc-ccccccccccc1';
UPDATE public.pending_actions SET status = 'executado'
 WHERE id = 'cccccccc-cccc-4ccc-8ccc-ccccccccccc2';
RESET ROLE;
DO $$ BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.operation_drafts
     WHERE id = 'cccccccc-cccc-4ccc-8ccc-ccccccccccc1'
       AND status = 'confirmado'
       AND atualizado_em IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'UPDATE legado em operation_drafts com atualizado_em NULL falhou ou não preencheu o snapshot';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.pending_actions
     WHERE id = 'cccccccc-cccc-4ccc-8ccc-ccccccccccc2'
       AND status = 'executado'
       AND atualizado_em IS NOT NULL
  ) THEN
    RAISE EXCEPTION 'UPDATE legado em pending_actions com atualizado_em NULL falhou ou não preencheu o snapshot';
  END IF;
END $$;
-- A 0001 também adiciona uma CHECK constraint que valida
-- investigacao_instante_operacional(atualizado_em) em toda linha nova ou
-- atualizada; ela por si só já impediria qualquer INSERT/UPDATE com um
-- timestamp inválido a partir de agora. Para provar que a EXCEPTION do
-- trigger continua de pé para o caso de uma linha legada que já carregasse
-- um timestamp inválido antes da constraint existir, removemos a constraint
-- temporariamente, produzimos esse estado antigo, testamos o trigger e
-- restauramos a constraint validada ao final.
ALTER TABLE public.operation_drafts
  DROP CONSTRAINT operation_drafts_atualizado_em_operacional;
ALTER TABLE public.pending_actions
  DROP CONSTRAINT pending_actions_atualizado_em_operacional;
INSERT INTO public.operation_drafts
  (id, agente, status, tipo_operacao, entidade_final_tipo, dados_extraidos,
   campos_pendentes, inferencias, escopo, atualizado_em)
VALUES ('cccccccc-cccc-4ccc-8ccc-ccccccccccc3', 'teste', 'em_revisao',
  'compra', 'compras', '{}', '{}', '{}', 'teste_backfill_atualizado_em_nulo',
  timestamptz '1990-01-01 00:00:00+00');
INSERT INTO public.pending_actions
  (id, agente, canal, acao_tipo, entidade_tipo, resumo, payload, status,
   escopo, atualizado_em)
VALUES ('cccccccc-cccc-4ccc-8ccc-ccccccccccc4', 'teste', 'teste',
  'revisar_consolidacao_negocio', 'operation_draft',
  'fluxo legado sintético com atualizado_em de 1990', '{}',
  'aguardando_confirmacao', 'teste_backfill_atualizado_em_nulo',
  timestamptz '1990-01-01 00:00:00+00');
SET ROLE authenticated;
DO $$ BEGIN
  BEGIN
    UPDATE public.operation_drafts SET status = 'confirmado'
     WHERE id = 'cccccccc-cccc-4ccc-8ccc-ccccccccccc3';
    RAISE EXCEPTION 'UPDATE em operation_drafts com atualizado_em de 1990 deveria ter sido rejeitado';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%Snapshot temporal inválido%' THEN RAISE; END IF;
  END;
  BEGIN
    UPDATE public.pending_actions SET status = 'executado'
     WHERE id = 'cccccccc-cccc-4ccc-8ccc-ccccccccccc4';
    RAISE EXCEPTION 'UPDATE em pending_actions com atualizado_em de 1990 deveria ter sido rejeitado';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%Snapshot temporal inválido%' THEN RAISE; END IF;
  END;
END $$;
RESET ROLE;
DELETE FROM public.operation_drafts
 WHERE escopo = 'teste_backfill_atualizado_em_nulo';
DELETE FROM public.pending_actions
 WHERE escopo = 'teste_backfill_atualizado_em_nulo';
ALTER TABLE public.operation_drafts
  ADD CONSTRAINT operation_drafts_atualizado_em_operacional
  CHECK (public.investigacao_instante_operacional(atualizado_em));
ALTER TABLE public.pending_actions
  ADD CONSTRAINT pending_actions_atualizado_em_operacional
  CHECK (public.investigacao_instante_operacional(atualizado_em));
"""
    resultado = psql(banco, sql)
    if resultado.returncode:
        raise RuntimeError(
            "Falha no backfill de atualizado_em NULL legado: "
            f"{erro_comando(resultado)}"
        )


def testar_executor_legado_operacional(banco: str, *, apos_rollback: bool) -> None:
    """Prova o contrato persistido pelo executor legado nos quatro destinos."""
    sufixo = "ROLLBACK" if apos_rollback else "SOMBRA"
    final = "2" if apos_rollback else "1"
    compras_promocao = f"80810000-0000-4000-8000-00000000000{final}"
    vendas_promocao = f"80810000-0000-4000-8000-00000000001{final}"
    pesagens_promocao = f"80810000-0000-4000-8000-00000000002{final}"
    abates_promocao = f"80810000-0000-4000-8000-00000000003{final}"
    base_uuid = "8082" if apos_rollback else "8081"
    valor_venda = 21 if apos_rollback else 11
    valor_pesagem = 22 if apos_rollback else 12
    valor_abate = 23 if apos_rollback else 13
    sql = f"""
SET ROLE service_role;
INSERT INTO public.compras
  (id, operacao_id, data, quantidade, valor_total, idempotency_key)
VALUES ({sql_texto(base_uuid + '0000-0000-4000-8000-000000000001')}::uuid,
        {sql_texto('LEGADO-' + sufixo)}, DATE '2026-08-29', 1,
        {'20' if apos_rollback else '10'},
        {sql_texto('promocao_operacional:' + compras_promocao)});
INSERT INTO public.vendas
  (id, operacao_id, data, quantidade, valor_total, promocao_origem_id)
VALUES ({sql_texto(base_uuid + '0000-0000-4000-8000-000000000002')}::uuid,
        {sql_texto('VENDA-' + sufixo)}, DATE '2026-08-29', 1, {valor_venda},
        {sql_texto(vendas_promocao)}::uuid);
INSERT INTO public.pesagens_caderno
  (id, operacao_id, data, quantidade, valor_total, promocao_origem_id)
VALUES ({sql_texto(base_uuid + '0000-0000-4000-8000-000000000003')}::uuid,
        {sql_texto('PESAGEM-' + sufixo)}, DATE '2026-08-29', 1, {valor_pesagem},
        {sql_texto(pesagens_promocao)}::uuid);
INSERT INTO public.abates
  (id, operacao_id, data, quantidade, valor_total, promocao_origem_id)
VALUES ({sql_texto(base_uuid + '0000-0000-4000-8000-000000000004')}::uuid,
        {sql_texto('ABATE-' + sufixo)}, DATE '2026-08-29', 1, {valor_abate},
        {sql_texto(abates_promocao)}::uuid);
INSERT INTO public.eventos
  (tipo, agente, entidade_tipo, entidade_id, origem, status, dados)
SELECT 'promocao_operacional_executada', 'executor-legado', item.tipo,
       item.entidade_id, 'teste', 'executado',
       jsonb_build_object('pending_action_id', item.promocao_id)
  FROM (VALUES
    ('compras', {sql_texto(base_uuid + '0000-0000-4000-8000-000000000001')}::uuid,
      {sql_texto(compras_promocao)}::uuid),
    ('vendas', {sql_texto(base_uuid + '0000-0000-4000-8000-000000000002')}::uuid,
      {sql_texto(vendas_promocao)}::uuid),
    ('pesagens_caderno', {sql_texto(base_uuid + '0000-0000-4000-8000-000000000003')}::uuid,
      {sql_texto(pesagens_promocao)}::uuid),
    ('abates', {sql_texto(base_uuid + '0000-0000-4000-8000-000000000004')}::uuid,
      {sql_texto(abates_promocao)}::uuid)
  ) AS item(tipo, entidade_id, promocao_id);
RESET ROLE;
DO $$ BEGIN
  IF (SELECT count(*) FROM public.pending_actions
       WHERE id IN ({sql_texto(compras_promocao)}::uuid,
                    {sql_texto(vendas_promocao)}::uuid,
                    {sql_texto(pesagens_promocao)}::uuid,
                    {sql_texto(abates_promocao)}::uuid)) <> 4
     OR NOT EXISTS (SELECT 1 FROM public.compras
          WHERE idempotency_key={sql_texto('promocao_operacional:' + compras_promocao)}
            AND to_jsonb(compras) @> (SELECT payload->'proposed_record'
              FROM public.pending_actions
             WHERE id={sql_texto(compras_promocao)}::uuid))
     OR NOT EXISTS (SELECT 1 FROM public.vendas
          WHERE promocao_origem_id={sql_texto(vendas_promocao)}::uuid
            AND to_jsonb(vendas) @> (SELECT payload->'proposed_record'
              FROM public.pending_actions
             WHERE id={sql_texto(vendas_promocao)}::uuid))
     OR NOT EXISTS (SELECT 1 FROM public.pesagens_caderno
          WHERE promocao_origem_id={sql_texto(pesagens_promocao)}::uuid
            AND to_jsonb(pesagens_caderno) @> (SELECT payload->'proposed_record'
              FROM public.pending_actions
             WHERE id={sql_texto(pesagens_promocao)}::uuid))
     OR NOT EXISTS (SELECT 1 FROM public.abates
          WHERE promocao_origem_id={sql_texto(abates_promocao)}::uuid
            AND to_jsonb(abates) @> (SELECT payload->'proposed_record'
              FROM public.pending_actions
             WHERE id={sql_texto(abates_promocao)}::uuid))
     OR (SELECT count(*) FROM public.eventos
          WHERE tipo='promocao_operacional_executada'
            AND dados->>'pending_action_id' IN (
              {sql_texto(compras_promocao)}, {sql_texto(vendas_promocao)},
              {sql_texto(pesagens_promocao)}, {sql_texto(abates_promocao)})) <> 4 THEN
    RAISE EXCEPTION 'executor legado não persistiu os quatro vínculos e eventos';
  END IF;
END $$;
"""
    resultado = psql(banco, sql)
    if resultado.returncode:
        fase = "após rollback" if apos_rollback else "após somente 0001"
        raise RuntimeError(
            f"Falha no executor legado {fase}: {erro_comando(resultado)}"
        )


def testar_guardiao_operacional_ativado(banco: str) -> None:
    """Com 0002, vínculo legado sem lease deve falhar fechado."""
    sql = r"""
SET ROLE service_role;
DO $$ BEGIN
  BEGIN
    INSERT INTO public.compras
      (operacao_id, data, quantidade, valor_total, idempotency_key)
    VALUES ('LEGADO-ATIVO', DATE '2026-08-29', 1, 30,
            'promocao_operacional:80810000-0000-4000-8000-000000000003');
    RAISE EXCEPTION 'cutover aceitou executor legado sem lease';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%Promoção não possui lease ativo%' THEN RAISE; END IF;
  END;
  IF EXISTS (SELECT 1 FROM public.compras
              WHERE idempotency_key=
                'promocao_operacional:80810000-0000-4000-8000-000000000003') THEN
    RAISE EXCEPTION 'falha fechada deixou compra operacional';
  END IF;
END $$;
RESET ROLE;
"""
    resultado = psql(banco, sql)
    if resultado.returncode:
        raise RuntimeError(
            f"Falha no guardião operacional ativado: {erro_comando(resultado)}"
        )


def testar_sombra_sem_outbox(banco: str) -> None:
    """A fundação OFF não pode reagir a uma promoção terminal legada."""
    sql = r"""
DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM public.investigacao_sucessoes_pendentes) THEN
    RAISE EXCEPTION 'fase sombra possui outbox sem ativação';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_trigger
     WHERE tgrelid='public.pending_actions'::regclass
       AND tgname='pending_actions_sucessoes_promocao_terminal'
       AND NOT tgisinternal
  ) THEN
    RAISE EXCEPTION 'fase sombra instalou trigger terminal';
  END IF;
END $$;
"""
    resultado = psql(banco, sql)
    if resultado.returncode:
        raise RuntimeError(
            f"Falha ao provar sombra sem outbox: {erro_comando(resultado)}"
        )


def preparar_gate_ativacao(banco: str) -> None:
    """Atesta, no banco efêmero, o broker e um adaptador isolado sintéticos."""
    banco_ident = '"' + banco.replace('"', '""') + '"'
    sql = f"""
ALTER DATABASE {banco_ident}
  SET confinex.broker_version_esperada = 'broker-teste-v1';
ALTER DATABASE {banco_ident}
  SET confinex.broker_hash_esperado = '{'a' * 64}';
ALTER DATABASE {banco_ident}
  SET confinex.teste_capacidades_hash = '{'b' * 64}';
INSERT INTO public.investigacao_adaptadores_config (
  adaptador, adaptador_version, artefato_hash, familia_fonte,
  autoridade_fonte, fontes_tipo_permitidas, tabelas_permitidas,
  tabelas_nativas, identidades_permitidas, capacidades, habilitado
) VALUES (
  'outro', 'v1', '{'c' * 64}', 'auxiliar', 'auxiliar',
  ARRAY['b3','outro','planilha']::text[],
  ARRAY['evidencias_negocio','fontes_importacao','negocios_candidatos']::text[],
  '{{}}'::text[], ARRAY['hash_anexo']::text[],
  ARRAY['history','read','search']::text[], true
);
INSERT INTO public.investigacao_adaptador_credenciais (
  adaptador, adaptador_version, chave_id, chave_hmac,
  valida_desde, emite_ate, aceita_ate
) VALUES (
  'outro', 'v1', 'key_teste-runtime', decode(repeat('d', 64), 'hex'),
  clock_timestamp() - interval '1 minute',
  clock_timestamp() + interval '30 minutes',
  clock_timestamp() + interval '60 minutes'
);
INSERT INTO public.investigacao_configuracao_ativacao (
  broker_version, broker_artefato_hash, teste_capacidades_hash,
  atestado_por, adaptadores_isolados, workers_sem_service_role, atestado_em
) VALUES (
  'broker-teste-v1', '{'a' * 64}', '{'b' * 64}',
  session_user, true, true, clock_timestamp()
);
"""
    resultado = psql(banco, sql)
    if resultado.returncode:
        raise RuntimeError(
            f"Falha ao preparar gate de ativação: {erro_comando(resultado)}"
        )


def validar_reversao_ativacao(banco: str) -> None:
    """Confirma que o rollback restaura o contrato legado sem apagar auditoria."""
    sql = r"""
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM pg_trigger
     WHERE tgrelid IN (
       'public.pending_actions'::regclass,
       'public.operation_drafts'::regclass
     )
       AND tgname IN (
         'pending_actions_bloqueia_investigacao',
         'operation_drafts_bloqueia_investigacao'
       )
       AND NOT tgisinternal
  ) THEN
    RAISE EXCEPTION 'rollback manteve guardião do mediador';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_policies
     WHERE schemaname = 'public'
       AND tablename = 'pending_actions'
       AND policyname IN (
         'pending_actions_authenticated_revisoes_select',
         'pending_actions_authenticated_revisoes_insert',
         'pending_actions_authenticated_revisoes_update',
         'pending_actions_authenticated_revisoes_delete'
       )
  ) THEN
    RAISE EXCEPTION 'rollback manteve política segmentada do mediador';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_policies
     WHERE schemaname = 'public'
       AND tablename = 'pending_actions'
       AND policyname = 'pending_actions_authenticated_revisoes'
       AND cmd = 'ALL'
       AND 'authenticated' = ANY (roles)
       AND qual = 'true'
       AND with_check = 'true'
  ) THEN
    RAISE EXCEPTION 'rollback não restaurou exatamente a política ampla anterior';
  END IF;
  IF to_regclass('public.investigacoes_revisao') IS NULL
     OR NOT EXISTS (SELECT 1 FROM public.investigacao_eventos) THEN
    RAISE EXCEPTION 'rollback removeu fundação ou trilha de auditoria';
  END IF;
END $$;
"""
    resultado = psql(banco, sql)
    if resultado.returncode:
        raise RuntimeError(
            f"Falha ao validar rollback da ativação: {erro_comando(resultado)}"
        )


def inserir_fixture_e_validar_guardas(banco: str) -> None:
    plano, plano_hash, manifesto = contrato_plano(
        fonte_ref="pitem_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        sintese_ref="pitem_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        pergunta="valor sintético",
        campos_obrigatorios=["data", "negocio", "quantidade", "valor_total"],
    )
    fonte = next(item for item in manifesto if item["adaptador"] == "outro")
    sintese = next(item for item in manifesto if item["adaptador"] == "sintese")
    plano_campos_invalidos_obj = json.loads(plano)
    plano_campos_invalidos_obj["campos_obrigatorios"] = [""]
    plano_campos_invalidos = json_canonico(plano_campos_invalidos_obj)
    plano_campos_invalidos_hash = hashlib.sha256(
        plano_campos_invalidos.encode("utf-8")
    ).hexdigest()
    consulta_parcial = {"pergunta": "localizar documento sintético"}
    fonte_parcial = selar_fonte_adaptador(
        adaptador="outro",
        versao_adaptador="v1",
        consulta=consulta_parcial,
        cobertura="completa",
        candidatos=[{
            "campo": "quantidade", "valor": 1,
            "tipo_correspondencia": "extracao_llm",
        }],
        linhagem_registrada="fonte-sintetica",
        prova_cobertura={
            "estado": "concluida",
            "inicio_confirmado": True,
            "fim_confirmado": True,
            "consulta_hash": contrato_consulta(
                normalizar_consulta(consulta_parcial)
            )["consulta_hash"],
        },
    )
    plano_parcial = planejar_investigacao(
        {"tipo": "compra", "titulo": "Teste sintético", "contexto_nome": "Contexto sintético"},
        {
            "canal": "teste", "conversa_id": "conversa-sintetica",
            "mensagem_id": "mensagem-sintetica", "linhagem": "fonte-sintetica",
        },
        consulta_parcial,
        fingerprint_base="a" * 64,
        cobertura="completa",
        instante_referencia="2026-08-29T12:00:00Z",
        campos_obrigatorios=["data", "negocio", "quantidade", "valor_total"],
        versao_politica="investigacao-v1",
        fontes=[fonte_parcial],
    )
    alternativa_parcial = plano_parcial["registros"][
        "investigacao_alternativas"
    ][0]
    evidencia_planejada = plano_parcial["registros"][
        "investigacao_evidencias"
    ][0]
    pendencia_planejada = plano_parcial["registros"][
        "investigacao_pendencias"
    ][0]
    alternativa_parcial = {
        chave: alternativa_parcial[chave]
        for chave in (
            "id_logico", "chave_idempotencia", "titulo", "campos_snapshot",
            "confianca_campos", "confianca_geral", "classificacao",
            "regra_confianca_version", "justificativa_sanitizada",
            "origem_modelo",
        )
    }
    evidencia_fonte = {
        "id_logico": evidencia_planejada["id_logico"],
        "fonte_tipo": "outro",
        "fonte_tabela": None,
        "fonte_registro_id": None,
        "registro_origem_ref": evidencia_planejada["registro_origem_ref"],
        "snapshot_fonte_ref": evidencia_planejada["snapshot_fonte_ref"],
        "linhagem": evidencia_planejada["linhagem"],
        "chave_natural_hash": evidencia_planejada["chave_natural_hash"],
        "referencia_opaca": evidencia_planejada["referencia_opaca"],
        "fatos_normalizados": evidencia_planejada["fatos_normalizados"],
        "provas_campos": evidencia_planejada["provas_campos"],
        "provas_campos_canonico": evidencia_planejada[
            "provas_campos_canonico"
        ],
        "provas_campos_hash": evidencia_planejada["provas_campos_hash"],
        "resumo_sanitizado": "Pista sintética encontrada no extrato.",
        "evidenciado_em": "2026-08-29T12:00:00Z",
    }
    pendencia_parcial = {
        "id_logico": pendencia_planejada["id_logico"],
        "chave_idempotencia": pendencia_planejada["chave_idempotencia"],
        "tipo": pendencia_planejada["tipo"],
        "campo": pendencia_planejada["campo"],
        "fonte_tipo": None,
        "descricao_sanitizada": pendencia_planejada["descricao_sanitizada"],
        "estado": pendencia_planejada["estado"],
    }
    ligacao_parcial = {
        "alternativa_id_logico": alternativa_parcial["id_logico"],
        "evidencia_id_logico": evidencia_fonte["id_logico"],
        "evidencia_tarefa_id": "44444444-4444-4444-8444-444444444444",
        "papel": "favoravel",
        "campos_suportados": ["quantidade"],
        "campos_contestados": [],
    }
    sql = r"""
SET ROLE service_role;
INSERT INTO public.negocios_candidatos (id)
VALUES ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa');
INSERT INTO public.operation_drafts
  (id, agente, status, tipo_operacao, entidade_final_tipo, dados_extraidos,
   campos_pendentes, inferencias, pending_action_id, escopo)
VALUES
  ('11111111-1111-4111-8111-111111111111', 'teste', 'em_revisao',
   'consolidacao_compra_planilha', 'compras', '{}', '{}',
   '{"staging_candidato_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"}',
   '22222222-2222-4222-8222-222222222222',
   'teste_runtime');
INSERT INTO public.pending_actions
  (id, agente, usuario_solicitante, canal, acao_tipo, entidade_tipo,
   entidade_id, resumo, payload, status, escopo)
VALUES
  ('22222222-2222-4222-8222-222222222222', 'teste', 'teste', 'teste',
   'revisar_consolidacao_negocio', 'operation_draft',
   '11111111-1111-4111-8111-111111111111', 'Revisão sintética', '{}',
   'aguardando_confirmacao', 'teste_runtime');
DO $$
DECLARE
  v_plano jsonb := __PLANO__::jsonb -> 'tarefas';
  v_campo text;
BEGIN
  FOREACH v_campo IN ARRAY ARRAY[
    'adaptador', 'adaptador_version', 'consulta_schema_version'
  ] LOOP
    IF public.investigacao_plano_tarefas_valido(
      jsonb_set(v_plano, ARRAY['0', v_campo], 'null'::jsonb)
    ) THEN
      RAISE EXCEPTION 'plano aceitou campo obrigatório NULL: %', v_campo;
    END IF;
  END LOOP;
END $$;
INSERT INTO public.investigacoes_revisao
  (id, chave_idempotencia, assunto_tipo, titulo, fingerprint_base,
   plano_hash, plano_canonico, plano_tarefas, policy_version,
   policy_schema_hash, campos_obrigatorios,
   source_draft_id, source_draft_atualizado_em, escopo)
VALUES (
   '33333333-3333-4333-8333-333333333333', 'teste-runtime-investigacao',
   'compra', 'Investigação sintética', repeat('a', 64),
   __PLANO_HASH__, __PLANO__, __PLANO__::jsonb -> 'tarefas', 'investigacao-v1',
   public.investigacao_politica_schema_hash('investigacao-v1'),
   ARRAY['data','negocio','quantidade','valor_total'],
   '11111111-1111-4111-8111-111111111111',
   (SELECT atualizado_em FROM public.operation_drafts
     WHERE id = '11111111-1111-4111-8111-111111111111'), 'teste_runtime'
);
DO $$
BEGIN
  BEGIN
    INSERT INTO public.investigacoes_revisao
      (id, chave_idempotencia, assunto_tipo, titulo, fingerprint_base,
       plano_hash, plano_canonico, plano_tarefas, policy_version,
       policy_schema_hash,
       campos_obrigatorios, escopo)
    VALUES (
      '33333333-3333-4333-8333-333333333334',
      'teste-runtime-campo-obrigatorio-vazio', 'compra',
      'Investigação sintética inválida', repeat('d', 64),
      __PLANO_INVALIDO_HASH__, __PLANO_INVALIDO__,
      __PLANO_INVALIDO__::jsonb -> 'tarefas', 'investigacao-v1',
      public.investigacao_politica_schema_hash('investigacao-v1'), ARRAY[''],
      'teste_runtime'
    );
    RAISE EXCEPTION 'campo obrigatório vazio foi persistido';
  EXCEPTION WHEN check_violation THEN
    NULL;
  END;
  BEGIN
    INSERT INTO public.investigacoes_revisao
      (id, chave_idempotencia, assunto_tipo, titulo, negocio_candidato_id,
       negocio_candidato_ids, source_candidato_atualizado_em,
       source_candidatos_atualizados_em, fingerprint_base, plano_hash,
       plano_canonico, plano_tarefas, policy_version, policy_schema_hash,
       campos_obrigatorios,
       escopo)
    VALUES (
      '33333333-3333-4333-8333-333333333335',
      'teste-runtime-principal-nulo', 'compra',
      'Investigação sintética sem principal', NULL,
      ARRAY['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid], now(),
      jsonb_build_object('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', now()),
      repeat('e', 64), __PLANO_HASH__, __PLANO__,
      __PLANO__::jsonb -> 'tarefas', 'investigacao-v1',
      public.investigacao_politica_schema_hash('investigacao-v1'),
      ARRAY['data','negocio','quantidade','valor_total'],
      'teste_runtime'
    );
    RAISE EXCEPTION 'grupo não vazio aceitou candidato principal NULL';
  EXCEPTION WHEN check_violation THEN
    NULL;
  END;
  BEGIN
    INSERT INTO public.investigacoes_revisao
      (id, chave_idempotencia, assunto_tipo, titulo, negocio_candidato_id,
       negocio_candidato_ids, source_candidato_atualizado_em,
       source_candidatos_atualizados_em, fingerprint_base, plano_hash,
       plano_canonico, plano_tarefas, policy_version, policy_schema_hash,
       campos_obrigatorios,
       escopo)
    VALUES (
      '33333333-3333-4333-8333-333333333336',
      'teste-runtime-snapshot-invalido', 'compra',
      'Investigação sintética com instante inválido',
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      ARRAY['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid], now(),
      '{"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa":"instante-invalido"}',
      repeat('f', 64), __PLANO_HASH__, __PLANO__,
      __PLANO__::jsonb -> 'tarefas', 'investigacao-v1',
      public.investigacao_politica_schema_hash('investigacao-v1'),
      ARRAY['data','negocio','quantidade','valor_total'],
      'teste_runtime'
    );
    RAISE EXCEPTION 'snapshot inválido de candidato foi persistido';
  EXCEPTION WHEN check_violation THEN
    NULL;
  END;
END $$;
INSERT INTO public.investigacao_tarefas
  (id, investigacao_id, chave_idempotencia, plano_item_ref, adaptador, consulta_ref,
   consulta_schema_version, consulta_spec, consulta_canonico, consulta_hash,
   adaptador_version)
VALUES
  ('44444444-4444-4444-8444-444444444444',
   '33333333-3333-4333-8333-333333333333', 'teste-runtime-tarefa',
   'pitem_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 'outro', __CONSULTA_REF__,
   __CONSULTA_SCHEMA__, __CONSULTA_SPEC__::jsonb, __CONSULTA_CANONICO__,
   __CONSULTA_HASH__, 'v1');

DO $$
BEGIN
  BEGIN
    UPDATE public.investigacao_tarefas
       SET consulta_ref = 'qref_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
     WHERE id = '44444444-4444-4444-8444-444444444444';
    RAISE EXCEPTION 'service_role recebeu UPDATE direto na tarefa';
  EXCEPTION WHEN insufficient_privilege THEN
    NULL;
  END;
  BEGIN
    INSERT INTO public.pending_actions
      (id, agente, canal, acao_tipo, entidade_tipo, entidade_id, resumo,
       payload, status, escopo)
    VALUES ('55555555-5555-4555-8555-555555555555', 'teste', 'teste',
      'promover_revisao_operacional', 'operation_draft',
      '11111111-1111-4111-8111-111111111111', 'promoção proibida', '{}',
      'aguardando_confirmacao', 'teste_runtime');
    RAISE EXCEPTION 'promoção sem rascunho explícito não foi bloqueada';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%rascunho de origem explícito%' THEN RAISE; END IF;
  END;
  BEGIN
    INSERT INTO public.pending_actions
      (id, agente, canal, acao_tipo, entidade_tipo, entidade_id, resumo,
       payload, status, escopo)
    VALUES ('55555555-5555-4555-8555-555555555556', 'teste', 'teste',
      'promover_revisao_operacional', 'compras',
      '11111111-1111-4111-8111-111111111111', 'promoção proibida',
      '{"source_draft_id":"11111111-1111-4111-8111-111111111111"}',
      'aguardando_confirmacao', 'teste_runtime');
    RAISE EXCEPTION 'promoção sem controle lease-v1 não foi bloqueada';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%controle concorrente lease-v1%' THEN RAISE; END IF;
  END;
  BEGIN
    UPDATE public.investigacoes_revisao
       SET source_draft_atualizado_em = source_draft_atualizado_em + interval '1 second'
     WHERE id = '33333333-3333-4333-8333-333333333333';
    RAISE EXCEPTION 'service_role recebeu UPDATE direto na investigação';
  EXCEPTION WHEN insufficient_privilege THEN
    NULL;
  END;
  BEGIN
    UPDATE public.operation_drafts
       SET dados_extraidos = '{"status_confirmacao":"promocao_preparada"}'
     WHERE id = '11111111-1111-4111-8111-111111111111';
    RAISE EXCEPTION 'guardião do rascunho não bloqueou promoção';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%investigação precisa terminar e ser anexada%' THEN RAISE; END IF;
  END;
END $$;
RESET ROLE;
DO $$
BEGIN
  BEGIN
    UPDATE public.investigacao_tarefas
       SET consulta_ref = 'qref_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb'
     WHERE id = '44444444-4444-4444-8444-444444444444';
    RAISE EXCEPTION 'imutabilidade da consulta não bloqueou o proprietário';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%consulta assumida por um worker é imutável%' THEN RAISE; END IF;
  END;
  BEGIN
    UPDATE public.investigacoes_revisao
       SET source_draft_atualizado_em = source_draft_atualizado_em + interval '1 second'
     WHERE id = '33333333-3333-4333-8333-333333333333';
    RAISE EXCEPTION 'snapshot temporal do draft pôde ser alterado pelo proprietário';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%snapshot temporal não podem ser trocados%' THEN RAISE; END IF;
  END;
END $$;
SET ROLE service_role;
SELECT public.assumir_tarefa_investigacao('outro', 'worker-fonte', 60);
RESET ROLE;
DO $$
DECLARE
  v_tarefa public.investigacao_tarefas%ROWTYPE;
  v_bundle jsonb := jsonb_build_object(
    'evidencias', jsonb_build_array(__EVIDENCIA_FONTE__::jsonb),
    'alternativas', '[]'::jsonb,
    'pendencias', '[]'::jsonb,
    'ligacoes', '[]'::jsonb
  );
  v_pedido_hash text;
  v_metadados jsonb;
  v_atestado jsonb;
BEGIN
  SELECT * INTO STRICT v_tarefa
    FROM public.investigacao_tarefas
   WHERE id = '44444444-4444-4444-8444-444444444444';
  v_pedido_hash := encode(extensions.digest(convert_to(
    public.investigacao_json_canonico(jsonb_build_object(
      'estado_cobertura', 'completa',
      'estado_resultado', 'evidencia_insuficiente',
      'bundle', v_bundle,
      'resumo_sanitizado', 'fonte sintética coberta',
      'erro_codigo', NULL,
      'erro_sanitizado', NULL
    )), 'UTF8'
  ), 'sha256'), 'hex');
  v_metadados := jsonb_build_object(
    'schema_version', 'cobertura-hmac-v1',
    'chave_id', 'key_teste-runtime',
    'adaptador', 'outro',
    'adaptador_version', 'v1',
    'artefato_hash', repeat('c', 64),
    'familia_fonte', 'auxiliar',
    'consulta_hash', v_tarefa.consulta_hash,
    'consulta_ref', v_tarefa.consulta_ref,
    'tarefa_id', v_tarefa.id::text,
    'investigacao_id', v_tarefa.investigacao_id::text,
    'lease_token', v_tarefa.lease_token::text,
    'fencing_token', v_tarefa.fencing_token::text,
    'estado_cobertura', 'completa',
    'estado_resultado', 'evidencia_insuficiente',
    'inicio_confirmado', true,
    'fim_confirmado', true,
    'paginas_confirmadas', 1,
    'registros_confirmados', 1,
    'paginacao_modo', 'nao_paginado',
    'artefato_cobertura_tipo', 'snapshot_fonte',
    'cursor_final_hash', NULL,
    'snapshot_fonte_hash', repeat('e', 64),
    'pedido_hash', v_pedido_hash
  );
  v_atestado := v_metadados || jsonb_build_object(
    'hmac', encode(extensions.hmac(
      convert_to(public.investigacao_json_canonico(v_metadados), 'UTF8'),
      decode(repeat('d', 64), 'hex'), 'sha256'
    ), 'hex')
  );
  EXECUTE 'SET LOCAL ROLE service_role';
  PERFORM public.publicar_resultado_tarefa_investigacao(
    v_tarefa.id, v_tarefa.lease_token, v_tarefa.fencing_token,
    'completa', 'evidencia_insuficiente',
    v_bundle, v_atestado,
    'fonte sintética coberta'
  );
END $$;
SET ROLE service_role;
DO $$
BEGIN
  IF public.assumir_tarefa_investigacao('sintese', 'worker-sem-manifesto', 60) IS NOT NULL THEN
    RAISE EXCEPTION 'síntese foi assumida antes de o manifesto estar materializado';
  END IF;
END $$;
INSERT INTO public.investigacao_tarefas
  (id, investigacao_id, chave_idempotencia, plano_item_ref, adaptador,
   consulta_ref, consulta_schema_version, consulta_spec, consulta_canonico,
   consulta_hash, adaptador_version)
VALUES
  ('44444444-4444-4444-8444-444444444445',
   '33333333-3333-4333-8333-333333333333', 'teste-runtime-sintese',
   'pitem_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb', 'sintese',
   __SINT_REF__, __SINT_SCHEMA__, __SINT_SPEC__::jsonb, __SINT_CANONICO__,
   __SINT_HASH__, 'investigacao-v1');
SELECT public.assumir_tarefa_investigacao('sintese', 'worker-sintese', 60);
DO $$
DECLARE
  v_tarefa public.investigacao_tarefas%ROWTYPE;
BEGIN
  SELECT * INTO STRICT v_tarefa
    FROM public.investigacao_tarefas
   WHERE id = '44444444-4444-4444-8444-444444444445';
  BEGIN
    PERFORM public.publicar_resultado_tarefa_investigacao(
      v_tarefa.id, v_tarefa.lease_token, v_tarefa.fencing_token,
      'cobertura_incompleta', 'cobertura_incompleta',
      '{"evidencias":[],"alternativas":[],"pendencias":[],"ligacoes":[]}'::jsonb,
      NULL,
      'síntese inválida'
    );
    RAISE EXCEPTION 'síntese aceitou cobertura diferente das fontes';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%cobertura da síntese deve refletir todas as fontes%' THEN
      RAISE;
    END IF;
  END;
  BEGIN
    PERFORM public.publicar_resultado_tarefa_investigacao(
      v_tarefa.id, v_tarefa.lease_token, v_tarefa.fencing_token,
      'completa', 'alternativa_unica',
      jsonb_build_object(
        'evidencias', '[]'::jsonb,
        'alternativas', jsonb_build_array(__ALTERNATIVA_PARCIAL__::jsonb),
        'pendencias', '[]'::jsonb,
        'ligacoes', jsonb_build_array(__LIGACAO_PARCIAL__::jsonb)
      ),
      NULL,
      'síntese parcial indevidamente conclusiva'
    );
    RAISE EXCEPTION 'alternativa parcial encerrou como conclusiva';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%Resultado declarado não corresponde ao estado derivado das provas%' THEN
      RAISE;
    END IF;
  END;
  PERFORM public.publicar_resultado_tarefa_investigacao(
    v_tarefa.id, v_tarefa.lease_token, v_tarefa.fencing_token,
    'completa', 'evidencia_insuficiente',
    jsonb_build_object(
      'evidencias', '[]'::jsonb,
      'alternativas', jsonb_build_array(__ALTERNATIVA_PARCIAL__::jsonb),
      'pendencias', jsonb_build_array(__PENDENCIA_PARCIAL__::jsonb),
      'ligacoes', jsonb_build_array(__LIGACAO_PARCIAL__::jsonb)
    ),
    NULL,
    'síntese parcial controlada'
  );
END $$;

DO $$
BEGIN
  BEGIN
    UPDATE public.operation_drafts
       SET status = NULL
     WHERE id = '11111111-1111-4111-8111-111111111111';
    PERFORM public.anexar_investigacao_revisao(
      '33333333-3333-4333-8333-333333333333'
    );
    RAISE EXCEPTION 'anexo aceitou status NULL do rascunho';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT ILIKE '%revisão já foi encerrada%' THEN
      RAISE;
    END IF;
  END;
END $$;
SELECT public.anexar_investigacao_revisao(
  '33333333-3333-4333-8333-333333333333'
);
DO $$
DECLARE
  v_pedido jsonb;
BEGIN
  -- Colunas legadas são anuláveis. Cada NULL abaixo precisa falhar fechado,
  -- antes de criar ação/evento de promoção.
  UPDATE public.pending_actions
     SET status = NULL
   WHERE id = '22222222-2222-4222-8222-222222222222';
  SELECT jsonb_build_object(
    'versao', 1,
    'target_table', 'compras',
    'source_draft_atualizado_em', draft.atualizado_em,
    'source_pending_action_atualizado_em', acao.atualizado_em,
    'codigo_sugerido', NULL,
    'dados_revisados', '{}'::jsonb,
    'inferencias', '{}'::jsonb,
    'campos_pendentes', '[]'::jsonb,
    'proposed_record', jsonb_build_object(
      'operacao_id', 'OP-TESTE', 'data', '2026-08-29',
      'quantidade', 1, 'valor_total', 100
    )
  ) INTO v_pedido
    FROM public.operation_drafts draft
    JOIN public.pending_actions acao
      ON acao.id = draft.pending_action_id
   WHERE draft.id = '11111111-1111-4111-8111-111111111111';
  BEGIN
    PERFORM public.preparar_promocao_revisao_investigada(
      '11111111-1111-4111-8111-111111111111',
      '22222222-2222-4222-8222-222222222222', v_pedido
    );
    RAISE EXCEPTION 'preparação aceitou status NULL da pendência-fonte';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT ILIKE '%revisão já foi encerrada ou possui promoção preparada%' THEN
      RAISE;
    END IF;
  END;

  UPDATE public.pending_actions
     SET status = 'aguardando_confirmacao', acao_tipo = NULL
   WHERE id = '22222222-2222-4222-8222-222222222222';
  SELECT jsonb_set(
    jsonb_set(v_pedido, '{source_draft_atualizado_em}', to_jsonb(draft.atualizado_em)),
    '{source_pending_action_atualizado_em}', to_jsonb(acao.atualizado_em)
  ) INTO v_pedido
    FROM public.operation_drafts draft
    JOIN public.pending_actions acao
      ON acao.id = draft.pending_action_id
   WHERE draft.id = '11111111-1111-4111-8111-111111111111';
  BEGIN
    PERFORM public.preparar_promocao_revisao_investigada(
      '11111111-1111-4111-8111-111111111111',
      '22222222-2222-4222-8222-222222222222', v_pedido
    );
    RAISE EXCEPTION 'preparação aceitou tipo NULL da pendência-fonte';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT ILIKE '%revisão já foi encerrada ou possui promoção preparada%' THEN
      RAISE;
    END IF;
  END;

  UPDATE public.pending_actions
     SET acao_tipo = 'revisar_consolidacao_negocio'
   WHERE id = '22222222-2222-4222-8222-222222222222';
  UPDATE public.operation_drafts
     SET status = NULL
   WHERE id = '11111111-1111-4111-8111-111111111111';
  SELECT jsonb_set(
    jsonb_set(v_pedido, '{source_draft_atualizado_em}', to_jsonb(draft.atualizado_em)),
    '{source_pending_action_atualizado_em}', to_jsonb(acao.atualizado_em)
  ) INTO v_pedido
    FROM public.operation_drafts draft
    JOIN public.pending_actions acao
      ON acao.id = draft.pending_action_id
   WHERE draft.id = '11111111-1111-4111-8111-111111111111';
  BEGIN
    PERFORM public.preparar_promocao_revisao_investigada(
      '11111111-1111-4111-8111-111111111111',
      '22222222-2222-4222-8222-222222222222', v_pedido
    );
    RAISE EXCEPTION 'preparação aceitou status NULL do rascunho';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT ILIKE '%revisão já foi encerrada ou possui promoção preparada%' THEN
      RAISE;
    END IF;
  END;
  UPDATE public.operation_drafts
     SET status = 'em_revisao'
   WHERE id = '11111111-1111-4111-8111-111111111111';
END $$;
RESET ROLE;

SET ROLE authenticated;
DO $$
BEGIN
  BEGIN
    INSERT INTO public.pending_actions
      (id, agente, canal, acao_tipo, entidade_tipo, entidade_id, resumo,
       payload, status, escopo)
    VALUES ('aaaaaaaa-1111-4111-8111-111111111111', 'teste', 'teste',
      'promover_revisao_operacional', 'compras',
      '11111111-1111-4111-8111-111111111111', 'atalho proibido',
      '{"source_draft_id":"11111111-1111-4111-8111-111111111111"}',
      'aguardando_confirmacao', 'teste_runtime');
    RAISE EXCEPTION 'authenticated conseguiu inserir promoção direta';
  EXCEPTION WHEN insufficient_privilege THEN
    NULL;
  END;
END $$;
INSERT INTO public.pending_actions
  (agente, canal, acao_tipo, entidade_tipo, resumo, payload, status, escopo)
VALUES ('teste', 'teste',
  'revisar_consolidacao_negocio', 'operation_draft', 'pendência comum', '{}',
  'aguardando_confirmacao', 'teste_runtime');
RESET ROLE;
"""
    substituicoes = {
        "__PLANO_HASH__": sql_texto(plano_hash),
        "__PLANO__": sql_texto(plano),
        "__PLANO_INVALIDO_HASH__": sql_texto(plano_campos_invalidos_hash),
        "__PLANO_INVALIDO__": sql_texto(plano_campos_invalidos),
        "__CONSULTA_REF__": sql_texto(str(fonte["consulta_ref"])),
        "__CONSULTA_SCHEMA__": sql_texto(str(fonte["consulta_schema_version"])),
        "__CONSULTA_SPEC__": sql_texto(json_canonico(fonte["consulta_spec"])),
        "__CONSULTA_CANONICO__": sql_texto(str(fonte["consulta_canonico"])),
        "__CONSULTA_HASH__": sql_texto(str(fonte["consulta_hash"])),
        "__SINT_REF__": sql_texto(str(sintese["consulta_ref"])),
        "__SINT_SCHEMA__": sql_texto(str(sintese["consulta_schema_version"])),
        "__SINT_SPEC__": sql_texto(json_canonico(sintese["consulta_spec"])),
        "__SINT_CANONICO__": sql_texto(str(sintese["consulta_canonico"])),
        "__SINT_HASH__": sql_texto(str(sintese["consulta_hash"])),
        "__ALTERNATIVA_PARCIAL__": sql_texto(
            json_canonico(alternativa_parcial)
        ),
        "__EVIDENCIA_FONTE__": sql_texto(json_canonico(evidencia_fonte)),
        "__PENDENCIA_PARCIAL__": sql_texto(json_canonico(pendencia_parcial)),
        "__LIGACAO_PARCIAL__": sql_texto(json_canonico(ligacao_parcial)),
    }
    for marcador, valor in substituicoes.items():
        sql = sql.replace(marcador, valor)
    resultado = psql(banco, sql)
    if resultado.returncode:
        raise RuntimeError(f"Falha nas inserções/guardas sintéticas: {erro_comando(resultado)}")


def testar_sucessoes_terminal_sem_gravacao(banco: str) -> None:
    """Exercita outbox real, draft+candidato e retry sem consultar fonte viva."""
    plano, plano_hash, _ = contrato_plano(
        fonte_ref="pitem_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        sintese_ref="pitem_ffffffffffffffffffffffffffffffff",
        pergunta="complemento terminal sintético",
        campos_obrigatorios=["data", "negocio", "quantidade", "valor_total"],
    )
    sql = r"""
RESET ROLE;
ALTER TABLE public.pending_actions
  DISABLE TRIGGER pending_actions_bloqueia_investigacao;
DO $$
DECLARE
  v_promocao_id uuid := '90910000-0000-4000-8000-000000000020';
  v_pedido_hash text;
  v_primeiro jsonb;
  v_repetido jsonb;
  v_filha uuid;
BEGIN
  -- A capacidade sintética reproduz exatamente a fronteira da RPC canônica,
  -- sem depender do fixture anterior ter conservado o timestamp do anexo.
  INSERT INTO public.investigacao_autorizacoes_promocao (
    txid, backend_pid, pending_action_id, operacao, status_anterior, status_novo
  ) VALUES (
    txid_current(), pg_backend_pid(), v_promocao_id, 'INSERT', NULL,
    'aguardando_confirmacao'
  );
  EXECUTE 'SET LOCAL ROLE service_role';
  -- A API obsoleta dedicada deve falhar antes de materializar qualquer linha.
  BEGIN
    PERFORM public.substituir_investigacao_corretiva_stale(
      '90910000-0000-4000-8000-000000000001',
      'snp_' || repeat('1', 32), 'snp_' || repeat('2', 32),
      'teste-runtime', 'fonte mudou'
    );
    RAISE EXCEPTION 'stale dedicada clonou antes de materialização';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%Replanejamento explícito é necessário%' THEN RAISE; END IF;
  END;
  INSERT INTO public.pending_actions (
    id, agente, usuario_solicitante, canal, acao_tipo, entidade_tipo,
    entidade_id, resumo, payload, status, origem_canal,
    origem_conversa_id, origem_mensagem_id, escopo,
    promocao_controle_version, promocao_preparacao_chave,
    promocao_preparacao_hash
  ) VALUES (
    v_promocao_id, 'teste', 'teste', 'teste',
    'promover_revisao_operacional', 'compras',
    '11111111-1111-4111-8111-111111111111', 'promoção terminal sintética',
    jsonb_build_object(
      'source_draft_id', '11111111-1111-4111-8111-111111111111',
      'source_pending_action_id', '22222222-2222-4222-8222-222222222222',
      'target_table', 'compras',
      'proposed_record', jsonb_build_object(
        'operacao_id', 'OP-TERMINAL-SEM-GRAVACAO', 'data', '2026-08-29',
        'quantidade', 1, 'valor_total', 100
      )
    ), 'aguardando_confirmacao', 'teste', 'conversa-terminal',
    'mensagem-terminal', 'teste_terminal', 'lease-v1',
    'preparacao-terminal', repeat('a',64)
  );

  -- Uma complementar ligada ao draft e outra exclusivamente ao candidato.
  INSERT INTO public.investigacoes_revisao (
    id, chave_idempotencia, assunto_tipo, titulo, fingerprint_base,
    plano_hash, plano_canonico, plano_tarefas, policy_version,
    policy_schema_hash, campos_obrigatorios, source_draft_id,
    source_draft_atualizado_em, negocio_candidato_id, negocio_candidato_ids,
    source_candidato_atualizado_em, source_candidatos_atualizados_em, escopo
  ) VALUES (
    '90910000-0000-4000-8000-000000000010', 'terminal-draft', 'compra',
    'Complementar do rascunho', repeat('1',64), __PLANO_HASH__, __PLANO__,
    __PLANO__::jsonb->'tarefas', 'investigacao-v1',
    public.investigacao_politica_schema_hash('investigacao-v1'),
    ARRAY['data','negocio','quantidade','valor_total'],
    '11111111-1111-4111-8111-111111111111',
    (SELECT atualizado_em FROM public.operation_drafts WHERE id='11111111-1111-4111-8111-111111111111'),
    NULL, '{}'::uuid[], NULL, '{}'::jsonb, 'teste_terminal'
  ), (
    '90910000-0000-4000-8000-000000000011', 'terminal-candidato', 'compra',
    'Complementar apenas do candidato', repeat('2',64), __PLANO_HASH__, __PLANO__,
    __PLANO__::jsonb->'tarefas', 'investigacao-v1',
    public.investigacao_politica_schema_hash('investigacao-v1'),
    ARRAY['data','negocio','quantidade','valor_total'], NULL, NULL,
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    ARRAY['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid],
    (SELECT atualizado_em FROM public.negocios_candidatos WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
    jsonb_build_object('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      (SELECT atualizado_em FROM public.negocios_candidatos WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')),
    'teste_terminal'
  );
  IF (SELECT count(*) FROM public.investigacoes_revisao
       WHERE promocao_ativa_id=v_promocao_id
         AND obsolescencia_motivo='complementar_promocao_ativa') <> 2 THEN
    RAISE EXCEPTION 'complementares draft/candidate-only não foram serializadas';
  END IF;

  PERFORM public.decidir_promocao_operacional(
    v_promocao_id, 'aguardando_confirmacao', 'rejeitado',
    'teste-runtime', 'decisão terminal sintética'
  );
  SELECT pedido_hash INTO STRICT v_pedido_hash
    FROM public.listar_sucessoes_promocao_terminal_pendentes(100)
   WHERE promocao_id=v_promocao_id;
  v_primeiro := public.consumir_sucessoes_promocao_terminal(
    v_promocao_id, v_pedido_hash, 'teste-runtime'
  );
  IF coalesce((v_primeiro->>'processada')::boolean,false) IS NOT TRUE
     OR (v_primeiro->>'criadas')::integer <> 2 THEN
    RAISE EXCEPTION 'consumo sem gravação não criou duas sucessoras: %', v_primeiro;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.investigacoes_revisao
     WHERE sucessora_de_id='90910000-0000-4000-8000-000000000010'
       AND source_draft_id='11111111-1111-4111-8111-111111111111'
  ) OR NOT EXISTS (
    SELECT 1 FROM public.investigacoes_revisao
     WHERE sucessora_de_id='90910000-0000-4000-8000-000000000011'
       AND source_draft_id IS NULL
       AND negocio_candidato_id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
  ) THEN
    RAISE EXCEPTION 'sucessoras não preservaram draft/candidate-only';
  END IF;

  -- A repetição deve usar o mapa persistido, mesmo com a fonte já alterada.
  UPDATE public.negocios_candidatos SET atualizado_em=clock_timestamp()
   WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  v_repetido := public.consumir_sucessoes_promocao_terminal(
    v_promocao_id, v_pedido_hash, 'teste-runtime'
  );
  IF coalesce((v_repetido->>'repetida')::boolean,false) IS NOT TRUE
     OR v_repetido->'sucessoras' IS DISTINCT FROM v_primeiro->'sucessoras' THEN
    RAISE EXCEPTION 'retry não retornou mapa persistido idêntico: %', v_repetido;
  END IF;

  SELECT id INTO STRICT v_filha FROM public.investigacoes_revisao
   WHERE sucessora_de_id IN (
     '90910000-0000-4000-8000-000000000010',
     '90910000-0000-4000-8000-000000000011'
   )
   ORDER BY id LIMIT 1;
  BEGIN
    PERFORM public.substituir_investigacao_corretiva_stale(
      v_filha, 'snp_' || repeat('3',32), 'snp_' || repeat('4',32),
      'teste-runtime', 'fonte mudou depois'
    );
    RAISE EXCEPTION 'stale dedicada clonou após materialização';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%Replanejamento explícito é necessário%' THEN RAISE; END IF;
  END;
END $$;
RESET ROLE;
ALTER TABLE public.pending_actions
  ENABLE TRIGGER pending_actions_bloqueia_investigacao;
"""
    sql = sql.replace("__PLANO_HASH__", sql_texto(plano_hash))
    sql = sql.replace("__PLANO__", sql_texto(plano))
    resultado = psql(banco, sql)
    if resultado.returncode:
        raise RuntimeError(
            f"Falha no lifecycle terminal sem gravação: {erro_comando(resultado)}"
        )


def testar_replanejamento_terminal_com_gravacao(banco: str) -> None:
    """Prova contexto/CAS, replanejamento corretivo, supersessão e retry."""
    plano_pai, plano_pai_hash, _ = contrato_plano(
        fonte_ref="pitem_12121212121212121212121212121212",
        sintese_ref="pitem_13131313131313131313131313131313",
        pergunta="fonte anterior à gravação",
        campos_obrigatorios=["data", "negocio", "quantidade", "valor_total"],
    )
    plano_novo, plano_novo_hash, _ = contrato_plano(
        fonte_ref="pitem_14141414141414141414141414141414",
        sintese_ref="pitem_15151515151515151515151515151515",
        pergunta="replanejar após gravação confirmada",
        campos_obrigatorios=["data", "negocio", "quantidade", "valor_total"],
    )
    sql = r"""
RESET ROLE;
ALTER TABLE public.pending_actions
  DISABLE TRIGGER pending_actions_bloqueia_investigacao;
CREATE TABLE public._teste_replanejamento_concorrencia (
  promocao_id uuid, pedido_hash text, contexto_hash text, payload jsonb
);
GRANT SELECT, INSERT ON public._teste_replanejamento_concorrencia TO service_role;
DO $$
DECLARE
  v_promocao_id uuid := '90920000-0000-4000-8000-000000000020';
  v_registro_id uuid := '90920000-0000-4000-8000-000000000030';
  v_pai_id uuid := '90920000-0000-4000-8000-000000000010';
  v_claim jsonb;
  v_pedido_hash text;
  v_consumo jsonb;
  v_contexto jsonb;
  v_contexto_novo jsonb;
  v_payload jsonb;
  v_resultado jsonb;
  v_retry jsonb;
  v_filha public.investigacoes_revisao%ROWTYPE;
BEGIN
  INSERT INTO public.investigacao_autorizacoes_promocao (
    txid, backend_pid, pending_action_id, operacao, status_anterior, status_novo
  ) VALUES (
    txid_current(), pg_backend_pid(), v_promocao_id, 'INSERT', NULL,
    'aguardando_confirmacao'
  );
  EXECUTE 'SET LOCAL ROLE service_role';
  INSERT INTO public.pending_actions (
    id, agente, usuario_solicitante, canal, acao_tipo, entidade_tipo,
    entidade_id, resumo, payload, status, origem_canal,
    origem_conversa_id, origem_mensagem_id, escopo,
    promocao_controle_version, promocao_preparacao_chave,
    promocao_preparacao_hash
  ) VALUES (
    v_promocao_id, 'teste', 'teste', 'teste',
    'promover_revisao_operacional', 'compras',
    '11111111-1111-4111-8111-111111111111', 'promoção com gravação sintética',
    jsonb_build_object(
      'source_draft_id', '11111111-1111-4111-8111-111111111111',
      'source_pending_action_id', '22222222-2222-4222-8222-222222222222',
      'target_table', 'compras',
      'proposed_record', jsonb_build_object(
        'operacao_id', 'OP-TERMINAL-COM-GRAVACAO', 'data', '2026-08-29',
        'quantidade', 2, 'valor_total', 200
      )
    ), 'aguardando_confirmacao', 'teste', 'conversa-gravacao',
    'mensagem-gravacao', 'teste_replanejamento', 'lease-v1',
    'preparacao-gravacao', repeat('b',64)
  );
  INSERT INTO public.investigacoes_revisao (
    id, chave_idempotencia, assunto_tipo, titulo, fingerprint_base,
    plano_hash, plano_canonico, plano_tarefas, policy_version,
    policy_schema_hash, campos_obrigatorios, source_draft_id,
    source_draft_atualizado_em, negocio_candidato_id, negocio_candidato_ids,
    source_candidato_atualizado_em, source_candidatos_atualizados_em, escopo
  ) VALUES (
    v_pai_id, 'terminal-com-gravacao-pai', 'compra',
    'Complementar antes da gravação', repeat('5',64),
    __PLANO_PAI_HASH__, __PLANO_PAI__, __PLANO_PAI__::jsonb->'tarefas',
    'investigacao-v1', public.investigacao_politica_schema_hash('investigacao-v1'),
    ARRAY['data','negocio','quantidade','valor_total'],
    '11111111-1111-4111-8111-111111111111',
    (SELECT atualizado_em FROM public.operation_drafts WHERE id='11111111-1111-4111-8111-111111111111'),
    'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    ARRAY['aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'::uuid],
    (SELECT atualizado_em FROM public.negocios_candidatos WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'),
    jsonb_build_object('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      (SELECT atualizado_em FROM public.negocios_candidatos WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa')),
    'teste_replanejamento'
  );
  v_claim := public.assumir_promocao_operacional(
    v_promocao_id, 'aguardando_confirmacao', 'executor-runtime',
    'conversa-gravacao', 'confirmacao-gravacao', 60
  );
  INSERT INTO public.compras (
    id, operacao_id, data, quantidade, valor_total, idempotency_key
  ) VALUES (
    v_registro_id, 'OP-TERMINAL-COM-GRAVACAO', '2026-08-29', 2, 200,
    'promocao_operacional:' || v_promocao_id::text
  );
  PERFORM public.concluir_promocao_operacional(
    v_promocao_id, (v_claim->>'lease_token')::uuid,
    (v_claim->>'fencing_token')::bigint, 'executado',
    jsonb_build_object(
      'target_table', 'compras', 'target_record_id', v_registro_id,
      'promovido_para_operacional', true
    )
  );
  SELECT pedido_hash INTO STRICT v_pedido_hash
    FROM public.listar_sucessoes_promocao_terminal_pendentes(100)
   WHERE promocao_id=v_promocao_id;
  v_consumo := public.consumir_sucessoes_promocao_terminal(
    v_promocao_id, v_pedido_hash, 'teste-runtime'
  );
  IF v_consumo->>'motivo' IS DISTINCT FROM 'planejamento_fontes_necessario' THEN
    RAISE EXCEPTION 'com_gravacao não aguardou planejamento: %', v_consumo;
  END IF;
  v_contexto := public.obter_contexto_replanejamento_sucessoes_promocao_terminal(
    v_promocao_id, v_pedido_hash
  );
  SELECT jsonb_build_object(
    'versao', 'replanejamento-terminal-v1',
    'outbox_id', v_contexto->>'outbox_id',
    'promocao_id', v_promocao_id,
    'pedido_hash', v_pedido_hash,
    'contexto_cas_hash', v_contexto->>'contexto_cas_hash',
    'predecessoras', jsonb_agg(jsonb_build_object(
      'predecessora_id', item->>'predecessora_id',
      'contexto_hash', item->>'contexto_hash',
      'plano_hash', __PLANO_NOVO_HASH__,
      'plano_canonico', __PLANO_NOVO__,
      'plano_tarefas', __PLANO_NOVO__::jsonb->'tarefas',
      'policy_version', item->>'policy_version',
      'policy_schema_hash', item->>'policy_schema_hash',
      'campos_obrigatorios', item->'campos_obrigatorios'
    ) ORDER BY item->>'predecessora_id')
  ) INTO v_payload
    FROM jsonb_array_elements(v_contexto->'predecessoras') item;

  -- Mudar uma fonte entre leitura e escrita invalida o CAS sem consumir o pai.
  UPDATE public.negocios_candidatos SET atualizado_em=clock_timestamp()
   WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  BEGIN
    PERFORM public.replanejar_sucessoes_promocao_terminal(
      v_promocao_id, v_pedido_hash, v_contexto->>'contexto_cas_hash',
      v_payload, 'teste-runtime'
    );
    RAISE EXCEPTION 'replanejamento aceitou contexto stale';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%RETRY_CONTEXTO_CAS_DIVERGIU%'
       AND SQLERRM NOT LIKE '%RETRY_CONJUNTO_FONTES_MUDOU%' THEN RAISE; END IF;
  END;
  IF EXISTS (SELECT 1 FROM public.investigacoes_revisao WHERE sucessora_de_id=v_pai_id)
     OR NOT EXISTS (SELECT 1 FROM public.investigacoes_revisao
                     WHERE id=v_pai_id AND promocao_ativa_id=v_promocao_id) THEN
    RAISE EXCEPTION 'CAS stale alterou pai ou criou sucessora';
  END IF;

  v_contexto_novo := public.obter_contexto_replanejamento_sucessoes_promocao_terminal(
    v_promocao_id, v_pedido_hash
  );
  SELECT jsonb_build_object(
    'versao', 'replanejamento-terminal-v1',
    'outbox_id', v_contexto_novo->>'outbox_id',
    'promocao_id', v_promocao_id,
    'pedido_hash', v_pedido_hash,
    'contexto_cas_hash', v_contexto_novo->>'contexto_cas_hash',
    'predecessoras', jsonb_agg(jsonb_build_object(
      'predecessora_id', item->>'predecessora_id',
      'contexto_hash', item->>'contexto_hash',
      'plano_hash', __PLANO_NOVO_HASH__, 'plano_canonico', __PLANO_NOVO__,
      'plano_tarefas', __PLANO_NOVO__::jsonb->'tarefas',
      'policy_version', item->>'policy_version',
      'policy_schema_hash', item->>'policy_schema_hash',
      'campos_obrigatorios', item->'campos_obrigatorios'
    ) ORDER BY item->>'predecessora_id')
  ) INTO v_payload
    FROM jsonb_array_elements(v_contexto_novo->'predecessoras') item;
  v_resultado := public.replanejar_sucessoes_promocao_terminal(
    v_promocao_id, v_pedido_hash, v_contexto_novo->>'contexto_cas_hash',
    v_payload, 'teste-runtime'
  );
  IF coalesce((v_resultado->>'processada')::boolean,false) IS NOT TRUE THEN
    RAISE EXCEPTION 'replanejamento corretivo não processou: %', v_resultado;
  END IF;
  SELECT * INTO STRICT v_filha FROM public.investigacoes_revisao
   WHERE sucessora_de_id=v_pai_id;
  IF v_filha.fluxo_tipo <> 'corretiva_pos_gravacao'
     OR v_filha.source_draft_id IS NOT NULL
     OR v_filha.promocao_origem_id IS DISTINCT FROM v_promocao_id
     OR v_filha.registro_operacional_origem_id IS DISTINCT FROM v_registro_id
     OR v_filha.plano_hash IS DISTINCT FROM __PLANO_NOVO_HASH__ THEN
    RAISE EXCEPTION 'folha corretiva não preservou vínculo/plano novo';
  END IF;
  IF EXISTS (SELECT 1 FROM public.investigacoes_revisao
              WHERE id=v_pai_id AND promocao_ativa_id IS NOT NULL) THEN
    RAISE EXCEPTION 'predecessora não foi consumida atomicamente';
  END IF;
  v_retry := public.replanejar_sucessoes_promocao_terminal(
    v_promocao_id, v_pedido_hash, v_contexto_novo->>'contexto_cas_hash',
    v_payload, 'teste-runtime'
  );
  IF coalesce((v_retry->>'repetida')::boolean,false) IS NOT TRUE
     OR v_retry->'sucessoras' IS DISTINCT FROM v_resultado->'sucessoras' THEN
    RAISE EXCEPTION 'retry corretivo divergiu do mapa persistido: %', v_retry;
  END IF;
  INSERT INTO public._teste_replanejamento_concorrencia
    (promocao_id, pedido_hash, contexto_hash, payload)
  VALUES (
    v_promocao_id, v_pedido_hash,
    v_contexto_novo->>'contexto_cas_hash', v_payload
  );
END $$;
RESET ROLE;
UPDATE public.investigacao_tarefas
   SET estado_execucao='cancelada', lease_executor=NULL, lease_token=NULL,
       lease_expira_em=NULL, lease_chave_id=NULL
 WHERE investigacao_id IN (
   SELECT id FROM public.investigacoes_revisao
    WHERE sucessora_de_id='90920000-0000-4000-8000-000000000010'
 ) AND estado_execucao IN ('pendente','em_execucao','aguardando_retentativa');
ALTER TABLE public.pending_actions
  ENABLE TRIGGER pending_actions_bloqueia_investigacao;
"""
    substituicoes = {
        "__PLANO_PAI_HASH__": sql_texto(plano_pai_hash),
        "__PLANO_PAI__": sql_texto(plano_pai),
        "__PLANO_NOVO_HASH__": sql_texto(plano_novo_hash),
        "__PLANO_NOVO__": sql_texto(plano_novo),
    }
    for marcador, valor in substituicoes.items():
        sql = sql.replace(marcador, valor)
    resultado = psql(banco, sql)
    if resultado.returncode:
        raise RuntimeError(
            f"Falha no replanejamento terminal com gravação: {erro_comando(resultado)}"
        )
    comando = [
        "psql", "-X", "-v", "ON_ERROR_STOP=1", "-At", "-d", banco, "-c",
        """SET ROLE service_role;
        SELECT public.replanejar_sucessoes_promocao_terminal(
          promocao_id, pedido_hash, contexto_hash, payload, 'caller-concorrente'
        )::text FROM public._teste_replanejamento_concorrencia;""",
    ]
    processos = [
        subprocess.Popen(
            comando, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=os.environ.copy(),
        )
        for _ in range(2)
    ]
    saidas: list[str] = []
    for processo in processos:
        stdout, stderr = processo.communicate(timeout=20)
        if processo.returncode:
            raise RuntimeError(
                "Falha no retry concorrente do replanejamento: "
                + (stderr or stdout).strip().splitlines()[-1]
            )
        saidas.append(stdout.strip().splitlines()[-1])
    if any('"repetida": true' not in saida for saida in saidas):
        raise RuntimeError(f"dois callers não receberam retry idempotente: {saidas!r}")
    limpeza = psql(banco, "DROP TABLE public._teste_replanejamento_concorrencia")
    if limpeza.returncode:
        raise RuntimeError(
            f"Falha ao limpar fixture concorrente: {erro_comando(limpeza)}"
        )


def testar_replanejamento_corretiva_stale_materializada(banco: str) -> None:
    """Materializa a corretiva, muda o registro e prova supersessão auditada."""
    plano_novo, plano_novo_hash, _ = contrato_plano(
        fonte_ref="pitem_16161616161616161616161616161616",
        sintese_ref="pitem_17171717171717171717171717171717",
        pergunta="reavaliar corretiva após novo retrato operacional",
        campos_obrigatorios=["data", "negocio", "quantidade", "valor_total"],
    )
    sql = r"""
RESET ROLE;
DO $$
DECLARE
  v_investigacao public.investigacoes_revisao%ROWTYPE;
  v_draft_id uuid := '90930000-0000-4000-8000-000000000001';
  v_acao_id uuid := '90930000-0000-4000-8000-000000000002';
  v_evento_id uuid := '90930000-0000-4000-8000-000000000003';
  v_draft jsonb;
  v_acao jsonb;
  v_evento jsonb;
BEGIN
  SELECT * INTO STRICT v_investigacao
    FROM public.investigacoes_revisao
   WHERE sucessora_de_id='90920000-0000-4000-8000-000000000010';
  UPDATE public.investigacao_tarefas SET estado_execucao='em_execucao',
         lease_executor='fixture-corretiva',
         lease_token='90930000-0000-4000-8000-000000000010',
         lease_expira_em=clock_timestamp()+interval '5 minutes',
         lease_chave_id='key_teste-runtime', fencing_token=1,
         tentativas=1, iniciado_em=clock_timestamp()
   WHERE id=(SELECT id FROM public.investigacao_tarefas
              WHERE investigacao_id=v_investigacao.id
                AND adaptador<>'sintese' ORDER BY id LIMIT 1);
  INSERT INTO public.investigacao_pendencias (
    id_logico, investigacao_id, tarefa_id, tarefa_lease_token,
    tarefa_fencing_token, chave_idempotencia, tipo, campo,
    descricao_sanitizada, estado
  ) SELECT 'pendencia_corretiva_runtime', v_investigacao.id, tarefa.id,
           '90930000-0000-4000-8000-000000000010', 1,
           'pendencia-corretiva-runtime', 'decisao_humana', 'valor_total',
           'Conferir a correção operacional.', 'aberta'
      FROM public.investigacao_tarefas tarefa
     WHERE tarefa.investigacao_id=v_investigacao.id
       AND tarefa.adaptador<>'sintese'
     ORDER BY tarefa.id LIMIT 1;
  UPDATE public.investigacao_tarefas
     SET estado_execucao='concluida', estado_cobertura='completa',
         estado_resultado='evidencia_insuficiente', fencing_token=1,
         resultado_fencing_token=1,
         resultado_lease_token='90930000-0000-4000-8000-000000000010',
         resultado_pedido_hash=repeat('9',64), concluido_em=clock_timestamp(),
         lease_executor=NULL, lease_token=NULL, lease_expira_em=NULL,
         lease_chave_id=NULL
   WHERE investigacao_id=v_investigacao.id;
  UPDATE public.investigacoes_revisao
     SET estado_execucao='concluida', estado_resultado='evidencia_insuficiente',
         concluida_em=clock_timestamp()
   WHERE id=v_investigacao.id;
  v_draft := jsonb_build_object(
    'id', v_draft_id, 'agente', 'teste', 'status', 'em_revisao',
    'tipo_operacao', 'correcao_pos_gravacao',
    'entidade_final_tipo', 'correcao_pos_gravacao', 'confianca', 0.8,
    'dados_extraidos', '{}'::jsonb, 'campos_pendentes', '[]'::jsonb,
    'inferencias', jsonb_build_object(
      'fingerprint_base', v_investigacao.fingerprint_base,
      'exige_confirmacao', true, 'promovido_para_operacional', false),
    'pending_action_id', v_acao_id, 'origem_canal', 'teste',
    'origem_conversa_id', 'conversa-corretiva',
    'origem_mensagem_id', 'mensagem-corretiva',
    'contexto_canonico', 'teste:corretiva',
    'contexto_nome', 'Corretiva sintética', 'escopo', 'teste_corretiva');
  v_acao := jsonb_build_object(
    'id', v_acao_id, 'agente', 'teste', 'usuario_solicitante', 'teste',
    'canal', 'teste', 'acao_tipo', 'revisar_correcao_pos_gravacao',
    'entidade_tipo', 'operation_draft', 'entidade_id', v_draft_id,
    'resumo', 'Revisão corretiva sintética',
    'payload', jsonb_build_object(
      'operation_draft_id', v_draft_id,
      'fingerprint_base', v_investigacao.fingerprint_base,
      'dados_extraidos', '{}'::jsonb, 'campos_pendentes', '[]'::jsonb,
      'executavel', false, 'promovido_para_operacional', false),
    'resultado', '{}'::jsonb, 'status', 'aguardando_confirmacao',
    'origem_canal', 'teste', 'origem_conversa_id', 'conversa-corretiva',
    'origem_mensagem_id', 'mensagem-corretiva',
    'contexto_canonico', 'teste:corretiva',
    'contexto_nome', 'Corretiva sintética', 'escopo', 'teste_corretiva');
  v_evento := jsonb_build_object(
    'id', v_evento_id, 'tipo', 'correcao_pos_gravacao_enviada_para_revisao',
    'agente', 'teste', 'usuario', 'teste',
    'entidade_tipo', 'operation_draft', 'entidade_id', v_draft_id,
    'origem', 'teste', 'origem_canal', 'teste',
    'origem_conversa_id', 'conversa-corretiva',
    'origem_mensagem_id', 'mensagem-corretiva',
    'contexto_canonico', 'teste:corretiva',
    'contexto_nome', 'Corretiva sintética', 'escopo', 'teste_corretiva',
    'status', 'pendente', 'fonte_ref', v_investigacao.referencia_publica,
    'confianca', 0.8,
    'dados', jsonb_build_object(
      'operation_draft_id', v_draft_id, 'pending_action_id', v_acao_id,
      'fingerprint_base', v_investigacao.fingerprint_base,
      'promovido_para_operacional', false),
    'observacao', 'Materialização corretiva sintética');
  EXECUTE 'SET LOCAL ROLE service_role';
  BEGIN
    PERFORM public.materializar_revisao_investigada(
      v_investigacao.id,
      jsonb_set(v_draft, '{dados_extraidos,documento}',
        to_jsonb('NFe12345678901234567890123456789012345678901234'::text)),
      v_acao, v_evento);
    RAISE EXCEPTION 'materialização aceitou NFe prefixada de 44 dígitos';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%campo fora do contrato fechado%' THEN RAISE; END IF;
  END;
  BEGIN
    PERFORM public.materializar_revisao_investigada(
      v_investigacao.id,
      jsonb_set(v_draft, '{dados_extraidos,evidencia}',
        jsonb_build_object('documento',
          'NFe 12345678901234567890123456789012345678901234')),
      v_acao, v_evento);
    RAISE EXCEPTION 'materialização aceitou NFe aninhada de 44 dígitos';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%campo fora do contrato fechado%' THEN RAISE; END IF;
  END;
  PERFORM public.materializar_revisao_investigada(
    v_investigacao.id, v_draft, v_acao, v_evento);
END $$;

RESET ROLE;
CREATE TABLE public._teste_corretiva_stale_contexto AS
SELECT investigacao.id AS investigacao_id,
       investigacao.registro_operacional_origem_snapshot_ref AS snapshot_anterior,
       public.investigacao_snapshot_registro_promocao(
         investigacao.destino_operacional_origem,
         investigacao.registro_operacional_origem_id,
         investigacao.promocao_origem_id,
         promocao.payload->'proposed_record'
       )->>'snapshot_ref' AS snapshot_novo
  FROM public.investigacoes_revisao investigacao
 JOIN public.pending_actions promocao ON promocao.id=investigacao.promocao_origem_id
 WHERE investigacao.sucessora_de_id='90920000-0000-4000-8000-000000000010';
ALTER TABLE public._teste_corretiva_stale_contexto
  ADD COLUMN contexto_inicial jsonb,
  ADD COLUMN contexto_final jsonb,
  ADD COLUMN plano jsonb;
GRANT SELECT, UPDATE ON public._teste_corretiva_stale_contexto TO service_role;

-- SESSAO_GETTER_REPLANEJAMENTO
SET ROLE service_role;
DO $$
DECLARE
  v_pai public.investigacoes_revisao%ROWTYPE;
  v_promocao public.pending_actions%ROWTYPE;
  v_snapshot_novo text;
  v_contexto jsonb;
  v_plano jsonb;
  v_resultado jsonb;
  v_retry jsonb;
  v_filha public.investigacoes_revisao%ROWTYPE;
BEGIN
  SELECT * INTO STRICT v_pai FROM public.investigacoes_revisao
   WHERE sucessora_de_id='90920000-0000-4000-8000-000000000010';
  SELECT * INTO STRICT v_promocao FROM public.pending_actions
   WHERE id=v_pai.promocao_origem_id;
  SELECT snapshot_novo INTO STRICT v_snapshot_novo
    FROM public._teste_corretiva_stale_contexto
   WHERE investigacao_id=v_pai.id;
  IF v_snapshot_novo IS NOT DISTINCT FROM
       v_pai.registro_operacional_origem_snapshot_ref THEN
    RAISE EXCEPTION 'mudança operacional não alterou o snapshot';
  END IF;
  v_plano := jsonb_build_object(
    'policy_version', 'investigacao-v1',
    'policy_schema_hash', public.investigacao_politica_schema_hash('investigacao-v1'),
    'campos_obrigatorios', to_jsonb(public.investigacao_politica_campos(
      v_pai.assunto_tipo, 'investigacao-v1')),
    'plano_tarefas', __PLANO_NOVO__::jsonb->'tarefas',
    'plano_canonico', __PLANO_NOVO__, 'plano_hash', __PLANO_NOVO_HASH__);
  v_contexto := public.obter_contexto_replanejamento_corretiva_stale(
    v_pai.id, v_pai.registro_operacional_origem_snapshot_ref, v_snapshot_novo);
  UPDATE public._teste_corretiva_stale_contexto
     SET contexto_final=v_contexto, plano=v_plano
   WHERE investigacao_id=v_pai.id;
  v_resultado := public.replanejar_investigacao_corretiva_stale(
    v_pai.id, v_pai.registro_operacional_origem_snapshot_ref, v_snapshot_novo,
    v_contexto->>'contexto_cas_hash', v_plano,
    'teste-runtime', 'registro operacional mudou');
  IF coalesce((v_resultado->>'substituida')::boolean,false) IS NOT TRUE THEN
    RAISE EXCEPTION 'corretiva materializada não foi substituída: %', v_resultado;
  END IF;
  SELECT * INTO STRICT v_filha FROM public.investigacoes_revisao
   WHERE sucessora_de_id=v_pai.id;
  IF v_filha.plano_hash IS DISTINCT FROM __PLANO_NOVO_HASH__
     OR v_filha.registro_operacional_origem_snapshot_ref
          IS DISTINCT FROM v_snapshot_novo
     OR v_filha.source_draft_id IS NOT NULL THEN
    RAISE EXCEPTION 'filha stale não recebeu plano/snapshot corretos';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM public.investigacoes_revisao
                  WHERE id=v_pai.id AND estado_execucao='obsoleta'
                    AND obsolescencia_motivo='registro_operacional_stale')
     OR (SELECT coalesce(jsonb_agg(jsonb_build_object(
          'plano_item_ref', tarefa.plano_item_ref,
          'adaptador', tarefa.adaptador,
          'adaptador_version', tarefa.adaptador_version,
          'consulta_ref', tarefa.consulta_ref,
          'consulta_schema_version', tarefa.consulta_schema_version,
          'consulta_spec', tarefa.consulta_spec,
          'consulta_canonico', tarefa.consulta_canonico,
          'consulta_hash', tarefa.consulta_hash
        ) ORDER BY tarefa.plano_item_ref), '[]'::jsonb)
          FROM public.investigacao_tarefas tarefa
         WHERE tarefa.investigacao_id=v_filha.id)
          IS DISTINCT FROM (__PLANO_NOVO__::jsonb->'tarefas')
     OR EXISTS (SELECT 1 FROM public.investigacao_tarefas
              WHERE investigacao_id=v_pai.id
                AND estado_execucao IN ('pendente','em_execucao','aguardando_retentativa'))
     OR NOT EXISTS (SELECT 1 FROM public.operation_drafts
                     WHERE id='90930000-0000-4000-8000-000000000001'
                       AND status='cancelado')
     OR NOT EXISTS (SELECT 1 FROM public.pending_actions
                     WHERE id='90930000-0000-4000-8000-000000000002'
                       AND status IN ('rejeitado','cancelado'))
     OR NOT EXISTS (SELECT 1 FROM public.eventos
                     WHERE tipo='revisao_corretiva_substituida'
                       AND entidade_id='90930000-0000-4000-8000-000000000001'
                       AND dados->>'investigacao_sucessora_id'=v_filha.id::text) THEN
    RAISE EXCEPTION 'supersessão corretiva não cancelou/selou todo o estado';
  END IF;
END $$;
RESET ROLE;
"""
    sql = sql.replace("__PLANO_NOVO_HASH__", sql_texto(plano_novo_hash))
    sql = sql.replace("__PLANO_NOVO__", sql_texto(plano_novo))
    preparacao, replanejamento = sql.split(
        "-- SESSAO_GETTER_REPLANEJAMENTO", 1
    )
    for etapa, trecho in (("materializa/muta", preparacao),):
        resultado = psql(banco, trecho)
        if resultado.returncode:
            raise RuntimeError(
                f"Falha na corretiva stale ({etapa}): {erro_comando(resultado)}"
            )
    # Com a revisão humana ativa, o guardião operacional deve tornar o stale
    # inalcançável: a escrita normal falha e o retrato/xmin permanece igual.
    guardiao = psql(banco, r"""
DO $$
DECLARE v_snapshot_antes text; v_snapshot_depois text;
BEGIN
  SELECT snapshot_novo INTO STRICT v_snapshot_antes
    FROM public._teste_corretiva_stale_contexto;
  BEGIN
    UPDATE public.compras SET nota_runtime='tentativa enquanto revisão ativa'
     WHERE id='90920000-0000-4000-8000-000000000030';
    RAISE EXCEPTION 'guardião aceitou UPDATE com revisão corretiva ativa';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%Há uma conferência corretiva aberta%' THEN RAISE; END IF;
  END;
  SELECT public.investigacao_snapshot_registro_promocao(
           investigacao.destino_operacional_origem,
           investigacao.registro_operacional_origem_id,
           investigacao.promocao_origem_id,
           promocao.payload->'proposed_record'
         )->>'snapshot_ref'
    INTO STRICT v_snapshot_depois
    FROM public.investigacoes_revisao investigacao
    JOIN public.pending_actions promocao ON promocao.id=investigacao.promocao_origem_id
   WHERE investigacao.id=(SELECT investigacao_id
                            FROM public._teste_corretiva_stale_contexto);
  IF v_snapshot_depois IS DISTINCT FROM v_snapshot_antes
     OR EXISTS (SELECT 1 FROM public.investigacoes_revisao filha
                 WHERE filha.sucessora_de_id=(SELECT investigacao_id
                   FROM public._teste_corretiva_stale_contexto)) THEN
    RAISE EXCEPTION 'UPDATE bloqueado alterou snapshot ou criou sucessora';
  END IF;
END $$;
""")
    if guardiao.returncode:
        raise RuntimeError(
            f"Falha na corretiva ativa protegida: {erro_comando(guardiao)}"
        )
    # A decisão humana canônica encerra a revisão em outra sessão. Só então a
    # alteração auxiliar é legitimamente aceita e cria um snapshot stale.
    decisao = psql(banco, r"""
SET ROLE service_role;
DO $$
DECLARE v_draft public.operation_drafts%ROWTYPE;
        v_acao public.pending_actions%ROWTYPE;
        v_pedido jsonb; v_resultado jsonb;
BEGIN
  SELECT * INTO STRICT v_draft FROM public.operation_drafts
   WHERE id='90930000-0000-4000-8000-000000000001';
  SELECT * INTO STRICT v_acao FROM public.pending_actions
   WHERE id='90930000-0000-4000-8000-000000000002';
  v_pedido := jsonb_build_object(
    'versao', 1, 'modo', 'rejeitar',
    'draft_atualizado_em', v_draft.atualizado_em,
    'action_atualizado_em', v_acao.atualizado_em,
    'dados_extraidos', v_draft.dados_extraidos,
    'inferencias', v_draft.inferencias,
    'campos_pendentes', to_jsonb(v_draft.campos_pendentes),
    'codigo_sugerido', v_draft.codigo_sugerido,
    'resumo', v_acao.resumo,
    'contexto', jsonb_build_object(
      'contexto_canonico', v_draft.contexto_canonico,
      'contexto_nome', v_draft.contexto_nome,
      'origem_canal', v_draft.origem_canal,
      'origem_conversa_id', v_draft.origem_conversa_id,
      'origem_mensagem_id', v_draft.origem_mensagem_id,
      'escopo', v_draft.escopo),
    'motivo', 'revisão humana encerrada antes da nova fonte');
  v_resultado := public.decidir_revisao_corretiva(v_draft.id, v_acao.id, v_pedido);
  IF coalesce((v_resultado->>'decidida')::boolean,false) IS NOT TRUE
     OR NOT EXISTS (SELECT 1 FROM public.operation_drafts
                     WHERE id=v_draft.id AND status='cancelado')
     OR NOT EXISTS (SELECT 1 FROM public.pending_actions
                     WHERE id=v_acao.id AND status='rejeitado') THEN
    RAISE EXCEPTION 'decisão canônica não encerrou revisão: %', v_resultado;
  END IF;
END $$;
RESET ROLE;
""")
    if decisao.returncode:
        raise RuntimeError(
            f"Falha ao encerrar revisão stale: {erro_comando(decisao)}"
        )
    mutacao = psql(banco, r"""
UPDATE public.compras SET nota_runtime='snapshot posterior terminal'
 WHERE id='90920000-0000-4000-8000-000000000030';
UPDATE public._teste_corretiva_stale_contexto contexto
   SET snapshot_novo = public.investigacao_snapshot_registro_promocao(
         investigacao.destino_operacional_origem,
         investigacao.registro_operacional_origem_id,
         investigacao.promocao_origem_id,
         promocao.payload->'proposed_record'
       )->>'snapshot_ref'
  FROM public.investigacoes_revisao investigacao
  JOIN public.pending_actions promocao ON promocao.id=investigacao.promocao_origem_id
 WHERE investigacao.id=contexto.investigacao_id;
""")
    if mutacao.returncode:
        raise RuntimeError(
            f"Falha na mutação operacional pós-decisão: {erro_comando(mutacao)}"
        )
    getter = psql(banco, r"""
SET ROLE service_role;
UPDATE public._teste_corretiva_stale_contexto contexto
   SET contexto_inicial = public.obter_contexto_replanejamento_corretiva_stale(
     contexto.investigacao_id, contexto.snapshot_anterior, contexto.snapshot_novo
   );
RESET ROLE;
""")
    if getter.returncode:
        raise RuntimeError(
            f"Falha na corretiva stale (getter): {erro_comando(getter)}"
        )
    plano_sql = sql_texto(json_canonico({
        "policy_version": "investigacao-v1",
        "policy_schema_hash": POLITICA_SCHEMA_HASH,
        "campos_obrigatorios": ["data", "negocio", "quantidade", "valor_total"],
        "plano_tarefas": json.loads(plano_novo)["tarefas"],
        "plano_canonico": plano_novo,
        "plano_hash": plano_novo_hash,
    }))
    cas_sql = r"""
RESET ROLE;
UPDATE public.negocios_candidatos SET atualizado_em=clock_timestamp()
 WHERE id='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
SET ROLE service_role;
DO $$
DECLARE v_contexto record; v_pai public.investigacoes_revisao%ROWTYPE;
BEGIN
  SELECT * INTO STRICT v_contexto FROM public._teste_corretiva_stale_contexto;
  SELECT * INTO STRICT v_pai FROM public.investigacoes_revisao
   WHERE id=v_contexto.investigacao_id;
  BEGIN
    PERFORM public.replanejar_investigacao_corretiva_stale(
      v_pai.id, v_contexto.snapshot_anterior, v_contexto.snapshot_novo,
      v_contexto.contexto_inicial->>'contexto_cas_hash', __PLANO__::jsonb,
      'teste-runtime', 'registro operacional mudou');
    RAISE EXCEPTION 'corretiva stale aceitou CAS antigo';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%RETRY_CONTEXTO_CORRETIVO_DIVERGIU%' THEN RAISE; END IF;
  END;
  IF EXISTS (SELECT 1 FROM public.investigacoes_revisao WHERE sucessora_de_id=v_pai.id)
     OR v_pai.estado_execucao='obsoleta'
     OR NOT EXISTS (SELECT 1 FROM public.pending_actions
                     WHERE id='90930000-0000-4000-8000-000000000002'
                       AND status IN ('rejeitado','cancelado')) THEN
    RAISE EXCEPTION 'CAS negativo deixou efeitos';
  END IF;
END $$;
RESET ROLE;
""".replace("__PLANO__", plano_sql)
    cas = psql(banco, cas_sql)
    if cas.returncode:
        raise RuntimeError(
            f"Falha na corretiva stale (CAS negativo): {erro_comando(cas)}"
        )
    resultado = psql(banco, replanejamento)
    if resultado.returncode:
        raise RuntimeError(
            f"Falha na corretiva stale (replanejar): {erro_comando(resultado)}"
        )
    retry = psql(banco, r"""
SET ROLE service_role;
DO $$
DECLARE v_contexto record; v_pai public.investigacoes_revisao%ROWTYPE;
        v_resultado jsonb; v_filha uuid;
BEGIN
  SELECT * INTO STRICT v_contexto FROM public._teste_corretiva_stale_contexto;
  SELECT * INTO STRICT v_pai FROM public.investigacoes_revisao
   WHERE id=v_contexto.investigacao_id;
  SELECT id INTO STRICT v_filha FROM public.investigacoes_revisao
   WHERE sucessora_de_id=v_pai.id;
  v_resultado := public.replanejar_investigacao_corretiva_stale(
    v_pai.id, v_contexto.snapshot_anterior, v_contexto.snapshot_novo,
    v_contexto.contexto_final->>'contexto_cas_hash', v_contexto.plano,
    'teste-runtime', 'registro operacional mudou');
  IF coalesce((v_resultado->>'repeticao_idempotente')::boolean,false) IS NOT TRUE
     OR v_resultado->>'investigacao_sucessora_id' IS DISTINCT FROM v_filha::text THEN
    RAISE EXCEPTION 'retry pós-COMMIT divergiu: %', v_resultado;
  END IF;
END $$;
RESET ROLE;
""")
    if retry.returncode:
        raise RuntimeError(
            f"Falha na corretiva stale (retry pós-COMMIT): {erro_comando(retry)}"
        )
    limpeza = psql(banco, r"""
UPDATE public.investigacao_tarefas
   SET estado_execucao='cancelada', lease_executor=NULL, lease_token=NULL,
       lease_expira_em=NULL, lease_chave_id=NULL
 WHERE investigacao_id IN (
   SELECT filha.id FROM public.investigacoes_revisao filha
   JOIN public.investigacoes_revisao pai ON pai.id=filha.sucessora_de_id
   WHERE pai.anexado_draft_id='90930000-0000-4000-8000-000000000001'
 ) AND estado_execucao IN ('pendente','em_execucao','aguardando_retentativa');
DROP TABLE public._teste_corretiva_stale_contexto;
""")
    if limpeza.returncode:
        raise RuntimeError(
            f"Falha na limpeza stale: {erro_comando(limpeza)}"
        )


def testar_obsolescencia_draft(banco: str) -> None:
    plano, plano_hash, manifesto = contrato_plano(
        fonte_ref="pitem_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee",
        sintese_ref="pitem_ffffffffffffffffffffffffffffffff",
        pergunta="mudança sintética no rascunho",
        campos_obrigatorios=["data", "negocio", "quantidade", "valor_total"],
    )
    fonte = next(item for item in manifesto if item["adaptador"] == "outro")
    sintese = next(item for item in manifesto if item["adaptador"] == "sintese")
    sql = r"""
SET ROLE service_role;
INSERT INTO public.operation_drafts
  (id, agente, status, tipo_operacao, entidade_final_tipo, dados_extraidos,
   campos_pendentes, inferencias, pending_action_id, escopo)
VALUES
  ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1', 'teste', 'em_revisao',
   'consolidacao_compra_planilha', 'compras', '{}', '{}', '{}',
   'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2', 'teste_obsolescencia');
INSERT INTO public.pending_actions
  (id, agente, canal, acao_tipo, entidade_tipo, entidade_id, resumo,
   payload, status, escopo)
VALUES
  ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2', 'teste', 'teste',
   'revisar_consolidacao_negocio', 'operation_draft',
   'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1', 'Revisão editável sintética',
   '{}', 'aguardando_confirmacao', 'teste_obsolescencia');
INSERT INTO public.investigacoes_revisao
  (id, chave_idempotencia, assunto_tipo, titulo, source_draft_id,
   source_draft_atualizado_em, fingerprint_base, plano_hash, plano_canonico,
   plano_tarefas, policy_version, policy_schema_hash, campos_obrigatorios, escopo)
VALUES
  ('cccccccc-cccc-4ccc-8ccc-ccccccccccc3', 'teste-obsolescencia-draft',
   'compra', 'Investigação antes de edição',
   'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1',
   (SELECT atualizado_em FROM public.operation_drafts
     WHERE id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1'),
   repeat('c', 64), __PLANO_HASH__, __PLANO__,
   __PLANO__::jsonb -> 'tarefas', 'investigacao-v1',
   public.investigacao_politica_schema_hash('investigacao-v1'),
   ARRAY['data','negocio','quantidade','valor_total'],
   'teste_obsolescencia');
INSERT INTO public.investigacao_tarefas
  (id, investigacao_id, chave_idempotencia, plano_item_ref, adaptador,
   consulta_ref, consulta_schema_version, consulta_spec, consulta_canonico,
   consulta_hash, adaptador_version)
VALUES
  ('dddddddd-dddd-4ddd-8ddd-ddddddddddd4',
   'cccccccc-cccc-4ccc-8ccc-ccccccccccc3', 'obsoleta-fonte',
   'pitem_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee', 'outro', __CONSULTA_REF__,
   __CONSULTA_SCHEMA__, __CONSULTA_SPEC__::jsonb, __CONSULTA_CANONICO__,
   __CONSULTA_HASH__, 'v1'),
  ('dddddddd-dddd-4ddd-8ddd-ddddddddddd5',
   'cccccccc-cccc-4ccc-8ccc-ccccccccccc3', 'obsoleta-sintese',
   'pitem_ffffffffffffffffffffffffffffffff', 'sintese', __SINT_REF__,
   __SINT_SCHEMA__, __SINT_SPEC__::jsonb, __SINT_CANONICO__,
   __SINT_HASH__, 'investigacao-v1');
RESET ROLE;

-- Monta uma rodada já concluída e, depois, reproduz uma edição humana do
-- formulário antes do anexo. A RPC precisa liberar a fila sem apagar a trilha.
UPDATE public.investigacao_tarefas
   SET estado_execucao = 'concluida', estado_cobertura = 'completa',
       estado_resultado = 'evidencia_insuficiente', fencing_token = 1,
       resultado_fencing_token = 1,
       resultado_lease_token = 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
       resultado_pedido_hash = repeat('e', 64),
       concluido_em = now()
 WHERE investigacao_id = 'cccccccc-cccc-4ccc-8ccc-ccccccccccc3';
UPDATE public.investigacoes_revisao
   SET estado_execucao = 'concluida',
       estado_resultado = 'evidencia_insuficiente', concluida_em = now()
 WHERE id = 'cccccccc-cccc-4ccc-8ccc-ccccccccccc3';

SET ROLE authenticated;
UPDATE public.operation_drafts
   SET dados_extraidos = '{"ajuste_humano":"sim"}'::jsonb
 WHERE id = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1';
RESET ROLE;

CREATE TEMP TABLE teste_fencing_obsolescencia AS
SELECT public.investigacao_fencing_snapshot(
  'cccccccc-cccc-4ccc-8ccc-ccccccccccc3'
) AS valor;
GRANT SELECT ON teste_fencing_obsolescencia TO service_role;
SET ROLE service_role;
SELECT public.obsoletar_investigacao_por_mudanca_draft(
  'cccccccc-cccc-4ccc-8ccc-ccccccccccc3',
  (SELECT source_draft_atualizado_em
     FROM public.investigacoes_revisao
    WHERE id = 'cccccccc-cccc-4ccc-8ccc-ccccccccccc3'),
  (SELECT valor FROM teste_fencing_obsolescencia)
);
-- A repetição deve ser idempotente e não criar um segundo evento.
SELECT public.obsoletar_investigacao_por_mudanca_draft(
  'cccccccc-cccc-4ccc-8ccc-ccccccccccc3',
  (SELECT source_draft_atualizado_em
     FROM public.investigacoes_revisao
    WHERE id = 'cccccccc-cccc-4ccc-8ccc-ccccccccccc3'),
  (SELECT valor FROM teste_fencing_obsolescencia)
);
DO $$
BEGIN
  BEGIN
    PERFORM public.exigir_investigacao_anexada_para_promocao(
      'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1'
    );
    RAISE EXCEPTION 'draft alterado ficou promovível sem nova investigação';
  EXCEPTION WHEN OTHERS THEN
    IF SQLERRM NOT LIKE '%dados mudaram; conclua e anexe a investigação%' THEN
      RAISE;
    END IF;
  END;
END $$;
RESET ROLE;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.investigacoes_revisao
     WHERE id = 'cccccccc-cccc-4ccc-8ccc-ccccccccccc3'
       AND estado_execucao = 'obsoleta'
       AND estado_resultado IS NULL
       AND anexado_em IS NULL
  ) THEN
    RAISE EXCEPTION 'investigação editada não ficou obsoleta';
  END IF;
  IF EXISTS (
    SELECT 1 FROM public.investigacao_tarefas
     WHERE investigacao_id = 'cccccccc-cccc-4ccc-8ccc-ccccccccccc3'
       AND (
         estado_execucao IS DISTINCT FROM 'obsoleta'
         OR lease_token IS NOT NULL OR lease_executor IS NOT NULL
         OR lease_expira_em IS NOT NULL
       )
  ) THEN
    RAISE EXCEPTION 'tarefas obsoletas mantiveram execução ou lease';
  END IF;
  IF (
    SELECT count(*) FROM public.investigacao_eventos
     WHERE investigacao_id = 'cccccccc-cccc-4ccc-8ccc-ccccccccccc3'
       AND tipo = 'investigacao_obsoleta'
  ) <> 1 THEN
    RAISE EXCEPTION 'obsolescência não registrou exatamente um evento';
  END IF;
END $$;
"""
    substituicoes = {
        "__PLANO_HASH__": sql_texto(plano_hash),
        "__PLANO__": sql_texto(plano),
        "__CONSULTA_REF__": sql_texto(str(fonte["consulta_ref"])),
        "__CONSULTA_SCHEMA__": sql_texto(str(fonte["consulta_schema_version"])),
        "__CONSULTA_SPEC__": sql_texto(json_canonico(fonte["consulta_spec"])),
        "__CONSULTA_CANONICO__": sql_texto(str(fonte["consulta_canonico"])),
        "__CONSULTA_HASH__": sql_texto(str(fonte["consulta_hash"])),
        "__SINT_REF__": sql_texto(str(sintese["consulta_ref"])),
        "__SINT_SCHEMA__": sql_texto(str(sintese["consulta_schema_version"])),
        "__SINT_SPEC__": sql_texto(json_canonico(sintese["consulta_spec"])),
        "__SINT_CANONICO__": sql_texto(str(sintese["consulta_canonico"])),
        "__SINT_HASH__": sql_texto(str(sintese["consulta_hash"])),
    }
    for marcador, valor in substituicoes.items():
        sql = sql.replace(marcador, valor)
    resultado = psql(banco, sql)
    if resultado.returncode:
        raise RuntimeError(
            f"Falha no teste de obsolescência do rascunho: {erro_comando(resultado)}"
        )


def testar_lease_concorrente(banco: str) -> None:
    plano, plano_hash, manifesto = contrato_plano(
        fonte_ref="pitem_cccccccccccccccccccccccccccccccc",
        sintese_ref="pitem_dddddddddddddddddddddddddddddddd",
        pergunta="concorrência sintética",
        campos_obrigatorios=["data", "negocio", "quantidade", "valor_total"],
    )
    fonte = next(item for item in manifesto if item["adaptador"] == "outro")
    preparar = r"""
SET ROLE service_role;
INSERT INTO public.investigacoes_revisao
  (id, chave_idempotencia, assunto_tipo, titulo, fingerprint_base,
   plano_hash, plano_canonico, plano_tarefas, policy_version,
   policy_schema_hash, campos_obrigatorios, escopo)
VALUES ('66666666-6666-4666-8666-666666666666', 'teste-runtime-concorrencia',
  'compra', 'Fila concorrente sintética', repeat('b', 64),
  __PLANO_HASH__, __PLANO__, __PLANO__::jsonb -> 'tarefas',
  'investigacao-v1',
  public.investigacao_politica_schema_hash('investigacao-v1'),
  ARRAY['data','negocio','quantidade','valor_total'], 'teste_runtime');
INSERT INTO public.investigacao_tarefas
  (id, investigacao_id, chave_idempotencia, plano_item_ref, adaptador, consulta_ref,
   consulta_schema_version, consulta_spec, consulta_canonico, consulta_hash,
   adaptador_version)
VALUES ('77777777-7777-4777-8777-777777777777', '66666666-6666-4666-8666-666666666666',
  'teste-runtime-concorrente-tarefa', 'pitem_cccccccccccccccccccccccccccccccc',
  'outro', __CONSULTA_REF__, __CONSULTA_SCHEMA__, __CONSULTA_SPEC__::jsonb,
  __CONSULTA_CANONICO__, __CONSULTA_HASH__, 'v1');
RESET ROLE;
"""
    substituicoes = {
        "__PLANO_HASH__": sql_texto(plano_hash),
        "__PLANO__": sql_texto(plano),
        "__CONSULTA_REF__": sql_texto(str(fonte["consulta_ref"])),
        "__CONSULTA_SCHEMA__": sql_texto(str(fonte["consulta_schema_version"])),
        "__CONSULTA_SPEC__": sql_texto(json_canonico(fonte["consulta_spec"])),
        "__CONSULTA_CANONICO__": sql_texto(str(fonte["consulta_canonico"])),
        "__CONSULTA_HASH__": sql_texto(str(fonte["consulta_hash"])),
    }
    for marcador, valor in substituicoes.items():
        preparar = preparar.replace(marcador, valor)
    resultado = psql(banco, preparar)
    if resultado.returncode:
        raise RuntimeError(f"Falha ao preparar teste concorrente: {erro_comando(resultado)}")

    comando = [
        "psql", "-X", "-v", "ON_ERROR_STOP=1", "-At", "-d", banco,
        "-c", "SELECT coalesce(public.assumir_tarefa_investigacao('outro', 'worker-sintetico', 60)::text, 'NULL')",
    ]
    processos = [subprocess.Popen(comando, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ.copy()) for _ in range(2)]
    saidas = []
    for processo in processos:
        stdout, stderr = processo.communicate(timeout=20)
        if processo.returncode:
            raise RuntimeError(f"Falha no worker concorrente: {(stderr or stdout).strip().splitlines()[-1]}")
        saidas.append(stdout.strip())
    if sum(saida != "NULL" for saida in saidas) != 1:
        raise RuntimeError(f"lease não foi exclusivo: {saidas!r}")
    resultado = psql(
        banco,
        """
        UPDATE public.investigacao_tarefas
           SET estado_execucao = 'cancelada',
               lease_executor = NULL, lease_token = NULL,
               lease_expira_em = NULL, lease_chave_id = NULL
         WHERE id = '77777777-7777-4777-8777-777777777777';
        """,
    )
    if resultado.returncode:
        raise RuntimeError(
            f"Falha ao drenar lease concorrente sintético: {erro_comando(resultado)}"
        )



def _sql_insert_planejador(payload: dict[str, object], tabela: str) -> str:
    colunas, valores = [], []
    for chave, valor in payload.items():
        colunas.append(chave)
        if chave in {"plano_tarefas", "consulta_spec"}:
            valores.append(sql_texto(json_canonico(valor)) + "::jsonb")
        elif chave == "campos_obrigatorios":
            valores.append(
                "ARRAY[" + ",".join(sql_texto(str(c)) for c in valor)
                + "]::text[]"
            )
        else:
            valores.append(sql_texto(str(valor)))
    return (
        f"INSERT INTO public.{tabela} ({', '.join(colunas)}) "
        f"VALUES ({', '.join(valores)})"
    )


def testar_planejador_investigacoes(banco: str) -> None:
    """Prova que os payloads REAIS do planejador passam no schema pós-0002.

    Usa o próprio módulo tools/planejador_investigacoes.py para derivar o
    plano de um rascunho sintético e insere exatamente o que
    ``registrar_investigacao`` enviaria via PostgREST: criação completa,
    repetição idempotente (unique_violation = caminho 409/'ja_existia') e o
    reparo de uma investigação que ficou sem a tarefa de fonte.
    """
    draft_id = "9a000000-0000-4000-8000-000000000001"
    resultado = psql(banco, f"""
INSERT INTO public.operation_drafts
  (id, agente, status, tipo_operacao, entidade_final_tipo, dados_extraidos,
   campos_pendentes, origem_canal, origem_conversa_id, origem_mensagem_id,
   contexto_nome, escopo, codigo_sugerido)
VALUES ('{draft_id}', 'juan', 'aguardando_confirmacao', 'compra_gado',
   'compras', '{{"quantidade": 12}}', ARRAY['valor_total'], 'telegram',
   '-100200300', '555', 'Compra Sintetica Planejador', 'teste_runtime',
   'NEG-26-901')
RETURNING atualizado_em;""")
    if resultado.returncode:
        raise RuntimeError(
            f"Falha ao criar rascunho do planejador: {erro_comando(resultado)}"
        )
    atualizado_em = resultado.stdout.strip().splitlines()[0]
    draft = {
        "id": draft_id, "status": "aguardando_confirmacao",
        "atualizado_em": atualizado_em, "entidade_final_tipo": "compras",
        "tipo_operacao": "compra_gado", "codigo_sugerido": "NEG-26-901",
        "contexto_nome": "Compra Sintetica Planejador",
        "origem_canal": "telegram", "origem_conversa_id": "-100200300",
        "origem_mensagem_id": "555", "dados_extraidos": {"quantidade": 12},
        "campos_pendentes": ["valor_total"], "escopo": "teste_runtime",
    }
    plano = planejador_modulo.planejar([draft], set(), 1, set())
    if len(plano["itens"]) != 1 or plano["itens"][0]["modo"] != "criar":
        raise RuntimeError(f"planejador não derivou o lote esperado: {plano!r}")
    item = plano["itens"][0]
    payload_inv = {
        "id": "9a000000-0000-4000-8000-000000000002",
        "chave_idempotencia": item["chaves"]["investigacao"],
        "assunto_tipo": item["assunto"]["tipo"],
        "titulo": item["assunto"]["titulo"],
        "fingerprint_base": item["fingerprint_base"],
        "plano_hash": item["plano_hash"],
        "plano_canonico": item["plano_canonico"],
        "plano_tarefas": item["tarefas"],
        "policy_version": planejador_modulo.biblioteca.VERSAO_POLITICA_PADRAO,
        "policy_schema_hash":
            planejador_modulo.biblioteca.HASH_SCHEMA_POLITICAS,
        "campos_obrigatorios": item["campos_obrigatorios"],
        "source_draft_id": item["operation_draft_id"],
        "source_draft_atualizado_em": item["source_draft_atualizado_em"],
        "escopo": item["escopo"],
    }
    fonte = next(t for t in item["tarefas"]
                 if t["adaptador"] == planejador_modulo.ADAPTADOR_FONTE)
    payload_tar = {
        "id": "9a000000-0000-4000-8000-000000000003",
        "investigacao_id": "9a000000-0000-4000-8000-000000000002",
        "chave_idempotencia": item["chaves"]["tarefa"],
        "plano_item_ref": fonte["plano_item_ref"],
        "adaptador": fonte["adaptador"],
        "adaptador_version": fonte["adaptador_version"],
        "consulta_ref": fonte["consulta_ref"],
        "consulta_schema_version": fonte["consulta_schema_version"],
        "consulta_spec": fonte["consulta_spec"],
        "consulta_canonico": fonte["consulta_canonico"],
        "consulta_hash": fonte["consulta_hash"],
    }
    sql_inv = _sql_insert_planejador(payload_inv, "investigacoes_revisao")
    sql_tar = _sql_insert_planejador(payload_tar, "investigacao_tarefas")
    resultado = psql(
        banco, "SET ROLE service_role; " + sql_inv + "; " + sql_tar
        + "; RESET ROLE;",
    )
    if resultado.returncode:
        raise RuntimeError(
            "payload real do planejador foi recusado pelo schema: "
            + erro_comando(resultado)
        )
    resultado = psql(banco, "SET ROLE service_role; " + sql_inv + ";")
    if not resultado.returncode or "duplicate key" not in (
        (resultado.stderr or "") + (resultado.stdout or "")
    ):
        raise RuntimeError(
            "repetição do planejador deveria ser unique_violation "
            f"(caminho 409/'ja_existia'): {erro_comando(resultado)}"
        )
    # Reparo: uma segunda investigação nasce SEM a tarefa (execução anterior
    # interrompida entre os dois INSERTs); o planejador precisa reoferecê-la
    # e a inserção tardia da tarefa precisa passar no schema.
    draft2 = dict(
        draft, id="9a000000-0000-4000-8000-000000000011",
        codigo_sugerido="NEG-26-902",
    )
    resultado = psql(banco, f"""
INSERT INTO public.operation_drafts
  (id, agente, status, tipo_operacao, entidade_final_tipo, origem_canal,
   escopo, codigo_sugerido)
VALUES ('{draft2["id"]}', 'juan', 'aguardando_confirmacao', 'compra_gado',
   'compras', 'telegram', 'teste_runtime', 'NEG-26-902')
RETURNING atualizado_em;""")
    if resultado.returncode:
        raise RuntimeError(
            f"Falha ao criar segundo rascunho: {erro_comando(resultado)}"
        )
    draft2["atualizado_em"] = resultado.stdout.strip().splitlines()[0]
    item2 = planejador_modulo.plano_do_draft(draft2)
    reparo = planejador_modulo.planejar(
        [draft2], {item2["chaves"]["investigacao"]}, 1, set(),
    )
    if (len(reparo["itens"]) != 1
            or reparo["itens"][0]["modo"] != "reparar_tarefa"):
        raise RuntimeError(f"reparo de tarefa ausente não planejado: {reparo!r}")
    payload_inv2 = dict(payload_inv)
    payload_inv2.update({
        "id": "9a000000-0000-4000-8000-000000000012",
        "chave_idempotencia": item2["chaves"]["investigacao"],
        "titulo": item2["assunto"]["titulo"],
        "fingerprint_base": item2["fingerprint_base"],
        "plano_hash": item2["plano_hash"],
        "plano_canonico": item2["plano_canonico"],
        "plano_tarefas": item2["tarefas"],
        "source_draft_id": item2["operation_draft_id"],
        "source_draft_atualizado_em": item2["source_draft_atualizado_em"],
    })
    fonte2 = next(t for t in item2["tarefas"]
                  if t["adaptador"] == planejador_modulo.ADAPTADOR_FONTE)
    payload_tar2 = dict(payload_tar)
    payload_tar2.update({
        "id": "9a000000-0000-4000-8000-000000000013",
        "investigacao_id": "9a000000-0000-4000-8000-000000000012",
        "chave_idempotencia": item2["chaves"]["tarefa"],
        "plano_item_ref": fonte2["plano_item_ref"],
        "consulta_ref": fonte2["consulta_ref"],
        "consulta_spec": fonte2["consulta_spec"],
        "consulta_canonico": fonte2["consulta_canonico"],
        "consulta_hash": fonte2["consulta_hash"],
    })
    resultado = psql(
        banco,
        "SET ROLE service_role; "
        + _sql_insert_planejador(payload_inv2, "investigacoes_revisao")
        + "; RESET ROLE;",
    )
    if resultado.returncode:
        raise RuntimeError(
            f"investigação do reparo foi recusada: {erro_comando(resultado)}"
        )
    resultado = psql(
        banco,
        "SET ROLE service_role; "
        + _sql_insert_planejador(payload_tar2, "investigacao_tarefas")
        + "; RESET ROLE;",
    )
    if resultado.returncode:
        raise RuntimeError(
            "inserção tardia da tarefa de fonte (reparo) foi recusada: "
            + erro_comando(resultado)
        )
    # Drena o cenário (como no teste de lease) para o rollback da 0002
    # encontrar a fila vazia.
    resultado = psql(banco, """
UPDATE public.investigacao_tarefas
   SET estado_execucao = 'cancelada',
       lease_executor = NULL, lease_token = NULL,
       lease_expira_em = NULL, lease_chave_id = NULL
 WHERE investigacao_id IN ('9a000000-0000-4000-8000-000000000002',
                           '9a000000-0000-4000-8000-000000000012');
UPDATE public.investigacoes_revisao
   SET estado_execucao = 'cancelada'
 WHERE id IN ('9a000000-0000-4000-8000-000000000002',
              '9a000000-0000-4000-8000-000000000012');
""")
    if resultado.returncode:
        raise RuntimeError(
            f"Falha ao drenar cenário do planejador: {erro_comando(resultado)}"
        )


def testar_worker_fonte_outro(banco: str) -> None:
    """Prova de ponta a ponta do worker de fonte contra o schema real.

    Planejador cria investigação+tarefa → ``assumir_tarefa_investigacao``
    entrega a tarefa (com a credencial sintética do gate) → o MÓDULO REAL
    ``tools/worker_fonte_outro.py`` busca no snapshot local, sela e assina o
    atestado HMAC → ``publicar_resultado_tarefa_investigacao`` aceita o
    resultado; a repetição idêntica é idempotente.
    """
    import worker_fonte_outro as worker_modulo

    draft = {
        "id": "9a000000-0000-4000-8000-000000000021",
        "status": "aguardando_confirmacao",
        "entidade_final_tipo": "compras", "tipo_operacao": "compra_gado",
        "codigo_sugerido": "NEG-26-903", "contexto_nome": "Compra Worker",
        "origem_canal": "telegram", "origem_conversa_id": "-100200300",
        "origem_mensagem_id": "556", "dados_extraidos": {},
        "campos_pendentes": [], "escopo": "teste_runtime",
    }
    resultado = psql(banco, f"""
INSERT INTO public.operation_drafts
  (id, agente, status, tipo_operacao, entidade_final_tipo, origem_canal,
   escopo, codigo_sugerido)
VALUES ('{draft["id"]}', 'juan', 'aguardando_confirmacao', 'compra_gado',
   'compras', 'telegram', 'teste_runtime', 'NEG-26-903')
RETURNING atualizado_em;""")
    if resultado.returncode:
        raise RuntimeError(
            f"Falha ao criar rascunho do worker: {erro_comando(resultado)}"
        )
    draft["atualizado_em"] = resultado.stdout.strip().splitlines()[0]
    item = planejador_modulo.plano_do_draft(draft)
    payload_inv = {
        "id": "9a000000-0000-4000-8000-000000000022",
        "chave_idempotencia": item["chaves"]["investigacao"],
        "assunto_tipo": item["assunto"]["tipo"],
        "titulo": item["assunto"]["titulo"],
        "fingerprint_base": item["fingerprint_base"],
        "plano_hash": item["plano_hash"],
        "plano_canonico": item["plano_canonico"],
        "plano_tarefas": item["tarefas"],
        "policy_version": planejador_modulo.biblioteca.VERSAO_POLITICA_PADRAO,
        "policy_schema_hash":
            planejador_modulo.biblioteca.HASH_SCHEMA_POLITICAS,
        "campos_obrigatorios": item["campos_obrigatorios"],
        "source_draft_id": item["operation_draft_id"],
        "source_draft_atualizado_em": item["source_draft_atualizado_em"],
        "escopo": item["escopo"],
    }
    fonte = next(t for t in item["tarefas"]
                 if t["adaptador"] == planejador_modulo.ADAPTADOR_FONTE)
    payload_tar = {
        "id": "9a000000-0000-4000-8000-000000000023",
        "investigacao_id": "9a000000-0000-4000-8000-000000000022",
        "chave_idempotencia": item["chaves"]["tarefa"],
        "plano_item_ref": fonte["plano_item_ref"],
        "adaptador": fonte["adaptador"],
        "adaptador_version": fonte["adaptador_version"],
        "consulta_ref": fonte["consulta_ref"],
        "consulta_schema_version": fonte["consulta_schema_version"],
        "consulta_spec": fonte["consulta_spec"],
        "consulta_canonico": fonte["consulta_canonico"],
        "consulta_hash": fonte["consulta_hash"],
    }
    resultado = psql(
        banco,
        "SET ROLE service_role; "
        + _sql_insert_planejador(payload_inv, "investigacoes_revisao") + "; "
        + _sql_insert_planejador(payload_tar, "investigacao_tarefas")
        + "; RESET ROLE;",
    )
    if resultado.returncode:
        raise RuntimeError(
            f"Falha ao plantar investigação do worker: {erro_comando(resultado)}"
        )
    resultado = psql(banco, (
        "SET ROLE service_role; "
        "SELECT public.assumir_tarefa_investigacao('outro', 'worker-e2e', 120);"
    ))
    if resultado.returncode:
        raise RuntimeError(f"Falha ao assumir tarefa: {erro_comando(resultado)}")
    linha_json = next(
        (l for l in resultado.stdout.splitlines() if l.strip().startswith("{")),
        None,
    )
    if not linha_json:
        raise RuntimeError(
            f"assumir não devolveu tarefa: {resultado.stdout!r}"
        )
    tarefa = json.loads(linha_json)
    if str(tarefa.get("id")) != payload_tar["id"]:
        raise RuntimeError(
            f"assumir devolveu tarefa inesperada: {tarefa.get('id')!r}"
        )
    snapshot = {
        "modo": "somente_leitura",
        "gerado_em": "2026-09-01T12:00:00+00:00",
        "tabelas": {"negocios_candidatos": [{
            "codigo_fonte": "NEG-26-903", "chave_rastreio": "rastreio-903",
            "nome": "Fornecedor Sintetico", "data_base": "2026-08-20",
            "quantidade": 12, "valor_total": 123456.78,
        }]},
    }
    bytes_snapshot = json_canonico(snapshot).encode("utf-8")
    leitura = {
        "ok": True, "snapshot": snapshot,
        "hash": hashlib.sha256(bytes_snapshot).hexdigest(),
    }
    resultado_worker = worker_modulo.montar_resultado(tarefa, leitura)
    if (resultado_worker["estado_cobertura"] != "completa"
            or len(resultado_worker["bundle"]["evidencias"]) != 1):
        raise RuntimeError(
            f"worker não achou a pista esperada: {resultado_worker!r}"
        )
    pedido = worker_modulo.montar_pedido_publicacao(
        tarefa, resultado_worker,
        segredo=bytes.fromhex("d" * 64),
        chave_id="key_teste-runtime",
        artefato_hash="c" * 64,
    )
    publicar_sql = (
        "SET ROLE service_role; "
        "SELECT public.publicar_resultado_tarefa_investigacao("
        f"'{pedido['p_tarefa_id']}'::uuid, "
        f"'{pedido['p_lease_token']}'::uuid, "
        f"{pedido['p_fencing_token']}, "
        f"{sql_texto(pedido['p_estado_cobertura'])}, "
        f"{sql_texto(pedido['p_estado_resultado'])}, "
        f"{sql_texto(json_canonico(pedido['p_bundle']))}::jsonb, "
        f"{sql_texto(json_canonico(pedido['p_atestado_cobertura']))}::jsonb, "
        f"{sql_texto(pedido['p_resumo_sanitizado'])});"
    )
    resultado = psql(banco, publicar_sql)
    if resultado.returncode:
        raise RuntimeError(
            "publicação do worker foi recusada pelo schema: "
            + erro_comando(resultado)
        )
    resultado = psql(banco, publicar_sql)
    if resultado.returncode:
        raise RuntimeError(
            "repetição idêntica da publicação deveria ser idempotente: "
            + erro_comando(resultado)
        )
    resultado = psql(banco, f"""
SELECT (SELECT count(*) FROM public.investigacao_evidencias
         WHERE tarefa_id = '{payload_tar["id"]}')
    || ':' ||
       (SELECT estado_execucao FROM public.investigacao_tarefas
         WHERE id = '{payload_tar["id"]}');""")
    if resultado.returncode or resultado.stdout.strip().splitlines()[0] != "1:concluida":
        raise RuntimeError(
            "estado final do worker inesperado: "
            + (resultado.stdout or resultado.stderr)
        )
    # Drena para o rollback da 0002 encontrar a fila vazia.
    resultado = psql(banco, f"""
UPDATE public.investigacoes_revisao
   SET estado_execucao = 'cancelada'
 WHERE id = '{payload_inv["id"]}';
""")
    if resultado.returncode:
        raise RuntimeError(
            f"Falha ao drenar cenário do worker: {erro_comando(resultado)}"
        )


def executar_teste(obrigatorio: bool) -> int:
    for migracao in (MIGRACAO, MIGRACAO_ATIVACAO, ROLLBACK_ATIVACAO):
        if not migracao.exists():
            raise RuntimeError(f"migração não encontrada: {migracao}")
    if not all(shutil.which(comando) for comando in ("psql", "createdb", "dropdb")):
        mensagem = "PostgreSQL CLI não disponível; runtime será executado no CI"
        if obrigatorio:
            raise RuntimeError(mensagem)
        print(f"SKIP_RUNTIME_POSTGRES: {mensagem}")
        return 0
    nome = f"confinex_rt_{secrets.token_hex(6)}"
    roles_antes = roles_existentes()
    criado = False
    try:
        resultado = executar(["createdb", nome])
        if resultado.returncode:
            raise RuntimeError(f"não foi possível criar banco efêmero: {erro_comando(resultado)}")
        criado = True
        resultado = psql(nome, fixture_sql())
        if resultado.returncode:
            raise RuntimeError(f"falha no fixture PostgreSQL: {erro_comando(resultado)}")
        aplicar_migracao(nome, MIGRACAO)
        # Reaplicar a migração verifica CREATE IF NOT EXISTS/OR REPLACE e não duplica objetos.
        aplicar_migracao(nome, MIGRACAO, segunda_vez=True)
        validar_compatibilidade_sombra(nome)
        testar_backfill_atualizado_em_nulo_legado(nome)
        testar_executor_legado_operacional(nome, apos_rollback=False)
        testar_sombra_sem_outbox(nome)
        preparar_gate_ativacao(nome)
        aplicar_migracao(nome, MIGRACAO_ATIVACAO)
        aplicar_migracao(nome, MIGRACAO_ATIVACAO, segunda_vez=True)
        validar_catalogo(nome)
        testar_guardiao_operacional_ativado(nome)
        inserir_fixture_e_validar_guardas(nome)
        testar_sucessoes_terminal_sem_gravacao(nome)
        testar_replanejamento_terminal_com_gravacao(nome)
        testar_replanejamento_corretiva_stale_materializada(nome)
        testar_obsolescencia_draft(nome)
        testar_lease_concorrente(nome)
        testar_planejador_investigacoes(nome)
        testar_worker_fonte_outro(nome)
        aplicar_migracao(nome, ROLLBACK_ATIVACAO)
        aplicar_migracao(nome, ROLLBACK_ATIVACAO, segunda_vez=True)
        validar_reversao_ativacao(nome)
        validar_compatibilidade_sombra(nome)
        testar_executor_legado_operacional(nome, apos_rollback=True)
        print(
            "RUNTIME_POSTGRES_OK: migração, RLS, privilégios, guardas, "
            "lease exclusivo e rollback passaram"
        )
        return 0
    finally:
        if criado:
            executar(["dropdb", "--if-exists", nome])
        # Só remove papéis que este banco de teste criou em um cluster efêmero.
        for papel in ("anon", "authenticated", "service_role"):
            if papel not in roles_antes:
                executar(["dropuser", "--if-exists", papel])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--obrigatorio",
        action="store_true",
        help="falha se psql/createdb/dropdb não estiverem disponíveis (use no CI)",
    )
    args = parser.parse_args()
    try:
        return executar_teste(args.obrigatorio)
    except (RuntimeError, subprocess.TimeoutExpired) as erro:
        print(f"RUNTIME_POSTGRES_FALHOU: {erro}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
