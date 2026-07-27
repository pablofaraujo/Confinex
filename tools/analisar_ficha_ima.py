#!/usr/bin/env python3
"""Cruza ficha sanitária do IMA com snapshots locais, sem executar escrita."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
import shutil
import subprocess
from datetime import date, datetime
from pathlib import Path
from typing import Any


def normalizar_numero(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def extrair_texto_pdf(caminho: Path, destino: Path) -> str:
    executavel = shutil.which("pdftotext")
    if not executavel:
        raise RuntimeError("pdftotext não encontrado; informe --texto-extraido")
    subprocess.run(
        [executavel, "-layout", str(caminho), str(destino)],
        check=True,
        capture_output=True,
        text=True,
    )
    return destino.read_text(encoding="utf-8", errors="replace")


def ler_ficha(texto: str, sha256: str) -> dict[str, Any]:
    periodo = re.search(
        r"Período de\s+(\d{2}/\d{2}/\d{4})\s+a\s+(\d{2}/\d{2}/\d{4})",
        texto,
    )
    total = re.search(r"Total:\s+(\d+)", texto)
    if not periodo or not total:
        raise ValueError("período ou saldo do rebanho não encontrado na ficha")

    inicio_bovinos = texto.find("GTA - Bovino e Bubalino")
    fim_bovinos = texto.find("GTAs de Outras Espécies", inicio_bovinos)
    if inicio_bovinos < 0:
        raise ValueError("seção de GTAs bovinas não encontrada")
    trecho = texto[inicio_bovinos:fim_bovinos if fim_bovinos >= 0 else None]

    sentido = None
    movimentos = []
    for linha in trecho.splitlines():
        limpa = linha.strip()
        if limpa == "GTAs de Saída":
            sentido = "saida"
            continue
        if limpa == "GTAs de Entrada":
            sentido = "entrada"
            continue
        if sentido and limpa.startswith("BOVINO"):
            encontrado = re.search(
                r"BOVINO\s+(\d+)\s+\w+\s+(\d{2}/\d{2}/\d{2}).*?\s(\d+)\s*$",
                limpa,
            )
            if encontrado:
                movimentos.append({
                    "gta": encontrado.group(1),
                    "data": datetime.strptime(encontrado.group(2), "%d/%m/%y").date().isoformat(),
                    "quantidade": int(encontrado.group(3)),
                    "sentido": sentido,
                })
    if not movimentos:
        raise ValueError("nenhuma movimentação bovina encontrada na ficha")

    return {
        "sha256": sha256,
        "periodo_inicial": datetime.strptime(periodo.group(1), "%d/%m/%Y").date().isoformat(),
        "periodo_final": datetime.strptime(periodo.group(2), "%d/%m/%Y").date().isoformat(),
        "saldo_rebanho": int(total.group(1)),
        "movimentos": movimentos,
    }


def gerar_plano(
    ficha: dict[str, Any],
    gtas: list[dict[str, Any]],
    entradas: list[dict[str, Any]],
    fiscal: list[dict[str, Any]],
    ledger: list[dict[str, Any]],
    referencia: date,
) -> dict[str, Any]:
    ids_ficha = {item["gta"] for item in ficha["movimentos"]}
    conjuntos = {
        "gtas": {normalizar_numero(item.get("numero")) for item in gtas if normalizar_numero(item.get("numero"))},
        "entradas_confinamento": {
            normalizar_numero(item.get("gta")) for item in entradas if normalizar_numero(item.get("gta"))
        },
        "fiscal": {normalizar_numero(item.get("gta")) for item in fiscal if normalizar_numero(item.get("gta"))},
    }
    uniao = set().union(*conjuntos.values())
    por_sentido = collections.Counter(item["sentido"] for item in ficha["movimentos"])
    animais_por_sentido = collections.Counter()
    for item in ficha["movimentos"]:
        animais_por_sentido[item["sentido"]] += item["quantidade"]

    datas_ledger = sorted(
        str(item.get("data") or "")[:10]
        for item in ledger
        if item.get("data")
    )
    ledger_ordenado = sorted(ledger, key=lambda item: str(item.get("data") or ""), reverse=True)
    saldo_ledger = next(
        (item.get("saldo_apos_movimento") for item in ledger_ordenado if item.get("saldo_apos_movimento") is not None),
        None,
    )
    plano = {
        "gerado_em": datetime.now().astimezone().isoformat(),
        "data_referencia": referencia.isoformat(),
        "modo": "dry_run_somente_leitura",
        "escritas_executadas": 0,
        "ficha": {
            "sha256": ficha["sha256"],
            "periodo_inicial": ficha["periodo_inicial"],
            "periodo_final": ficha["periodo_final"],
            "saldo_rebanho": ficha["saldo_rebanho"],
            "gtas": len(ids_ficha),
            "gtas_saida": por_sentido["saida"],
            "gtas_entrada": por_sentido["entrada"],
            "animais_saida": animais_por_sentido["saida"],
            "animais_entrada": animais_por_sentido["entrada"],
            "saldo_movimentos": animais_por_sentido["entrada"] - animais_por_sentido["saida"],
        },
        "cruzamento": {
            "presentes_em_gtas": len(ids_ficha & conjuntos["gtas"]),
            "presentes_em_entradas_confinamento": len(ids_ficha & conjuntos["entradas_confinamento"]),
            "presentes_no_fiscal": len(ids_ficha & conjuntos["fiscal"]),
            "presentes_em_alguma_fonte": len(ids_ficha & uniao),
            "sem_qualquer_vinculo": len(ids_ficha - uniao),
        },
        "ledger_fazenda": {
            "registros": len(ledger),
            "data_final": max(datas_ledger) if datas_ledger else None,
            "saldo_mais_recente": saldo_ledger,
            "diferenca_para_ficha": (
                ficha["saldo_rebanho"] - int(saldo_ledger)
                if saldo_ledger is not None else None
            ),
        },
        "pendencias": [],
    }
    if ficha["periodo_final"] != referencia.isoformat():
        plano["pendencias"].append("ficha_ima_nao_chega_a_data_de_referencia")
    if plano["cruzamento"]["sem_qualquer_vinculo"]:
        plano["pendencias"].append("gtas_ima_sem_vinculo_exigem_revisao")
    if saldo_ledger is None or int(saldo_ledger) != ficha["saldo_rebanho"]:
        plano["pendencias"].append("saldo_rebanho_diverge_do_ledger")
    assinavel = {chave: valor for chave, valor in plano.items() if chave != "gerado_em"}
    assinatura = json.dumps(assinavel, ensure_ascii=False, sort_keys=True).encode()
    plano["plano_id"] = hashlib.sha256(assinatura).hexdigest()[:12]
    return plano


def relatorio_markdown(plano: dict[str, Any]) -> str:
    ficha = plano["ficha"]
    cruzamento = plano["cruzamento"]
    ledger = plano["ledger_fazenda"]
    return f"""# Complemento privado — ficha sanitária do IMA

