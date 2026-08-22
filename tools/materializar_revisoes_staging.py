#!/usr/bin/env python3
"""Converte candidatos fortes do staging em revisões, nunca em operações."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from collections import Counter
from typing import Any

try:
    from auditar_staging_consolidacao import (
        chave_aparente,
        quantidade_correspondencias,
        referencias_candidato,
        referencias_json,
        separar_campos_faltantes,
    )
    from exportar_snapshot_consolidacao import LeitorSupabase
except ModuleNotFoundError:
    from tools.auditar_staging_consolidacao import (
        chave_aparente,
        quantidade_correspondencias,
        referencias_candidato,
        referencias_json,
        separar_campos_faltantes,
    )
    from tools.exportar_snapshot_consolidacao import LeitorSupabase


NAMESPACE = uuid.UUID("0ea87d02-6a6f-475f-b6e0-f4c2d9ad9b29")
TABELAS_ESCRITA = {"operation_drafts", "pending_actions", "eventos"}


def id_deterministico(tipo: str, candidato_id: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{tipo}:{candidato_id}"))


def contexto_canonico(nome: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", nome.casefold()).strip("-") or "geral"
    return f"consolidacao:{slug}"


def dados_revisao(item: dict[str, Any]) -> dict[str, Any]:
    contexto = str(item.get("contexto") or "Consolidação operacional")
    referencia = str(item.get("codigo_fonte") or item.get("chave_rastreio") or "")
    return {
        "operacao_negocio": referencia,
        "referencia_negocio": referencia,
        "tipo_negocio": "compra",
        "lote": referencia,
        "fornecedor": item.get("nome"),
        "cabecas": item.get("quantidade"),
        "categoria": item.get("categoria"),
        "peso_total_kg": item.get("peso_total_kg"),
        "preco_arroba": item.get("preco_arroba"),
        "valor_total": item.get("valor_total"),
        "data": item.get("data_base"),
        "pagamento": item.get("pagamento_descricao"),
        "destino": item.get("destino"),
        "sexo": item.get("sexo"),
        "situacao": "Candidato consolidado de fontes privadas, aguardando conferência.",
        "acao_recomendada": item.get("acao_recomendada") or "Conferir e relacionar ao negócio correto.",
        "evidencia": "Dados preservados no staging auditável; nenhuma operação foi criada.",
        "staging_candidato_id": item.get("id"),
        "contexto_nome": contexto,
        "contexto_operacional": contexto,
        "grupo_telegram": contexto,
        "origem_canal": "consolidacao_privada",
        "origem_conversa_id": f"staging:{item.get('id')}",
        "origem_mensagem_id": f"staging:{item.get('id')}",
        "agente": "codex",
        "escopo": "consolidacao_operacional",
        "status_confirmacao": "em_revisao",
    }


def montar_registros(item: dict[str, Any]) -> dict[str, dict[str, Any]]:
    candidato_id = str(item["id"])
    draft_id = id_deterministico("draft", candidato_id)
    action_id = id_deterministico("action", candidato_id)
    event_id = id_deterministico("event", candidato_id)
    contexto = str(item.get("contexto") or "Consolidação operacional")
    canonico = contexto_canonico(contexto)
    origem = f"staging:{candidato_id}"
    dados = dados_revisao(item)
    pendentes = separar_campos_faltantes(item.get("campos_faltantes"))
    pendentes.append("confirmar vínculo com negócio operacional existente ou novo")
    draft = {
        "id": draft_id,
        "agente": "codex",
        "status": "em_revisao",
        "tipo_operacao": "consolidacao_compra_planilha",
        "entidade_final_tipo": "compras",
        "confianca": 0.8,
        "dados_extraidos": dados,
        "campos_pendentes": list(dict.fromkeys(pendentes)),
        "inferencias": {
            "staging_candidato_id": candidato_id,
            "confirmado_na_planilha": bool((item.get("dados_origem") or {}).get("confirmado_na_planilha")),
            "promovido_para_operacional": False,
            "exige_confirmacao": True,
        },
        "pending_action_id": action_id,
        "origem_canal": "consolidacao_privada",
        "origem_conversa_id": origem,
        "origem_mensagem_id": origem,
        "contexto_canonico": canonico,
        "contexto_nome": contexto,
        "escopo": "consolidacao_operacional",
    }
    action = {
        "id": action_id,
        "agente": "codex",
        "usuario_solicitante": "sistema",
        "canal": "consolidacao_privada",
        "acao_tipo": "revisar_consolidacao_negocio",
        "entidade_tipo": "operation_draft",
        "entidade_id": draft_id,
        "resumo": "Conferir candidato consolidado e relacionar ao negócio correto",
        "payload": {
            "operation_draft_id": draft_id,
            "staging_candidato_id": candidato_id,
            "dados_extraidos": dados,
            "campos_pendentes": draft["campos_pendentes"],
            "promovido_para_operacional": False,
        },
        "resultado": {"operation_draft_id": draft_id, "staging_candidato_id": candidato_id},
        "status": "aguardando_confirmacao",
        "origem_canal": "consolidacao_privada",
        "origem_conversa_id": origem,
        "origem_mensagem_id": origem,
        "contexto_canonico": canonico,
        "contexto_nome": contexto,
        "escopo": "consolidacao_operacional",
    }
    event = {
        "id": event_id,
        "tipo": "candidato_consolidado_enviado_para_revisao",
        "agente": "codex",
        "usuario": "sistema",
        "entidade_tipo": "operation_draft",
        "entidade_id": draft_id,
        "origem": "staging_consolidacao_privada",
        "origem_canal": "consolidacao_privada",
        "origem_conversa_id": origem,
        "origem_mensagem_id": origem,
        "contexto_canonico": canonico,
        "contexto_nome": contexto,
        "escopo": "consolidacao_operacional",
        "status": "pendente",
        "fonte_ref": origem,
        "confianca": 0.8,
        "dados": {
            "operation_draft_id": draft_id,
            "pending_action_id": action_id,
            "staging_candidato_id": candidato_id,
            "promovido_para_operacional": False,
        },
        "observacao": "Candidato consolidado disponibilizado para conferência, sem lançamento operacional.",
    }
    return {"pending_actions": action, "operation_drafts": draft, "eventos": event}


def planejar(
    candidatos: list[dict[str, Any]],
    operacoes: list[dict[str, Any]],
    drafts: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    grupos = Counter(chave_aparente(item) for item in candidatos)
    existentes_ids = {
        "operation_drafts": {str(item.get("id")) for item in drafts if item.get("id")},
        "pending_actions": {str(item.get("id")) for item in actions if item.get("id")},
        "eventos": {str(item.get("id")) for item in events if item.get("id")},
    }
    revisoes = drafts + actions
    selecionados, motivos = [], Counter()
    for item in candidatos:
        if item.get("estado") not in {"rascunho", "em_revisao"}:
            motivos["estado_fora_da_revisao"] += 1
            continue
        if item.get("prioridade") != "alta":
            motivos["prioridade_nao_alta"] += 1
            continue
        if separar_campos_faltantes(item.get("campos_faltantes")):
            motivos["campos_faltantes"] += 1
            continue
        if item.get("operacao_id"):
            motivos["ja_vinculado_operacao"] += 1
            continue
        if grupos[chave_aparente(item)] > 1:
            motivos["duplicidade_aparente_preservada"] += 1
            continue
        registros = montar_registros(item)
        presencas = {
            tabela: registro["id"] in existentes_ids[tabela]
            for tabela, registro in registros.items()
        }
        if all(presencas.values()):
            motivos["conjunto_deterministico_existente"] += 1
            continue
        if any(presencas.values()):
            motivos["conjunto_parcial_a_completar"] += 1
            selecionados.append(registros)
            continue
        refs = referencias_candidato(item)
        if quantidade_correspondencias(refs, operacoes):
            motivos["referencia_ja_operacional"] += 1
            continue
        if quantidade_correspondencias(refs, revisoes):
            motivos["referencia_ja_na_fila"] += 1
            continue
        selecionados.append(registros)
    assinatura_ids = sorted(
        registro["operation_drafts"]["id"] for registro in selecionados
    )
    plano_id = hashlib.sha256("\n".join(assinatura_ids).encode()).hexdigest()[:12]
    return {
        "plano_id": plano_id,
        "modo": "dry_run",
        "registros": selecionados,
        "resumo": {
            "candidatos_lidos": len(candidatos),
            "revisoes_planejadas": len(selecionados),
            "ignorados_por_motivo": dict(sorted(motivos.items())),
            "operation_drafts": len(selecionados),
            "pending_actions": len(selecionados),
            "eventos": len(selecionados),
            "tabelas_operacionais_alteradas": 0,
        },
    }


class EscritorRevisao:
    def __init__(self, url: str, chave: str, timeout: int = 20) -> None:
        self.url, self.chave = url.rstrip("/"), chave
        self.timeout = max(1, min(int(timeout), 20))

    def inserir(self, tabela: str, payload: dict[str, Any]) -> None:
        if tabela not in TABELAS_ESCRITA:
            raise ValueError(f"escrita não permitida: {tabela}")
        requisicao = urllib.request.Request(
            f"{self.url}/rest/v1/{tabela}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "apikey": self.chave,
                "Authorization": f"Bearer {self.chave}",
                "Content-Type": "application/json",
                "Prefer": "resolution=ignore-duplicates,return=minimal",
            },
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=self.timeout) as resposta:
                if resposta.status not in {200, 201, 204}:
                    raise RuntimeError(f"HTTP inesperado em {tabela}: {resposta.status}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"escrita em {tabela} falhou com HTTP {exc.code}") from exc


def executar(plano: dict[str, Any], escritor: EscritorRevisao, limite: int) -> dict[str, int]:
    if limite <= 0:
        raise ValueError("execução exige limite positivo")
    registros = plano["registros"][:limite]
    for conjunto in registros:
        for tabela in ("pending_actions", "operation_drafts", "eventos"):
            escritor.inserir(tabela, conjunto[tabela])
    return {
        "revisoes_criadas": len(registros),
        "operation_drafts_criados": len(registros),
        "pending_actions_criadas": len(registros),
        "eventos_criados": len(registros),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executar", action="store_true")
    parser.add_argument("--limite", type=int, default=0)
    parser.add_argument("--confirmacao")
    args = parser.parse_args()
    url = os.environ.get("SUPABASE_URL") or os.environ.get("CONFINEX_DB_URL") or ""
    chave = (
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("CONFINEX_DB_KEY")
        or ""
    )
    leitor = LeitorSupabase(url, chave)
    plano = planejar(
        leitor.listar("negocios_candidatos"),
        leitor.listar("operacoes"),
        leitor.listar("operation_drafts"),
        leitor.listar("pending_actions"),
        leitor.listar("eventos"),
    )
    resultado = {
        "revisoes_criadas": 0,
        "operation_drafts_criados": 0,
        "pending_actions_criadas": 0,
        "eventos_criados": 0,
    }
    if args.executar:
        esperada = f"MATERIALIZAR REVISOES {plano['plano_id']}"
        if args.confirmacao != esperada:
            raise SystemExit(f"confirmação inválida; use: {esperada}")
        resultado = executar(plano, EscritorRevisao(url, chave), args.limite)
        plano["modo"] = "executado"
    print(json.dumps({
        "plano_id": plano["plano_id"],
        "modo": plano["modo"],
        "resumo": plano["resumo"],
        **resultado,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
