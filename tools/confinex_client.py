#!/usr/bin/env python3
"""Cliente REST minimo para rotinas controladas do Confinex no Supabase."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ENV_PATH = Path("/root/.openclaw/gateway.systemd.env")
TIMEOUT_MAX_SECONDS = 20
TENTATIVAS_LEITURA_PADRAO = 3
ESPERA_LEITURA_PADRAO = 0.4

READ_TABLES = {
    "acertos",
    "abates",
    "compras",
    "crm_followups",
    "confinex_avaliacoes",
    "confinex_consolidacoes",
    "confinex_desvios",
    "confinex_estimativas",
    "confinex_testes",
    "contatos",
    "confinamento_contatos",
    "confinamentos",
    "contexto_handoff",
    "contextos_canais",
    "eventos",
    "interacoes_crm",
    "gtas",
    "memorias_agentes",
    "operacoes",
    "operation_drafts",
    "negociacoes_gado",
    "ofertas_gado",
    "pendencias_documentos",
    "pending_actions",
    "pesagens_caderno",
    "vendas",
}

WRITE_TABLES = {
    "contexto_handoff",
    "contextos_canais",
    "eventos",
    "interacoes_crm",
    "memorias_agentes",
    "operation_drafts",
    "negociacoes_gado",
    "ofertas_gado",
    "pending_actions",
    "crm_followups",
}

OPERATIONAL_WRITE_TABLES = {
    "abates",
    "compras",
    "pesagens_caderno",
    "vendas",
}

RPC_FUNCTIONS = {
    "consolidar_negocio_confinex",
    "revisar_estimativa_confinex",
    "submeter_negocio_confinex",
}

IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9_.-]+:[^\s]{1,180}$")


class ConfinexError(RuntimeError):
    """Erro esperado em rotinas operacionais do Confinex."""


class ConfinexHTTPError(ConfinexError):
    """Erro HTTP com status preservado para decisões seguras do cliente."""

    def __init__(
        self,
        message: str | int,
        legacy_message: str | None = None,
        *,
        status: int | None = None,
    ) -> None:
        if isinstance(message, int):
            status = message
            message = legacy_message or f"HTTP {status}"
        if status is None:
            raise TypeError("status HTTP é obrigatório")
        super().__init__(message)
        self.status = status


class ConfinexConnectionError(ConfinexError):
    """Falha de transporte cuja conclusão no servidor pode ser desconhecida."""


ConfinexNetworkError = ConfinexConnectionError


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
        env: dict[str, str] | None = None,
        *,
        url: str | None = None,
        key: str | None = None,
        timeout: int = TIMEOUT_MAX_SECONDS,
        tentativas_leitura: int = TENTATIVAS_LEITURA_PADRAO,
        espera_leitura: float = ESPERA_LEITURA_PADRAO,
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
        self.tentativas_leitura = max(1, min(int(tentativas_leitura), 5))
        self.espera_leitura = max(0.0, min(float(espera_leitura), 5.0))
        if not self.url or not self.key:
            raise ConfinexError(
                "credenciais protegidas do Supabase não estão disponíveis"
            )
        self.base_url = f"{self.url}/rest/v1"
        self.env = {
            "CONFINEX_DB_URL": self.url,
            "CONFINEX_DB_KEY": self.key,
        }

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
        tentativas = self.tentativas_leitura if method == "GET" else 1
        for tentativa in range(1, tentativas + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    text = resp.read().decode("utf-8")
                break
            except urllib.error.HTTPError as exc:
                # Erros HTTP são respostas definitivas. Repeti-los pode mascarar
                # autorização, contrato de API ou indisponibilidade persistente.
                raise ConfinexHTTPError(
                    f"Supabase {method} {table} falhou com HTTP {exc.code}",
                    status=exc.code,
                ) from exc
            except urllib.error.URLError as exc:
                erro = exc
                detalhe = type(exc.reason).__name__
            except (TimeoutError, OSError) as exc:
                erro = exc
                detalhe = type(exc).__name__
            if tentativa >= tentativas:
                raise ConfinexConnectionError(
                    f"falha de rede em {method} {table} após {tentativas} tentativa(s): {detalhe}"
                ) from erro
            time.sleep(self.espera_leitura * tentativa)
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
        if len(key) > 200 or not IDEMPOTENCY_KEY_RE.fullmatch(key):
            raise ConfinexError("chave idempotente invalida")

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

    def find_operational_by_idempotency(
        self,
        table: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        if table != "compras":
            raise ConfinexError(
                "reconciliacao idempotente ainda nao habilitada para esta tabela"
            )
        if not IDEMPOTENCY_KEY_RE.fullmatch(idempotency_key):
            raise ConfinexError("chave idempotente invalida")
        rows = self._find_purchase_by_idempotency_key(idempotency_key)
        if len(rows) > 1:
            raise ConfinexError("violacao de unicidade da chave idempotente")
        return rows[0] if rows else None

    def insert_operational_idempotent(
        self,
        table: str,
        payload: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Compatibilidade segura com o executor anterior da VPS."""
        try:
            result = self.insert_operational(
                table,
                payload,
                idempotency_key=idempotency_key,
            )
            if isinstance(result, OperationalInsertResult):
                status = "success" if result.status == "inserted" else result.status
                return {"status": status, "record": result.record}
            return {"status": "success", "record": result}
        except ConfinexHTTPError as exc:
            if exc.status != 409 and exc.status < 500:
                return {"status": "failed", "http_status": exc.status}
        except ConfinexConnectionError:
            pass
        except ConfinexError:
            return {"status": "failed"}
        try:
            found = self.find_operational_by_idempotency(table, idempotency_key)
        except ConfinexError:
            found = None
        if found is not None:
            return {"status": "duplicate", "record": found}
        return {"status": "uncertain"}

    def update(self, table: str, filters: dict[str, Any], payload: dict[str, Any]) -> list[dict[str, Any]]:
        if table not in WRITE_TABLES:
            raise ConfinexError(f"alteração não permitida para tabela: {table}")
        rows = self._request("PATCH", table, params=filters, payload=payload)
        if not isinstance(rows, list):
            raise ConfinexError(f"update em {table} retornou formato inesperado")
        return rows

    def rpc(self, function: str, payload: dict[str, Any]) -> Any:
        """Executa somente RPCs transacionais previamente autorizadas."""
        if function not in RPC_FUNCTIONS:
            raise ConfinexError(f"RPC nao permitida: {function}")
        if not isinstance(payload, dict):
            raise ConfinexError("payload da RPC deve ser um objeto")
        url = f"{self.base_url}/rpc/{function}"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise ConfinexHTTPError(
                f"Supabase RPC {function} falhou com HTTP {exc.code}",
                status=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise ConfinexConnectionError(
                f"falha de rede em RPC {function}: {type(exc.reason).__name__}"
            ) from exc
        except (TimeoutError, OSError) as exc:
            raise ConfinexConnectionError(
                f"falha de rede em RPC {function}: {type(exc).__name__}"
            ) from exc
        if not text:
            return None
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ConfinexError(f"resposta invalida em RPC {function}") from exc
