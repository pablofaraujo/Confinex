#!/usr/bin/env python3
"""Converte candidatos fortes do staging em revisões, nunca em operações."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.parse
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
RPC_MATERIALIZACAO_INVESTIGADA = "materializar_revisao_investigada"
VIEW_INVESTIGACOES_MATERIALIZACAO = "v_investigacoes_revisao_materializacao"
CAMPO_RETRATO_REVISAO = (
    "codigo_fonte", "chave_rastreio", "nome", "contexto", "sexo", "categoria",
    "destino", "data_base", "situacao_origem", "prioridade", "quantidade",
    "peso_total_kg", "preco_arroba", "valor_total", "pagamento_descricao",
    "campos_faltantes", "divergencias", "acao_recomendada", "operacao_id",
)
CAMPOS_TECNICOS_VERSAO = {
    "staging_candidato_id",
    "staging_candidato_ids",
    "staging_candidato_atualizado_em",
    "staging_candidatos_atualizados_em",
    "fingerprint_base",
    "fingerprint_grupo",
    "origem_conversa_id",
    "origem_mensagem_id",
}

CAMPOS_HUMANOS_CORRETIVA = {
    "operacao_negocio", "referencia_negocio", "tipo_negocio", "lote",
    "fornecedor", "contraparte", "cabecas", "quantidade", "categoria",
    "sexo", "peso_total_kg", "peso_medio_kg", "peso_liquido_kg",
    "peso_carcaca_total", "preco_arroba", "valor_total", "valor_bruto",
    "valor_liquido", "data", "data_compra", "data_abate",
    "previsao_recebimento", "prazo_recebimento", "pagamento", "documento",
    "numero_nf", "destino", "situacao", "acao_recomendada", "evidencia",
    "contexto_nome", "contexto_operacional", "grupo_telegram",
}


def id_deterministico(tipo: str, candidato_id: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{tipo}:{candidato_id}"))


def contexto_canonico(nome: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", nome.casefold()).strip("-") or "geral"
    return f"consolidacao:{slug}"


def retrato_candidato_revisao(item: dict[str, Any]) -> dict[str, Any]:
    """Recorte público e determinístico dos campos que alimentam a Revisão."""
    return {campo: item.get(campo) for campo in CAMPO_RETRATO_REVISAO}


def fingerprint_retrato_candidato(item: dict[str, Any]) -> str:
    retrato = retrato_candidato_revisao(item)
    serializado = json.dumps(
        retrato, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    )
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def metadados_grupo_staging(candidatos: list[dict[str, Any]]) -> dict[str, Any]:
    """Projeção determinística de todos os candidatos que compõem o grupo."""
    membros = sorted(candidatos, key=lambda candidato: str(candidato.get("id") or ""))
    retratos_ordenados = sorted(
        (retrato_candidato_revisao(membro) for membro in membros),
        key=lambda retrato: json.dumps(
            retrato, ensure_ascii=False, sort_keys=True,
            separators=(",", ":"), default=str,
        ),
    )
    serializado = json.dumps(
        retratos_ordenados, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str,
    )
    ids = [str(membro["id"]) for membro in membros]
    return {
        "staging_candidato_ids": ids,
        "staging_candidatos_atualizados_em": {
            candidato_id: membro.get("atualizado_em")
            for candidato_id, membro in zip(ids, membros)
        },
        "fingerprint_grupo": hashlib.sha256(serializado.encode("utf-8")).hexdigest(),
    }


def dados_revisao(item: dict[str, Any]) -> dict[str, Any]:
    contexto = str(item.get("contexto") or "Consolidação operacional")
    referencia = str(item.get("codigo_fonte") or item.get("chave_rastreio") or "")
    fingerprint_base = fingerprint_retrato_candidato(item)
    staging_atualizado_em = item.get("atualizado_em")
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
        "fingerprint_base": fingerprint_base,
        "staging_candidato_atualizado_em": staging_atualizado_em,
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


def montar_registros(
    item: dict[str, Any],
    *, identidade: str | None = None,
    versoes: list[dict[str, Any]] | None = None,
    candidatos_grupo: list[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    candidato_id = str(item["id"])
    identidade = str(identidade or candidato_id)
    draft_id = id_deterministico("draft", identidade)
    action_id = id_deterministico("action", identidade)
    event_id = id_deterministico("event", identidade)
    contexto = str(item.get("contexto") or "Consolidação operacional")
    canonico = contexto_canonico(contexto)
    origem = f"staging:{candidato_id}"
    dados = dados_revisao(item)
    staging_atualizado_em = item.get("atualizado_em")
    metadados_grupo = metadados_grupo_staging(candidatos_grupo or [item])
    fingerprint_base = str(metadados_grupo["fingerprint_grupo"])
    dados.update(metadados_grupo)
    dados["fingerprint_base"] = fingerprint_base
    if versoes:
        dados["versoes_revisao"] = versoes
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
            "fingerprint_base": fingerprint_base,
            "staging_candidato_atualizado_em": staging_atualizado_em,
            **metadados_grupo,
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
            "fingerprint_base": fingerprint_base,
            "staging_candidato_atualizado_em": staging_atualizado_em,
            **metadados_grupo,
            "dados_extraidos": dados,
            "campos_pendentes": draft["campos_pendentes"],
            "promovido_para_operacional": False,
        },
        "resultado": {
            "operation_draft_id": draft_id, "staging_candidato_id": candidato_id,
            "fingerprint_base": fingerprint_base,
            "staging_candidato_atualizado_em": staging_atualizado_em,
            **metadados_grupo,
        },
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
            "fingerprint_base": fingerprint_base,
            "staging_candidato_atualizado_em": staging_atualizado_em,
            **metadados_grupo,
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
        if item.get("operacao_id"):
            motivos["ja_vinculado_operacao"] += 1
            continue
        chave_grupo = chave_aparente(item)
        if grupos[chave_grupo] > 1:
            membros = sorted(
                (candidato for candidato in candidatos if chave_aparente(candidato) == chave_grupo),
                key=lambda candidato: str(candidato.get("id") or ""),
            )
            identidade = f"duplicidade:{chave_grupo}"
            snapshots = []
            vistos = set()
            for membro in membros:
                snapshot = dados_revisao(membro)
                for campo in CAMPOS_TECNICOS_VERSAO:
                    snapshot.pop(campo, None)
                serializado = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, default=str)
                if serializado in vistos:
                    continue
                vistos.add(serializado)
                snapshots.append(snapshot)
            if item is not membros[0]:
                motivos["duplicidade_aparente_colapsada"] += 1
                continue
            motivos["grupo_duplicado_materializado_com_versoes"] += 1
            registros = montar_registros(
                membros[0], identidade=identidade, versoes=snapshots,
                candidatos_grupo=membros,
            )
        else:
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


def associar_investigacoes_concluidas(
    plano: dict[str, Any],
    investigacoes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Vincula somente investigação concluída cujo grupo e snapshot são exatos."""
    for conjunto in plano.get("registros", []):
        draft = conjunto["operation_drafts"]
        inferencias = draft.get("inferencias") or {}
        ids = sorted(str(item) for item in inferencias.get("staging_candidato_ids") or [])
        fingerprint = str(
            inferencias.get("fingerprint_grupo")
            or inferencias.get("fingerprint_base")
            or ""
        )
        correspondentes = [
            item for item in investigacoes
            if item.get("estado_execucao") == "concluida"
            and not item.get("anexado_em")
            and not item.get("source_draft_id")
            and sorted(str(valor) for valor in item.get("negocio_candidato_ids") or []) == ids
            and str(item.get("fingerprint_base") or "") == fingerprint
        ]
        if len(correspondentes) > 1:
            raise ValueError("mais_de_uma_investigacao_compativel")
        if correspondentes:
            conjunto["investigacao_id"] = str(correspondentes[0]["id"])
    return plano


