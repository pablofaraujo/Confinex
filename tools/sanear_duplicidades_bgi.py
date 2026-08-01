#!/usr/bin/env python3
"""Detecta e remove somente pares legado/Portfólio B3 comprovadamente iguais."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from decimal import Decimal, InvalidOperation
from typing import Any, Protocol


ENV_PATH = Path("/root/.openclaw/gateway.systemd.env")
TABELAS_OPERACIONAIS = ("compras", "vendas", "abates", "pesagens_caderno")
CAMPOS_ECONOMICOS = (
    "contrato", "direcao", "categoria", "contratos_qtd", "preco_entrada",
    "preco_saida", "data_entrada", "data_saida", "status",
    "custo_corretagem", "custo_finpec", "resultado_realizado",
)
CAMPOS_DESCRITIVOS = ("negocio_rateio", "detalhes", "obs", "mes", "rolada_para")
CAMPOS_VISUAIS = ("contrato", "direcao", "contratos_qtd", "preco_entrada", "status")


class Cliente(Protocol):
    def selecionar(self, tabela: str) -> list[dict[str, Any]]: ...
    def excluir_posicao(self, registro_id: str) -> list[dict[str, Any]]: ...


def _carregar_ambiente() -> dict[str, str]:
    valores: dict[str, str] = {}
    try:
        linhas = ENV_PATH.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, PermissionError):
        linhas = []
    for linha in linhas:
        if "=" not in linha or linha.lstrip().startswith("#"):
            continue
        chave, valor = linha.split("=", 1)
        valores[chave] = valor.strip().strip('"').strip("'")
    return {**valores, **os.environ}


class ClienteRest:
    def __init__(self) -> None:
        env = _carregar_ambiente()
        self.url = (env.get("SUPABASE_URL") or env.get("CONFINEX_DB_URL") or "").rstrip("/")
        self.key = env.get("SUPABASE_SERVICE_KEY") or env.get("CONFINEX_DB_KEY") or ""
        if not self.url or not self.key:
            raise RuntimeError("credenciais protegidas do Supabase não estão disponíveis")

    def _requisicao(self, metodo: str, tabela: str, parametros: dict[str, str]) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode(parametros)
        url = f"{self.url}/rest/v1/{tabela}?{query}"
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "Prefer": "return=representation",
        }
        req = urllib.request.Request(url, headers=headers, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=20) as resposta:
                conteudo = resposta.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"Supabase recusou {metodo} em {tabela}: HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError(f"falha de conexão em {metodo} {tabela}: {type(exc).__name__}") from exc
        return json.loads(conteudo or "[]")

    def selecionar(self, tabela: str) -> list[dict[str, Any]]:
        return self._requisicao("GET", tabela, {"select": "*", "order": "id.asc"})

    def excluir_posicao(self, registro_id: str) -> list[dict[str, Any]]:
        return self._requisicao("DELETE", "posicoes_hedge", {"id": f"eq.{registro_id}"})


def _normalizar(valor: Any) -> Any:
    if valor in ("", None):
        return None
    if isinstance(valor, float):
        return round(valor, 10)
    return valor


def _normalizar_campo(campo: str, valor: Any) -> Any:
    normalizado = _normalizar(valor)
    if campo in {"custo_corretagem", "custo_finpec"}:
        if normalizado is None:
            return 0
        try:
            if Decimal(str(normalizado)) == 0:
                return 0
        except InvalidOperation:
            pass
    return normalizado


def _classe_valor(valor: Any) -> str:
    normalizado = _normalizar(valor)
    if normalizado is None:
        return "vazio"
    try:
        return "zero" if Decimal(str(normalizado)) == 0 else "nao_zero"
    except InvalidOperation:
        return "preenchido"


def _enriquecimento_seguro(campo: str, legado: Any, canonico: Any) -> bool:
    return (
        campo in {"custo_corretagem", "custo_finpec"}
        and _normalizar(legado) is None
        and _normalizar(canonico) is not None
    )


def _chave_economica(posicao: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(_normalizar_campo(campo, posicao.get(campo)) for campo in CAMPOS_ECONOMICOS)


def _chave_visual(posicao: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(_normalizar(posicao.get(campo)) for campo in CAMPOS_VISUAIS)


def _tem_dado_exclusivo(legado: dict[str, Any], canonico: dict[str, Any]) -> bool:
    for campo in CAMPOS_DESCRITIVOS:
        valor_legado = _normalizar(legado.get(campo))
        valor_canonico = _normalizar(canonico.get(campo))
        if valor_legado is not None and valor_legado != valor_canonico:
            return True
    return False


def _assinatura(rows: list[dict[str, Any]]) -> str:
    serializado = json.dumps(rows, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def _referencia(registro_id: str) -> str:
    return hashlib.sha256(registro_id.encode("utf-8")).hexdigest()[:12]


def montar_plano(cliente: Cliente) -> tuple[dict[str, Any], dict[str, Any]]:
    posicoes = cliente.selecionar("posicoes_hedge")
    alocacoes = cliente.selecionar("alocacoes_hedge")
    por_posicao: dict[str, list[dict[str, Any]]] = {}
    for alocacao in alocacoes:
        por_posicao.setdefault(str(alocacao.get("posicao_id")), []).append(alocacao)

    grupos: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for posicao in posicoes:
        grupos.setdefault(_chave_visual(posicao), []).append(posicao)

    remover: list[dict[str, Any]] = []
    ambiguos: list[dict[str, Any]] = []
    for grupo in grupos.values():
        gerenciadas = [p for p in grupo if str(p.get("termo") or "").startswith("bgp:")]
        legadas = [p for p in grupo if not str(p.get("termo") or "").startswith("bgp:")]
        if not gerenciadas or not legadas:
            continue
        if len(gerenciadas) != 1:
            ambiguos.append({"motivo": "mais_de_um_registro_gerenciado", "quantidade": len(grupo)})
            continue
        canonico = gerenciadas[0]
        for legado in legadas:
            motivos = []
            campos_divergentes = [
                campo for campo in CAMPOS_ECONOMICOS
                if _normalizar_campo(campo, legado.get(campo))
                != _normalizar_campo(campo, canonico.get(campo))
            ]
            campos_enriquecidos = [
                campo for campo in campos_divergentes
                if _enriquecimento_seguro(campo, legado.get(campo), canonico.get(campo))
            ]
            campos_bloqueantes = [
                campo for campo in campos_divergentes if campo not in campos_enriquecidos
            ]
            if campos_bloqueantes:
                motivos.append("campos_economicos_divergentes")
            if por_posicao.get(str(legado.get("id"))):
                motivos.append("legado_possui_alocacao")
            if _tem_dado_exclusivo(legado, canonico):
                motivos.append("legado_possui_dado_exclusivo")
            if motivos:
                ambiguos.append({
                    "referencia": _referencia(str(legado["id"])),
                    "motivos": motivos,
                    "campos_divergentes": campos_divergentes,
                    "classes_divergentes": {
                        campo: {
                            "legado": _classe_valor(legado.get(campo)),
                            "canonico": _classe_valor(canonico.get(campo)),
                        }
                        for campo in campos_divergentes
                    },
                })
                continue
            remover.append({
                "legado_id": str(legado["id"]),
                "canonico_id": str(canonico["id"]),
                "referencia": _referencia(str(legado["id"])),
                "enriquecimentos_preservados": campos_enriquecidos,
            })

    base_plano = {
        "remover": sorted(remover, key=lambda item: item["legado_id"]),
        "ambiguos": ambiguos,
    }
    plano_id = hashlib.sha256(json.dumps(base_plano, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    plano_publico = {
        "modo": "dry-run",
        "plano_id": plano_id,
        "duplicidades_seguras": len(remover),
        "referencias_seguras": [item["referencia"] for item in remover],
        "ambiguidades_preservadas": len(ambiguos),
        "ambiguidades": ambiguos,
        "escritas_realizadas": 0,
        "tabelas_operacionais_alteradas": 0,
        "confirmacao_exigida": f"SANEAR DUPLICIDADES BGI {plano_id}",
    }
    snapshot = {
        "plano_id": plano_id,
        "posicoes_hedge": posicoes,
        "alocacoes_hedge": alocacoes,
        "assinaturas": {
            "posicoes_hedge": _assinatura(posicoes),
            "alocacoes_hedge": _assinatura(alocacoes),
        },
        "alvos": base_plano["remover"],
    }
    return plano_publico, snapshot


def executar(cliente: Cliente, plano: dict[str, Any], snapshot: dict[str, Any], confirmacao: str, backup: Path) -> dict[str, Any]:
    esperado = plano["confirmacao_exigida"]
    if confirmacao != esperado:
        raise RuntimeError(f"confirmação inválida; use exatamente: {esperado}")

    operacionais_antes = {tabela: cliente.selecionar(tabela) for tabela in TABELAS_OPERACIONAIS}
    backup.parent.mkdir(parents=True, exist_ok=True)
    backup.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True, default=str), encoding="utf-8")
    backup.chmod(0o600)

    excluidos = []
    for alvo in snapshot["alvos"]:
        retorno = cliente.excluir_posicao(alvo["legado_id"])
        if len(retorno) != 1 or str(retorno[0].get("id")) != alvo["legado_id"]:
            restantes = cliente.selecionar("posicoes_hedge")
            if any(str(item.get("id")) == alvo["legado_id"] for item in restantes):
                raise RuntimeError(f"remoção não confirmada para {alvo['referencia']}")
        excluidos.append(alvo["referencia"])

    posicoes_depois = cliente.selecionar("posicoes_hedge")
    alocacoes_depois = cliente.selecionar("alocacoes_hedge")
    ids_alvo = {item["legado_id"] for item in snapshot["alvos"]}
    posicoes_esperadas = [p for p in snapshot["posicoes_hedge"] if str(p.get("id")) not in ids_alvo]
    operacionais_depois = {tabela: cliente.selecionar(tabela) for tabela in TABELAS_OPERACIONAIS}
    operacionais_iguais = all(
        _assinatura(operacionais_antes[tabela]) == _assinatura(operacionais_depois[tabela])
        for tabela in TABELAS_OPERACIONAIS
    )
    if _assinatura(posicoes_depois) != _assinatura(posicoes_esperadas):
        raise RuntimeError("houve alteração inesperada fora dos registros duplicados")
    if _assinatura(alocacoes_depois) != snapshot["assinaturas"]["alocacoes_hedge"]:
        raise RuntimeError("alocações foram alteradas inesperadamente")
    if not operacionais_iguais:
        raise RuntimeError("uma tabela operacional foi alterada inesperadamente")

    return {
        **plano,
        "modo": "executado",
        "duplicidades_removidas": len(excluidos),
        "referencias_removidas": excluidos,
        "escritas_realizadas": len(excluidos),
        "alocacoes_alteradas": 0,
        "tabelas_operacionais_alteradas": 0,
        "snapshot": str(backup),
        "verificacao_posicoes": "somente_alvos_removidos",
        "verificacao_operacional": "inalterada",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Saneia duplicidades exatas do Portfólio B3")
    parser.add_argument("--executar", action="store_true")
    parser.add_argument("--confirmacao", default="")
    parser.add_argument("--backup", type=Path, default=Path("/private/tmp/confinex-bgi-duplicidades.json"))
    args = parser.parse_args()
    try:
        cliente = ClienteRest()
        plano, snapshot = montar_plano(cliente)
        resultado = executar(cliente, plano, snapshot, args.confirmacao, args.backup) if args.executar else plano
        print(json.dumps(resultado, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except RuntimeError as exc:
        print(json.dumps({"erro": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
