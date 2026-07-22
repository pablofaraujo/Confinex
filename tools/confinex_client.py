#!/usr/bin/env python3
"""Cliente REST minimo para rotinas controladas do Confinex no Supabase."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class ConfinexError(RuntimeError):
    """Erro esperado em rotinas operacionais do Confinex."""


class ConfinexClient:
    def __init__(self, *, url: str | None = None, key: str | None = None) -> None:
        self.url = (url or os.getenv("SUPABASE_URL") or os.getenv("CONFINEX_SUPABASE_URL") or "").rstrip("/")
        self.key = key or os.getenv("SUPABASE_SERVICE_KEY") or os.getenv("SUPABASE_ANON_KEY") or ""
        if not self.url or not self.key:
            raise ConfinexError("configure SUPABASE_URL e SUPABASE_SERVICE_KEY no ambiente")

    def _request(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        prefer: str | None = "return=representation",
    ) -> Any:
        query = urllib.parse.urlencode(params or {}, doseq=True)
        url = f"{self.url}/rest/v1/{table}"
        if query:
            url += f"?{query}"
        body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
        }
        if prefer:
            headers["Prefer"] = prefer
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                text = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ConfinexError(f"Supabase {method} {table} falhou: {exc.code} {detail}") from exc
        except urllib.error.URLError as exc:
            raise ConfinexError(f"nao foi possivel conectar ao Supabase: {exc.reason}") from exc
        if not text:
            return []
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfinexError(f"resposta invalida do Supabase em {table}") from exc

    def select(self, table: str, **params: Any) -> list[dict[str, Any]]:
        rows = self._request("GET", table, params=params, payload=None, prefer=None)
        if not isinstance(rows, list):
            raise ConfinexError(f"select em {table} retornou formato inesperado")
        return rows

    def insert(self, table: str, payload: dict[str, Any]) -> dict[str, Any]:
        rows = self._request("POST", table, payload=payload)
        if isinstance(rows, list) and rows:
            return rows[0]
        raise ConfinexError(f"insert em {table} nao retornou registro")

    def insert_operational(self, table: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self.insert(table, payload)

    def update(self, table: str, filters: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
        rows = self._request("PATCH", table, params=filters, payload=payload)
        if not isinstance(rows, list):
            raise ConfinexError(f"update em {table} retornou formato inesperado")
        return rows
