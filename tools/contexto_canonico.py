#!/usr/bin/env python3
"""Contrato compartilhado de contexto do Confinex."""

from __future__ import annotations

import re
from typing import Any

CONTEXT_FIELDS = (
    "contexto_canonico",
    "contexto_nome",
    "origem_canal",
    "origem_conversa_id",
    "origem_mensagem_id",
    "agente",
    "escopo",
)

TELEGRAM_ID_RE = re.compile(r"^-?\d{6,}$")


def origem_conversa_tecnica(value: Any) -> str | None:
    """Remove prefixos históricos sem converter o ID em nome humano."""
    raw = str(value or "").strip()
    if not raw:
        return None
    for prefix in ("telegram:grupo:", "telegram:direto:", "telegram:"):
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break
    return raw if TELEGRAM_ID_RE.fullmatch(raw) else None


def escopo_contexto(canal: Any, conversa_id: Any, escopo: Any = None) -> str:
    explicit = str(escopo or "").strip().lower()
    if explicit in {"grupo", "direto", "sistema"}:
        return explicit
    technical = origem_conversa_tecnica(conversa_id)
    if str(canal or "").strip().lower() == "telegram" and technical:
        return "grupo" if technical.startswith("-") else "direto"
    return "sistema"


def contexto_canonico(canal: Any, conversa_id: Any, escopo: Any = None) -> str | None:
    channel = str(canal or "").strip().lower()
    technical = origem_conversa_tecnica(conversa_id)
    if channel == "telegram" and technical:
        return f"telegram:{escopo_contexto(channel, technical, escopo)}:{technical}"
    return None


def montar_contexto(
    *,
    contexto_nome: Any,
    origem_canal: Any,
    origem_conversa_id: Any,
    origem_mensagem_id: Any = None,
    agente: Any = None,
    escopo: Any = None,
) -> dict[str, str | None]:
    channel = str(origem_canal or "").strip().lower() or None
    technical = origem_conversa_tecnica(origem_conversa_id)
    scope = escopo_contexto(channel, technical, escopo)
    return {
        "contexto_canonico": contexto_canonico(channel, technical, scope),
        "contexto_nome": str(contexto_nome or "").strip() or None,
        "origem_canal": channel,
        "origem_conversa_id": technical,
        "origem_mensagem_id": str(origem_mensagem_id or "").strip() or None,
        "agente": str(agente or "").strip().lower() or None,
        "escopo": scope,
    }


def contexto_valido(contexto: dict[str, Any]) -> bool:
    return all(contexto.get(field) for field in CONTEXT_FIELDS if field != "origem_mensagem_id")
