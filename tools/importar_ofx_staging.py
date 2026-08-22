#!/usr/bin/env python3
"""Importa OFX somente para o staging privado, com confirmação e idempotência."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import urllib.error
import urllib.request
import uuid
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

try:
    from analisar_extrato_ofx import campo_ofx, data_ofx
    from exportar_snapshot_consolidacao import LeitorSupabase
except ModuleNotFoundError:
    from tools.analisar_extrato_ofx import campo_ofx, data_ofx
    from tools.exportar_snapshot_consolidacao import LeitorSupabase


NAMESPACE = uuid.UUID("93a6d927-d28d-4ed7-8c10-905554ce02eb")
TABELAS_ESCRITA = {"fontes_importacao", "transacoes_banco_staging"}


def ler_ofx(caminho: Path) -> dict[str, Any]:
    conteudo_bytes = caminho.read_bytes()
    conteudo = conteudo_bytes.decode("latin-1", errors="replace")
    if "<OFX>" not in conteudo.upper():
        raise ValueError("arquivo sem estrutura OFX reconhecível")
    banco = campo_ofx(conteudo, "BANKID")
    conta_id = campo_ofx(conteudo, "ACCTID")
    conta = ":".join(valor for valor in (banco, conta_id) if valor)
    if not conta:
        raise ValueError("conta bancária ausente no OFX")
    transacoes = []
    chaves_lidas: set[tuple[str, str]] = set()
    for bloco in re.findall(
        r"<STMTTRN>(.*?)(?=<STMTTRN>|</BANKTRANLIST>)", conteudo, re.S | re.I
    ):
        fitid = campo_ofx(bloco, "FITID")
        data = data_ofx(campo_ofx(bloco, "DTPOSTED"))
        bruto = campo_ofx(bloco, "TRNAMT").replace(",", ".")
        try:
            valor = Decimal(bruto).quantize(Decimal("0.01"))
        except InvalidOperation:
            valor = Decimal("0")
        if not fitid or not data or not valor:
            continue
        chave_transacao = (conta, fitid)
        if chave_transacao in chaves_lidas:
            continue
        chaves_lidas.add(chave_transacao)
        transacoes.append({
            "fitid": fitid,
            "conta": conta,
            "banco": banco or None,
            "data": data,
            "tipo": campo_ofx(bloco, "TRNTYPE") or None,
            "valor": str(valor),
            "descricao": campo_ofx(bloco, "NAME") or None,
            "memo": campo_ofx(bloco, "MEMO") or None,
        })
    return {
        "arquivo": caminho.name,
        "sha256": hashlib.sha256(conteudo_bytes).hexdigest(),
        "transacoes": transacoes,
    }


def montar_plano(ofx: dict[str, Any], fontes: list[dict[str, Any]], staging: list[dict[str, Any]]) -> dict[str, Any]:
    fonte_id = str(uuid.uuid5(NAMESPACE, f"fonte:ofx:{ofx['sha256']}"))
    existentes = {
        (str(item.get("conta") or ""), str(item.get("fitid") or ""))
        for item in staging
    }
    novos = [
        item for item in ofx["transacoes"]
        if (item["conta"], item["fitid"]) not in existentes
    ]
    datas = sorted(item["data"] for item in ofx["transacoes"])
    fonte_existe = any(
        item.get("tipo") == "ofx" and item.get("hash_sha256") == ofx["sha256"]
        for item in fontes
    )
    fonte = {
        "id": fonte_id,
        "tipo": "ofx",
        "nome_arquivo": ofx["arquivo"],
        "hash_sha256": ofx["sha256"],
        "periodo_inicio": min(datas) if datas else None,
        "periodo_fim": max(datas) if datas else None,
        "quantidade_registros": len(ofx["transacoes"]),
        "origem_canal": "arquivo_enviado",
        "origem_referencia": ofx["arquivo"],
        "estado": "importada_staging",
        "metadados": {"formato": "ofx", "promovido_para_operacional": False},
        "criado_por": "codex",
    }
    transacoes = [{
        "id": str(uuid.uuid5(NAMESPACE, f"transacao:{item['conta']}:{item['fitid']}")),
        "fonte_importacao_id": fonte_id,
        **item,
        "estado": "nao_revisada",
        "dados_origem": {"arquivo_sha256": ofx["sha256"], "promovido": False},
    } for item in novos]
    assinatura = "\n".join(sorted(item["id"] for item in transacoes) + [fonte_id])
    return {
        "plano_id": hashlib.sha256(assinatura.encode()).hexdigest()[:12],
        "modo": "dry_run",
        "fonte_existe": fonte_existe,
        "fonte": fonte,
        "transacoes": transacoes,
        "resumo": {
            "transacoes_no_arquivo": len(ofx["transacoes"]),
            "transacoes_ja_no_staging": len(ofx["transacoes"]) - len(novos),
            "transacoes_novas": len(novos),
            "periodo_inicio": min(datas) if datas else None,
            "periodo_fim": max(datas) if datas else None,
            "tabelas_operacionais_alteradas": 0,
        },
    }


class EscritorStaging:
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


def executar(plano: dict[str, Any], escritor: EscritorStaging) -> dict[str, int]:
    if not plano["fonte_existe"]:
        escritor.inserir("fontes_importacao", plano["fonte"])
    for item in plano["transacoes"]:
        escritor.inserir("transacoes_banco_staging", item)
    return {
        "fontes_criadas": 0 if plano["fonte_existe"] else 1,
        "transacoes_criadas": len(plano["transacoes"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ofx", required=True, type=Path)
    parser.add_argument("--executar", action="store_true")
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
    plano = montar_plano(
        ler_ofx(args.ofx),
        leitor.listar("fontes_importacao"),
        leitor.listar("transacoes_banco_staging"),
    )
    resultado = {"fontes_criadas": 0, "transacoes_criadas": 0}
    if args.executar:
        esperada = f"IMPORTAR OFX STAGING {plano['plano_id']}"
        if args.confirmacao != esperada:
            raise SystemExit(f"confirmação inválida; use: {esperada}")
        resultado = executar(plano, EscritorStaging(url, chave))
        plano["modo"] = "executado"
    print(json.dumps({
        "plano_id": plano["plano_id"],
        "modo": plano["modo"],
        "resumo": plano["resumo"],
        **resultado,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
