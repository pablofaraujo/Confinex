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
from pathlib import Path
from typing import Any

ENV_PATH = Path("/root/.openclaw/gateway.systemd.env")
TIMEOUT_MAX_SECONDS = 20

READ_TABLES = {
    "abates",
    "compras",
    "confinex_avaliacoes",
    "confinex_consolidacoes",
    "confinex_desvios",
    "confinex_estimativas",
    "confinex_testes",
    "contatos",
    "contexto_handoff",
    "contextos_canais",
    "eventos",
    "gtas",
    "memorias_agentes",
    "operacoes",
    "operation_drafts",
    "pendencias_documentos",
    "pending_actions",
    "pesagens_caderno",
    "vendas",
}

WRITE_TABLES = {
    "contexto_handoff",
    "contextos_canais",
    "eventos",
    "memorias_agentes",
    "operation_drafts",
    "pending_actions",
}

OPERATIONAL_WRITE_TABLES = {
    "abates",
    "compras",
    "pesagens_caderno",
    "vendas",
}


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


def _load_protected_env() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, PermissionError):
        return values
    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, value = line.split("=", 1)
        values[key] = value
    return values


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
    def __init__(
        self,
        *,
        url: str | None = None,
        key: str | None = None,
        env: dict[str, str] | None = None,
        timeout: int = TIMEOUT_MAX_SECONDS,
    ) -> None:
        values = {**_load_protected_env(), **os.environ, **(env or {})}
        self.url = (
            url
            or values.get("SUPABASE_URL")
            or values.get("CONFINEX_SUPABASE_URL")
            or values.get("CONFINEX_DB_URL")
            or ""
        ).rstrip("/")
        self.key = (
            key
            or values.get("SUPABASE_SERVICE_KEY")
            or values.get("SUPABASE_ANON_KEY")
            or values.get("CONFINEX_DB_KEY")
            or ""
        )
        self.timeout = max(1, min(int(timeout), TIMEOUT_MAX_SECONDS))
        if not self.url or not self.key:
            raise ConfinexError(
                "credenciais protegidas do Supabase não estão disponíveis"
            )

    def _request(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | None = None,
        prefer: str | None = "return=representation",
    ) -> Any:
        method = method.upper()
        if table not in READ_TABLES:
            raise ConfinexError(f"tabela não permitida: {table}")
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
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise ConfinexHTTPError(
                f"Supabase {method} {table} falhou com HTTP {exc.code}",
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
        if table not in WRITE_TABLES:
            raise ConfinexError(f"escrita não permitida para tabela: {table}")
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
        if table not in OPERATIONAL_WRITE_TABLES:
            raise ConfinexError(f"tabela operacional não permitida: {table}")
        if not isinstance(payload, dict) or not payload:
            raise ConfinexError("payload operacional deve ser um objeto não vazio")
        if table != "compras" or idempotency_key is None:
            rows = self._request("POST", table, payload=payload)
            if isinstance(rows, list) and rows:
                return OperationalInsertResult(status="inserted", record=rows[0])
            raise ConfinexError(
                f"insert operacional em {table} não retornou registro"
            )

        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise ConfinexError("chave idempotente de compra nao pode ser vazia")
        key = idempotency_key.strip()
        if len(key) > 200:
            raise ConfinexError("chave idempotente de compra excede 200 caracteres")

        requested = {**payload, "idempotency_key": key}
        try:
            rows = self._request("POST", "compras", payload=requested)
            if not isinstance(rows, list) or not rows:
                raise ConfinexError(
                    "insert operacional em compras não retornou registro"
                )
            inserted = rows[0]
            return OperationalInsertResult(status="inserted", record=inserted)
        except ConfinexHTTPError as exc:
            if exc.status != 409 and exc.status < 500:
                raise
            reconciled = self._reconcile_purchase(key, requested)
            if reconciled is not None:
                return reconciled
            if exc.status == 409:
                raise ConfinexError(
                    "Supabase informou duplicidade, mas a compra nao foi localizada"
                ) from exc
            raise ConfinexConnectionError(
                "resultado incerto após falha do servidor; não repetir a compra"
            ) from exc
        except ConfinexConnectionError:
            reconciled = self._reconcile_purchase(key, requested)
            if reconciled is not None:
                return reconciled
            raise

    def update(self, table: str, filters: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
        if table not in WRITE_TABLES:
            raise ConfinexError(f"alteração não permitida para tabela: {table}")
        rows = self._request("PATCH", table, params=filters, payload=payload)
        if not isinstance(rows, list):
            raise ConfinexError(f"update em {table} retornou formato inesperado")
        return rows
