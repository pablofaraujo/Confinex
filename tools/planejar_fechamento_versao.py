#!/usr/bin/env python3
"""Mostra o estado objetivo de fechamento da versão Confinex.

A rotina é somente leitura. Ela não substitui os testes; resume os gates que
precisam estar verdes antes de considerar a versão encerrada.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = [
    "revisoes.html",
    "revisoes.js",
    "tools/test_ecossistema.py",
    "tools/test_revisoes_frontend.js",
    "tools/sanear_fila_revisoes.py",
    "tools/test_sanear_fila_revisoes.py",
    "docs/fila-revisoes.md",
    "docs/fila-revisoes-prioridades.md",
    "docs/testes-ecossistema.md",
]
REQUIRED_COMMANDS = [
    "python3 tools/test_ecossistema.py",
    "python3 tools/sanear_fila_revisoes.py",
]


def run_git(args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, text=True, capture_output=True, check=False
    )
    if completed.returncode:
        return ""
    return completed.stdout.strip()


def git_state() -> dict[str, Any]:
    status = run_git(["status", "--porcelain=v1", "--branch"])
    ahead = "ahead" in status.splitlines()[0] if status else False
    dirty = any(line and not line.startswith("##") for line in status.splitlines())
    head = run_git(["rev-parse", "--short", "HEAD"])
    upstream = run_git(["rev-parse", "--short", "@{upstream}"])
    return {"head": head, "upstream": upstream, "ahead": ahead, "dirty": dirty, "status": status}


def file_gates() -> list[dict[str, Any]]:
    return [
        {"arquivo": path, "ok": (ROOT / path).exists()}
        for path in REQUIRED_FILES
    ]


def build_plan(*, saneamento_dry_run: bool = False, validacao_completa: bool = False, publicado: bool | None = None) -> dict[str, Any]:
    git = git_state()
    files = file_gates()
    missing = [item["arquivo"] for item in files if not item["ok"]]
    if publicado is None:
        publicado = not git["ahead"] and not git["dirty"]
    gates = [
        {"gate": "arquivos essenciais", "ok": not missing, "detalhe": missing},
        {"gate": "worktree limpo", "ok": not git["dirty"], "detalhe": git["status"]},
        {"gate": "publicado no GitHub", "ok": publicado, "detalhe": {"head": git["head"], "origin": git["upstream"], "ahead": git["ahead"]}},
        {"gate": "validação local", "ok": validacao_completa, "detalhe": REQUIRED_COMMANDS[0]},
        {"gate": "saneamento dry-run", "ok": saneamento_dry_run, "detalhe": REQUIRED_COMMANDS[1]},
        {"gate": "validação completa VPS/Juan", "ok": validacao_completa, "detalhe": "python3 tools/test_ecossistema.py --completa"},
    ]
    pendentes = [gate["gate"] for gate in gates if gate["ok"] is not True]
    return {
        "versao_pronta": not pendentes,
        "pendencias": pendentes,
        "gates": gates,
        "proximas_acoes": [
            "publicar commits locais" if git["ahead"] else None,
            "rodar python3 tools/test_ecossistema.py" if not validacao_completa else None,
            "rodar python3 tools/sanear_fila_revisoes.py em dry-run" if not saneamento_dry_run else None,
            "rodar validação completa VPS/Juan antes de encerrar a versão" if not validacao_completa else None,
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Planeja o fechamento objetivo da versão")
    parser.add_argument("--saneamento-dry-run-ok", action="store_true")
    parser.add_argument("--validacao-completa-ok", action="store_true")
    parser.add_argument("--publicado", action="store_true", help="marca publicação como conferida externamente")
    args = parser.parse_args()
    print(json.dumps(build_plan(
        saneamento_dry_run=args.saneamento_dry_run_ok,
        validacao_completa=args.validacao_completa_ok,
        publicado=args.publicado or None,
    ), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