def montar_registros_corretivos(
    investigacao: dict[str, Any],
    draft_origem: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Monta revisão humana pós-gravação, sem prévia nem destino executável."""
    investigacao_id = str(investigacao["id"])
    draft_id = id_deterministico("draft-corretivo", investigacao_id)
    action_id = id_deterministico("action-corretiva", investigacao_id)
    event_id = id_deterministico("event-corretivo", investigacao_id)
    contexto = str(
        draft_origem.get("contexto_nome") or "Conferência pós-lançamento"
    )
    dados_origem = draft_origem.get("dados_extraidos") or {}
    dados = {
        chave: valor
        for chave, valor in dados_origem.items()
        if chave in CAMPOS_HUMANOS_CORRETIVA and valor not in (None, "")
    }
    dados["situacao"] = (
        "Conferência corretiva de evidência recebida depois do lançamento."
    )
    dados["acao_recomendada"] = (
        "Conferir a diferença e registrar a decisão; não criar novo lançamento."
    )
    fingerprint = str(investigacao.get("fingerprint_base") or "")
    pendentes = list(draft_origem.get("campos_pendentes") or [])
    pendentes.append("conferir a correção do lançamento já realizado")
    if investigacao.get("vinculo_operacional_estado") == "incerto":
        pendentes.append("localizar o registro operacional antes de concluir")
    pendentes = list(dict.fromkeys(str(item) for item in pendentes if item))
    origem_canal = draft_origem.get("origem_canal") or "investigacao_revisao"
    origem_conversa = draft_origem.get("origem_conversa_id")
    origem_mensagem = draft_origem.get("origem_mensagem_id")
    canonico = draft_origem.get("contexto_canonico")
    escopo = draft_origem.get("escopo") or "investigacao_operacional"
    draft = {
        "id": draft_id,
        "agente": "codex",
        "status": "em_revisao",
        "tipo_operacao": "correcao_pos_gravacao",
        "entidade_final_tipo": "correcao_pos_gravacao",
        "confianca": 0.5,
        "dados_extraidos": dados,
        "campos_pendentes": pendentes,
        "inferencias": {
            "fingerprint_base": fingerprint,
            "exige_confirmacao": True,
            "promovido_para_operacional": False,
        },
        "pending_action_id": action_id,
        "origem_canal": origem_canal,
        "origem_conversa_id": origem_conversa,
        "origem_mensagem_id": origem_mensagem,
        "contexto_canonico": canonico,
        "contexto_nome": contexto,
        "escopo": escopo,
    }
    action = {
        "id": action_id,
        "agente": "codex",
        "usuario_solicitante": "sistema",
        "canal": "investigacao_revisao",
        "acao_tipo": "revisar_correcao_pos_gravacao",
        "entidade_tipo": "operation_draft",
        "entidade_id": draft_id,
        "resumo": "Conferir evidência posterior ao lançamento",
        "payload": {
            "operation_draft_id": draft_id,
            "fingerprint_base": fingerprint,
            "dados_extraidos": dados,
            "campos_pendentes": pendentes,
            "executavel": False,
            "promovido_para_operacional": False,
        },
        "resultado": {},
        "status": "aguardando_confirmacao",
        "origem_canal": origem_canal,
        "origem_conversa_id": origem_conversa,
        "origem_mensagem_id": origem_mensagem,
        "contexto_canonico": canonico,
        "contexto_nome": contexto,
        "escopo": escopo,
    }
    event = {
        "id": event_id,
        "tipo": "correcao_pos_gravacao_enviada_para_revisao",
        "agente": "codex",
        "usuario": "sistema",
        "entidade_tipo": "operation_draft",
        "entidade_id": draft_id,
        "origem": "investigacoes_revisao",
        "origem_canal": origem_canal,
        "origem_conversa_id": origem_conversa,
        "origem_mensagem_id": origem_mensagem,
        "contexto_canonico": canonico,
        "contexto_nome": contexto,
        "escopo": escopo,
        "status": "pendente",
        "fonte_ref": str(investigacao.get("referencia_publica") or ""),
        "confianca": 0.5,
        "dados": {
            "operation_draft_id": draft_id,
            "pending_action_id": action_id,
            "fingerprint_base": fingerprint,
            "promovido_para_operacional": False,
        },
        "observacao": (
            "Revisão corretiva aberta sem preparar nem criar lançamento."
        ),
    }
    return {"operation_drafts": draft, "pending_actions": action, "eventos": event}


def adicionar_corretivas_materializaveis(
    plano: dict[str, Any],
    investigacoes: list[dict[str, Any]],
    drafts: list[dict[str, Any]],
) -> dict[str, Any]:
    drafts_por_id = {str(item.get("id")): item for item in drafts}
    existentes = {
        str(conjunto["operation_drafts"]["id"])
        for conjunto in plano.get("registros", [])
    }
    for investigacao in investigacoes:
        if (
            investigacao.get("fluxo_tipo") != "corretiva_pos_gravacao"
            or investigacao.get("estado_execucao") != "concluida"
            or investigacao.get("anexado_em")
        ):
            continue
        origem_id = str(investigacao.get("draft_operacional_origem_id") or "")
        draft_origem = drafts_por_id.get(origem_id)
        if not draft_origem:
            continue
        conjunto = montar_registros_corretivos(investigacao, draft_origem)
        conjunto["investigacao_id"] = str(investigacao["id"])
        if conjunto["operation_drafts"]["id"] in existentes:
            continue
        plano.setdefault("registros", []).append(conjunto)
        existentes.add(conjunto["operation_drafts"]["id"])
    plano["resumo"]["revisoes_planejadas"] = len(plano.get("registros", []))
    for tabela in ("operation_drafts", "pending_actions", "eventos"):
        plano["resumo"][tabela] = len(plano.get("registros", []))
    assinatura = sorted(
        conjunto["operation_drafts"]["id"]
        for conjunto in plano.get("registros", [])
    )
    plano["plano_id"] = hashlib.sha256(
        "\n".join(assinatura).encode()
    ).hexdigest()[:12]
    return plano


def listar_investigacoes_materializaveis(
    leitor: LeitorSupabase,
) -> list[dict[str, Any]]:
    """Lê somente a projeção técnica fechada, sem ampliar o snapshot canônico."""
    linhas: list[dict[str, Any]] = []
    limite, inicio = 1000, 0
    while True:
        consulta = urllib.parse.urlencode({
            "select": (
                "id,referencia_publica,source_draft_id,negocio_candidato_ids,"
                "fingerprint_base,"
                "estado_execucao,anexado_em,fluxo_tipo,promocao_origem_id,"
                "draft_operacional_origem_id,destino_operacional_origem,"
                "registro_operacional_origem_id,"
                "registro_operacional_origem_snapshot_ref,"
                "vinculo_operacional_estado"
            ),
            "order": "id.asc",
            "limit": limite,
            "offset": inicio,
        })
        requisicao = urllib.request.Request(
            f"{leitor.url}/rest/v1/{VIEW_INVESTIGACOES_MATERIALIZACAO}?{consulta}",
            method="GET",
            headers={
                "apikey": leitor.chave,
                "Authorization": f"Bearer {leitor.chave}",
            },
        )
        try:
            with leitor.opener(requisicao, timeout=leitor.timeout) as resposta:
                pagina = json.loads(resposta.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"leitura da investigação falhou com HTTP {exc.code}"
            ) from exc
        if not isinstance(pagina, list) or not all(
            isinstance(item, dict) for item in pagina
        ):
            raise RuntimeError("resposta inválida na projeção de investigações")
        linhas.extend(pagina)
        if len(pagina) < limite:
            return linhas
        inicio += limite


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

    def materializar_investigada(
        self,
        investigacao_id: str,
        conjunto: dict[str, Any],
    ) -> dict[str, Any]:
        payload = {
            "p_investigacao_id": str(investigacao_id),
            "p_operation_draft": conjunto["operation_drafts"],
            "p_pending_action": conjunto["pending_actions"],
            "p_evento": conjunto["eventos"],
        }
        requisicao = urllib.request.Request(
            f"{self.url}/rest/v1/rpc/{RPC_MATERIALIZACAO_INVESTIGADA}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers={
                "apikey": self.chave,
                "Authorization": f"Bearer {self.chave}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=self.timeout) as resposta:
                if resposta.status not in {200, 201}:
                    raise RuntimeError(
                        f"RPC de materialização falhou com HTTP {resposta.status}"
                    )
                corpo = resposta.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"RPC de materialização falhou com HTTP {exc.code}"
            ) from exc
        retorno = json.loads(corpo or "{}")
        if not isinstance(retorno, dict):
            raise RuntimeError("RPC de materialização retornou formato inválido")
        return retorno


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


def executar_investigado(
    plano: dict[str, Any], escritor: EscritorRevisao, limite: int
) -> dict[str, int]:
    if limite <= 0:
        raise ValueError("execução exige limite positivo")
    registros = plano["registros"][:limite]
    if any(not conjunto.get("investigacao_id") for conjunto in registros):
        raise ValueError("investigacao_concluida_obrigatoria")
    criadas = 0
    idempotentes = 0
    nao_materializadas = 0
    for conjunto in registros:
        retorno = escritor.materializar_investigada(
            conjunto["investigacao_id"], conjunto
        )
        if not isinstance(retorno.get("materializada"), bool):
            raise RuntimeError("RPC não informou se a revisão foi materializada")
        if retorno["materializada"]:
            criadas += 1
        elif retorno.get("motivo") == "investigacao_ja_materializada":
            idempotentes += 1
        else:
            nao_materializadas += 1
    return {
        "revisoes_criadas": criadas,
        "operation_drafts_criados": criadas,
        "pending_actions_criadas": criadas,
        "eventos_criados": criadas,
        "revisoes_idempotentes": idempotentes,
        "revisoes_nao_materializadas": nao_materializadas,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executar", action="store_true")
    parser.add_argument("--limite", type=int, default=0)
    parser.add_argument("--confirmacao")
    parser.add_argument(
        "--exigir-investigacao",
        action="store_true",
        help="materializa e anexa por uma única RPC após investigação concluída",
    )
    args = parser.parse_args()
    url = os.environ.get("SUPABASE_URL") or os.environ.get("CONFINEX_DB_URL") or ""
    chave = (
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("CONFINEX_DB_KEY")
        or ""
    )
    leitor = LeitorSupabase(url, chave)
    candidatos = leitor.listar("negocios_candidatos")
    operacoes = leitor.listar("operacoes")
    drafts = leitor.listar("operation_drafts")
    actions = leitor.listar("pending_actions")
    events = leitor.listar("eventos")
    plano = planejar(
        candidatos, operacoes, drafts, actions, events,
    )
    if args.exigir_investigacao:
        investigacoes = listar_investigacoes_materializaveis(leitor)
        associar_investigacoes_concluidas(plano, investigacoes)
        adicionar_corretivas_materializaveis(plano, investigacoes, drafts)
    resultado = {
        "revisoes_criadas": 0,
        "operation_drafts_criados": 0,
        "pending_actions_criadas": 0,
        "eventos_criados": 0,
        "revisoes_idempotentes": 0,
        "revisoes_nao_materializadas": 0,
    }
    if args.executar:
        esperada = f"MATERIALIZAR REVISOES {plano['plano_id']}"
        if args.confirmacao != esperada:
            raise SystemExit(f"confirmação inválida; use: {esperada}")
        escritor = EscritorRevisao(url, chave)
        resultado = (
            executar_investigado(plano, escritor, args.limite)
            if args.exigir_investigacao
            else executar(plano, escritor, args.limite)
        )
        plano["modo"] = "executado"
    print(json.dumps({
        "plano_id": plano["plano_id"],
        "modo": plano["modo"],
        "resumo": plano["resumo"],
        **resultado,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
