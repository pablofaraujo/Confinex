#!/usr/bin/env python3
"""Recupera texto humano de uma conversa exata sem inferir operações."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


SCHEMA_ENTRADA = "mensagens-whatsapp-normalizadas-v1"
SCHEMA_SAIDA = "textos-mesa-recuperados-v1"
LIMITE_MAXIMO = 1_000
LIMITE_TEXTO_BYTES = 64_000
LIMITE_TOTAL_BYTES = 4_000_000
TERMOS_INFORMAIS = (
    "fechamos", "fechei", "zeramos", "zerei", "montamos", "montei",
    "rolamos", "rolei",
)
AUTORIAS = {"titular", "interlocutor", "nao_informada"}
MOTIVOS_COBERTURA = {
    "timestamp_ausente_ou_invalido", "limite_de_mensagens",
    "mensagem_sem_identidade_comprovada", "cobertura_do_export_nao_atestada",
    "cache_ou_export_normalizado_nao_fornecido",
}


def _utc(valor: Any) -> datetime:
    if not isinstance(valor, str) or not valor:
        raise ValueError("timestamp_invalido")
    try:
        data = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("timestamp_invalido") from None
    if data.tzinfo is None:
        raise ValueError("timestamp_sem_timezone")
    return data.astimezone(timezone.utc)


def _normalizar(valor: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", valor).casefold().split())


def _termos(termos: Iterable[str]) -> tuple[tuple[str, str], ...]:
    if not isinstance(termos, (list, tuple)):
        raise ValueError("termos_invalidos")
    fornecidos = tuple(termos)
    if len(fornecidos) > 20:
        raise ValueError("termos_acima_do_limite")
    saida = [(termo, "informal") for termo in TERMOS_INFORMAIS]
    for termo in fornecidos:
        if not isinstance(termo, str) or not termo.strip() or len(termo.encode("utf-8")) > 160:
            raise ValueError("termo_invalido")
        saida.append((termo.strip(), "fornecido"))
    unicos: dict[str, tuple[str, str]] = {}
    for termo, origem in saida:
        chave = _normalizar(termo)
        unicos.setdefault(chave, (termo, origem))
    return tuple(unicos[chave] for chave in sorted(unicos))


def _realces(texto: str, termos: tuple[tuple[str, str], ...]) -> list[dict[str, str]]:
    normalizado = _normalizar(texto)
    achados: list[dict[str, str]] = []
    for termo, origem in termos:
        alvo = _normalizar(termo)
        if origem == "informal":
            presente = re.search(r"(?<!\w)" + re.escape(alvo) + r"(?!\w)", normalizado) is not None
        else:
            presente = alvo in normalizado
        if presente:
            achados.append({"termo": termo, "origem": origem})
    return achados


def recuperar_textos_mesa(
    documento: Any,
    *,
    conversa_ref: str,
    inicio: str,
    fim: str,
    limite: int,
    contexto_adjacente: int = 2,
    termos: Iterable[str] = (),
) -> dict[str, Any]:
    """Preserva o corpus do recorte e realça pistas sem classificá-las."""
    if not isinstance(conversa_ref, str) or not conversa_ref:
        raise ValueError("conversa_ref_invalida")
    if isinstance(limite, bool) or not isinstance(limite, int) or not 1 <= limite <= LIMITE_MAXIMO:
        raise ValueError("limite_invalido")
    if (
        isinstance(contexto_adjacente, bool) or not isinstance(contexto_adjacente, int)
        or not 0 <= contexto_adjacente <= 10
    ):
        raise ValueError("contexto_adjacente_invalido")
    data_inicio, data_fim = _utc(inicio), _utc(fim)
    if data_inicio > data_fim or data_fim - data_inicio > timedelta(days=31):
        raise ValueError("intervalo_invertido")
    if not isinstance(documento, dict) or documento.get("schema_version") != SCHEMA_ENTRADA:
        raise ValueError("documento_invalido")
    cobertura = documento.get("cobertura")
    mensagens = documento.get("mensagens")
    estado_cobertura = cobertura.get("estado") if isinstance(cobertura, dict) else None
    if (
        not isinstance(estado_cobertura, str)
        or estado_cobertura not in {"completa", "parcial", "indisponivel"}
        or not isinstance(mensagens, list)
    ):
        raise ValueError("documento_invalido")
    if len(mensagens) > limite + 1 or len(mensagens) > LIMITE_MAXIMO + 1:
        raise ValueError("mensagens_entrada_acima_do_limite")
    diagnostico_fonte = cobertura.get("diagnostico_sanitizado")
    if diagnostico_fonte is None:
        diagnostico_fonte = {}
    chaves_diagnostico = {
        "omitidas_sem_texto", "omitidas_tamanho", "omitidas_anexo_sem_texto",
        "omitidas_por_estado", "editadas_sem_historico", "truncada_quantidade",
        "truncada_bytes",
    }
    if not isinstance(diagnostico_fonte, dict) or not set(diagnostico_fonte) <= chaves_diagnostico:
        raise ValueError("diagnostico_fonte_invalido")
    for chave, valor in diagnostico_fonte.items():
        if chave.startswith("truncada_"):
            valido = isinstance(valor, bool)
        else:
            valido = isinstance(valor, int) and not isinstance(valor, bool) and 0 <= valor <= 1_000_000
        if not valido:
            raise ValueError("diagnostico_fonte_invalido")
    metadados_cobertura: dict[str, Any] = {}
    for chave in ("identidade_pendente", "captura_ativa_confirmada", "truncada"):
        if chave in cobertura:
            if not isinstance(cobertura[chave], bool):
                raise ValueError("cobertura_fonte_invalida")
            metadados_cobertura[chave] = cobertura[chave]
    motivo = cobertura.get("motivo")
    if isinstance(motivo, str) and motivo in MOTIVOS_COBERTURA:
        metadados_cobertura["motivo"] = motivo
    termos_validados = _termos(termos)
    candidatos: list[tuple[datetime, str, str, dict[str, Any]]] = []
    identidades: dict[tuple[str, str], str] = {}
    bytes_total = 0
    campos = {
        "conversa_ref", "mensagem_ref", "timestamp", "texto", "hash_conteudo",
        "origem_autoria",
    }
    for indice, mensagem in enumerate(mensagens):
        if not isinstance(mensagem, dict) or not set(mensagem) <= campos:
            raise ValueError("mensagem_invalida")
        if mensagem.get("conversa_ref") != conversa_ref:
            raise ValueError("mensagem_fora_da_conversa")
        texto = mensagem.get("texto")
        if not isinstance(texto, str) or not texto:
            raise ValueError("mensagem_sem_texto")
        tamanho = len(texto.encode("utf-8"))
        if tamanho > LIMITE_TEXTO_BYTES or bytes_total + tamanho > LIMITE_TOTAL_BYTES:
            raise ValueError("texto_acima_do_limite")
        instante = _utc(mensagem.get("timestamp"))
        if instante < data_inicio or instante > data_fim:
            continue
        autoria = mensagem.get("origem_autoria", "nao_informada")
        if not isinstance(autoria, str) or autoria not in AUTORIAS:
            raise ValueError("autoria_invalida")
        mensagem_ref = mensagem.get("mensagem_ref")
        if mensagem_ref is not None and (not isinstance(mensagem_ref, str) or not mensagem_ref):
            raise ValueError("mensagem_ref_invalida")
        hash_conteudo = hashlib.sha256(texto.encode("utf-8")).hexdigest()
        hash_recebido = mensagem.get("hash_conteudo")
        if hash_recebido is not None and not isinstance(hash_recebido, str):
            raise ValueError("hash_invalido")
        if hash_recebido not in {None, hash_conteudo}:
            raise ValueError("hash_divergente")
        if mensagem_ref is not None:
            identidade = (mensagem_ref, hash_conteudo)
            if identidade in identidades and identidades[identidade] != autoria:
                raise ValueError("autoria_conflitante")
            if identidade in identidades:
                continue
            identidades[identidade] = autoria
        bytes_total += tamanho
        item = {
            "mensagem_ref": mensagem_ref,
            "timestamp": instante.isoformat().replace("+00:00", "Z"),
            "texto": texto,
            "hash_conteudo": hash_conteudo,
            "origem_autoria": autoria,
            "realces": _realces(texto, termos_validados),
        }
        chave_ref = mensagem_ref or "~" + hash_conteudo
        candidatos.append((instante, chave_ref, hash_conteudo, item))

    candidatos.sort(key=lambda valor: (valor[0], valor[1], valor[2], valor[3]["origem_autoria"]))
    cortadas = max(0, len(candidatos) - limite)
    selecionados = candidatos[:limite]
    textos: list[dict[str, Any]] = []
    for ordem, (_, _, _, item) in enumerate(selecionados):
        base_ref = json.dumps(
            [conversa_ref, item["mensagem_ref"], item["timestamp"], item["hash_conteudo"], ordem],
            ensure_ascii=False, separators=(",", ":"),
        )
        textos.append({
            "texto_ref": "txt_" + hashlib.sha256(base_ref.encode("utf-8")).hexdigest()[:24],
            **item,
        })

    hits = [indice for indice, item in enumerate(textos) if item["realces"]]
    intervalos: list[list[int]] = []
    for indice in hits:
        atual = [max(0, indice - contexto_adjacente), min(len(textos) - 1, indice + contexto_adjacente)]
        if intervalos and atual[0] <= intervalos[-1][1] + 1:
            intervalos[-1][1] = max(intervalos[-1][1], atual[1])
        else:
            intervalos.append(atual)
    blocos = []
    for comeco, termino in intervalos:
        refs = [item["texto_ref"] for item in textos[comeco:termino + 1]]
        blocos.append({
            "bloco_ref": "blc_" + hashlib.sha256("|".join(refs).encode("utf-8")).hexdigest()[:24],
            "texto_refs": refs,
            "realces_refs": [item["texto_ref"] for item in textos[comeco:termino + 1] if item["realces"]],
        })

    estado_fonte = cobertura["estado"]
    if estado_fonte == "indisponivel" and candidatos:
        raise ValueError("fonte_indisponivel_com_mensagens")
    fonte_atestada = False
    if estado_fonte == "completa" and cobertura.get("atestado") is True:
        try:
            fonte_atestada = (
                _utc(cobertura.get("intervalo_inicio")) == data_inicio
                and _utc(cobertura.get("intervalo_fim")) == data_fim
            )
        except ValueError:
            fonte_atestada = False
    estado = estado_fonte
    if estado == "completa" and (not fonte_atestada or cortadas):
        estado = "parcial"
    resultado = {
        "schema_version": SCHEMA_SAIDA,
        "estado": "texto_para_revisao",
        "autoriza_escrita": False,
        "modelo_acionado": False,
        "atualizacao_operacional": False,
        "conversa_ref": conversa_ref,
        "cobertura": {
            "estado": estado,
            "estado_fonte": estado_fonte,
            "intervalo_inicio": data_inicio.isoformat().replace("+00:00", "Z"),
            "intervalo_fim": data_fim.isoformat().replace("+00:00", "Z"),
            "atestado": False,
            "corte_por_limite": cortadas > 0,
            "omissoes_fonte_declaradas": cobertura.get("detalhe_sanitizado") is not None,
            "diagnostico_sanitizado": dict(sorted(diagnostico_fonte.items())),
            **metadados_cobertura,
        },
        "textos": textos,
        "blocos": blocos,
        "diagnostico": {
            "mensagens_no_intervalo": len(candidatos),
            "textos_preservados": len(textos),
            "textos_com_realce": len(hits),
            "blocos": len(blocos),
            "cortadas_por_limite": cortadas,
            "bytes_preservados": sum(len(item["texto"].encode("utf-8")) for item in textos),
            "fonte": dict(sorted(diagnostico_fonte.items())),
        },
    }
    serializado = json.dumps(resultado, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    resultado["recuperacao_hash"] = hashlib.sha256(serializado.encode("utf-8")).hexdigest()
    return resultado
