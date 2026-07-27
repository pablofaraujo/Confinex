#!/usr/bin/env python3
"""Cruza snapshots privados do Confinex sem executar qualquer escrita externa."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


CLASSIFICADORES = {
    "compra": re.compile(r"\bcompr", re.I),
    "venda_abate": re.compile(r"\bvend|\babate|romaneio", re.I),
    "pesagem": re.compile(r"pesag|peso|balan[çc]a", re.I),
    "gta": re.compile(r"\bgta\b", re.I),
    "banco": re.compile(r"extrato|banco|sicoob|pagamento|pix", re.I),
    "negocio": re.compile(r"neg[oó]cio|lote", re.I),
}


def normalizar_documento(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def data_iso(valor: Any) -> str | None:
    texto = str(valor or "").strip()
    encontrado = re.match(r"(\d{4}-\d{2}-\d{2})", texto)
    return encontrado.group(1) if encontrado else None


def chave_data_valor(registro: dict[str, Any]) -> tuple[str, str] | None:
    data = data_iso(registro.get("data"))
    try:
        valor = abs(Decimal(str(registro.get("valor")))).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError):
        return None
    return (data, str(valor)) if data else None


def linhas_planilha(payload: dict[str, Any], aba: str) -> list[dict[str, Any]]:
    linhas = payload.get(aba) or []
    if not linhas:
        return []
    cabecalho = [str(valor or "") for valor in linhas[0]]
    return [
        dict(zip(cabecalho, linha + [None] * (len(cabecalho) - len(linha))))
        for linha in linhas[1:]
    ]


def contagem_status(linhas: list[dict[str, Any]]) -> dict[str, int]:
    return dict(sorted(collections.Counter(str(item.get("status") or "sem_status") for item in linhas).items()))


def gerar_plano(
    snapshot: dict[str, Any],
    juan: dict[str, Any],
    gta_planilha: dict[str, Any],
    banco_planilha: dict[str, Any],
    referencia: date,
) -> dict[str, Any]:
    tabelas = snapshot["tabelas"]
    mensagens = juan.get("mensagens") or []
    gtas_excel = linhas_planilha(gta_planilha, "NFs e GTA")
    banco_excel = linhas_planilha(banco_planilha, "Lançamentos Mar-Jul 2026")

    mencoes = collections.Counter()
    for mensagem in mensagens:
        texto = str(mensagem.get("conteudo") or "")
        for classe, padrao in CLASSIFICADORES.items():
            if padrao.search(texto):
                mencoes[classe] += 1

    fitids_excel = {str(item.get("FITID") or "").strip() for item in banco_excel if item.get("FITID")}
    fitids_banco = {
        str(item.get("id_externo") or "").strip()
        for item in tabelas["transacoes_banco"]
        if item.get("id_externo")
    }
    por_chave_banco: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    por_chave_fluxo: dict[tuple[str, str], list[dict[str, Any]]] = collections.defaultdict(list)
    for item in tabelas["transacoes_banco"]:
        if chave := chave_data_valor(item):
            por_chave_banco[chave].append(item)
    for item in tabelas["fluxo_caixa"]:
        if chave := chave_data_valor(item):
            por_chave_fluxo[chave].append(item)

    candidatos_banco = []
    ambiguidades_banco = []
    for chave, transacoes in por_chave_banco.items():
        fluxos = por_chave_fluxo.get(chave, [])
        if len(transacoes) == len(fluxos) == 1:
            candidatos_banco.append({
                "transacao_banco_id": transacoes[0].get("id"),
                "fluxo_caixa_id": fluxos[0].get("id"),
                "data": chave[0],
                "criterio": "data_e_valor_exatos",
                "confirmado": False,
            })
        elif transacoes and fluxos:
            ambiguidades_banco.append({
                "data": chave[0],
                "transacoes": len(transacoes),
                "fluxos": len(fluxos),
            })

    gtas_planilha = {
        normalizar_documento(item.get("GTA")) for item in gtas_excel
        if normalizar_documento(item.get("GTA"))
    }
    nfs_planilha = {
        normalizar_documento(item.get("Nº Nota Fiscal")) for item in gtas_excel
        if normalizar_documento(item.get("Nº Nota Fiscal"))
    }
    notas_raw = tabelas["notas_fiscais_xml_raw"]
    gtas_raw = {
        normalizar_documento(item.get("gta")) for item in notas_raw
        if normalizar_documento(item.get("gta"))
    }
    nfs_raw = {
        normalizar_documento(item.get("numero")) for item in notas_raw
        if normalizar_documento(item.get("numero"))
    }
    gtas_entradas = {
        normalizar_documento(item.get("gta")) for item in tabelas["entradas_confinamento"]
        if normalizar_documento(item.get("gta"))
    }
    gtas_tabela = {
        normalizar_documento(item.get("numero")) for item in tabelas["gtas"]
        if normalizar_documento(item.get("numero"))
    }

    datas_banco = sorted(filter(None, (data_iso(item.get("data")) for item in tabelas["transacoes_banco"])))
    datas_fiscais = sorted(filter(None, (data_iso(item.get("data")) for item in notas_raw)))
    datas_fluxo = sorted(filter(None, (data_iso(item.get("data")) for item in tabelas["fluxo_caixa"])))
    limite_outlier = referencia.replace(year=referencia.year + 5).isoformat()
    outliers_fluxo = [
        {"id": item.get("id"), "data": data_iso(item.get("data")), "motivo": "mais_de_cinco_anos"}
        for item in tabelas["fluxo_caixa"]
        if data_iso(item.get("data")) and data_iso(item.get("data")) > limite_outlier
    ]

    plano = {
        "gerado_em": datetime.now().astimezone().isoformat(),
        "data_referencia": referencia.isoformat(),
        "modo": "dry_run_somente_leitura",
        "escritas_executadas": 0,
        "fontes": {
            "supabase": {nome: len(linhas) for nome, linhas in tabelas.items()},
            "juan": {
                "mensagens_deduplicadas": len(mensagens),
                "por_papel": dict(collections.Counter(str(item.get("papel") or "sem_papel") for item in mensagens)),
                "mencoes": dict(sorted(mencoes.items())),
                "com_anexo": sum(bool(item.get("medias")) for item in mensagens),
            },
        },
        "banco": {
            "data_corte": max(datas_banco) if datas_banco else None,
            "planilha_linhas": len(banco_excel),
            "supabase_linhas": len(tabelas["transacoes_banco"]),
            "fitids_comuns": len(fitids_excel & fitids_banco),
            "fitids_so_planilha": len(fitids_excel - fitids_banco),
            "fitids_so_supabase": len(fitids_banco - fitids_excel),
            "ja_conciliadas": sum(bool(item.get("conciliada")) for item in tabelas["transacoes_banco"]),
            "ja_ligadas_fluxo": sum(bool(item.get("fluxo_caixa_id")) for item in tabelas["transacoes_banco"]),
            "candidatos_data_valor": candidatos_banco,
            "ambiguidades_data_valor": ambiguidades_banco,
        },
        "gta_documentos": {
            "data_corte_fiscal": max(datas_fiscais) if datas_fiscais else None,
            "nfs_planilha": len(nfs_planilha),
            "nfs_raw": len(nfs_raw),
            "nfs_so_raw": len(nfs_raw - nfs_planilha),
            "gtas_planilha": len(gtas_planilha),
            "gtas_raw": len(gtas_raw),
            "gtas_entradas": len(gtas_entradas),
            "gtas_tabela": len(gtas_tabela),
            "gtas_raw_e_entradas": len(gtas_raw & gtas_entradas),
            "gtas_raw_sem_entrada": len(gtas_raw - gtas_entradas),
            "gtas_entrada_sem_raw": len(gtas_entradas - gtas_raw),
            "gtas_raw_e_tabela": len(gtas_raw & gtas_tabela),
        },
        "fila": {
            "rascunhos_por_status": contagem_status(tabelas["operation_drafts"]),
            "acoes_por_status": contagem_status(tabelas["pending_actions"]),
            "eventos_por_status": contagem_status(tabelas["eventos"]),
        },
        "qualidade": {
            "fluxo_data_corte": max(datas_fluxo) if datas_fluxo else None,
            "fluxo_datas_outlier": outliers_fluxo,
            "pesagens_operacionais": len(tabelas["pesagens_caderno"]),
            "abates_operacionais": len(tabelas["abates"]),
        },
        "pendencias": [],
    }
    if plano["banco"]["data_corte"] != referencia.isoformat():
        plano["pendencias"].append("extrato_bancario_nao_chega_a_data_de_referencia")
    if plano["gta_documentos"]["data_corte_fiscal"] != referencia.isoformat():
        plano["pendencias"].append("movimentacao_fiscal_gta_nao_chega_a_data_de_referencia")
    if outliers_fluxo:
        plano["pendencias"].append("fluxo_caixa_com_data_futura_atipica")
    if candidatos_banco:
        plano["pendencias"].append("conciliacoes_bancarias_candidatas_exigem_confirmacao")
    if (gtas_raw - gtas_entradas) or (gtas_entradas - gtas_raw):
        plano["pendencias"].append("gtas_sem_vinculo_forte_exigem_revisao")

    conteudo_assinavel = {chave: valor for chave, valor in plano.items() if chave != "gerado_em"}
    assinatura = json.dumps(conteudo_assinavel, ensure_ascii=False, sort_keys=True, default=str).encode()
    plano["plano_id"] = hashlib.sha256(assinatura).hexdigest()[:12]
    return plano


def relatorio_markdown(plano: dict[str, Any]) -> str:
    b = plano["banco"]
    g = plano["gta_documentos"]
    f = plano["fila"]
    return f"""# Consolidação privada das fontes operacionais

