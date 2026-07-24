#!/usr/bin/env python3
"""Atualiza o artefato do painel a partir de uma fonte JSON configurada."""
from __future__ import annotations

import argparse
import json
import os
import tempfile
import urllib.request
from pathlib import Path
from typing import Any


ARTEFATO = Path(__file__).resolve().parents[1] / "dados" / "painel-boi-gordo.json"


def validar(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("fonte não retornou objeto JSON")
    obrigatorios = ("atualizadoEm", "fonte", "indicadores", "curvaBGI")
    faltantes = [campo for campo in obrigatorios if not payload.get(campo)]
    if faltantes:
        raise ValueError("fonte sem campos: " + ", ".join(faltantes))
    if not isinstance(payload["indicadores"], list) or not isinstance(payload["curvaBGI"], list):
        raise ValueError("indicadores e curvaBGI devem ser listas")
    return payload


def baixar(url: str, timeout: int = 30) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Confinex-Painel/1"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return validar(json.load(response))


def atualizar(url: str, destino: Path = ARTEFATO) -> dict[str, Any]:
    payload = baixar(url)
    destino.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=destino.parent, delete=False) as temporario:
        json.dump(payload, temporario, ensure_ascii=False, indent=2)
        temporario.write("\n")
        nome = temporario.name
    Path(nome).replace(destino)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=os.environ.get("PAINEL_BOI_GORDO_SOURCE_URL"))
    parser.add_argument("--destino", type=Path, default=ARTEFATO)
    args = parser.parse_args()
    if not args.url:
        print("PAINEL_BOI_GORDO_SOURCE_URL não configurada; artefato preservado")
        return 2
    try:
        payload = atualizar(args.url, args.destino)
    except Exception as erro:  # fonte externa não pode apagar o último dado válido
        print(f"Fonte indisponível; artefato preservado: {erro}")
        return 1
    print(json.dumps({"ok": True, "atualizadoEm": payload["atualizadoEm"], "fonte": payload["fonte"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

