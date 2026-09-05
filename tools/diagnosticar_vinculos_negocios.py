#!/usr/bin/env python3
"""Diagnóstico offline de vínculos entre snapshots projetados.

Este módulo nunca consulta rede ou Supabase diretamente. Recebe projeções de
registros já exportados e bloqueia quando as duas fotografias diferem. Os
detalhes continuam privados; somente contagens e assinaturas vão ao terminal.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from typing import Any


TABELAS = (
    "operacoes",
    "compras",
    "compras_componentes",
    "confinex_avaliacoes",
    "confinex_estimativas",
    "negocios_candidatos",
    "transacoes_banco_staging",
    "transacoes_banco",
)

CAMPOS = {
    "operacoes": ("id", "codigo", "sexo", "tipo_negocio", "status", "confinamento_id"),
    "compras": ("id", "operacao_id", "quantidade", "peso_total_kg", "valor_total", "data", "idempotency_key"),
    "compras_componentes": (
        "id", "compra_agregada_id", "quantidade", "peso_total_kg", "valor_total",
        "chave_rastreio", "dimensoes_origem", "dimensoes_formato_inesperado",
    ),
    "confinex_avaliacoes": ("id", "codigo", "operacao_id", "status"),
    "confinex_estimativas": ("id", "avaliacao_id", "versao", "tipo"),
    "negocios_candidatos": (
        "id", "codigo_fonte", "fonte_importacao_id", "sexo", "categoria", "destino",
        "estado", "operacao_id", "incorporado_no_candidato_id", "quantidade",
    ),
    "transacoes_banco_staging": ("id", "conta", "fitid", "data", "valor", "transacao_banco_id"),
    "transacoes_banco": ("id", "conta", "id_externo", "data", "valor", "fluxo_caixa_id"),
}

CAMPOS_DECIMAIS = {
    "quantidade", "peso_total_kg", "valor_total", "versao", "valor",
}
_DECIMAL_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_DESCONHECIDO = {"desconhecido", "unknown", "n/a", "na", "não informado", "nao informado"}
_CAMPOS_BOOLEANOS = {"dimensoes_formato_inesperado"}


def _falha(mensagem: str) -> ValueError:
    return ValueError(mensagem)


def _nulo(valor: Any) -> bool:
    return valor is None or (isinstance(valor, str) and not valor.strip())


def _decimal(valor: Any, campo: str) -> Decimal | None:
    if valor is None:
        return None
    if not isinstance(valor, str) or not _DECIMAL_RE.fullmatch(valor):
        raise _falha(f"decimal_invalido:{campo}")
    try:
        numero = Decimal(valor)
    except InvalidOperation as erro:
        raise _falha(f"decimal_invalido:{campo}") from erro
    if not numero.is_finite():
        raise _falha(f"decimal_invalido:{campo}")
    if len(numero.as_tuple().digits) > 10_000 or abs(numero.as_tuple().exponent) > 10_000:
        raise _falha(f"decimal_acima_do_limite:{campo}")
    return numero


def _canonico(valor: Any, campo: str | None = None) -> Any:
    """Representação estável com tipos explícitos; não converte IDs/textos."""

    if campo in CAMPOS_DECIMAIS and valor is not None:
        numero = _decimal(valor, campo or "")
        assert numero is not None
        # ``normalize`` usa o contexto global e pode arredondar valores longos.
        # A tupla do Decimal conserva sinal, dígitos e escala sem conversão.
        sinal, digitos, expoente = numero.as_tuple()
        return ["decimal", [sinal, list(digitos), expoente]]
    if valor is None:
        return ["nulo"]
    if isinstance(valor, bool):
        return ["booleano", valor]
    if isinstance(valor, int):
        return ["inteiro", valor]
    if isinstance(valor, float):
        return ["flutuante", repr(valor)]
    if isinstance(valor, str):
        return ["texto", valor]
    if isinstance(valor, list):
        return ["lista", [_canonico(item) for item in valor]]
    if isinstance(valor, dict):
        return ["objeto", [[str(chave), _canonico(valor[chave])] for chave in sorted(valor)]]
    raise _falha("tipo_json_invalido")


def _chave(valor: Any, campo: str | None = None) -> str:
    return json.dumps(_canonico(valor, campo), ensure_ascii=False, separators=(",", ":"))


def assinatura(valor: Any) -> str:
    return hashlib.sha256(
        json.dumps(valor, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _validar_dimensoes(valor: Any) -> None:
    if valor is None:
        return
    if not isinstance(valor, dict) or set(valor) != {"sexo", "categoria", "destino"}:
        raise _falha("dimensoes_invalidas")
    for campo in ("sexo", "categoria", "destino"):
        if valor[campo] is not None and (not isinstance(valor[campo], str) or len(valor[campo]) > 80):
            raise _falha("dimensoes_invalidas")


def _validar_linha(nome_tabela: str, linha: Any) -> dict[str, Any]:
    if not isinstance(linha, dict):
        raise _falha("linha_invalida")
    campos = set(CAMPOS[nome_tabela])
    if set(linha) != campos:
        raise _falha("campo_nao_projetado")
    if not isinstance(linha.get("id"), str) or not linha["id"].strip():
        raise _falha("id_obrigatorio")
    for campo in CAMPOS_DECIMAIS.intersection(campos):
        if campo in linha:
            _decimal(linha[campo], campo)
    for campo in _CAMPOS_BOOLEANOS.intersection(campos):
        if campo in linha and type(linha[campo]) is not bool:
            raise _falha(f"booleano_invalido:{campo}")
    for campo in campos - CAMPOS_DECIMAIS - _CAMPOS_BOOLEANOS - {"id", "dimensoes_origem"}:
        if campo in linha and linha[campo] is not None and not isinstance(linha[campo], str):
            raise _falha(f"texto_invalido:{campo}")
    if "dimensoes_origem" in campos:
        _validar_dimensoes(linha.get("dimensoes_origem"))
    return dict(linha)


def _projecao_tabela(nome_tabela: str, linhas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    linhas_canonicas = []
    for linha in linhas:
        linhas_canonicas.append({campo: _canonico(linha.get(campo), campo) for campo in CAMPOS[nome_tabela]})
    return sorted(linhas_canonicas, key=lambda linha: json.dumps(linha["id"], ensure_ascii=False, separators=(",", ":")))


def validar_snapshot(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, dict) or set(snapshot) != {"versao", "modo", "tabelas", "contagens"}:
        raise _falha("snapshot_invalido")
    if type(snapshot["versao"]) is not int or snapshot["versao"] != 1 or snapshot["modo"] != "somente_leitura":
        raise _falha("snapshot_invalido")
    if not isinstance(snapshot["tabelas"], dict) or set(snapshot["tabelas"]) != set(TABELAS):
        raise _falha("tabelas_incompletas")
    if not isinstance(snapshot["contagens"], dict) or set(snapshot["contagens"]) != set(TABELAS):
        raise _falha("contagens_incompletas")
    tabelas: dict[str, list[dict[str, Any]]] = {}
    for nome in TABELAS:
        linhas = snapshot["tabelas"][nome]
        if not isinstance(linhas, list):
            raise _falha("tabela_nao_lista")
        contagem = snapshot["contagens"][nome]
        if type(contagem) is not int or contagem < 0 or contagem != len(linhas):
            raise _falha("contagem_incoerente")
        validas = [_validar_linha(nome, linha) for linha in linhas]
        vistos: set[str] = set()
        for linha in validas:
            chave = _chave(linha["id"])
            if chave in vistos:
                raise _falha("id_duplicado")
            vistos.add(chave)
        tabelas[nome] = validas
    return {"versao": 1, "modo": "somente_leitura", "tabelas": tabelas,
            "contagens": {nome: len(tabelas[nome]) for nome in TABELAS}}


def assinatura_snapshot(snapshot: Any) -> str:
    validado = validar_snapshot(snapshot)
    projeccoes = {nome: _projecao_tabela(nome, validado["tabelas"][nome]) for nome in TABELAS}
    return assinatura(projeccoes)


def validar_planilha_registros(valor: Any) -> dict[str, Any] | None:
    if valor is None:
        return None
    if not isinstance(valor, dict) or set(valor) != {"fontes", "fontes_inalteradas"}:
        raise _falha("fontes_invalidas")
    if valor["fontes_inalteradas"] is not True:
        raise _falha("fontes_alteradas")
    fontes = valor["fontes"]
    if not isinstance(fontes, list) or not fontes:
        raise _falha("fontes_invalidas")
    saida = []
    ids_vistos: set[str] = set()
    for fonte in fontes:
        if (not isinstance(fonte, dict)
                or not {"id", "aba", "sha256", "linhas"}.issubset(fonte)
                or set(fonte) - {"id", "aba", "sha256", "linhas", "leitura"}):
            raise _falha("fonte_invalida")
        if _nulo(fonte["id"]) or not isinstance(fonte["id"], str):
            raise _falha("fonte_id_invalida")
        fonte_id = _chave(fonte["id"])
        if fonte_id in ids_vistos:
            raise _falha("fonte_id_duplicada")
        ids_vistos.add(fonte_id)
        if not isinstance(fonte["aba"], str) or _nulo(fonte["aba"]):
            raise _falha("aba_invalida")
        if not isinstance(fonte["sha256"], str) or not re.fullmatch(r"[0-9a-fA-F]{64}", fonte["sha256"]):
            raise _falha("sha256_invalido")
        linhas = fonte["linhas"]
        if not isinstance(linhas, list):
            raise _falha("linhas_invalidas")
        linhas_saida = []
        numeros_linha: set[int] = set()
        for linha in linhas:
            if not isinstance(linha, dict) or set(linha) != {"linha", "codigo", "sexo"}:
                raise _falha("linha_planilha_invalida")
            if type(linha["linha"]) is not int or linha["linha"] < 1:
                raise _falha("numero_linha_invalido")
            if linha["linha"] in numeros_linha:
                raise _falha("linha_planilha_duplicada")
            numeros_linha.add(linha["linha"])
            if linha["codigo"] is not None and not isinstance(linha["codigo"], str):
                raise _falha("codigo_planilha_invalido")
            if linha["sexo"] is not None and not isinstance(linha["sexo"], str):
                raise _falha("sexo_planilha_invalido")
            linhas_saida.append(dict(linha))
        leitura = fonte.get("leitura")
        if leitura is not None:
            if not isinstance(leitura, dict) or set(leitura) - {
                "formulas_sem_valor_armazenado", "celulas_com_erro", "aviso"
            }:
                raise _falha("metadados_leitura_invalidos")
            for campo in ("formulas_sem_valor_armazenado", "celulas_com_erro"):
                if campo in leitura and (type(leitura[campo]) is not int or leitura[campo] < 0):
                    raise _falha("metadados_leitura_invalidos")
            if "aviso" in leitura and leitura["aviso"] is not None and not isinstance(leitura["aviso"], str):
                raise _falha("metadados_leitura_invalidos")
            leitura = dict(leitura)
        saida.append({"id": fonte["id"], "aba": fonte["aba"], "sha256": fonte["sha256"],
                      "linhas": linhas_saida, "leitura": leitura})
    return {"fontes": saida, "fontes_inalteradas": True}


def _mapa_id(linhas: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {_chave(linha["id"]): linha for linha in linhas}


def _ids(linhas: list[dict[str, Any]]) -> list[Any]:
    return [linha["id"] for linha in linhas]


def _achado(tipo: str, observacao: str, hipotese: str, proxima: str,
            ids: list[Any] | None = None, linhas: list[dict[str, Any]] | None = None,
            evidencia: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "tipo": tipo,
        "observacao": observacao,
        "hipotese": hipotese,
        "proxima_verificacao": proxima,
        "ids": ids or [],
        "linhas": linhas or [],
        "evidencia": evidencia or {},
    }


def _linha_fonte(fonte: dict[str, Any], linha: dict[str, Any]) -> dict[str, Any]:
    return {"fonte_id": fonte["id"], "aba": fonte["aba"], "linha": linha["linha"]}


def _diagnosticar_planilha(tabelas: dict[str, list[dict[str, Any]]], fontes: dict[str, Any], achados: list[dict[str, Any]]) -> None:
    operacoes = tabelas["operacoes"]
    por_codigo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for operacao in operacoes:
        codigo = operacao.get("codigo")
        if not _nulo(codigo):
            por_codigo[_chave(codigo)].append(operacao)
    for fonte in fontes["fontes"]:
        linhas_por_codigo: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for linha in fonte["linhas"]:
            referencia = [_linha_fonte(fonte, linha)]
            if _nulo(linha.get("codigo")):
                achados.append(_achado("codigo_planilha_nulo", "Linha sem código exato para vínculo.",
                                       "A origem pode estar incompleta.", "Conferir a linha na fonte original.", linhas=referencia))
                continue
            candidatos = por_codigo.get(_chave(linha["codigo"]), [])
            if not candidatos:
                achados.append(_achado("codigo_operacao_ausente", "Código da planilha não encontra operação.",
                                       "O código pode ainda não ter sido incorporado ou divergir exatamente.",
                                       "Conferir código, zeros, caixa e espaços sem normalizar.", linhas=referencia))
            elif len(candidatos) > 1:
                achados.append(_achado("codigo_operacao_ambiguo", "Código da planilha encontra múltiplas operações.",
                                       "Há duplicidade de código nas operações.", "Escolher vínculo humano entre todos os candidatos.",
                                       ids=_ids(candidatos), linhas=referencia))
            else:
                achados.append(_achado("codigo_planilha_correspondencia_exata", "Código da planilha encontra uma operação por igualdade exata.",
                                       "O vínculo observado é candidato, não confirmação de negócio.",
                                       "Conferir a evidência da fonte antes de qualquer promoção.",
                                       ids=_ids(candidatos), linhas=referencia))
            linhas_por_codigo[_chave(linha["codigo"])].append(linha)
        for linhas in linhas_por_codigo.values():
            if len(linhas) > 1:
                referencias = [_linha_fonte(fonte, linha) for linha in linhas]
                candidatos = por_codigo.get(_chave(linhas[0]["codigo"]), [])
                achados.append(_achado("codigo_planilha_multiplas_linhas", "Código exato aparece em múltiplas linhas da mesma fonte.",
                                       "As linhas podem representar componentes; isto não prova duplicidade de negócio.",
                                       "Conferir cada linha e não fundir candidatos automaticamente.",
                                       ids=_ids(candidatos), linhas=referencias))
        leitura = fonte.get("leitura") or {}
        if leitura.get("aviso") or leitura.get("formulas_sem_valor_armazenado", 0) or leitura.get("celulas_com_erro", 0):
            achados.append(_achado("aviso_leitura_planilha", "A leitura da planilha contém aviso, fórmula sem cache ou erro de célula.",
                                   "O valor exportado pode estar incompleto ou desatualizado.",
                                   "Reabrir a fonte em aplicativo que calcule fórmulas e revisar células com erro."))


def _diagnosticar_avaliacoes(tabelas: dict[str, list[dict[str, Any]]], achados: list[dict[str, Any]]) -> None:
    operacoes = _mapa_id(tabelas["operacoes"])
    avaliacoes = tabelas["confinex_avaliacoes"]
    avaliacoes_por_operacao: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for avaliacao in avaliacoes:
        operacao_id = avaliacao.get("operacao_id")
        if _nulo(operacao_id):
            if avaliacao.get("status") == "cancelado":
                achados.append(_achado("avaliacao_cancelada_sem_operacao", "Avaliação cancelada sem operação vinculada.",
                                       "O cancelamento pode ser legítimo e não é pendência de vínculo operacional.",
                                       "Preservar o histórico e auditar somente se houver fonte contrária.", ids=[avaliacao["id"]]))
            else:
                achados.append(_achado("avaliacao_operacao_nula", "Avaliação não cancelada sem operação vinculada.",
                                       "Pode ser estudo legítimo ainda não associado; não é erro automático.",
                                       "Comprovar a operação em fonte autorizada antes de qualquer promoção.", ids=[avaliacao["id"]]))
        elif _chave(operacao_id) not in operacoes:
            achados.append(_achado("avaliacao_operacao_orfa", "Avaliação aponta para operação inexistente.",
                                   "O vínculo pode referenciar operação removida ou snapshot incompleto.", "Conferir o catálogo completo de operações.", ids=[avaliacao["id"]]))
        else:
            avaliacoes_por_operacao[_chave(operacao_id)].append(avaliacao)
    for avaliacao_id, grupo in avaliacoes_por_operacao.items():
        if len(grupo) > 1:
            achados.append(_achado("multiplas_avaliacoes_operacao", "Uma operação possui múltiplas avaliações.",
                                   "Podem ser versões ou avaliações concorrentes; não é erro automático.",
                                   "Conferir status e versão congelada antes de escolher uma.", ids=_ids(grupo)))
    avaliacoes_ids = _mapa_id(avaliacoes)
    for estimativa in tabelas["confinex_estimativas"]:
        avaliacao_id = estimativa.get("avaliacao_id")
        if _nulo(avaliacao_id) or _chave(avaliacao_id) not in avaliacoes_ids:
            achados.append(_achado("estimativa_avaliacao_orfa", "Estimativa sem avaliação existente.",
                                   "A estimativa pode estar desacompanhada da avaliação canônica.",
                                   "Conferir avaliação e versão congelada.", ids=[estimativa["id"]]))


def _diagnosticar_componentes(tabelas: dict[str, list[dict[str, Any]]], achados: list[dict[str, Any]]) -> None:
    compras = _mapa_id(tabelas["compras"])
    componentes_por_pai: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for componente in tabelas["compras_componentes"]:
        pai = componente.get("compra_agregada_id")
        if _nulo(pai):
            achados.append(_achado("componente_pai_nulo", "Componente sem compra agregada.",
                                   "O item não pode ser somado a um pai identificado.", "Conferir a origem do componente.", ids=[componente["id"]]))
        elif _chave(pai) not in compras:
            achados.append(_achado("componente_pai_orfao", "Componente aponta para compra inexistente.",
                                   "O pai pode estar ausente no snapshot.", "Conferir a exportação da compra agregada.", ids=[componente["id"]]))
        else:
            componentes_por_pai[_chave(pai)].append(componente)
        dimensoes = componente.get("dimensoes_origem")
        if componente.get("dimensoes_formato_inesperado") is True:
            achados.append(_achado("dimensoes_componente_formato_inesperado", "O formato original das dimensões foi inesperado e o valor foi omitido.",
                                   "Não é possível concluir se a dimensão existia na fonte.",
                                   "Revisar a célula original sem inferir ausência.", ids=[componente["id"]]))
        elif not isinstance(dimensoes, dict) or any(_nulo(dimensoes.get(campo)) for campo in ("sexo", "categoria", "destino")):
            achados.append(_achado("dimensoes_componente_incompletas", "Componente sem todas as dimensões de origem.",
                                   "O componente descreve fornecedores/corretores; não prova um subgrupo de animais nem um cadastro defeituoso.",
                                   "Verificar a finalidade e a fonte antes de exigir sexo, categoria ou destino neste componente.",
                                   ids=[componente["id"]], evidencia={"obrigatoriedade_dimensoes": "nao_estabelecida_para_componente"}))
        elif any(dimensoes[campo].strip().casefold() in _DESCONHECIDO for campo in ("sexo", "categoria", "destino")):
            achados.append(_achado("dimensoes_componente_desconhecidas", "Componente contém dimensão explicitamente desconhecida.",
                                   "A classificação de origem não foi determinada; componente não equivale a subgrupo de animais.",
                                   "Preservar o desconhecido e conferir a finalidade e a evidência original.", ids=[componente["id"]]))
    for chave_pai, componentes in componentes_por_pai.items():
        pai = compras[chave_pai]
        for campo in ("quantidade", "peso_total_kg", "valor_total"):
            valor_pai = _decimal(pai.get(campo), campo)
            valores_filhos = [_decimal(item.get(campo), campo) for item in componentes]
            if valor_pai is None or any(valor is None for valor in valores_filhos):
                ausentes = [campo for campo, valor in [("pai", valor_pai), *[(f"filho:{item['id']}", valor) for item, valor in zip(componentes, valores_filhos)]] if valor is None]
                achados.append(_achado("totais_componentes_incompletos", "Total pai ou filho está ausente.",
                                       "Ausência não equivale a zero; a soma não é conclusiva.",
                                       "Conferir cobertura, período e fonte antes de propor complemento; o total econômico continua na compra agregada.",
                                       ids=[pai["id"], *_ids(componentes)],
                                       evidencia={"campo": campo, "nulos": ausentes}))
            else:
                # A soma também deve ficar fora do contexto padrão (28 casas).
                # Considerar também a distância entre as casas decimais:
                # 1E+40 + 0.01 exige mais precisão que o número de dígitos.
                numeros = [valor_pai, *valores_filhos]
                menor_expoente = min(valor.as_tuple().exponent for valor in numeros)
                maior_casa = max(valor.adjusted() for valor in numeros)
                precisao = max(1, maior_casa - menor_expoente + 1) + len(str(len(valores_filhos))) + 2
                with localcontext() as contexto:
                    contexto.prec = precisao
                    soma_filhos = sum(valores_filhos, Decimal("0"))
                if valor_pai == soma_filhos:
                    continue
                achados.append(_achado("totais_componentes_divergentes", "Total pai difere da soma dos componentes.",
                                       "Pode haver cobertura parcial, cortes diferentes ou divergência; isto não prova erro do total.",
                                       "Conferir a cobertura sem substituir o total da compra agregada nem somar os componentes novamente.",
                                       ids=[pai["id"], *_ids(componentes)],
                                       evidencia={"campo": campo, "valor_pai": str(valor_pai),
                                                  "soma_filhos": str(soma_filhos)}))


def _diagnosticar_candidatos(tabelas: dict[str, list[dict[str, Any]]], achados: list[dict[str, Any]]) -> None:
    operacoes = _mapa_id(tabelas["operacoes"])
    candidatos_ids = {_chave(item["id"]) for item in tabelas["negocios_candidatos"]}
    grupos: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for candidato in tabelas["negocios_candidatos"]:
        codigo = candidato.get("codigo_fonte")
        if not _nulo(codigo):
            grupos[_chave(codigo)].append(candidato)
        incorporado = candidato.get("incorporado_no_candidato_id")
        if not _nulo(incorporado) and _chave(incorporado) not in candidatos_ids:
            achados.append(_achado("candidato_incorporacao_orfa", "Candidato aponta para incorporação inexistente.",
                                   "A cadeia de candidatos pode estar incompleta.", "Conferir o grupo de candidatos.", ids=[candidato["id"]]))
        operacao_id = candidato.get("operacao_id")
        if not _nulo(operacao_id) and _chave(operacao_id) not in operacoes:
            achados.append(_achado("candidato_operacao_orfa", "Candidato aponta para operação inexistente.",
                                   "O vínculo pode estar desatualizado ou o snapshot está incompleto.",
                                   "Comprovar o mapeamento em fonte autorizada, sem inferir pelo código.", ids=[candidato["id"]]))
        if any(_nulo(candidato.get(campo)) for campo in ("sexo", "categoria", "destino")):
            achados.append(_achado("candidato_dimensoes_incompletas", "Candidato não possui todas as dimensões de divisão.",
                                   "A possibilidade não pode ser comparada como grupo completo.",
                                   "Comprovar sexo, categoria e destino na fonte antes de agrupar.", ids=[candidato["id"]]))
        elif any(candidato[campo].strip().casefold() in _DESCONHECIDO for campo in ("sexo", "categoria", "destino")):
            achados.append(_achado("candidato_dimensoes_desconhecidas", "Candidato contém dimensão explicitamente desconhecida.",
                                   "Ainda não é possível confirmar a divisão.",
                                   "Buscar a classificação na fonte antes de comparar divisões.", ids=[candidato["id"]]))
    for grupo in grupos.values():
        completos = [item for item in grupo if all(
            not _nulo(item[campo]) and item[campo].strip().casefold() not in _DESCONHECIDO
            for campo in ("sexo", "categoria", "destino")
        )]
        divisões = {_chave([item[campo] for campo in ("sexo", "categoria", "destino")]) for item in completos}
        if len(divisões) > 1:
            achados.append(_achado("candidatos_divisoes_distintas", "Mesmo código de origem possui divisões distintas.",
                                   "São possibilidades separadas e não devem ser fundidas automaticamente.",
                                   "Conferir cada divisão de sexo, categoria e destino separadamente.", ids=_ids(completos)))


def _diagnosticar_banco(tabelas: dict[str, list[dict[str, Any]]], achados: list[dict[str, Any]]) -> None:
    operacionais = _mapa_id(tabelas["transacoes_banco"])
    por_vinculo: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for staging in tabelas["transacoes_banco_staging"]:
        vinculo = staging.get("transacao_banco_id")
        if _nulo(vinculo):
            achados.append(_achado("transacao_banco_id_nulo", "Staging bancário não possui vínculo explícito.",
                                   "A transação pode já existir; FITID isolado não comprova a relação.",
                                   "Verificar a regra do importador e a identidade da conta antes de propor vínculo.", ids=[staging["id"]]))
            continue
        chave_vinculo = _chave(vinculo)
        por_vinculo[chave_vinculo].append(staging)
        operacional = operacionais.get(chave_vinculo)
        if operacional is None:
            achados.append(_achado("transacao_banco_orfa", "Staging aponta para transação bancária inexistente.",
                                   "O destino pode ter sido removido ou o snapshot está incompleto.",
                                   "Conferir o ID explícito no catálogo operacional.", ids=[staging["id"]]))
            continue
        campos_divergentes = []
        for campo in ("conta", "data", "valor"):
            esquerdo = _decimal(staging.get(campo), campo) if campo == "valor" else staging.get(campo)
            direito = _decimal(operacional.get(campo), campo) if campo == "valor" else operacional.get(campo)
            if (esquerdo is None) != (direito is None) or (esquerdo is not None and esquerdo != direito):
                campos_divergentes.append(campo)
        if campos_divergentes:
            achados.append(_achado("transacao_banco_dados_divergentes", "Staging e destino divergem em conta, data ou valor.",
                                   "A referência pode apontar para a transação errada ou uma versão diferente.",
                                   "Conferir somente o vínculo explícito e a evidência bancária.", ids=[staging["id"], operacional["id"]],
                                   evidencia={"campos": campos_divergentes}))
    for chave_vinculo, grupo in por_vinculo.items():
        if len(grupo) > 1:
            achados.append(_achado("transacao_banco_multiplamente_vinculada", "Mais de um staging aponta para a mesma transação.",
                                   "Pode haver repetição de importação ou concorrência.", "Conferir idempotência e origem de cada staging.", ids=_ids(grupo)))
    fitids: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for staging in tabelas["transacoes_banco_staging"]:
        if not _nulo(staging.get("fitid")):
            fitids[_chave(staging["fitid"])].append(staging)
    for grupo in fitids.values():
        contas = {_chave(item.get("conta")) for item in grupo}
        if len(contas) > 1:
            achados.append(_achado("risco_escopo_fitid_entre_contas", "FITID aparece com identificações de conta textualmente diferentes no staging.",
                                   "Podem ser contas distintas ou nomes diferentes da mesma conta; não comprova duplicidade.",
                                   "Comprovar a identidade bancária, sem unir contas nem vincular por FITID isolado.", ids=_ids(grupo)))
    sem_externo = [item for item in tabelas["transacoes_banco"] if _nulo(item.get("id_externo"))]
    if sem_externo:
        achados.append(_achado("transacoes_banco_id_externo_nulo", "Transações operacionais não possuem id_externo.",
                               "A ausência impede comparação confiável com FITID.", "Preservar o nulo e conferir a origem bancária.", ids=_ids(sem_externo)))


def diagnosticar(antes: Any, depois: Any, planilha_registros: Any = None) -> dict[str, Any]:
    antes_validado = validar_snapshot(antes)
    depois_validado = validar_snapshot(depois)
    assinatura_antes = assinatura_snapshot(antes_validado)
    assinatura_depois = assinatura_snapshot(depois_validado)
    if assinatura_antes != assinatura_depois:
        raise _falha("snapshots_mudaram_durante_consulta")
    fontes = validar_planilha_registros(planilha_registros)
    achados: list[dict[str, Any]] = []
    tabelas = antes_validado["tabelas"]
    if fontes is not None:
        _diagnosticar_planilha(tabelas, fontes, achados)
    _diagnosticar_avaliacoes(tabelas, achados)
    _diagnosticar_componentes(tabelas, achados)
    _diagnosticar_candidatos(tabelas, achados)
    _diagnosticar_banco(tabelas, achados)
    achados.sort(key=lambda item: (item["tipo"], json.dumps(item["ids"], ensure_ascii=False, sort_keys=True, default=str)))
    por_tipo: dict[str, int] = defaultdict(int)
    for item in achados:
        por_tipo[item["tipo"]] += 1
    fontes_relatorio = [] if fontes is None else [
        {"id": fonte["id"], "aba": fonte["aba"], "sha256": fonte["sha256"], "leitura": fonte.get("leitura")}
        for fonte in fontes["fontes"]
    ]
    fontes_com_aviso = sum(
        bool((fonte.get("leitura") or {}).get("aviso")
             or (fonte.get("leitura") or {}).get("formulas_sem_valor_armazenado", 0)
             or (fonte.get("leitura") or {}).get("celulas_com_erro", 0))
        for fonte in fontes_relatorio
    )
    fontes_para_assinatura = [] if fontes is None else sorted(
        [{**fonte, "linhas": sorted(fonte["linhas"], key=lambda linha: linha["linha"])} for fonte in fontes["fontes"]],
        key=lambda fonte: fonte["id"],
    )
    assinatura_fontes = assinatura(fontes_para_assinatura)
    plano_id = assinatura({"snapshot": assinatura_antes, "fontes": assinatura_fontes})[:12]
    registros_exportados = sum(len(linhas) for linhas in tabelas.values())
    return {
        "versao": 1,
        "modo": "somente_leitura",
        "plano_id": plano_id,
        "resumo": {"achados": len(achados), "por_tipo": dict(sorted(por_tipo.items())),
                   "tabelas": {nome: len(tabelas[nome]) for nome in TABELAS},
                   "assinatura_antes": assinatura_antes, "assinatura_depois": assinatura_depois,
                   "assinatura_fontes": assinatura_fontes, "fontes_com_aviso": fontes_com_aviso},
        "verificacao": {"assinatura_antes": assinatura_antes, "assinatura_depois": assinatura_depois,
                        "snapshots_inalterados": True, "fontes_inalteradas": None if fontes is None else fontes["fontes_inalteradas"],
                        "consultas_diretas_banco": 0, "acessos_rede": 0,
                        "registros_exportados_analisados": registros_exportados,
                        "escritas_operacionais": 0},
        "achados": achados,
        "fontes": fontes_relatorio,
        "limites": ["Achados estatísticos e de integridade de exportação não equivalem a PK/FK ou decisão de negócio.",
                    "Códigos, FITID, conta, data e valores não são normalizados nem associados por heurística.",
                    "A comparação de totais só ocorre quando pai e todos os filhos possuem Decimal válido; nulo não é zero.",
                    "A extração dos snapshots ocorreu fora deste motor; consultas diretas ao banco são zero.",
                    "A estabilidade atestada cobre somente os campos exportados, não o banco inteiro.",
                    "Fontes inalteradas é declaração do extrator; este motor não reabre a planilha original."],
    }


gerar_relatorio = diagnosticar
analisar = diagnosticar
diagnosticar_vinculos_negocios = diagnosticar


def markdown(relatorio: dict[str, Any]) -> str:
    linhas = ["# Diagnóstico offline de vínculos", "", f"Plano: `{relatorio['plano_id']}`.",
              "Nenhuma rede ou consulta direta ao banco foi feita por este motor; os registros vieram de snapshots exportados.",
              "", "## Resumo", ""]
    for chave, valor in relatorio["resumo"].items():
        linhas.append(f"- {chave}: `{json.dumps(valor, ensure_ascii=False, sort_keys=True)}`")
    linhas.extend(["", "## Achados", ""])
    for numero, achado in enumerate(relatorio["achados"], 1):
        linhas.extend([f"### {numero}. {achado['tipo']}", f"- Observação: {achado['observacao']}",
                       f"- Hipótese: {achado['hipotese']}", f"- Próxima verificação: {achado['proxima_verificacao']}",
                       f"- IDs: `{json.dumps(achado['ids'], ensure_ascii=False)}`",
                       f"- Linhas: `{json.dumps(achado['linhas'], ensure_ascii=False)}`",
                       f"- Evidência privada: `{json.dumps(achado['evidencia'], ensure_ascii=False)}`", ""])
    return "\n".join(linhas)


def _destino_privado(saida: Path) -> bool:
    destino = saida.resolve()
    privado = any(destino.parts[i:i + 2] == ("docs", "privado") for i in range(len(destino.parts) - 1))
    temporario = any(destino.is_relative_to(raiz.resolve()) for raiz in (Path("/tmp"), Path("/private/tmp"), Path(tempfile.gettempdir())))
    return privado or temporario


def salvar(relatorio: dict[str, Any], saida: Path) -> None:
    destino = saida.resolve()
    if not _destino_privado(destino):
        raise _falha("saida_deve_ser_privada")
    destino.mkdir(mode=0o700, parents=True, exist_ok=False)
    for nome, conteudo in (("analise.json", json.dumps(relatorio, ensure_ascii=False, indent=2) + "\n"),
                           ("analise.md", markdown(relatorio))):
        caminho = destino / nome
        with caminho.open("x", encoding="utf-8") as arquivo:
            os.chmod(caminho, 0o600)
            arquivo.write(conteudo)


salvar_relatorio = salvar


def _ler_bytes(caminho: Path) -> bytes:
    with caminho.open("rb") as arquivo:
        bruto = arquivo.read(10_000_001)
    if len(bruto) > 10_000_000:
        raise _falha("arquivo_acima_do_limite")
    return bruto


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--antes", required=True, type=Path)
    parser.add_argument("--depois", required=True, type=Path)
    parser.add_argument("--planilha-registros", type=Path)
    parser.add_argument("--saida", type=Path)
    parser.add_argument("--stdout", action="store_true", help="imprime somente resumo sanitizado")
    args = parser.parse_args(argv)
    try:
        caminhos = [args.antes, args.depois] + ([args.planilha_registros] if args.planilha_registros else [])
        arquivos = {caminho: _ler_bytes(caminho) for caminho in caminhos}
        entradas = {caminho: json.loads(bruto.decode("utf-8")) for caminho, bruto in arquivos.items()}
        planilha = entradas[args.planilha_registros] if args.planilha_registros else None
        relatorio = diagnosticar(entradas[args.antes], entradas[args.depois], planilha)
        if any(_ler_bytes(caminho) != bruto for caminho, bruto in arquivos.items()):
            raise _falha("arquivo_mudou_durante_analise")
        relatorio["verificacao"]["arquivos_entrada_inalterados_durante_analise"] = True
        if args.saida:
            salvar(relatorio, args.saida)
        resumo = {"plano_id": relatorio["plano_id"], "resumo": relatorio["resumo"], "verificacao": relatorio["verificacao"]}
        print(json.dumps(resumo, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        print("Diagnóstico não gerado: confira snapshots e saída privada. Nenhuma escrita operacional foi executada.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