Gerado em {plano['gerado_em']}. Plano `{plano['plano_id']}`. Modo somente
leitura: nenhuma escrita foi executada.

## Datas de corte

- extrato bancário no Supabase: **{b['data_corte'] or 'sem data'}**;
- documentos fiscais/GTAs: **{g['data_corte_fiscal'] or 'sem data'}**;
- data de referência solicitada: **{plano['data_referencia']}**.

As fontes não permitem afirmar posição completa na data de referência enquanto
os arquivos mais recentes não forem incorporados e conferidos.

## Banco e fluxo de caixa

- {b['planilha_linhas']} linhas na planilha e {b['supabase_linhas']} no Supabase;
- {b['fitids_comuns']} identificadores coincidem; {b['fitids_so_planilha']} só na planilha e {b['fitids_so_supabase']} só no Supabase;
- {b['ja_conciliadas']} transações marcadas como conciliadas e {b['ja_ligadas_fluxo']} ligadas ao fluxo;
- {len(b['candidatos_data_valor'])} pares únicos por data e valor são apenas candidatos;
- {len(b['ambiguidades_data_valor'])} grupos por data/valor permanecem ambíguos.

## GTA e documentos

- {g['nfs_planilha']} NFs na planilha e {g['nfs_raw']} no staging fiscal;
- {g['nfs_so_raw']} NFs existem somente no staging mais recente;
- {g['gtas_planilha']} GTAs na planilha, {g['gtas_raw']} no staging, {g['gtas_entradas']} nas entradas e {g['gtas_tabela']} na tabela própria;
- {g['gtas_raw_e_entradas']} GTA possui correspondência forte entre staging e entrada;
- {g['gtas_raw_sem_entrada']} GTAs fiscais não têm entrada correspondente e {g['gtas_entrada_sem_raw']} GTAs de entrada não têm correspondente fiscal.

