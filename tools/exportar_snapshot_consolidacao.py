#!/usr/bin/env python3
"""Exporta um snapshot somente leitura para a consolidação privada.

O arquivo de saída pode conter dados operacionais e deve permanecer em
``docs/privado`` ou em diretório temporário. A ferramenta nunca faz chamadas
POST/PATCH/DELETE e não imprime linhas nem credenciais no terminal.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TABELAS_PERMITIDAS = (
    "fontes_importacao",
    "negocios_candidatos",
    "negocio_versoes",
    "evidencias_negocio",
    "transacoes_banco_staging",
    "conciliacoes_candidatas",
    "vinculos_documentais_candidatos",
    "decisoes_consolidacao",
    "operation_drafts",
    "pending_actions",
    "eventos",
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


def assinatura(linhas: list[dict[str, Any]]) -> str:
    canonico = json.dumps(
        sorted(linhas, key=lambda item: str(item.get("id") or "")),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


class LeitorSupabase:
    def __init__(
        self,
        url: str,
        chave: str,
        *,
        timeout: int = 20,
        opener: Any = urllib.request.urlopen,
    ) -> None:
        self.url = url.rstrip("/")
        self.chave = chave
        self.timeout = max(1, min(int(timeout), 20))
        self.opener = opener
        if not self.url or not self.chave:
            raise ValueError("credenciais protegidas do Supabase indisponíveis")

    def listar(self, tabela: str) -> list[dict[str, Any]]:
        if tabela not in TABELAS_PERMITIDAS:
            raise ValueError(f"tabela não permitida: {tabela}")
        linhas: list[dict[str, Any]] = []
        limite = 1000
        inicio = 0
        while True:
            consulta = urllib.parse.urlencode({
                "select": "*",
                "order": "id.asc",
                "limit": limite,
                "offset": inicio,
            })
            requisicao = urllib.request.Request(
                f"{self.url}/rest/v1/{tabela}?{consulta}",
                method="GET",
                headers={
                    "apikey": self.chave,
                    "Authorization": f"Bearer {self.chave}",
                },
            )
            try:
                with self.opener(requisicao, timeout=self.timeout) as resposta:
                    pagina = json.loads(resposta.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                raise RuntimeError(
                    f"leitura de {tabela} falhou com HTTP {exc.code}"
                ) from exc
            if not isinstance(pagina, list) or not all(isinstance(item, dict) for item in pagina):
                raise RuntimeError(f"resposta inválida em {tabela}")
            linhas.extend(pagina)
            if len(pagina) < limite:
                return linhas
            inicio += limite


def gerar_snapshot(leitor: LeitorSupabase) -> dict[str, Any]:
    tabelas: dict[str, list[dict[str, Any]]] = {}
    resumo: dict[str, dict[str, Any]] = {}
    for tabela in TABELAS_PERMITIDAS:
        linhas = leitor.listar(tabela)
        tabelas[tabela] = linhas
        resumo[tabela] = {
            "quantidade": len(linhas),
            "assinatura_sha256": assinatura(linhas),
        }
    return {
        "modo": "somente_leitura",
        "gerado_em": datetime.now(timezone.utc).isoformat(),
        "escritas_executadas": 0,
        "resumo": resumo,
        "tabelas": tabelas,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--saida", required=True, type=Path)
    parser.add_argument("--timeout", type=int, default=20)
    args = parser.parse_args()
    url = os.environ.get("SUPABASE_URL") or os.environ.get("CONFINEX_DB_URL") or ""
    chave = (
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("CONFINEX_DB_KEY")
        or ""
    )
    snapshot = gerar_snapshot(LeitorSupabase(url, chave, timeout=args.timeout))
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    os.chmod(args.saida, 0o600)
    print(json.dumps({
        "modo": snapshot["modo"],
        "escritas_executadas": 0,
        "tabelas": snapshot["resumo"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
