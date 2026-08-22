#!/usr/bin/env python3
"""Audita o staging privado em memória e emite somente métricas sanitizadas."""

from __future__ import annotations

import collections
import hashlib
import json
import os
import re
from typing import Any

try:
    from exportar_snapshot_consolidacao import LeitorSupabase, assinatura
except ModuleNotFoundError:
    from tools.exportar_snapshot_consolidacao import LeitorSupabase, assinatura


TABELAS_STAGING = (
    "fontes_importacao",
    "negocios_candidatos",
    "negocio_versoes",
    "evidencias_negocio",
    "transacoes_banco_staging",
    "conciliacoes_candidatas",
    "vinculos_documentais_candidatos",
    "decisoes_consolidacao",
)
TABELAS_REVISAO = ("operation_drafts", "pending_actions", "eventos")
TABELAS_OPERACIONAIS = (
    "operacoes",
    "compras",
    "vendas",
    "abates",
    "pesagens_caderno",
    "transacoes_banco",
    "fluxo_caixa",
    "gtas",
    "notas_fiscais_xml_raw",
)


def contagem(linhas: list[dict[str, Any]], campo: str) -> dict[str, int]:
    return dict(sorted(collections.Counter(
        str(item.get(campo) if item.get(campo) not in (None, "") else "não_informado")
        for item in linhas
    ).items()))


def separar_campos_faltantes(valor: Any) -> list[str]:
    if isinstance(valor, str):
        valores = [valor]
    elif isinstance(valor, list):
        valores = [str(item) for item in valor]
    else:
        valores = []
    return [
        campo.strip()
        for item in valores
        for campo in item.split(",")
        if campo.strip()
    ]


def normalizar(valor: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(valor or "").casefold())


def chave_aparente(item: dict[str, Any]) -> tuple[str, ...]:
    return (
        normalizar(item.get("nome")),
        normalizar(item.get("contexto")),
        str(item.get("data_base") or ""),
        normalizar(item.get("sexo")),
        normalizar(item.get("categoria")),
        normalizar(item.get("destino")),
        str(item.get("quantidade") or ""),
    )


def referencias_json(linhas: list[dict[str, Any]]) -> str:
    return json.dumps(linhas, ensure_ascii=False, sort_keys=True, default=str)


def referencias_candidato(item: dict[str, Any]) -> list[str]:
    referencias = []
    for campo in ("codigo_fonte", "chave_rastreio"):
        valor = str(item.get(campo) or "").strip()
        if len(valor) >= 5:
            referencias.append(valor.casefold())
    return referencias


def quantidade_correspondencias(referencias: list[str], linhas: list[dict[str, Any]]) -> int:
    if not referencias:
        return 0
    return sum(
        any(referencia in referencias_json([linha]).casefold() for referencia in referencias)
        for linha in linhas
    )


