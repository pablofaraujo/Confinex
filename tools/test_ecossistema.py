#!/usr/bin/env python3
"""Executa a validação contínua do ecossistema Confinex.

O modo padrão é inteiramente local e não grava dados. As verificações ao vivo
do Supabase também são somente leitura. O modo VPS envia e executa o verificador
versionado em ``tools/test_juan_vps.py`` sem instalar arquivos permanentes.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from confinex_client import ConfinexClient, ConfinexError


ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
TABLES_AUDITADAS = (
    "abates",
    "compras",
    "crm_followups",
    "contexto_handoff",
    "eventos",
    "interacoes_crm",
    "memorias_agentes",
    "operation_drafts",
    "negociacoes_gado",
    "ofertas_gado",
    "pending_actions",
    "pesagens_caderno",
    "vendas",
)
STATUS_EVENTO_VALIDOS = {"cancelado", "corrigido", "pendente", "registrado"}
CAMPOS_VALIDACAO_COMPLETA = (
    ("vps_host", "CONFINEX_VPS_HOST"),
    ("vps_pdf", "CONFINEX_TESTE_PDF"),
    ("vps_foto", "CONFINEX_TESTE_FOTO"),
    ("vps_grupo_id", "CONFINEX_TESTE_GRUPO_ID"),
    ("vps_legenda_pdf", "CONFINEX_TESTE_LEGENDA_PDF"),
    ("vps_legenda_foto", "CONFINEX_TESTE_LEGENDA_FOTO"),
)


class FalhaValidacao(RuntimeError):
    """Falha esperada de uma etapa da bateria."""


def executar(
    comando: list[str],
    *,
    cwd: Path = ROOT,
    timeout: int = 300,
    env: dict[str, str] | None = None,
    rotulo: str | None = None,
) -> subprocess.CompletedProcess[str]:
    descricao = rotulo or " ".join(comando)
    print("+", descricao)
    processo = subprocess.run(
        comando,
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if processo.stdout:
        print(processo.stdout.rstrip())
    if processo.stderr:
        print(processo.stderr.rstrip(), file=sys.stderr)
    if processo.returncode:
        raise FalhaValidacao(
            f"comando terminou com código {processo.returncode}: {descricao}"
        )
    return processo


def scripts_inline(html: str) -> list[str]:
    import re

    return [
        match.group(1)
        for match in re.finditer(
            r"<script(?:\s[^>]*)?>([\s\S]*?)</script>",
            html,
            flags=re.I,
        )
        if match.group(1).strip()
    ]


def validar_local() -> None:
    print("\n== Validação local ==")
    executar(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            "tools",
            "-p",
            "test_*.py",
        ]
    )
    executar(["node", "tools/test_revisoes_frontend.js"])
    executar(["node", "tools/test_gestao_frontend.js"])
    executar(["node", "tools/test_financeiro_frontend.js"])
    executar(["node", "tools/test_pendencias_eventos_frontend.js"])
    executar(["node", "tools/test_painel_boi_gordo.js"])
    executar(["node", "tools/test_confinados_hedge.js"])
    executar(["node", "tools/test_fazenda_inventario.js"])
    executar(["node", "tools/test_confinex_distancia.mjs"])
    executar(["node", "tools/test_confinex_acompanhamento.mjs"])
    executar(["node", "tools/test_confinex_pagamento_confinamento.mjs"])
    executar(["node", "tools/test_confinex_resultado_financeiro.mjs"])
    executar(["node", "tools/test_confinex_revenda_equivalente.mjs"])
    executar(["node", "tools/test_confinex_bgi.mjs"])
    executar(["node", "tools/test_confinex_referencias_transporte.mjs"])
    executar(["node", "tools/test_confinex_interunidades.mjs"])
    executar(["node", "tools/test_ocr_pesagem_contrato.mjs"])
    executar(["node", "tools/test_confinex_supabase_contrato.mjs"])
    executar(["node", "tools/test_confinex_supabase_adaptador.mjs"])
    executar(["node", "tools/test_confinex_supabase_ponte.mjs"])
    executar(["node", "tools/test_confinex_bases_online.mjs"])
    executar(["node", "tools/test_continuidade_juan.mjs"])
    executar(["node", "tools/test_prova_modelo_continuidade.mjs"])
    executar([sys.executable, "-m", "unittest", "tools.test_consolidar_fontes_operacionais"])
    executar([sys.executable, "-m", "unittest", "tools.test_analisar_extrato_ofx"])
    executar([sys.executable, "-m", "unittest", "tools.test_analisar_ficha_ima"])
    executar([sys.executable, "-m", "unittest", "tools.test_sanear_duplicidades_bgi"])

    html = (ROOT / "revisoes.html").read_text(encoding="utf-8")
    scripts = scripts_inline(html)
    if scripts:
        raise FalhaValidacao("revisoes.html não deve manter script inline")
    if './revisoes.js?v=20260829-6' not in html:
        raise FalhaValidacao("revisoes.html deve carregar revisoes.js versionado")
    executar(["node", "--check", "revisoes.js"])
    executar(["node", "--check", "js/cfagro-gestao.js"])
    executar(["node", "--check", "js/financeiro.js"])
    executar(["node", "--check", "js/pendencias.js"])
    executar(["node", "--check", "js/eventos.js"])
    executar(["node", "--check", "js/crm.js"])
    executar(["node", "--check", "js/confinados-hedge.js"])
    executar(["node", "--check", "js/confinex-pagamento-confinamento.mjs"])
    executar(["node", "--check", "js/confinex-resultado-financeiro.mjs"])
    executar(["node", "--check", "js/confinex-revenda-equivalente.mjs"])
    executar(["node", "--check", "js/confinex-bgi.mjs"])
    executar(["node", "--check", "js/confinex-referencias-transporte.mjs"])
    executar(["node", "--check", "js/confinex-interunidades.mjs"])
    executar(["node", "--check", "js/confinex-bases-online.mjs"])
    executar(["node", "--check", "confinex-app.latest.js"])
    executar(["node", "--check", "confinex-app.mobile.js"])
    executar(["node", "--check", "tools/auditar_ecossistema_browser.js"])
    executar(["node", "--check", "tools/auditar_ecossistema_webkit.js"])
    executar(
        [
            sys.executable,
            "tools/auditar_ecossistema.py",
            "--somente-estatico",
        ]
    )

    executar(["git", "diff", "--check"])


def assinatura_ids(rows: list[dict[str, Any]]) -> dict[str, Any]:
    ids = sorted(str(row.get("id")) for row in rows if row.get("id") is not None)
    return {
        "quantidade": len(ids),
        "assinatura": hashlib.sha256("\n".join(ids).encode()).hexdigest(),
    }


def selecionar_todos(
    client: ConfinexClient,
    table: str,
    *,
    select: str,
    order: str = "id.asc",
    pagina: int = 1000,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        lote = client.select(
            table,
            select=select,
            order=order,
            limit=str(pagina),
            offset=str(offset),
        )
        rows.extend(lote)
        if len(lote) < pagina:
            return rows
        offset += len(lote)


def snapshot_supabase(client: ConfinexClient) -> dict[str, dict[str, Any]]:
    snapshot: dict[str, dict[str, Any]] = {}
    for table in TABLES_AUDITADAS:
        rows = selecionar_todos(client, table, select="id")
        snapshot[table] = assinatura_ids(rows)
    return snapshot


def validar_supabase() -> None:
    print("\n== Supabase somente leitura ==")
    client = ConfinexClient()
    antes = snapshot_supabase(client)

    eventos = selecionar_todos(client, "eventos", select="id,status")
    invalidos = sorted(
        {
            str(row.get("status"))
            for row in eventos
            if row.get("status") not in STATUS_EVENTO_VALIDOS
        }
    )
    if invalidos:
        raise FalhaValidacao(
            "eventos possuem status fora do contrato: " + ", ".join(invalidos)
        )

    depois = snapshot_supabase(client)
    if antes != depois:
        raise FalhaValidacao(
            "o Supabase mudou durante uma validação declarada como somente leitura"
        )
    print(
        json.dumps(
            {
                "ok": True,
                "modo": "somente_leitura",
                "tabelas": antes,
                "status_eventos": sorted(
                    {str(row.get("status")) for row in eventos}
                ),
                "inalterado": True,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def validar_vps(args: argparse.Namespace) -> None:
    print("\n== VPS/Juan ==")
    if not args.vps_host:
        raise FalhaValidacao("informe --vps-host para executar a validação da VPS")

    config = {
        "pdf": args.vps_pdf,
        "foto": args.vps_foto,
        "grupo_id": args.vps_grupo_id,
        "legenda_pdf": args.vps_legenda_pdf,
        "legenda_foto": args.vps_legenda_foto,
        "testar_agente": args.testar_agente,
    }
    source = (TOOLS / "test_juan_vps.py").read_bytes()
    source_b64 = base64.b64encode(source).decode()
    argv_b64 = base64.b64encode(
        json.dumps(config, ensure_ascii=False).encode()
    ).decode()
    wrapper = (
        "import base64,json;"
        f"globals()['CONFIG_TESTE']=json.loads(base64.b64decode('{argv_b64}'));"
        f"exec(compile(base64.b64decode('{source_b64}'),"
        "'test_juan_vps.py','exec'))"
    )
    destino = f"{args.vps_user}@{args.vps_host}"
    comando = ["ssh"]
    if args.vps_identity:
        comando.extend(["-i", args.vps_identity])
    comando.extend([destino, f"python3 -c \"{wrapper}\""])
    executar(
        comando,
        timeout=1200,
        rotulo=f"ssh {destino} [verificador efêmero do Juan]",
    )


def preparar_validacao_completa(args: argparse.Namespace) -> None:
    if not args.completa:
        return
    faltantes = [
        variavel
        for atributo, variavel in CAMPOS_VALIDACAO_COMPLETA
        if not getattr(args, atributo, None)
    ]
    if faltantes:
        raise FalhaValidacao(
            "validação completa exige as variáveis protegidas: "
            + ", ".join(faltantes)
        )
    args.testar_agente = True


def validar_isolamento_agente(args: argparse.Namespace) -> None:
    if args.testar_agente:
        raise FalhaValidacao(
            "Teste do agente bloqueado antes de acesso externo: ferramentas de "
            "produção não estão isoladas. Consulte docs/continuidade-juan.md. "
            "A bateria local e as leituras independentes continuam disponíveis."
        )


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Roda a bateria permanente do ecossistema Confinex"
    )
    p.add_argument(
        "--supabase",
        action="store_true",
        help="faz auditoria somente leitura e compara assinaturas antes/depois",
    )
    p.add_argument(
        "--completa",
        action="store_true",
        help=(
            "roda local + VPS + arquivos reais + agente; a VPS também compara "
            "o Supabase antes/depois"
        ),
    )
    p.add_argument("--vps-host", default=os.getenv("CONFINEX_VPS_HOST"))
    p.add_argument("--vps-user", default=os.getenv("CONFINEX_VPS_USER", "root"))
    p.add_argument("--vps-identity", default=os.getenv("CONFINEX_VPS_IDENTITY"))
    p.add_argument("--vps-pdf", default=os.getenv("CONFINEX_TESTE_PDF"))
    p.add_argument("--vps-foto", default=os.getenv("CONFINEX_TESTE_FOTO"))
    p.add_argument("--vps-grupo-id", default=os.getenv("CONFINEX_TESTE_GRUPO_ID"))
    p.add_argument(
        "--vps-legenda-pdf",
        default=os.getenv("CONFINEX_TESTE_LEGENDA_PDF", ""),
    )
    p.add_argument(
        "--vps-legenda-foto",
        default=os.getenv("CONFINEX_TESTE_LEGENDA_FOTO", ""),
    )
    p.add_argument(
        "--testar-agente",
        action="store_true",
        help="gate bloqueado até isolar ferramentas do agente; não executa sessão de produção",
    )
    return p


def main() -> int:
    args = parser().parse_args()
    try:
        preparar_validacao_completa(args)
        validar_isolamento_agente(args)
        validar_local()
        if args.supabase:
            validar_supabase()
        if args.vps_host:
            validar_vps(args)
        print("\nVALIDAÇÃO DO ECOSSISTEMA: OK")
        return 0
    except (FalhaValidacao, ConfinexError, OSError, subprocess.TimeoutExpired) as exc:
        print(f"\nVALIDAÇÃO DO ECOSSISTEMA: FALHOU\n{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
