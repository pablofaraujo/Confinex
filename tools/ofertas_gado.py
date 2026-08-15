#!/usr/bin/env python3
"""Prepara e, sob confirmação explícita, registra ofertas no CRM do Confinex.

O padrão é somente simulação. Este utilitário nunca envia mensagem, cria compra,
venda ou operação, nem promove uma oferta para as tabelas operacionais.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from confinex_client import ConfinexClient, ConfinexError, ConfinexHTTPError

PERGUNTAS = {
    "preco_arroba": "Qual é o preço?",
    "quantidade": "Qual é a quantidade?",
    "sexo": "É macho ou fêmea?",
    "peso_medio_kg": "Qual é o peso estimado?",
    "localizacao": "Qual é a localização?",
}


def montar_oferta(valores: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    """Monta o candidato e lista perguntas; não acessa rede nem banco."""
    faltantes: list[str] = []
    if valores.preco_arroba is None:
        faltantes.append("preco_arroba")
    if valores.quantidade is None:
        faltantes.append("quantidade")
    if valores.sexo == "nao_informado":
        faltantes.append("sexo")
    if valores.peso_medio_kg is None:
        faltantes.append("peso_medio_kg")
    if not valores.municipio or not valores.uf:
        faltantes.append("localizacao")

    oferta = {
        "fornecedor_id": valores.fornecedor_id,
        "corretor_id": valores.corretor_id,
        "sexo": valores.sexo,
        "categoria": valores.categoria,
        "quantidade": valores.quantidade,
        "peso_medio_kg": valores.peso_medio_kg,
        "preco_arroba": valores.preco_arroba,
        "modalidade_preco": valores.modalidade_preco,
        "municipio": valores.municipio,
        "uf": valores.uf.upper() if valores.uf else None,
        "status": "incompleta" if faltantes else "nova",
        "origem_canal": valores.origem_canal,
        "origem_conversa_id": valores.origem_conversa_id,
        "origem_mensagem_id": valores.origem_mensagem_id,
        "campos_faltantes": faltantes,
        "observacoes": valores.observacoes,
        "metadados": {"captura": "juan_ofertas_v1"},
    }
    return {chave: valor for chave, valor in oferta.items() if valor is not None}, [
        PERGUNTAS[campo] for campo in faltantes
    ]


def registrar_oferta(
    client: ConfinexClient,
    oferta: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    """Registra com idempotência pela identidade da mensagem de origem."""
    canal = oferta.get("origem_canal")
    conversa = oferta.get("origem_conversa_id")
    mensagem = oferta.get("origem_mensagem_id")
    if canal == "manual" or not conversa or not mensagem:
        raise ConfinexError(
            "gravação automatizada exige canal, conversa e mensagem de origem"
        )
    filtros = {
        "select": "*",
        "origem_canal": f"eq.{canal}",
        "origem_conversa_id": f"eq.{conversa}",
        "origem_mensagem_id": f"eq.{mensagem}",
        "limit": 2,
    }
    existentes = client.select("ofertas_gado", **filtros)
    if existentes:
        if len(existentes) > 1:
            raise ConfinexError("identidade da oferta retornou mais de um registro")
        divergentes = [
            chave for chave, valor in oferta.items()
            if existentes[0].get(chave) != valor
        ]
        if divergentes:
            raise ConfinexError(
                "mensagem de origem já registrada com dados diferentes: "
                + ", ".join(sorted(divergentes))
            )
        return "duplicada", existentes[0]
    try:
        return "registrada", client.insert("ofertas_gado", oferta)
    except ConfinexHTTPError as exc:
        if exc.status != 409:
            raise
        existentes = client.select("ofertas_gado", **filtros)
        if len(existentes) == 1:
            divergentes = [
                chave for chave, valor in oferta.items()
                if existentes[0].get(chave) != valor
            ]
            if not divergentes:
                return "duplicada", existentes[0]
        raise ConfinexError("conflito sem oferta reconciliável") from exc


def criar_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fornecedor-id")
    parser.add_argument("--corretor-id")
    parser.add_argument(
        "--sexo",
        choices=("macho", "femea", "misto", "nao_informado"),
        default="nao_informado",
    )
    parser.add_argument("--categoria")
    parser.add_argument("--quantidade", type=int)
    parser.add_argument("--peso-medio-kg", type=float)
    parser.add_argument("--preco-arroba", type=float)
    parser.add_argument(
        "--modalidade-preco",
        choices=("arroba", "cabeca", "kg", "lote", "a_combinar"),
        default="arroba",
    )
    parser.add_argument("--municipio")
    parser.add_argument("--uf")
    parser.add_argument(
        "--origem-canal",
        choices=("manual", "whatsapp", "telegram", "telefone", "presencial", "outro"),
        default="manual",
    )
    parser.add_argument("--origem-conversa-id")
    parser.add_argument("--origem-mensagem-id")
    parser.add_argument("--observacoes")
    parser.add_argument(
        "--executar",
        action="store_true",
        help="grava somente a oferta CRM; o padrão é simulação",
    )
    return parser


def main() -> int:
    valores = criar_parser().parse_args()
    oferta, perguntas = montar_oferta(valores)
    saida: dict[str, Any] = {
        "modo": "executar" if valores.executar else "simulacao",
        "oferta": oferta,
        "perguntas_sugeridas": perguntas,
        "mensagem_enviada": False,
        "efeito_operacional": False,
    }
    if valores.executar:
        status, registro = registrar_oferta(ConfinexClient(), oferta)
        saida.update({"status": status, "registro_id": registro.get("id")})
    else:
        saida["status"] = "nao_gravada"
    print(json.dumps(saida, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
