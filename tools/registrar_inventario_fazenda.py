#!/usr/bin/env python3
"""Registra uma fotografia física da Fazenda sem criar movimentações.

O modo padrão é dry-run. A execução usa uma única requisição POST, sem retry,
e reconcilia pela chave idempotente se o resultado de rede for incerto.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


ENV_PROTEGIDO = Path("/root/.openclaw/gateway.systemd.env")
TABELA = "inventarios_fazenda"
UNIDADE_PADRAO = "fazenda_ametista"
CHAVE_RE = re.compile(r"^[a-z0-9_:-]{20,180}$")


class InventarioError(RuntimeError):
    """Falha segura e esperada no fluxo do inventário."""


def _decimal(valor: Any, campo: str) -> Decimal:
    try:
        numero = Decimal(str(valor))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise InventarioError(f"{campo} inválido") from exc
    if numero <= 0:
        raise InventarioError(f"{campo} deve ser maior que zero")
    return numero


def _decimal_texto(numero: Decimal) -> str:
    """Mantém JSON legível e estável, sem notação científica."""
    texto = format(numero, "f")
    return texto.rstrip("0").rstrip(".") if "." in texto else texto


def _texto(valor: Any, campo: str) -> str:
    texto = " ".join(str(valor or "").strip().split())
    if not texto:
        raise InventarioError(f"{campo} é obrigatório")
    return texto


def _chave_item(unidade: str, data: str, local: str, categoria: str, sexo: str | None) -> str:
    identidade = json.dumps(
        [unidade, data, local.casefold(), categoria.casefold(), sexo or "nao_informado"],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"inventario_fazenda:{data}:{hashlib.sha256(identidade).hexdigest()[:24]}"


def preparar_plano(documento: dict[str, Any]) -> dict[str, Any]:
    unidade = _texto(documento.get("unidade_codigo") or UNIDADE_PADRAO, "unidade_codigo")
    data = _texto(documento.get("data_referencia"), "data_referencia")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", data):
        raise InventarioError("data_referencia deve usar AAAA-MM-DD")
    fonte = _texto(documento.get("fonte"), "fonte")
    criado_por = _texto(documento.get("criado_por"), "criado_por")
    itens_origem = documento.get("itens")
    if not isinstance(itens_origem, list) or not itens_origem:
        raise InventarioError("itens deve conter ao menos uma linha")

    itens = []
    identidades = set()
    for posicao, origem in enumerate(itens_origem, 1):
        if not isinstance(origem, dict):
            raise InventarioError(f"item {posicao} inválido")
        local = _texto(origem.get("local_nome"), f"item {posicao}: local_nome")
        categoria = _texto(origem.get("categoria"), f"item {posicao}: categoria")
        sexo = origem.get("sexo") or None
        if sexo not in {None, "macho", "femea", "misto"}:
            raise InventarioError(f"item {posicao}: sexo inválido")
        cabecas_decimal = _decimal(origem.get("cabecas"), f"item {posicao}: cabecas")
        if cabecas_decimal != cabecas_decimal.to_integral_value():
            raise InventarioError(f"item {posicao}: cabeças deve ser inteiro")
        cabecas = int(cabecas_decimal)
        peso_medio = _decimal(origem.get("peso_medio_kg"), f"item {posicao}: peso_medio_kg")
        identidade = (local.casefold(), categoria.casefold(), sexo or "nao_informado")
        if identidade in identidades:
            raise InventarioError(f"item {posicao}: local/categoria/sexo repetido")
        identidades.add(identidade)
        chave = _chave_item(unidade, data, local, categoria, sexo)
        if not CHAVE_RE.fullmatch(chave):
            raise InventarioError("chave idempotente inválida")
        itens.append(
            {
                "unidade_codigo": unidade,
                "fazenda_id": documento.get("fazenda_id") or None,
                "data_referencia": data,
                "local_nome": local,
                "categoria": categoria,
                "sexo": sexo,
                "cabecas": cabecas,
                "peso_medio_kg": _decimal_texto(peso_medio),
                "fonte": fonte,
                "observacoes": origem.get("observacoes") or None,
                "idempotency_key": chave,
                "criado_por": criado_por,
            }
        )

    cabecas_total = sum(item["cabecas"] for item in itens)
    peso_total = sum(Decimal(item["peso_medio_kg"]) * item["cabecas"] for item in itens)
    esperado_cabecas = documento.get("cabecas_total")
    esperado_peso = documento.get("peso_total_kg")
    if esperado_cabecas is not None and int(esperado_cabecas) != cabecas_total:
        raise InventarioError("total de cabeças diverge dos itens")
    if esperado_peso is not None and Decimal(str(esperado_peso)) != peso_total:
        raise InventarioError("peso total diverge dos itens")

    assinatura = hashlib.sha256(
        json.dumps(itens, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        "plano_id": assinatura[:12],
        "unidade_codigo": unidade,
        "data_referencia": data,
        "itens": itens,
        "itens_total": len(itens),
        "cabecas_total": cabecas_total,
        "peso_total_kg": _decimal_texto(peso_total),
        "peso_medio_kg": _decimal_texto(
            (peso_total / cabecas_total).quantize(Decimal("0.001"))
        ),
    }


def frase_confirmacao(plano: dict[str, Any]) -> str:
    return f"REGISTRAR INVENTARIO FAZENDA {plano['plano_id']}"


def _env_protegido() -> dict[str, str]:
    valores = dict(os.environ)
    try:
        linhas = ENV_PROTEGIDO.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, PermissionError):
        linhas = []
    for linha in linhas:
        if "=" in linha and not linha.lstrip().startswith("#"):
            chave, valor = linha.split("=", 1)
            valores.setdefault(chave, valor)
    return valores


@dataclass
class ClienteInventario:
    url: str
    chave: str
    timeout: int = 20

    @classmethod
    def do_ambiente(cls) -> "ClienteInventario":
        env = _env_protegido()
        url = env.get("SUPABASE_URL") or env.get("CONFINEX_DB_URL") or ""
        chave = env.get("SUPABASE_SERVICE_KEY") or env.get("CONFINEX_DB_KEY") or ""
        if not url or not chave:
            raise InventarioError("credenciais protegidas do Supabase indisponíveis")
        return cls(url.rstrip("/"), chave)

    def _requisicao(self, metodo: str, caminho: str, corpo: Any = None) -> Any:
        dados = None if corpo is None else json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            f"{self.url}/rest/v1/{caminho}",
            data=dados,
            headers={
                "apikey": self.chave,
                "Authorization": f"Bearer {self.chave}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
            method=metodo,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resposta:
                texto = resposta.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise InventarioError(f"Supabase respondeu HTTP {exc.code}") from exc
        return json.loads(texto) if texto else []

    def buscar(self, chaves: list[str]) -> list[dict[str, Any]]:
        filtro = "(" + ",".join(chaves) + ")"
        consulta = urllib.parse.urlencode(
            {"select": "*", "idempotency_key": f"in.{filtro}", "order": "idempotency_key"}
        )
        return self._requisicao("GET", f"{TABELA}?{consulta}")

    def inserir_lote(self, itens: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self._requisicao("POST", TABELA, itens)


def _mesmo_item(existente: dict[str, Any], solicitado: dict[str, Any]) -> bool:
    campos = (
        "unidade_codigo", "fazenda_id", "data_referencia", "local_nome", "categoria",
        "sexo", "cabecas", "peso_medio_kg", "fonte", "observacoes", "idempotency_key", "criado_por",
    )
    for campo in campos:
        atual = existente.get(campo)
        esperado = solicitado.get(campo)
        if campo == "peso_medio_kg":
            if Decimal(str(atual)) != Decimal(str(esperado)):
                return False
        elif atual != esperado:
            return False
    return True


def executar_plano(cliente: ClienteInventario, plano: dict[str, Any]) -> dict[str, Any]:
    itens = plano["itens"]
    chaves = [item["idempotency_key"] for item in itens]
    existentes = cliente.buscar(chaves)
    if existentes:
        por_chave = {item["idempotency_key"]: item for item in existentes}
        if len(por_chave) != len(itens):
            raise InventarioError("inventário parcialmente existente; nenhuma escrita executada")
        if not all(_mesmo_item(por_chave[item["idempotency_key"]], item) for item in itens):
            raise InventarioError("chave idempotente já usada com conteúdo diferente")
        return {"estado": "duplicate", "registros": len(existentes), "post_executado": False}

    try:
        criados = cliente.inserir_lote(itens)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reconciliados = cliente.buscar(chaves)
        por_chave = {item["idempotency_key"]: item for item in reconciliados}
        if len(por_chave) == len(itens) and all(
            _mesmo_item(por_chave[item["idempotency_key"]], item) for item in itens
        ):
            return {"estado": "reconciled", "registros": len(reconciliados), "post_executado": True}
        raise InventarioError("resultado incerto após falha de rede; não repetir automaticamente") from exc
    if len(criados) != len(itens):
        raise InventarioError("Supabase não confirmou todos os itens do inventário")
    return {"estado": "success", "registros": len(criados), "post_executado": True}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--arquivo", required=True)
    parser.add_argument("--executar", action="store_true")
    parser.add_argument("--confirmacao")
    args = parser.parse_args()
    try:
        documento = json.loads(Path(args.arquivo).read_text(encoding="utf-8"))
        plano = preparar_plano(documento)
        resumo = {k: v for k, v in plano.items() if k != "itens"}
        resumo.update({"modo": "dry-run", "escritas_supabase": 0, "confirmacao_exigida": frase_confirmacao(plano)})
        if not args.executar:
            print(json.dumps(resumo, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        if args.confirmacao != frase_confirmacao(plano):
            raise InventarioError("frase de confirmação inválida")
        resultado = executar_plano(ClienteInventario.do_ambiente(), plano)
        resumo.update(resultado)
        resumo["modo"] = "executado"
        resumo["escritas_supabase"] = resultado["registros"] if resultado["estado"] == "success" else 0
        print(json.dumps(resumo, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (InventarioError, json.JSONDecodeError) as exc:
        print(json.dumps({"estado": "erro", "erro": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
