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
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


def normalizar_numero(valor: Any) -> str:
    return re.sub(r"\D", "", str(valor or ""))


def extrair_texto_pdf(caminho: Path, destino: Path) -> str:
    executavel = shutil.which("pdftotext")
    if executavel:
        subprocess.run(
            [executavel, "-layout", str(caminho), str(destino)],
            check=True,
            capture_output=True,
            text=True,
        )
        return destino.read_text(encoding="utf-8", errors="replace")
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "leitor de PDF não encontrado; instale pypdf ou informe --texto-extraido"
        ) from exc
    texto = "\n".join(
        pagina.extract_text(extraction_mode="layout") or ""
        for pagina in PdfReader(str(caminho)).pages
    )
    destino.write_text(texto, encoding="utf-8")
    return texto


def detectar_gtas_canceladas_pdf(caminho: Path) -> set[str]:
    """Detecta linhas de GTA riscadas no PDF, sem depender do texto extraído."""
    try:
        from pypdf import PdfReader
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "pypdf é necessário para conferir graficamente GTAs canceladas"
        ) from exc

    canceladas: set[str] = set()
    for pagina in PdfReader(str(caminho)).pages:
        textos: list[tuple[str, float, float]] = []
        segmentos: list[tuple[float, float, float, float]] = []
        caminho_atual: list[tuple[float, float]] = []

        def visitar_texto(texto: str, _cm: Any, tm: Any, _fonte: Any, _tamanho: Any) -> None:
            valor = " ".join(str(texto or "").split())
            if re.fullmatch(r"\d{5,12}", valor):
                textos.append((valor, float(tm[4]), float(tm[5])))

        def visitar_operador(operador: bytes, argumentos: Any, _cm: Any, _tm: Any) -> None:
            nonlocal caminho_atual
            if operador == b"m":
                caminho_atual = [(float(argumentos[0]), float(argumentos[1]))]
            elif operador == b"l" and caminho_atual:
                caminho_atual.append((float(argumentos[0]), float(argumentos[1])))
            elif operador in (b"S", b"s"):
                if len(caminho_atual) >= 2:
                    x1, y1 = caminho_atual[0]
                    x2, y2 = caminho_atual[-1]
                    segmentos.append((x1, y1, x2, y2))
                caminho_atual = []

        pagina.extract_text(
            visitor_text=visitar_texto,
            visitor_operand_before=visitar_operador,
        )
        riscos = [
            (x1, y1, x2, y2)
            for x1, y1, x2, y2 in segmentos
            if abs(y2 - y1) <= 2 and 70 <= abs(x2 - x1) <= 300
            and max(x1, x2) >= 250
        ]
        for numero, x, y in textos:
            if x <= 160 and any(abs(y - y1) <= 6 for _x1, y1, _x2, _y2 in riscos):
                canceladas.add(numero)
    return canceladas


def ler_ficha(texto: str, sha256: str, gtas_canceladas: set[str] | None = None) -> dict[str, Any]:
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

    canceladas = {normalizar_numero(item) for item in (gtas_canceladas or set())}
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
            if encontrado and encontrado.group(1) not in canceladas:
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
        "gtas_canceladas": sorted(canceladas),
    }


def carregar_ficha_pdf(caminho: Path, destino_texto: Path) -> dict[str, Any]:
    texto = extrair_texto_pdf(caminho, destino_texto)
    canceladas = detectar_gtas_canceladas_pdf(caminho)
    return ler_ficha(
        texto,
        hashlib.sha256(caminho.read_bytes()).hexdigest(),
        canceladas,
    )


def combinar_fichas(fichas: list[dict[str, Any]]) -> dict[str, Any]:
    if not fichas:
        raise ValueError("nenhuma ficha IMA informada")
    ordenadas = sorted(fichas, key=lambda item: item["periodo_inicial"])
    canceladas = {
        normalizar_numero(gta)
        for ficha in ordenadas
        for gta in ficha.get("gtas_canceladas", [])
        if normalizar_numero(gta)
    }
    movimentos: list[dict[str, Any]] = []
    vistos: set[tuple[Any, ...]] = set()
    duplicados = 0
    for ficha in ordenadas:
        for item in ficha["movimentos"]:
            if normalizar_numero(item.get("gta")) in canceladas:
                continue
            chave = (item["gta"], item["data"], item["quantidade"], item["sentido"])
            if chave in vistos:
                duplicados += 1
                continue
            vistos.add(chave)
            movimentos.append(item)
    mais_recente = max(
        enumerate(ordenadas),
        key=lambda par: (par[1]["periodo_final"], par[0]),
    )[1]
    lacunas = []
    fim_coberto = date.fromisoformat(ordenadas[0]["periodo_final"])
    for ficha in ordenadas[1:]:
        inicio = date.fromisoformat(ficha["periodo_inicial"])
        if inicio > fim_coberto + timedelta(days=1):
            lacunas.append({
                "inicio": (fim_coberto + timedelta(days=1)).isoformat(),
                "fim": (inicio - timedelta(days=1)).isoformat(),
            })
        fim_coberto = max(fim_coberto, date.fromisoformat(ficha["periodo_final"]))
    hashes = [item["sha256"] for item in ordenadas]
    return {
        "sha256": hashlib.sha256("|".join(hashes).encode()).hexdigest(),
        "arquivos_sha256": hashes,
        "periodo_inicial": min(item["periodo_inicial"] for item in ordenadas),
        "periodo_final": max(item["periodo_final"] for item in ordenadas),
        "saldo_rebanho": mais_recente["saldo_rebanho"],
        "movimentos": movimentos,
        "gtas_canceladas": sorted(canceladas),
        "movimentos_duplicados_ignorados": duplicados,
        "lacunas_periodo": lacunas,
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
            "gtas_canceladas": len(ficha.get("gtas_canceladas", [])),
            "movimentos_duplicados_ignorados": ficha.get("movimentos_duplicados_ignorados", 0),
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
    if ficha.get("lacunas_periodo"):
        plano["pendencias"].append("fichas_ima_possuem_lacuna_de_periodo")
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
- {ficha.get('gtas_canceladas', 0)} GTAs canceladas foram excluídas pela marcação gráfica;
- {ficha.get('movimentos_duplicados_ignorados', 0)} movimentos sobrepostos foram deduplicados;
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
    origem.add_argument("--pdf", type=Path, action="append")
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
        fichas = []
        for indice, caminho in enumerate(args.pdf, start=1):
            temporario = args.saida_json.with_suffix(f".{indice}.txt.tmp")
            try:
                fichas.append(carregar_ficha_pdf(caminho, temporario))
            finally:
                temporario.unlink(missing_ok=True)
        ficha = combinar_fichas(fichas)
    else:
        texto = args.texto_extraido.read_text(encoding="utf-8", errors="replace")
        sha256 = hashlib.sha256(texto.encode()).hexdigest()
        ficha = ler_ficha(texto, sha256)

    plano = gerar_plano(
        ficha,
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