## Juan e fila de Revisões

- {plano['fontes']['juan']['mensagens_deduplicadas']} mensagens deduplicadas;
- menções classificadas: {json.dumps(plano['fontes']['juan']['mencoes'], ensure_ascii=False)};
- rascunhos: {json.dumps(f['rascunhos_por_status'], ensure_ascii=False)};
- ações: {json.dumps(f['acoes_por_status'], ensure_ascii=False)};
- eventos: {json.dumps(f['eventos_por_status'], ensure_ascii=False)}.

## Qualidade e próximos vínculos

- datas futuras atípicas no fluxo: {len(plano['qualidade']['fluxo_datas_outlier'])};
- pesagens operacionais: {plano['qualidade']['pesagens_operacionais']};
- abates operacionais: {plano['qualidade']['abates_operacionais']};
- pendências: {', '.join(plano['pendencias']) or 'nenhuma'}.

Nenhum candidato deste relatório autoriza conciliação, criação de GTA, rascunho
ou lançamento operacional. A próxima execução deve receber extrato e fonte de
GTA com corte na data de referência, repetir as assinaturas e gerar uma proposta
registro a registro para aprovação.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--supabase", required=True, type=Path)
    parser.add_argument("--juan", required=True, type=Path)
    parser.add_argument("--gta-planilha", required=True, type=Path)
    parser.add_argument("--banco-planilha", required=True, type=Path)
    parser.add_argument("--data-referencia", required=True, type=date.fromisoformat)
    parser.add_argument("--saida-json", required=True, type=Path)
    parser.add_argument("--saida-md", required=True, type=Path)
    args = parser.parse_args()
    plano = gerar_plano(
        json.loads(args.supabase.read_text(encoding="utf-8")),
        json.loads(args.juan.read_text(encoding="utf-8")),
        json.loads(args.gta_planilha.read_text(encoding="utf-8")),
        json.loads(args.banco_planilha.read_text(encoding="utf-8")),
        args.data_referencia,
    )
    args.saida_json.parent.mkdir(parents=True, exist_ok=True)
    args.saida_md.parent.mkdir(parents=True, exist_ok=True)
    args.saida_json.write_text(json.dumps(plano, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    args.saida_md.write_text(relatorio_markdown(plano), encoding="utf-8")
    print(json.dumps({
        "plano_id": plano["plano_id"],
        "modo": plano["modo"],
        "escritas_executadas": plano["escritas_executadas"],
        "pendencias": plano["pendencias"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
