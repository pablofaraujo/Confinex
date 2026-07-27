#!/usr/bin/env python3
"""Compara um extrato OFX com um snapshot bancário, sem executar escrita."""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any


def campo_ofx(bloco: str, nome: str) -> str:
    encontrado = re.search(rf"<{nome}>([^<\r\n]+)", bloco, re.I)
    return encontrado.group(1).strip() if encontrado else ""


def data_ofx(valor: str) -> str | None:
    digitos = re.sub(r"\D", "", valor or "")
    if len(digitos) < 8:
        return None
    try:
        return datetime.strptime(digitos[:8], "%Y%m%d").date().isoformat()
    except ValueError:
        return None


def ler_ofx(caminho: Path) -> dict[str, Any]:
    conteudo_bytes = caminho.read_bytes()
    conteudo = conteudo_bytes.decode("latin-1", errors="replace")
    if "<OFX>" not in conteudo.upper():
        raise ValueError("arquivo sem estrutura OFX reconhecível")
    blocos = re.findall(
        r"<STMTTRN>(.*?)(?=<STMTTRN>|</BANKTRANLIST>)",
        conteudo,
        re.S | re.I,
    )
    transacoes = []
    for bloco in blocos:
        fitid = campo_ofx(bloco, "FITID")
        data = data_ofx(campo_ofx(bloco, "DTPOSTED"))
        if fitid and data:
            transacoes.append({"id_externo": fitid, "data": data})
    return {
        "sha256": hashlib.sha256(conteudo_bytes).hexdigest(),
        "transacoes": transacoes,
    }


def gerar_plano(ofx: dict[str, Any], snapshot: list[dict[str, Any]], referencia: date) -> dict[str, Any]:
    transacoes = ofx["transacoes"]
    ids_ofx = [str(item["id_externo"]).strip() for item in transacoes]
    ids_supabase = {
        str(item.get("id_externo") or "").strip()
        for item in snapshot
        if item.get("id_externo")
    }
    novos = [item for item in transacoes if item["id_externo"] not in ids_supabase]
    datas_ofx = sorted(item["data"] for item in transacoes)
    datas_supabase = sorted(
        str(item.get("data") or "")[:10]
        for item in snapshot
        if item.get("data")
    )
    novos_por_data = dict(sorted(collections.Counter(item["data"] for item in novos).items()))
    plano = {
        "gerado_em": datetime.now().astimezone().isoformat(),
        "data_referencia": referencia.isoformat(),
        "modo": "dry_run_somente_leitura",
        "escritas_executadas": 0,
        "arquivo": {
            "sha256": ofx["sha256"],
            "transacoes": len(transacoes),
            "identificadores_unicos": len(set(ids_ofx)),
            "duplicidades_internas": len(ids_ofx) - len(set(ids_ofx)),
            "data_inicial": min(datas_ofx) if datas_ofx else None,
            "data_final": max(datas_ofx) if datas_ofx else None,
        },
        "supabase": {
            "transacoes": len(snapshot),
            "identificadores_unicos": len(ids_supabase),
            "data_final": max(datas_supabase) if datas_supabase else None,
        },
        "cruzamento": {
            "ja_presentes": sum(item in ids_supabase for item in ids_ofx),
            "novos": len(novos),
            "novos_por_data": novos_por_data,
            "data_inicial_novos": min((item["data"] for item in novos), default=None),
            "data_final_novos": max((item["data"] for item in novos), default=None),
        },
        "pendencias": [],
    }
    if plano["arquivo"]["data_final"] != referencia.isoformat():
        plano["pendencias"].append("extrato_nao_chega_a_data_de_referencia")
    if novos:
        plano["pendencias"].append("lancamentos_novos_aguardam_importacao_controlada")
    if plano["arquivo"]["duplicidades_internas"]:
        plano["pendencias"].append("extrato_possui_identificadores_duplicados")
    assinavel = {chave: valor for chave, valor in plano.items() if chave != "gerado_em"}
    assinatura = json.dumps(assinavel, ensure_ascii=False, sort_keys=True).encode()
    plano["plano_id"] = hashlib.sha256(assinatura).hexdigest()[:12]
    return plano


def relatorio_markdown(plano: dict[str, Any]) -> str:
    arquivo = plano["arquivo"]
    supabase = plano["supabase"]
    cruzamento = plano["cruzamento"]
    return f"""# Complemento privado — extrato bancário OFX

Plano `{plano['plano_id']}`, gerado em {plano['gerado_em']}, exclusivamente em
modo de leitura. Nenhuma escrita foi executada.

- período disponível no OFX: **{arquivo['data_inicial']} a {arquivo['data_final']}**;
- data de referência solicitada: **{plano['data_referencia']}**;
- {arquivo['transacoes']} lançamentos e {arquivo['identificadores_unicos']} identificadores únicos;
- {cruzamento['ja_presentes']} já existem entre as {supabase['transacoes']} transações do Supabase;
- **{cruzamento['novos']} lançamentos novos**, de {cruzamento['data_inicial_novos']} a {cruzamento['data_final_novos']};
- novos por data: {json.dumps(cruzamento['novos_por_data'], ensure_ascii=False)};
- duplicidades internas no arquivo: {arquivo['duplicidades_internas']}.

O arquivo foi baixado na data de referência, mas seu último lançamento é de
{arquivo['data_final']}. Portanto, ele não comprova ausência de movimentação nos
dias posteriores. Os lançamentos novos não foram importados, conciliados ou
vinculados. Qualquer importação futura deve usar `FITID` como chave idempotente,
comparar contagens antes/depois e exigir autorização própria.
"""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ofx", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--data-referencia", required=True, type=date.fromisoformat)
    parser.add_argument("--saida-json", required=True, type=Path)
    parser.add_argument("--saida-md", required=True, type=Path)
    args = parser.parse_args()
    plano = gerar_plano(
        ler_ofx(args.ofx),
        json.loads(args.snapshot.read_text(encoding="utf-8")),
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
        "novos": plano["cruzamento"]["novos"],
        "data_final": plano["arquivo"]["data_final"],
        "pendencias": plano["pendencias"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
