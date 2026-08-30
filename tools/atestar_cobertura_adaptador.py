#!/usr/bin/env python3
"""Assinatura local do atestado de cobertura produzido por um adaptador.

Este módulo pertence à fronteira do worker de fonte. Ele não conhece JWT do
Supabase, não chama o broker e nunca registra o segredo. O broker transporta o
envelope pronto; o PostgreSQL recompõe o JSON canônico, o hash integral do
pedido e o HMAC antes de aceitar qualquer evidência.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
import hmac
import json
import math
from typing import Any, Mapping


COBERTURAS_SUCESSO = frozenset({"completa", "vazio_com_cobertura"})
COBERTURAS_FALHA = frozenset({
    "cobertura_incompleta", "indisponivel",
    "reautenticacao_necessaria", "erro_permanente",
})


def _numero_canonico(valor: int | float | Decimal) -> str:
    if isinstance(valor, bool):
        raise TypeError("booleano_nao_e_numero")
    if isinstance(valor, int):
        return str(valor)
    if isinstance(valor, float):
        if not math.isfinite(valor):
            raise ValueError("numero_json_nao_finito")
        decimal = Decimal(str(valor))
    else:
        decimal = valor
    if not decimal.is_finite():
        raise ValueError("numero_json_nao_finito")
    if decimal == 0:
        casas = max(0, -decimal.as_tuple().exponent)
        return "0" if casas == 0 else "0." + ("0" * casas)
    return format(decimal, "f")


def json_canonico_postgres(valor: Any) -> str:
    """Replica o contrato ``investigacao_json_canonico(jsonb)``.

    Números usam notação decimal sem expoente, como a saída textual de jsonb;
    objetos usam ordenação binária ``COLLATE C`` e listas preservam a ordem.
    """

    if valor is None:
        return "null"
    if valor is True:
        return "true"
    if valor is False:
        return "false"
    if isinstance(valor, (int, float, Decimal)):
        return _numero_canonico(valor)
    if isinstance(valor, str):
        return json.dumps(valor, ensure_ascii=False, separators=(",", ":"))
    if isinstance(valor, Mapping):
        if not all(isinstance(chave, str) for chave in valor):
            raise TypeError("chave_json_nao_textual")
        partes = []
        for chave in sorted(valor, key=lambda x: x.encode("utf-8")):
            partes.append(
                json.dumps(chave, ensure_ascii=False, separators=(",", ":"))
                + ":" + json_canonico_postgres(valor[chave])
            )
        return "{" + ",".join(partes) + "}"
    if isinstance(valor, (list, tuple)):
        return "[" + ",".join(json_canonico_postgres(item) for item in valor) + "]"
    raise TypeError(f"tipo_json_nao_suportado:{type(valor).__name__}")


def hash_pedido(
    *, estado_cobertura: str, estado_resultado: str,
    bundle: Mapping[str, Any], resumo_sanitizado: str | None,
    erro_codigo: str | None, erro_sanitizado: str | None,
) -> str:
    pedido = {
        "estado_cobertura": estado_cobertura,
        "estado_resultado": estado_resultado,
        "bundle": bundle,
        "resumo_sanitizado": resumo_sanitizado,
        "erro_codigo": erro_codigo,
        "erro_sanitizado": erro_sanitizado,
    }
    return hashlib.sha256(
        json_canonico_postgres(pedido).encode("utf-8")
    ).hexdigest()


def _validar_cobertura(
    *, estado_cobertura: str, inicio_confirmado: bool,
    fim_confirmado: bool, paginas_confirmadas: int,
    registros_confirmados: int, paginacao_modo: str,
    artefato_cobertura_tipo: str, cursor_final_hash: str | None,
    snapshot_fonte_hash: str | None, quantidade_evidencias: int,
) -> None:
    hash_valido = lambda valor: (
        isinstance(valor, str) and len(valor) == 64
        and all(letra in "0123456789abcdef" for letra in valor)
    )
    if (
        not isinstance(paginas_confirmadas, int)
        or isinstance(paginas_confirmadas, bool)
        or not isinstance(registros_confirmados, int)
        or isinstance(registros_confirmados, bool)
        or paginas_confirmadas < 0
        or registros_confirmados < 0
    ):
        raise ValueError("contagem_cobertura_invalida")
    if quantidade_evidencias != registros_confirmados:
        raise ValueError("evidencias_divergem_dos_registros_confirmados")
    if estado_cobertura in COBERTURAS_SUCESSO:
        if not (
            inicio_confirmado and fim_confirmado and paginas_confirmadas >= 1
            and artefato_cobertura_tipo == "snapshot_fonte"
            and hash_valido(snapshot_fonte_hash)
            and paginacao_modo in {"cursor_final", "nao_paginado"}
            and (
                paginacao_modo == "cursor_final" and hash_valido(cursor_final_hash)
                or paginacao_modo == "nao_paginado" and cursor_final_hash is None
            )
        ):
            raise ValueError("cobertura_de_sucesso_incoerente")
        if estado_cobertura == "vazio_com_cobertura" and registros_confirmados != 0:
            raise ValueError("fonte_vazia_com_registros")
        if estado_cobertura == "completa" and registros_confirmados == 0:
            raise ValueError("fonte_completa_sem_registros")
        return
    if estado_cobertura not in COBERTURAS_FALHA:
        raise ValueError("estado_cobertura_invalido")
    pre_resposta = (
        paginas_confirmadas == 0 and registros_confirmados == 0
        and not inicio_confirmado and not fim_confirmado
        and paginacao_modo == "nao_iniciada"
        and artefato_cobertura_tipo == "erro_pre_resposta"
        and cursor_final_hash is None and snapshot_fonte_hash is None
        and quantidade_evidencias == 0
    )
    parcial = (
        paginas_confirmadas >= 1 and inicio_confirmado and not fim_confirmado
        and paginacao_modo == "parcial"
        and artefato_cobertura_tipo == "snapshot_parcial"
        and hash_valido(cursor_final_hash) and hash_valido(snapshot_fonte_hash)
    )
    pos_cobertura = (
        paginas_confirmadas >= 1 and inicio_confirmado and fim_confirmado
        and paginacao_modo in {"cursor_final", "nao_paginado"}
        and artefato_cobertura_tipo == "erro_pos_cobertura"
        and hash_valido(snapshot_fonte_hash)
        and (
            paginacao_modo == "cursor_final" and hash_valido(cursor_final_hash)
            or paginacao_modo == "nao_paginado" and cursor_final_hash is None
        )
    )
    if not (pre_resposta or parcial or pos_cobertura):
        raise ValueError("cobertura_de_falha_incoerente")


def assinar_atestado_cobertura(
    *, segredo: bytes, chave_id: str, adaptador: str,
    adaptador_version: str, artefato_hash: str, familia_fonte: str,
    consulta_hash: str, consulta_ref: str, tarefa_id: str,
    investigacao_id: str, lease_token: str, fencing_token: int,
    estado_cobertura: str, estado_resultado: str,
    bundle: Mapping[str, Any], inicio_confirmado: bool,
    fim_confirmado: bool, paginas_confirmadas: int,
    registros_confirmados: int, paginacao_modo: str,
    artefato_cobertura_tipo: str, cursor_final_hash: str | None,
    snapshot_fonte_hash: str | None, resumo_sanitizado: str | None = None,
    erro_codigo: str | None = None, erro_sanitizado: str | None = None,
) -> dict[str, Any]:
    if not isinstance(segredo, bytes) or len(segredo) < 32:
        raise ValueError("segredo_hmac_invalido")
    if (
        not isinstance(fencing_token, int)
        or isinstance(fencing_token, bool)
        or fencing_token <= 0
    ):
        raise ValueError("fencing_token_invalido")
    evidencias = bundle.get("evidencias", [])
    if not isinstance(evidencias, list):
        raise ValueError("bundle_evidencias_invalido")
    _validar_cobertura(
        estado_cobertura=estado_cobertura,
        inicio_confirmado=inicio_confirmado,
        fim_confirmado=fim_confirmado,
        paginas_confirmadas=paginas_confirmadas,
        registros_confirmados=registros_confirmados,
        paginacao_modo=paginacao_modo,
        artefato_cobertura_tipo=artefato_cobertura_tipo,
        cursor_final_hash=cursor_final_hash,
        snapshot_fonte_hash=snapshot_fonte_hash,
        quantidade_evidencias=len(evidencias),
    )
    pedido_hash = hash_pedido(
        estado_cobertura=estado_cobertura,
        estado_resultado=estado_resultado,
        bundle=bundle,
        resumo_sanitizado=resumo_sanitizado,
        erro_codigo=erro_codigo,
        erro_sanitizado=erro_sanitizado,
    )
    metadados = {
        "schema_version": "cobertura-hmac-v1",
        "chave_id": chave_id,
        "adaptador": adaptador,
        "adaptador_version": adaptador_version,
        "artefato_hash": artefato_hash,
        "familia_fonte": familia_fonte,
        "consulta_hash": consulta_hash,
        "consulta_ref": consulta_ref,
        "tarefa_id": tarefa_id,
        "investigacao_id": investigacao_id,
        "lease_token": lease_token,
        "fencing_token": str(fencing_token),
        "estado_cobertura": estado_cobertura,
        "estado_resultado": estado_resultado,
        "inicio_confirmado": inicio_confirmado,
        "fim_confirmado": fim_confirmado,
        "paginas_confirmadas": paginas_confirmadas,
        "registros_confirmados": registros_confirmados,
        "paginacao_modo": paginacao_modo,
        "artefato_cobertura_tipo": artefato_cobertura_tipo,
        "cursor_final_hash": cursor_final_hash,
        "snapshot_fonte_hash": snapshot_fonte_hash,
        "pedido_hash": pedido_hash,
    }
    assinatura = hmac.new(
        segredo, json_canonico_postgres(metadados).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {**metadados, "hmac": assinatura}