def auditar(leitor: LeitorSupabase) -> dict[str, Any]:
    dados = {
        tabela: leitor.listar(tabela)
        for tabela in (*TABELAS_STAGING, *TABELAS_REVISAO, *TABELAS_OPERACIONAIS)
    }
    candidatos = dados["negocios_candidatos"]
    grupos = collections.Counter(chave_aparente(item) for item in candidatos)
    campos_faltantes = collections.Counter()
    chaves_origem = collections.Counter()
    for item in candidatos:
        for campo in separar_campos_faltantes(item.get("campos_faltantes")):
            campos_faltantes[campo] += 1
        if isinstance(item.get("dados_origem"), dict):
            chaves_origem.update(str(chave) for chave in item["dados_origem"])

    candidatos_ids = {str(item.get("id")) for item in candidatos}
    referencias_revisao = referencias_json(
        dados["operation_drafts"] + dados["pending_actions"] + dados["eventos"]
    )
    candidatos_referenciados = sum(
        1 for candidato_id in candidatos_ids if candidato_id and candidato_id in referencias_revisao
    )
    correspondencias_operacionais = collections.Counter()
    correspondencias_revisao = collections.Counter()
    alta_completa_sem_vinculo = 0
    for item in candidatos:
        refs = referencias_candidato(item)
        operacionais = quantidade_correspondencias(refs, dados["operacoes"])
        revisoes = quantidade_correspondencias(
            refs, dados["operation_drafts"] + dados["pending_actions"]
        )
        correspondencias_operacionais[
            "única" if operacionais == 1 else "ambígua" if operacionais > 1 else "nenhuma"
        ] += 1
        correspondencias_revisao[
            "única" if revisoes == 1 else "ambígua" if revisoes > 1 else "nenhuma"
        ] += 1
        if (
            item.get("prioridade") == "alta"
            and not separar_campos_faltantes(item.get("campos_faltantes"))
            and operacionais == 0
            and revisoes == 0
        ):
            alta_completa_sem_vinculo += 1

    fitids_operacionais = {
        str(item.get("id_externo") or item.get("fitid") or "").strip()
        for item in dados["transacoes_banco"]
        if item.get("id_externo") or item.get("fitid")
    }
    banco_ja_operacional = sum(
        str(item.get("fitid") or "").strip() in fitids_operacionais
        for item in dados["transacoes_banco_staging"]
        if item.get("fitid")
    )

    documentos_correspondencia = collections.Counter()
    candidatos_serializados = [referencias_json([item]) for item in candidatos]
    for item in dados["vinculos_documentais_candidatos"]:
        refs = [
            re.sub(r"\D", "", str(item.get(campo) or ""))
            for campo in ("gta_numero", "nf_referencia")
        ]
        refs = [ref for ref in refs if len(ref) >= 3]
        quantidade = sum(any(ref in candidato for ref in refs) for candidato in candidatos_serializados)
        documentos_correspondencia[
            "única" if quantidade == 1 else "ambígua" if quantidade > 1 else "nenhuma"
        ] += 1

    resumo_tabelas = {
        tabela: {
            "quantidade": len(linhas),
            "assinatura_sha256": assinatura(linhas),
        }
        for tabela, linhas in dados.items()
    }
    plano_base = {
        "resumo_tabelas": resumo_tabelas,
        "fontes": {
            "por_tipo": contagem(dados["fontes_importacao"], "tipo"),
            "periodo_final_por_tipo": {
                tipo: max(
                    (str(item.get("periodo_fim") or "não_informado")
                     for item in dados["fontes_importacao"] if str(item.get("tipo")) == tipo),
                    default="não_informado",
                )
                for tipo in sorted({str(item.get("tipo")) for item in dados["fontes_importacao"]})
            },
            "registros_declarados": sum(
                int(item.get("quantidade_registros") or 0)
                for item in dados["fontes_importacao"]
            ),
        },
        "candidatos": {
            "por_estado": contagem(candidatos, "estado"),
            "por_prioridade": contagem(candidatos, "prioridade"),
            "com_operacao_id": sum(bool(item.get("operacao_id")) for item in candidatos),
            "sem_operacao_id": sum(not item.get("operacao_id") for item in candidatos),
            "com_campos_faltantes": sum(bool(item.get("campos_faltantes")) for item in candidatos),
            "campos_faltantes": dict(sorted(campos_faltantes.items())),
            "grupos_duplicidade_aparente": sum(valor > 1 for valor in grupos.values()),
            "registros_em_duplicidade_aparente": sum(valor for valor in grupos.values() if valor > 1),
            "referenciados_na_fila": candidatos_referenciados,
            "sem_referencia_na_fila": len(candidatos) - candidatos_referenciados,
            "correspondencias_por_referencia_em_operacoes": dict(correspondencias_operacionais),
            "correspondencias_por_referencia_na_fila": dict(correspondencias_revisao),
            "alta_completa_sem_vinculo": alta_completa_sem_vinculo,
            "contextos_distintos": len({str(item.get("contexto")) for item in candidatos}),
            "chaves_dados_origem": dict(sorted(chaves_origem.items())),
        },
        "versoes": {
            "quantidade": len(dados["negocio_versoes"]),
            "correcoes_explicitas": sum(
                bool(item.get("correcao_explicita")) for item in dados["negocio_versoes"]
            ),
        },
        "banco_staging": {
            "por_estado": contagem(dados["transacoes_banco_staging"], "estado"),
            "com_transacao_operacional": sum(
                bool(item.get("transacao_banco_id"))
                for item in dados["transacoes_banco_staging"]
            ),
            "fitid_ja_existe_em_transacoes_banco": banco_ja_operacional,
            "fitid_ainda_nao_existe_em_transacoes_banco": (
                len(dados["transacoes_banco_staging"]) - banco_ja_operacional
            ),
            "conciliacoes_propostas": len(dados["conciliacoes_candidatas"]),
        },
        "documentos": {
            "por_estado": contagem(dados["vinculos_documentais_candidatos"], "estado"),
            "por_classificacao": contagem(
                dados["vinculos_documentais_candidatos"], "classificacao"
            ),
            "sem_negocio_candidato": sum(
                not item.get("negocio_candidato_id")
                for item in dados["vinculos_documentais_candidatos"]
            ),
            "correspondencias_nos_candidatos": dict(documentos_correspondencia),
        },
        "fila": {
            "rascunhos_por_status": contagem(dados["operation_drafts"], "status"),
            "acoes_por_status": contagem(dados["pending_actions"], "status"),
            "eventos_por_status": contagem(dados["eventos"], "status"),
        },
        "escritas_executadas": 0,
        "tabelas_operacionais_alteradas": 0,
    }
    plano_id = hashlib.sha256(
        json.dumps(plano_base, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:12]
    return {"plano_id": plano_id, "modo": "somente_leitura", **plano_base}


def main() -> None:
    url = os.environ.get("SUPABASE_URL") or os.environ.get("CONFINEX_DB_URL") or ""
    chave = (
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("CONFINEX_DB_KEY")
        or ""
    )
    print(json.dumps(auditar(LeitorSupabase(url, chave)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
