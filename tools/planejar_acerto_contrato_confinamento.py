#!/usr/bin/env python3
"""Planeja conferência de acertos e contratos a partir de um snapshot offline.

O módulo é deliberadamente puro: não lê arquivos, não consulta rede ou banco e
não executa ações. Os dados recebidos são evidências, nunca instruções nem
autorização.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from decimal import Decimal
from datetime import date
from typing import Any, Mapping

try:
    from .contratos_workflow import CAMPOS_JURIDICOS, CAMPOS_NEGOCIO, comparar, valor_canonico
except ImportError:  # execução direta a partir de tools/
    from contratos_workflow import CAMPOS_JURIDICOS, CAMPOS_NEGOCIO, comparar, valor_canonico


SCHEMA_ENTRADA = "entrada-acerto-contrato-confinamento-v1"
SCHEMA_SAIDA = "plano-acerto-contrato-confinamento-v1"
PAPEIS_CONTATO = {"confinamento", "administrativo", "intermediario", "finpec", "outro"}
ESCOPOS_LEITURA = {"acerto", "contrato", "aditivo"}
TIPOS_DOCUMENTO = {"acerto", "contrato", "aditivo"}
TIPOS_EVIDENCIA_RECEBIMENTO = {"extrato", "comprovante"}
ESTADOS_ETAPA = {"pendente", "confirmado", "desconhecido"}
CHAVES_ETAPA = (
    "recebido",
    "conferido",
    "aprovado",
    "recebimento_bancario",
    "assinatura",
    "envio",
)
REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
SHA256_RE = re.compile(r"^[a-f0-9]{64}$")
MAX_ITENS = 1000
MAX_BYTES_ENTRADA = 1_000_000


class ContratoPlanejamentoInvalido(ValueError):
    """Entrada offline fora do contrato fechado."""


def _objeto_exato(valor: Any, chaves: set[str], nome: str) -> Mapping[str, Any]:
    if not isinstance(valor, Mapping) or set(valor) != chaves:
        raise ContratoPlanejamentoInvalido(f"{nome}_invalido")
    return valor


def _lista(valor: Any, nome: str) -> list[Any]:
    if not isinstance(valor, list) or len(valor) > MAX_ITENS:
        raise ContratoPlanejamentoInvalido(f"{nome}_invalido")
    return valor


def _ref(valor: Any, nome: str, *, opcional: bool = False) -> str | None:
    if opcional and valor is None:
        return None
    if not isinstance(valor, str) or not REF_RE.fullmatch(valor):
        raise ContratoPlanejamentoInvalido(f"{nome}_invalida")
    return valor


def _texto_curto(valor: Any, nome: str, *, opcional: bool = False) -> str | None:
    if opcional and valor is None:
        return None
    if not isinstance(valor, str) or not valor.strip() or len(valor.encode("utf-8")) > 512:
        raise ContratoPlanejamentoInvalido(f"{nome}_invalido")
    return valor.strip()


def _data_iso(valor: Any, nome: str) -> str | None:
    texto = _texto_curto(valor, nome, opcional=True)
    if texto is None:
        return None
    try:
        if date.fromisoformat(texto).isoformat() != texto:
            raise ValueError
    except ValueError:
        raise ContratoPlanejamentoInvalido(f"{nome}_invalido") from None
    return texto


def _json_limitado(valor: Any, nome: str) -> dict[str, Any]:
    if not isinstance(valor, dict):
        raise ContratoPlanejamentoInvalido(f"{nome}_invalido")
    try:
        bruto = json.dumps(valor, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError):
        raise ContratoPlanejamentoInvalido(f"{nome}_invalido") from None
    if len(bruto.encode("utf-8")) > 128_000:
        raise ContratoPlanejamentoInvalido(f"{nome}_excede_limite")
    return valor


def _etapas(valor: Any, nome: str) -> dict[str, str]:
    item = _objeto_exato(valor, set(CHAVES_ETAPA), nome)
    resultado: dict[str, str] = {}
    for chave in CHAVES_ETAPA:
        estado = item[chave]
        if not isinstance(estado, str) or estado not in ESTADOS_ETAPA:
            raise ContratoPlanejamentoInvalido(f"{nome}_{chave}_invalido")
        resultado[chave] = estado
    return resultado


def _numero_nao_negativo(valor: Any, nome: str) -> Decimal:
    if isinstance(valor, bool) or not isinstance(valor, (int, float)):
        raise ContratoPlanejamentoInvalido(f"{nome}_invalido")
    if isinstance(valor, float) and not math.isfinite(valor):
        raise ContratoPlanejamentoInvalido(f"{nome}_invalido")
    numero = Decimal(str(valor))
    if numero < 0:
        raise ContratoPlanejamentoInvalido(f"{nome}_invalido")
    return numero


def _decimal_texto(valor: Decimal) -> str:
    texto = format(valor, "f")
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto or "0"


def _refs_unicas(itens: list[Mapping[str, Any]], chave: str, nome: str) -> None:
    vistos: set[str] = set()
    for item in itens:
        atual = _ref(item.get(chave), f"{nome}_{chave}")
        if atual in vistos:
            raise ContratoPlanejamentoInvalido(f"{nome}_{chave}_duplicada")
        vistos.add(atual)


def _itens_financeiros(valor: Any, nome: str) -> tuple[list[dict[str, Any]], Decimal]:
    itens = _lista(valor, nome)
    normalizados = []
    total = Decimal("0")
    for item in itens:
        linha = _objeto_exato(item, {"item_ref", "valor"}, nome)
        item_ref = _ref(linha["item_ref"], f"{nome}_item_ref")
        numero = _numero_nao_negativo(linha["valor"], f"{nome}_valor")
        total += numero
        normalizados.append({"item_ref": item_ref, "valor": _decimal_texto(numero)})
    _refs_unicas(normalizados, "item_ref", nome)
    return normalizados, total


def _resumir_divergencias(itens: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {"campo": str(item["campo"]), "tipo": str(item["tipo"])}
        for item in itens
    ]


def _valor_util(valor: Any) -> bool:
    canonico = valor_canonico(valor)
    if isinstance(canonico, Mapping):
        return any(_valor_util(item) for item in canonico.values())
    if isinstance(canonico, list):
        return any(_valor_util(item) for item in canonico)
    return canonico not in (None, "")


def _referencia_util(valor: Mapping[str, Any], campos: tuple[str, ...]) -> bool:
    return any(_valor_util(valor.get(campo)) for campo in campos)


def _alertas_etapas(etapas: Mapping[str, str], *, evidencia_bancaria: bool) -> list[str]:
    alertas = []
    if etapas["conferido"] == "confirmado" and etapas["recebido"] != "confirmado":
        alertas.append("conferencia_sem_recebimento_confirmado")
    if etapas["aprovado"] == "confirmado" and etapas["conferido"] != "confirmado":
        alertas.append("aprovacao_sem_conferencia_confirmada")
    if etapas["assinatura"] == "confirmado" and etapas["aprovado"] != "confirmado":
        alertas.append("assinatura_sem_aprovacao_confirmada")
    if etapas["envio"] == "confirmado" and etapas["assinatura"] != "confirmado":
        alertas.append("envio_sem_assinatura_confirmada")
    if etapas["recebimento_bancario"] == "confirmado" and not evidencia_bancaria:
        alertas.append("recebimento_bancario_sem_evidencia_vinculada")
    return alertas


def _selecionar_contatos(
    vinculos: list[dict[str, Any]],
    autorizacoes: dict[str, set[str]],
    confinamento_ref: str,
    escopo: str,
) -> dict[str, Any]:
    candidatos: dict[str, set[str]] = {}
    for vinculo in vinculos:
        if vinculo["confinamento_ref"] != confinamento_ref:
            continue
        contato_ref = vinculo["contato_ref"]
        if escopo in autorizacoes.get(contato_ref, set()):
            candidatos.setdefault(contato_ref, set()).add(vinculo["papel"])
    lista = [
        {"contato_ref": contato_ref, "papeis": sorted(papeis)}
        for contato_ref, papeis in sorted(candidatos.items())
    ]
    if len(lista) == 1:
        estado = "candidato_unico_com_autorizacao_declarada"
        selecionado = lista[0]["contato_ref"]
    elif not lista:
        estado = "pendente_sem_contato_autorizado"
        selecionado = None
    else:
        estado = "pendente_contatos_ambiguos"
        selecionado = None
    return {
        "estado": estado,
        "contato_selecionado_ref": selecionado,
        "candidatos": lista,
        "principal_nao_autoriza": True,
        "nome_nao_usado_para_vinculo": True,
    }


def planejar_acerto_contrato(
    entrada_normalizada: Mapping[str, Any],
    *,
    autorizacoes_leitura: list[dict[str, Any]],
) -> dict[str, Any]:
    """Retorna uma prévia determinística sem I/O ou ações externas."""

    try:
        tamanho = len(json.dumps(
            entrada_normalizada, ensure_ascii=False, sort_keys=True, allow_nan=False,
        ).encode("utf-8"))
    except (TypeError, ValueError):
        raise ContratoPlanejamentoInvalido("entrada_invalida") from None
    if tamanho > MAX_BYTES_ENTRADA:
        raise ContratoPlanejamentoInvalido("entrada_excede_limite")
    entrada = _objeto_exato(entrada_normalizada, {
        "schema_version", "operacao", "confinamento", "vinculos_contatos",
        "abates", "acertos", "documentos", "evidencias_recebimento",
    }, "entrada")
    if entrada["schema_version"] != SCHEMA_ENTRADA:
        raise ContratoPlanejamentoInvalido("schema_version_invalida")

    operacao = _objeto_exato(
        entrada["operacao"], {"operacao_ref", "confinamento_ref"}, "operacao",
    )
    operacao_ref = _ref(operacao["operacao_ref"], "operacao_ref")
    confinamento_ref = _ref(operacao["confinamento_ref"], "confinamento_ref")
    confinamento = _objeto_exato(
        entrada["confinamento"], {"confinamento_ref"}, "confinamento",
    )
    if _ref(confinamento["confinamento_ref"], "confinamento_ref") != confinamento_ref:
        raise ContratoPlanejamentoInvalido("vinculo_operacao_confinamento_divergente")

    vinculos_brutos = _lista(entrada["vinculos_contatos"], "vinculos_contatos")
    vinculos: list[dict[str, Any]] = []
    chaves_vinculo: set[tuple[str, str, str]] = set()
    for bruto in vinculos_brutos:
        item = _objeto_exato(
            bruto, {"confinamento_ref", "contato_ref", "papel", "principal"},
            "vinculo_contato",
        )
        conf = _ref(item["confinamento_ref"], "vinculo_confinamento_ref")
        contato = _ref(item["contato_ref"], "vinculo_contato_ref")
        if not isinstance(item["papel"], str) or item["papel"] not in PAPEIS_CONTATO:
            raise ContratoPlanejamentoInvalido("papel_contato_invalido")
        if not isinstance(item["principal"], bool):
            raise ContratoPlanejamentoInvalido("principal_invalido")
        chave = (conf, contato, item["papel"])
        if chave in chaves_vinculo:
            raise ContratoPlanejamentoInvalido("vinculo_contato_duplicado")
        chaves_vinculo.add(chave)
        vinculos.append({
            "confinamento_ref": conf, "contato_ref": contato,
            "papel": item["papel"], "principal": item["principal"],
        })
    if any(item["confinamento_ref"] != confinamento_ref for item in vinculos):
        raise ContratoPlanejamentoInvalido("vinculo_contato_confinamento_divergente")

    autorizacoes: dict[str, set[str]] = {}
    for bruto in _lista(autorizacoes_leitura, "autorizacoes_leitura"):
        item = _objeto_exato(bruto, {"contato_ref", "escopos"}, "autorizacao")
        contato = _ref(item["contato_ref"], "autorizacao_contato_ref")
        escopos = _lista(item["escopos"], "autorizacao_escopos")
        if (
            len(escopos) != len(set(escopos))
            or any(not isinstance(x, str) or x not in ESCOPOS_LEITURA for x in escopos)
        ):
            raise ContratoPlanejamentoInvalido("autorizacao_escopos_invalidos")
        if contato in autorizacoes:
            raise ContratoPlanejamentoInvalido("autorizacao_contato_duplicada")
        autorizacoes[contato] = set(escopos)
    contatos_vinculados = {item["contato_ref"] for item in vinculos}
    if any(contato not in contatos_vinculados for contato in autorizacoes):
        raise ContratoPlanejamentoInvalido("autorizacao_sem_vinculo_contato")

    evidencias: dict[tuple[str, str], list[str]] = {}
    evidencias_normalizadas = []
    pares_evidencia: list[tuple[str, str]] = []
    for bruto in _lista(entrada["evidencias_recebimento"], "evidencias_recebimento"):
        item = _objeto_exato(
            bruto,
            {"evidencia_ref", "operacao_ref", "abate_ref", "acerto_ref", "tipo", "vinculo_explicito"},
            "evidencia_recebimento",
        )
        evidencia_ref = _ref(item["evidencia_ref"], "evidencia_ref")
        if _ref(item["operacao_ref"], "evidencia_operacao_ref") != operacao_ref:
            raise ContratoPlanejamentoInvalido("evidencia_operacao_divergente")
        abate_ref = _ref(item["abate_ref"], "evidencia_abate_ref")
        acerto_ref = _ref(item["acerto_ref"], "evidencia_acerto_ref")
        if not isinstance(item["tipo"], str) or item["tipo"] not in TIPOS_EVIDENCIA_RECEBIMENTO:
            raise ContratoPlanejamentoInvalido("tipo_evidencia_recebimento_invalido")
        if not isinstance(item["vinculo_explicito"], bool):
            raise ContratoPlanejamentoInvalido("vinculo_explicito_invalido")
        evidencias_normalizadas.append({"evidencia_ref": evidencia_ref})
        pares_evidencia.append((abate_ref, acerto_ref))
        if item["vinculo_explicito"]:
            evidencias.setdefault((abate_ref, acerto_ref), []).append(evidencia_ref)
    _refs_unicas(evidencias_normalizadas, "evidencia_ref", "evidencias")

    abates = []
    abates_por_ref: dict[str, dict[str, Any]] = {}
    for bruto in _lista(entrada["abates"], "abates"):
        item = _objeto_exato(
            bruto,
            {"abate_ref", "operacao_ref", "periodo_inicio", "periodo_fim", "animais_qtd", "romaneio_ref"},
            "abate",
        )
        abate_ref = _ref(item["abate_ref"], "abate_ref")
        if abate_ref in abates_por_ref:
            raise ContratoPlanejamentoInvalido("abate_ref_duplicada")
        if _ref(item["operacao_ref"], "abate_operacao_ref") != operacao_ref:
            raise ContratoPlanejamentoInvalido("abate_operacao_divergente")
        inicio = _data_iso(item["periodo_inicio"], "periodo_inicio")
        fim = _data_iso(item["periodo_fim"], "periodo_fim")
        if inicio and fim and inicio > fim:
            raise ContratoPlanejamentoInvalido("periodo_invertido")
        qtd = item["animais_qtd"]
        if qtd is not None and (isinstance(qtd, bool) or not isinstance(qtd, int) or qtd <= 0):
            raise ContratoPlanejamentoInvalido("animais_qtd_invalida")
        romaneio = _ref(item["romaneio_ref"], "romaneio_ref", opcional=True)
        normalizado = {
            "abate_ref": abate_ref, "periodo_inicio": inicio, "periodo_fim": fim,
            "animais_qtd": qtd, "romaneio_ref": romaneio,
        }
        abates_por_ref[abate_ref] = normalizado
        abates.append(normalizado)

    acertos_por_abate: dict[str, list[dict[str, Any]]] = {}
    acertos_vistos: set[str] = set()
    total_custos = Decimal("0")
    total_descontos = Decimal("0")
    for bruto in _lista(entrada["acertos"], "acertos"):
        item = _objeto_exato(
            bruto,
            {"acerto_ref", "abate_ref", "operacao_ref", "status_textual", "custos", "descontos", "etapas"},
            "acerto",
        )
        acerto_ref = _ref(item["acerto_ref"], "acerto_ref")
        if acerto_ref in acertos_vistos:
            raise ContratoPlanejamentoInvalido("acerto_ref_duplicada")
        acertos_vistos.add(acerto_ref)
        if _ref(item["operacao_ref"], "acerto_operacao_ref") != operacao_ref:
            raise ContratoPlanejamentoInvalido("acerto_operacao_divergente")
        abate_ref = _ref(item["abate_ref"], "acerto_abate_ref")
        if abate_ref not in abates_por_ref:
            raise ContratoPlanejamentoInvalido("acerto_abate_inexistente")
        status_textual = _texto_curto(item["status_textual"], "status_textual", opcional=True)
        custos, soma_custos = _itens_financeiros(item["custos"], "custos")
        descontos, soma_descontos = _itens_financeiros(item["descontos"], "descontos")
        total_custos += soma_custos
        total_descontos += soma_descontos
        refs_bancarias = sorted(evidencias.get((abate_ref, acerto_ref), []))
        etapas = _etapas(item["etapas"], "etapas_acerto")
        acertos_por_abate.setdefault(abate_ref, []).append({
            "acerto_ref": acerto_ref,
            "status_textual_evidencia": status_textual,
            "status_textual_nao_comprova_pagamento": True,
            "custos": custos,
            "descontos": descontos,
            "soma_custos": _decimal_texto(soma_custos),
            "soma_descontos": _decimal_texto(soma_descontos),
            "evidencias_recebimento_refs": refs_bancarias,
            "etapas_declaradas": etapas,
            "alertas_etapas": _alertas_etapas(etapas, evidencia_bancaria=bool(refs_bancarias)),
        })
    pares_acerto = {
        (abate_ref, item["acerto_ref"])
        for abate_ref, itens in acertos_por_abate.items()
        for item in itens
    }
    if any(par not in pares_acerto for par in pares_evidencia):
        raise ContratoPlanejamentoInvalido("evidencia_recebimento_sem_acerto_exato")

    saida_abates = []
    animais_total = 0
    animais_cobertos = 0
    ambiguidade_financeira = False
    for abate in sorted(abates, key=lambda x: x["abate_ref"]):
        acertos_item = sorted(acertos_por_abate.get(abate["abate_ref"], []), key=lambda x: x["acerto_ref"])
        if abate["animais_qtd"] is not None:
            animais_total += abate["animais_qtd"]
            animais_cobertos += 1
        if not acertos_item:
            estado = "pendente_sem_acerto"
        elif len(acertos_item) > 1:
            estado = "pendente_multiplos_acertos"
            ambiguidade_financeira = True
        else:
            estado = "pendente_conferencia"
        refs_financeiras = [
            f"{tipo}:{item_financeiro['item_ref']}"
            for acerto in acertos_item
            for tipo in ("custo", "desconto")
            for item_financeiro in acerto["custos" if tipo == "custo" else "descontos"]
        ]
        repetidas = sorted({ref for ref in refs_financeiras if refs_financeiras.count(ref) > 1})
        if repetidas:
            ambiguidade_financeira = True
        saida_abates.append({
            **abate,
            "estado": estado,
            "campos": {
                "periodo": "presente" if abate["periodo_inicio"] and abate["periodo_fim"] else "pendente",
                "animais": "presente" if abate["animais_qtd"] is not None else "pendente",
                "romaneio": "presente" if abate["romaneio_ref"] else "pendente",
                "custos": "presente" if any(x["custos"] for x in acertos_item) else "pendente",
                "descontos": "presente" if any(x["descontos"] for x in acertos_item) else "pendente",
                "recebimento_bancario": "presente" if any(x["evidencias_recebimento_refs"] for x in acertos_item) else "pendente",
            },
            "acertos": acertos_item,
            "item_refs_repetidas_entre_acertos": repetidas,
            "outros_abates_nao_sao_fechados_por_este_acerto": True,
        })

    saida_documentos = []
    documentos_vistos: set[str] = set()
    documentos_brutos = _lista(entrada["documentos"], "documentos")
    documentos_por_hash: dict[str, list[str]] = {}
    for bruto in documentos_brutos:
        preliminar = _objeto_exato(
            bruto,
            {"documento_ref", "conteudo_sha256", "operacao_ref", "tipo", "extraido", "negocio", "termos_aprovados", "etapas"},
            "documento",
        )
        ref_preliminar = _ref(preliminar["documento_ref"], "documento_ref")
        hash_preliminar = preliminar["conteudo_sha256"]
        if not isinstance(hash_preliminar, str) or not SHA256_RE.fullmatch(hash_preliminar):
            raise ContratoPlanejamentoInvalido("conteudo_sha256_invalido")
        documentos_por_hash.setdefault(hash_preliminar, []).append(ref_preliminar)
    for refs in documentos_por_hash.values():
        refs.sort()
    for bruto in documentos_brutos:
        item = _objeto_exato(
            bruto,
            {"documento_ref", "conteudo_sha256", "operacao_ref", "tipo", "extraido", "negocio", "termos_aprovados", "etapas"},
            "documento",
        )
        documento_ref = _ref(item["documento_ref"], "documento_ref")
        if documento_ref in documentos_vistos:
            raise ContratoPlanejamentoInvalido("documento_ref_duplicada")
        documentos_vistos.add(documento_ref)
        conteudo_sha256 = item["conteudo_sha256"]
        if not isinstance(conteudo_sha256, str) or not SHA256_RE.fullmatch(conteudo_sha256):
            raise ContratoPlanejamentoInvalido("conteudo_sha256_invalido")
        refs_mesmo_conteudo = documentos_por_hash[conteudo_sha256]
        duplicado_no_snapshot = len(refs_mesmo_conteudo) > 1
        if _ref(item["operacao_ref"], "documento_operacao_ref") != operacao_ref:
            raise ContratoPlanejamentoInvalido("documento_operacao_divergente")
        tipo = item["tipo"]
        if not isinstance(tipo, str) or tipo not in TIPOS_DOCUMENTO:
            raise ContratoPlanejamentoInvalido("tipo_documento_invalido")
        extraido = _json_limitado(item["extraido"], "extraido")
        negocio = _json_limitado(item["negocio"], "negocio")
        termos = _json_limitado(item["termos_aprovados"], "termos_aprovados")
        pendencias_referencia = []
        negocio_util = _referencia_util(negocio, CAMPOS_NEGOCIO)
        termos_uteis = _referencia_util(termos, CAMPOS_JURIDICOS)
        extraido_util = _referencia_util(extraido, (*CAMPOS_NEGOCIO, *CAMPOS_JURIDICOS))
        if not extraido_util:
            pendencias_referencia.append("conteudo_extraido_sem_campo_util")
        if not negocio_util:
            pendencias_referencia.append("negocio_referencia_ausente")
        if tipo in {"contrato", "aditivo"} and not termos_uteis:
            pendencias_referencia.append("termos_aprovados_ausentes")
        if duplicado_no_snapshot:
            pendencias_referencia.append("conteudo_duplicado_no_snapshot")
        divergencias_negocio = (
            _resumir_divergencias(comparar(extraido, negocio, CAMPOS_NEGOCIO))
            if negocio_util else []
        )
        divergencias_termos = (
            _resumir_divergencias(comparar(extraido, termos, CAMPOS_JURIDICOS))
            if tipo in {"contrato", "aditivo"} and termos_uteis else []
        )
        etapas = _etapas(item["etapas"], "etapas_documento")
        pendente = bool(pendencias_referencia or divergencias_negocio or divergencias_termos)
        saida_documentos.append({
            "documento_ref": documento_ref,
            "conteudo_sha256_declarado": conteudo_sha256,
            "hash_nao_autentica_conteudo": True,
            "duplicado_no_snapshot": duplicado_no_snapshot,
            "documentos_refs_mesmo_conteudo": refs_mesmo_conteudo if duplicado_no_snapshot else [],
            "tipo": tipo,
            "natureza": "evidencia_nao_confiavel",
            "estado": "pendente_referencia_ou_divergencia" if pendente else "pronto_para_revisao_humana",
            "pendencias_referencia": pendencias_referencia,
            "divergencias_negocio": divergencias_negocio,
            "divergencias_termos": divergencias_termos,
            "etapas_declaradas": etapas,
            "alertas_etapas": _alertas_etapas(etapas, evidencia_bancaria=False),
            "dados_nao_sao_instrucoes": True,
            "nao_classificado_como_conferido_automaticamente": True,
        })

    contatos = {
        escopo: _selecionar_contatos(vinculos, autorizacoes, confinamento_ref, escopo)
        for escopo in sorted(ESCOPOS_LEITURA)
    }
    base = {
        "schema_version": SCHEMA_SAIDA,
        "estado": "somente_previa",
        "autoriza_escrita": False,
        "escritas": 0,
        "vinculo": {
            "operacao_ref": operacao_ref,
            "confinamento_ref": confinamento_ref,
            "estado": "exato",
        },
        "contatos_por_escopo": contatos,
        "autorizacoes_leitura": {
            "natureza": "declaradas_pelo_chamador_nao_verificadas",
            "nao_sao_credencial": True,
            "devem_vir_de_camada_confiavel_separada_do_documento": True,
        },
        "abates": saida_abates,
        "documentos": sorted(saida_documentos, key=lambda x: x["documento_ref"]),
        "cobertura": {
            "natureza": "snapshot_offline_fornecido",
            "nao_atesta_completude": True,
            "abates_total": len(saida_abates),
            "abates_com_acerto": sum(bool(x["acertos"]) for x in saida_abates),
            "abates_sem_acerto": sum(not x["acertos"] for x in saida_abates),
            "animais_qtd_soma_do_snapshot": animais_total,
            "animais_qtd_abates_cobertos": animais_cobertos,
            "financeiro_estado": (
                "indisponivel_para_consolidacao_por_ambiguidade"
                if ambiguidade_financeira else "soma_declarada_sem_ambiguidade_estrutural"
            ),
            "custos_soma_candidatos_do_snapshot": _decimal_texto(total_custos),
            "descontos_soma_candidatos_do_snapshot": _decimal_texto(total_descontos),
            "custos_soma_consolidavel": None if ambiguidade_financeira else _decimal_texto(total_custos),
            "descontos_soma_consolidavel": None if ambiguidade_financeira else _decimal_texto(total_descontos),
            "documentos_total": len(saida_documentos),
            "evidencias_recebimento_total": len(evidencias_normalizadas),
        },
        "acoes_externas": {
            "consultou_rede": False,
            "consultou_banco": False,
            "enviou_mensagem": False,
            "moveu_documento": False,
            "assinou": False,
            "marcou_pago": False,
            "fechou_abate": False,
            "atualizou_operacao": False,
        },
    }
    material = json.dumps(base, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {**base, "plano_id": "pac_" + hashlib.sha256(material.encode("utf-8")).hexdigest()}


__all__ = [
    "ContratoPlanejamentoInvalido",
    "SCHEMA_ENTRADA",
    "SCHEMA_SAIDA",
    "planejar_acerto_contrato",
]
