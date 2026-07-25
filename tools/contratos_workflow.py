#!/usr/bin/env python3
"""Pré-análise determinística de contratos, sem escrita ou envio externo."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path
from typing import Any


CAMPOS_NEGOCIO = (
    "quantidade",
    "peso_total_kg",
    "valor_total",
    "data_inicio",
    "data_fim",
    "pagamento",
)
CAMPOS_JURIDICOS = (
    "partes",
    "obrigacoes",
    "multas",
    "garantias",
    "foro",
    "rescisao",
)


def carregar_json(path: str | None, padrao: Any) -> Any:
    if not path:
        return padrao
    return json.loads(Path(path).read_text(encoding="utf-8"))


def sha256_arquivo(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valor_canonico(value: Any) -> Any:
    if isinstance(value, str):
        return " ".join(value.strip().split())
    if isinstance(value, list):
        return [valor_canonico(item) for item in value]
    if isinstance(value, dict):
        return {
            key: valor_canonico(current)
            for key, current in sorted(value.items())
        }
    return value


def comparar(
    extraido: dict[str, Any],
    referencia: dict[str, Any],
    campos: tuple[str, ...],
) -> list[dict[str, Any]]:
    divergencias = []
    for campo in campos:
        esperado = valor_canonico(referencia.get(campo))
        atual = valor_canonico(extraido.get(campo))
        if esperado in (None, "", []):
            continue
        if atual in (None, "", []):
            divergencias.append(
                {"campo": campo, "tipo": "ausente", "esperado": esperado}
            )
        elif atual != esperado:
            divergencias.append(
                {
                    "campo": campo,
                    "tipo": "divergente",
                    "esperado": esperado,
                    "encontrado": atual,
                }
            )
    return divergencias


def avaliar_finpec(
    extraido: dict[str, Any],
    negocio: dict[str, Any],
) -> dict[str, Any]:
    if not extraido.get("finpec") and not negocio.get("finpec"):
        return {
            "estado": "FINPEC_NAO_IDENTIFICADO",
            "garantia_criada": False,
            "revisao_juridica": False,
        }
    brincos = [
        str(item).strip()
        for item in extraido.get("brincos") or []
        if str(item).strip()
    ]
    unicos = sorted(set(brincos))
    duplicados = sorted({item for item in brincos if brincos.count(item) > 1})
    quantidade = negocio.get("quantidade")
    faltantes = (
        max(int(quantidade) - len(unicos), 0)
        if isinstance(quantidade, int) and quantidade >= 0
        else None
    )
    contrato_permite = extraido.get("alienacao_fiduciaria") is True
    if duplicados or not unicos or faltantes not in (0, None):
        estado = "BRINCOS_PENDENTES"
    elif not contrato_permite:
        estado = "CONTRATO_INSUFICIENTE"
    else:
        estado = "REVISAO_JURIDICA"
    return {
        "estado": estado,
        "brincos_informados": len(brincos),
        "brincos_unicos": len(unicos),
        "brincos_duplicados": duplicados,
        "brincos_faltantes": faltantes,
        "contrato_permite_alienacao": contrato_permite,
        "garantia_criada": False,
        "revisao_juridica": estado != "FINPEC_NAO_IDENTIFICADO",
    }


def destino_drive(negocio: dict[str, Any], documento: Path, digest: str) -> str:
    referencia = re.sub(
        r"[^A-Za-z0-9_-]+",
        "-",
        str(negocio.get("referencia") or "sem-vinculo"),
    ).strip("-")
    return (
        f"ClaudeCoWork/Contratos/{referencia or 'sem-vinculo'}/"
        f"{digest[:12]}-{documento.name}"
    )


def analisar(
    documento: Path,
    *,
    extraido: dict[str, Any],
    negocio: dict[str, Any],
    termos: dict[str, Any],
    historico_hashes: set[str],
) -> dict[str, Any]:
    digest = sha256_arquivo(documento)
    duplicado = digest in historico_hashes
    divergencias_negocio = comparar(extraido, negocio, CAMPOS_NEGOCIO)
    divergencias_termos = comparar(extraido, termos, CAMPOS_JURIDICOS)
    pendencias = [
        campo
        for campo in (*CAMPOS_NEGOCIO, *CAMPOS_JURIDICOS)
        if extraido.get(campo) in (None, "", [])
    ]
    finpec = avaliar_finpec(extraido, negocio)
    riscos = [
        {
            "categoria": "mudanca_contratual",
            "campo": item["campo"],
            "evidencia_pagina": extraido.get("paginas", {}).get(item["campo"]),
            "confianca": extraido.get("confianca", {}).get(
                item["campo"],
                "nao_informada",
            ),
            "revisao_necessaria": True,
        }
        for item in divergencias_termos
    ]
    bloqueios = []
    if duplicado:
        bloqueios.append("documento_duplicado")
    if divergencias_negocio:
        bloqueios.append("dados_divergentes_do_negocio")
    if divergencias_termos:
        bloqueios.append("clausulas_alteradas")
    if pendencias:
        bloqueios.append("campos_sem_confirmacao")
    if finpec["estado"] not in {"FINPEC_NAO_IDENTIFICADO", "BRINCOS_VALIDOS"}:
        bloqueios.append("finpec_exige_revisao")
    return {
        "modo": "dry_run",
        "documento": {
            "sha256": digest,
            "nome": documento.name,
            "tamanho_bytes": documento.stat().st_size,
            "duplicado": duplicado,
            "destino_drive_proposto": destino_drive(negocio, documento, digest),
        },
        "vinculo_negocio": negocio.get("referencia"),
        "divergencias_negocio": divergencias_negocio,
        "divergencias_termos": divergencias_termos,
        "pendencias_confirmacao": sorted(set(pendencias)),
        "triagem_juridica": {
            "natureza": "triagem_automatizada_nao_substitui_advogado",
            "riscos": riscos,
            "aprovacao_especifica_necessaria": True,
        },
        "finpec": finpec,
        "bloqueios": sorted(set(bloqueios)),
        "pode_pedir_aprovacao": not duplicado and not bloqueios,
        "pode_assinar": False,
        "pode_enviar": False,
        "pode_criar_garantia": False,
        "acoes_externas_executadas": 0,
    }


def parser() -> argparse.ArgumentParser:
    current = argparse.ArgumentParser(
        description="Analisa contrato em dry-run, sem mover, enviar ou assinar"
    )
    current.add_argument("--documento", required=True)
    current.add_argument("--extraido")
    current.add_argument("--negocio")
    current.add_argument("--termos")
    current.add_argument("--historico")
    return current


def main() -> int:
    args = parser().parse_args()
    documento = Path(args.documento)
    if not documento.is_file():
        raise SystemExit("documento não encontrado")
    historico = carregar_json(args.historico, [])
    if not isinstance(historico, list):
        raise SystemExit("histórico deve ser uma lista de hashes")
    report = analisar(
        documento,
        extraido=carregar_json(args.extraido, {}),
        negocio=carregar_json(args.negocio, {}),
        termos=carregar_json(args.termos, {}),
        historico_hashes={str(item) for item in historico},
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
