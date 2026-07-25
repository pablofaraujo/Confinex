#!/usr/bin/env python3
"""Cliente REST minimo para rotinas controladas do Confinex no Supabase."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


class ConfinexError(RuntimeError):
    """Erro esperado em rotinas operacionais do Confinex."""


class ConfinexHTTPError(ConfinexError):
    """Erro HTTP com status preservado para decisões seguras do cliente."""

    def __init__(self, message: str, *, status: int) -> None:
        super().__init__(message)
        self.status = status


class ConfinexConnectionError(ConfinexError):
    """Falha de transporte cuja conclusão no servidor pode ser desconhecida."""


class ConfinexIdempotencyConflict(ConfinexError):
    """A mesma chave idempotente foi associada a dados diferentes."""


@dataclass(frozen=True)
class OperationalInsertResult:
    """Resultado persistente de uma tentativa de gravação operacional."""

    status: str
    record: dict[str, Any]


def _canonical_value(value: Any) -> Any:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float, Decimal)):
        try:
            return ("numero", str(Decimal(str(value)).normalize()))
        except InvalidOperation:
            return value
    if isinstance(value, dict):
        return {key: _canonical_value(current) for key, current in sorted(value.items())}
    if isinstance(value, list):
        return [_canonical_value(current) for current in value]
    return value


def _same_requested_payload(existing: dict[str, Any], requested: dict[str, Any]) -> bool:
    expected = {
        key: _canonical_value(value)
        for key, value in requested.items()
        if key != "idempotency_key"
    }
    actual = {key: _canonical_value(existing.get(key)) for key in expected}
    return actual == expected


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
            raise ConfinexHTTPError(
                f"Supabase {method} {table} falhou: {exc.code} {detail}",
                status=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise ConfinexConnectionError(
                f"nao foi possivel conectar ao Supabase: {exc.reason}"
            ) from exc
        except TimeoutError as exc:
            raise ConfinexConnectionError(
                "tempo esgotado ao conectar ao Supabase"
            ) from exc
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

    def _find_purchase_by_idempotency_key(self, key: str) -> list[dict[str, Any]]:
        return self.select(
            "compras",
            select="*",
            idempotency_key=f"eq.{key}",
            limit=2,
        )

    def _reconcile_purchase(
        self,
        key: str,
        requested: dict[str, Any],
    ) -> OperationalInsertResult | None:
        rows = self._find_purchase_by_idempotency_key(key)
        if not rows:
            return None
        if len(rows) != 1:
            raise ConfinexError("chave idempotente de compra retornou mais de um registro")
        existing = rows[0]
        if not _same_requested_payload(existing, requested):
            raise ConfinexIdempotencyConflict(
                "chave idempotente ja existe com dados diferentes"
            )
        return OperationalInsertResult(status="duplicate", record=existing)

    def insert_operational(
        self,
        table: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str | None = None,
    ) -> OperationalInsertResult:
        if table != "compras" or idempotency_key is None:
            return OperationalInsertResult(status="inserted", record=self.insert(table, payload))

        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ConfinexError("chave idempotente de compra nao pode ser vazia")
        key = idempotency_key.strip()
        if len(key) > 200:
            raise ConfinexError("chave idempotente de compra excede 200 caracteres")

        requested = {**payload, "idempotency_key": key}
        try:
            inserted = self.insert("compras", requested)
            return OperationalInsertResult(status="inserted", record=inserted)
        except ConfinexHTTPError as exc:
            if exc.status != 409:
                raise
            reconciled = self._reconcile_purchase(key, requested)
            if reconciled is None:
                raise ConfinexError(
                    "Supabase informou duplicidade, mas a compra nao foi localizada"
                ) from exc
            return reconciled
        except ConfinexConnectionError:
            reconciled = self._reconcile_purchase(key, requested)
            if reconciled is not None:
                return reconciled
            raise

    def update(self, table: str, filters: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
        rows = self._request("PATCH", table, params=filters, payload=payload)
        if not isinstance(rows, list):
            raise ConfinexError(f"update em {table} retornou formato inesperado")
        return rows