Plano `{plano['plano_id']}`, gerado em {plano['gerado_em']}, exclusivamente em
modo de leitura. Nenhuma escrita foi executada.

- período da ficha: **{ficha['periodo_inicial']} a {ficha['periodo_final']}**;
- saldo informado pelo IMA: **{ficha['saldo_rebanho']} animais**;
- {ficha['gtas_saida']} GTAs de saída, com {ficha['animais_saida']} animais;
- {ficha['gtas_entrada']} GTAs de entrada, com {ficha['animais_entrada']} animais;
- saldo líquido das movimentações do período: {ficha['saldo_movimentos']} animais;
- {cruzamento['presentes_em_alguma_fonte']} das {ficha['gtas']} GTAs aparecem em alguma fonte central;
- **{cruzamento['sem_qualquer_vinculo']} GTAs permanecem sem vínculo**;
- o ledger da fazenda termina em {ledger['data_final']} com saldo {ledger['saldo_mais_recente']};
- diferença entre o saldo do IMA e o ledger: {ledger['diferenca_para_ficha']} animais.

A diferença de saldo não autoriza criar uma movimentação compensatória. As GTAs
precisam ser revisadas individualmente quanto a origem, destino, finalidade e
relação com negócios existentes. Nenhuma GTA, entrada, saída ou movimentação de
rebanho foi criada.
"""


def carregar_lista(caminho: Path) -> list[dict[str, Any]]:
    conteudo = json.loads(caminho.read_text(encoding="utf-8"))
    if not isinstance(conteudo, list):
        raise ValueError(f"snapshot inválido: {caminho.name}")
    return conteudo


def main() -> None:
    parser = argparse.ArgumentParser()
    origem = parser.add_mutually_exclusive_group(required=True)
    origem.add_argument("--pdf", type=Path)
    origem.add_argument("--texto-extraido", type=Path)
    parser.add_argument("--gtas", required=True, type=Path)
    parser.add_argument("--entradas", required=True, type=Path)
    parser.add_argument("--fiscal", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--data-referencia", required=True, type=date.fromisoformat)
    parser.add_argument("--saida-json", required=True, type=Path)
    parser.add_argument("--saida-md", required=True, type=Path)
    args = parser.parse_args()

    if args.pdf:
        temporario = args.saida_json.with_suffix(".txt.tmp")
        try:
            texto = extrair_texto_pdf(args.pdf, temporario)
        finally:
            temporario.unlink(missing_ok=True)
        sha256 = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
    else:
        texto = args.texto_extraido.read_text(encoding="utf-8", errors="replace")
        sha256 = hashlib.sha256(texto.encode()).hexdigest()

    plano = gerar_plano(
        ler_ficha(texto, sha256),
        carregar_lista(args.gtas),
        carregar_lista(args.entradas),
        carregar_lista(args.fiscal),
        carregar_lista(args.ledger),
        args.data_referencia,
    )
    args.saida_json.parent.mkdir(parents=True, exist_ok=True)
    args.saida_md.parent.mkdir(parents=True, exist_ok=True)
    args.saida_json.write_text(json.dumps(plano, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    args.saida_md.write_text(relatorio_markdown(plano), encoding="utf-8")
    print(json.dumps({
        "plano_id": plano["plano_id"],
        "modo": plano["modo"],
        "escritas_executadas": plano["escritas_executadas"],
        "periodo_final": plano["ficha"]["periodo_final"],
        "gtas_sem_vinculo": plano["cruzamento"]["sem_qualquer_vinculo"],
        "pendencias": plano["pendencias"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
