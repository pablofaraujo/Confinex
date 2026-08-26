#!/usr/bin/env python3
"""Verifica e repara a captura continua do WhatsApp no cache privado do Wey.

O verificador nao le mensagens e nao possui caminho de envio ao WhatsApp. Ele
consulta apenas o diagnostico do wacli e o estado de unidades systemd.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path


def executar(comando: list[str], timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        comando,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def unidade_ativa(unidade: str) -> bool:
    return executar(["systemctl", "is-active", "--quiet", unidade]).returncode == 0


def sessao_revogada(store: Path, unidade: str) -> bool:
    """Compara falhas de autenticacao com a versao atual da sessao local."""
    try:
        desde = int((store / "session.db").stat().st_mtime)
    except OSError:
        return True
    resposta = executar([
        "journalctl", "-u", unidade, "--since", f"@{desde}",
        "--no-pager", "-o", "cat",
    ], timeout=30)
    if resposta.returncode != 0:
        return False
    texto = resposta.stdout.lower()
    return any(marcador in texto for marcador in (
        "401: logged out from another device",
        "not authenticated; run `wacli auth`",
    ))


def diagnosticar(binario: Path, store: Path) -> dict[str, object]:
    resposta = executar([
        str(binario), "--store", str(store), "--read-only", "--json", "doctor"
    ])
    if resposta.returncode != 0:
        return {"autenticado": False, "conectado": False, "erro": "doctor_falhou"}
    try:
        payload = json.loads(resposta.stdout)
        dados = payload.get("data") or {}
    except (json.JSONDecodeError, AttributeError):
        return {"autenticado": False, "conectado": False, "erro": "doctor_invalido"}
    return {
        "autenticado": dados.get("authenticated") is True,
        "conectado": dados.get("connected") is True,
        "bloqueio_ativo": dados.get("lock_held") is True,
        "ultima_sincronizacao": (dados.get("store") or {}).get("last_sync_at"),
    }


def verificar(binario: Path, store: Path, unidade: str) -> dict[str, object]:
    estado = diagnosticar(binario, store)
    estado["servico_ativo"] = unidade_ativa(unidade)
    estado["sessao_revogada"] = sessao_revogada(store, unidade)
    estado["saudavel"] = all([
        estado["servico_ativo"],
        estado["autenticado"],
        estado["bloqueio_ativo"],
        not estado["sessao_revogada"],
    ])
    return estado


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wacli-bin", required=True, type=Path)
    parser.add_argument("--wacli-store", required=True, type=Path)
    parser.add_argument("--unidade", required=True)
    parser.add_argument("--unidade-manutencao")
    parser.add_argument("--reparar", action="store_true")
    parser.add_argument("--espera-reparo", type=float, default=15)
    args = parser.parse_args()

    if args.unidade_manutencao and unidade_ativa(args.unidade_manutencao):
        print(json.dumps({"saudavel": True, "ignorado": "manutencao_ativa"}))
        return 0

    estado = verificar(args.wacli_bin, args.wacli_store, args.unidade)
    if not estado["saudavel"] and args.reparar:
        if estado.get("autenticado") is not True or estado.get("sessao_revogada") is True:
            estado["reparo_solicitado"] = False
            estado["reparo_bloqueado"] = "reautenticacao_necessaria"
        else:
            reparo = executar(["systemctl", "restart", args.unidade], timeout=60)
            estado["reparo_solicitado"] = reparo.returncode == 0
            if reparo.returncode == 0:
                time.sleep(max(0, args.espera_reparo))
                estado = verificar(args.wacli_bin, args.wacli_store, args.unidade) | {
                    "reparo_solicitado": True
                }

    print(json.dumps(estado, ensure_ascii=False, sort_keys=True))
    return 0 if estado["saudavel"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
