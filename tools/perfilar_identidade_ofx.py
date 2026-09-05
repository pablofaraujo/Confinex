#!/usr/bin/env python3
"""Perfila identidades e transações OFX em memória, sem importar ou conciliar.

O hash de um ``STMTTRN`` XML é da serialização UTF-8 feita pelo
``ElementTree`` (inclui seus campos, inclusive MEMO, sem devolver o texto).
No SGML é do trecho textual interno do bloco, também incluindo MEMO.
Mudanças de conteúdo são detectadas; diferenças meramente de formatação XML
podem não alterar o hash.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
import codecs
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from typing import Any


MAX_BYTES = 10_000_000
MAX_DEMONSTRATIVOS = 1_000
MAX_TRANSACOES = 100_000
IDENTIDADE = ("BANKID", "BRANCHID", "ACCTID", "ACCTTYPE", "CURDEF")
_DECIMAL_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_TAG_RE = re.compile(r"<([A-Za-z][A-Za-z0-9_]*)\b[^>]*>")
_FOLHAS_SGML = {"BANKID", "BRANCHID", "ACCTID", "ACCTTYPE", "CURDEF", "TRNTYPE", "DTPOSTED", "TRNAMT", "FITID", "MEMO"}
_AGREGADOS_SGML = {"OFX", "BANKMSGSRSV1", "STMTTRNRS", "STMTRS", "BANKACCTFROM", "BANKTRANLIST", "STMTTRN"}


def _falha(mensagem: str) -> ValueError:
    return ValueError(mensagem)


def _texto(valor: str | None) -> str | None:
    if valor is None:
        return None
    return valor.strip()


def _decimal_texto(valor: str | None) -> str | None:
    valor = _texto(valor)
    if valor is None or not valor:
        return None
    if not _DECIMAL_RE.fullmatch(valor):
        raise _falha("valor_decimal_invalido")
    try:
        numero = Decimal(valor)
    except InvalidOperation as erro:
        raise _falha("valor_decimal_invalido") from erro
    if not numero.is_finite():
        raise _falha("valor_decimal_invalido")
    return valor


def _data_iso(valor: str | None) -> tuple[str | None, str | None, str | None]:
    valor = _texto(valor)
    if not valor:
        return None, None, None
    iso = re.fullmatch(
        r"(\d{4})-(\d{2})-(\d{2})(?:T(\d{2}):(\d{2}):(\d{2})(\.\d+)?(Z|[+-]\d{2}:\d{2})?)?",
        valor,
    )
    if iso:
        ano, mes, dia, hora, minuto, segundo, fracao, zona = iso.groups()
        try:
            if hora is None:
                date(int(ano), int(mes), int(dia))
            else:
                datetime(int(ano), int(mes), int(dia), int(hora), int(minuto), int(segundo))
        except ValueError as erro:
            raise _falha("data_iso_invalida") from erro
        if zona and zona != "Z" and (int(zona[1:3]) > 23 or int(zona[4:]) > 59):
            raise _falha("fuso_iso_invalido")
        return valor, "iso8601", valor
    encontrado = re.fullmatch(
        r"(\d{4})(\d{2})(\d{2})(?:([0-9]{2})([0-9]{2})([0-9]{2}))?"
        r"(\.\d+)?(?:(?:([+-])(\d{2})(\d{2}))|(?:\[([+-])(\d{1,2}):[^\]]+\]))?",
        valor,
    )
    if not encontrado:
        raise _falha("data_ofx_invalida")
    ano, mes, dia, hora, minuto, segundo, fracao, sinal, tz_hora, tz_minuto, sinal_colchete, tz_hora_colchete = encontrado.groups()
    # A fração pertence ao horário OFX; ``YYYYMMDD.123`` não é uma data
    # válida e não pode perder silenciosamente a precisão durante o perfil.
    if fracao and hora is None:
        raise _falha("data_ofx_invalida")
    try:
        date(int(ano), int(mes), int(dia))
    except ValueError as erro:
        raise _falha("data_ofx_invalida") from erro
    if hora is not None:
        try:
            datetime(int(ano), int(mes), int(dia), int(hora), int(minuto), int(segundo))
        except ValueError as erro:
            raise _falha("data_ofx_invalida") from erro
    if sinal:
        if hora is None or int(tz_hora) > 23 or int(tz_minuto) > 59:
            raise _falha("fuso_ofx_invalido")
    elif sinal_colchete:
        if hora is None or int(tz_hora_colchete) > 23:
            raise _falha("fuso_ofx_invalido")
    resultado = f"{ano}-{mes}-{dia}"
    if hora is not None:
        resultado += f"T{hora}:{minuto}:{segundo}{fracao or ''}"
        if sinal:
            resultado += f"{sinal}{tz_hora}:{tz_minuto}"
        elif sinal_colchete:
            resultado += f"{sinal_colchete}{int(tz_hora_colchete):02d}:00"
    return resultado, "ofx_compacto", valor


def _tag_nome(elemento: ET.Element) -> str:
    return elemento.tag.rsplit("}", 1)[-1].upper()


def _canonico(valor: Any) -> Any:
    if valor is None:
        return ["nulo"]
    if isinstance(valor, bool):
        return ["booleano", valor]
    if isinstance(valor, int):
        return ["inteiro", valor]
    if isinstance(valor, str):
        return ["texto", valor]
    if isinstance(valor, list):
        return ["lista", [_canonico(item) for item in valor]]
    if isinstance(valor, dict):
        return ["objeto", [[str(k), _canonico(valor[k])] for k in sorted(valor)]]
    raise _falha("tipo_interno_invalido")


def _chave(valor: Any) -> str:
    return json.dumps(_canonico(valor), ensure_ascii=False, separators=(",", ":"))


def _hash(valor: bytes) -> str:
    return hashlib.sha256(valor).hexdigest()


def _decodificar(dados: bytes) -> str:
    prefixo = dados[:4096].decode("ascii", errors="ignore")
    declaracao_xml = re.search(r"<\?xml\b[^>]*encoding\s*=\s*['\"]([^'\"]+)['\"]", prefixo, re.IGNORECASE)
    if declaracao_xml:
        nome = declaracao_xml.group(1)
    else:
        charset = re.search(r"^\s*CHARSET\s*:\s*([^\r\n]+)", prefixo, re.IGNORECASE | re.MULTILINE)
        encoding = re.search(r"^\s*ENCODING\s*:\s*([^\r\n]+)", prefixo, re.IGNORECASE | re.MULTILINE)
        nome = (charset.group(1).strip() if charset else (encoding.group(1).strip() if encoding else "utf-8"))
        if nome.upper() in {"1252", "WINDOWS-1252", "CP1252"}:
            nome = "cp1252"
        elif nome.upper() in {"8859-1", "ISO8859-1", "ISO-8859-1"}:
            nome = "iso-8859-1"
        elif nome.upper() == "USASCII":
            nome = "ascii"
    try:
        codec = codecs.lookup(nome).name
        if dados.startswith(b"\xef\xbb\xbf") and codec == "utf-8":
            codec = "utf-8-sig"
        return dados.decode(codec)
    except (LookupError, UnicodeDecodeError) as erro:
        raise _falha("codificacao_ofx_invalida") from erro


def _identidade(valores: dict[str, list[str]]) -> tuple[dict[str, str | None], list[str]]:
    identidade: dict[str, str | None] = {}
    faltantes = []
    for campo in IDENTIDADE:
        presentes = [_texto(valor) for valor in valores.get(campo, []) if _texto(valor)]
        distintos = {_chave(valor) for valor in presentes}
        if len(distintos) > 1:
            raise _falha("cabecalho_contraditorio")
        identidade[campo] = presentes[0] if presentes else None
        if identidade[campo] is None:
            faltantes.append(campo)
    return identidade, faltantes


def _campo_unico(valores: dict[str, list[str]], nome: str) -> str | None:
    presentes = [_texto(valor) for valor in valores.get(nome, []) if _texto(valor) is not None]
    if len({_chave(valor) for valor in presentes}) > 1:
        raise _falha("transacao_campo_contraditorio")
    return presentes[0] if presentes else None


def _transacao(fitid: str | None, data: str | None, valor: str | None, trntype: str | None, conteudo: bytes, ordinal: int) -> dict[str, Any]:
    fitid = _texto(fitid) or None
    data_iso, data_formato, data_original = _data_iso(data)
    return {
        "ordinal": ordinal,
        "fitid": _texto(fitid),
        "data": data_iso,
        "data_formato": data_formato,
        "data_ofx_original": data_original,
        "valor": _decimal_texto(valor),
        "trntype": _texto(trntype),
        "stmttrn_sha256": _hash(conteudo),
    }


def _valores_xml(elemento: ET.Element, nomes: tuple[str, ...], excluir: set[int] | None = None) -> dict[str, list[str]]:
    excluir = excluir or set()
    valores: dict[str, list[str]] = defaultdict(list)
    for item in elemento.iter():
        if id(item) in excluir:
            continue
        nome = _tag_nome(item)
        if nome in nomes:
            valores[nome].append(item.text or "")
    return valores


def _extrair_xml(bloco: ET.Element, ordinal: int) -> dict[str, Any]:
    transacoes_elementos = [item for item in bloco.iter() if _tag_nome(item) == "STMTTRN"]
    excluidos: set[int] = set()
    for item in transacoes_elementos:
        excluidos.update(id(descendente) for descendente in item.iter())
    valores = _valores_xml(bloco, IDENTIDADE, excluidos)
    identidade, faltantes = _identidade(valores)
    transacoes = []
    for numero, item in enumerate(transacoes_elementos, 1):
        campos = _valores_xml(item, ("FITID", "DTPOSTED", "TRNAMT", "TRNTYPE"))
        conteudo = ET.tostring(item, encoding="utf-8", short_empty_elements=True)
        transacoes.append(_transacao(
            _campo_unico(campos, "FITID"), _campo_unico(campos, "DTPOSTED"),
            _campo_unico(campos, "TRNAMT"), _campo_unico(campos, "TRNTYPE"), conteudo, numero,
        ))
    return {"ordinal": ordinal, "identidade": identidade, "faltantes": faltantes, "transacoes": transacoes}


def _sgml_tags(texto: str, nomes: tuple[str, ...]) -> dict[str, list[str]]:
    valores: dict[str, list[str]] = defaultdict(list)
    for nome in nomes:
        padrao = re.compile(rf"<{nome}\b[^>]*>(.*?)(?=</?\w+\b|$)", re.IGNORECASE | re.DOTALL)
        for encontrado in padrao.finditer(texto):
            valor = re.sub(rf"</{nome}>\s*$", "", encontrado.group(1), flags=re.IGNORECASE).strip()
            valores[nome].append(valor)
    return valores


def _blocos_sgml(texto: str, tag: str) -> list[str]:
    inicios = list(re.finditer(rf"<{tag}\b[^>]*>", texto, re.IGNORECASE))
    blocos = []
    for indice, inicio in enumerate(inicios):
        fim = inicios[indice + 1].start() if indice + 1 < len(inicios) else len(texto)
        trecho = texto[inicio.end():fim]
        fechamento = re.search(rf"</{tag}\s*>", trecho, re.IGNORECASE)
        if fechamento:
            trecho = trecho[:fechamento.start()]
        blocos.append(trecho)
    return blocos


def _remover_blocos_sgml(texto: str, tag: str) -> str:
    inicios = list(re.finditer(rf"<{tag}\b[^>]*>", texto, re.IGNORECASE))
    partes = []
    cursor = 0
    for indice, inicio in enumerate(inicios):
        fim = inicios[indice + 1].start() if indice + 1 < len(inicios) else len(texto)
        fechamento = re.search(rf"</{tag}\s*>", texto[inicio.end():fim], re.IGNORECASE)
        fim_real = inicio.end() + fechamento.end() if fechamento else fim
        partes.append(texto[cursor:inicio.start()])
        cursor = fim_real
    partes.append(texto[cursor:])
    return "".join(partes)


def _validar_nesting_sgml(texto: str) -> None:
    """Folhas OFX/SGML podem ficar abertas; agregados precisam fechar em ordem."""

    tokens = re.finditer(r"</?([A-Za-z][A-Za-z0-9_]*)\b[^>]*>", texto)
    pilha: list[str] = []
    for token in tokens:
        nome = token.group(1).upper()
        abertura = not token.group(0).startswith("</")
        if nome in _FOLHAS_SGML:
            continue
        if nome not in _AGREGADOS_SGML:
            continue
        if abertura:
            pilha.append(nome)
        elif not pilha or pilha[-1] != nome:
            raise _falha("agregados_sgml_desbalanceados")
        else:
            pilha.pop()
    if pilha:
        raise _falha("agregados_sgml_nao_fechados")


def _extrair_sgml(texto: str) -> list[dict[str, Any]]:
    _validar_nesting_sgml(texto)
    blocos = _blocos_sgml(texto, "STMTRS")
    if not blocos:
        raise _falha("stmttrrs_ausente")
    demonstrativos = []
    total = 0
    for ordinal, bloco in enumerate(blocos, 1):
        sem_transacoes = _remover_blocos_sgml(bloco, "STMTTRN")
        valores = _sgml_tags(sem_transacoes, IDENTIDADE)
        identidade, faltantes = _identidade(valores)
        transacoes = []
        for numero, trecho in enumerate(_blocos_sgml(bloco, "STMTTRN"), 1):
            campos = _sgml_tags(trecho, ("FITID", "DTPOSTED", "TRNAMT", "TRNTYPE"))
            conteudo = trecho.encode("utf-8")
            transacoes.append(_transacao(
                _campo_unico(campos, "FITID"), _campo_unico(campos, "DTPOSTED"),
                _campo_unico(campos, "TRNAMT"), _campo_unico(campos, "TRNTYPE"), conteudo, numero,
            ))
            total += 1
            if total > MAX_TRANSACOES:
                raise _falha("transacoes_acima_do_limite")
        demonstrativos.append({"ordinal": ordinal, "identidade": identidade,
                               "faltantes": faltantes, "transacoes": transacoes})
    return demonstrativos


def _perfil_repeticoes(demonstrativos: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    grupos: dict[tuple[str, str], list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    incompletas: dict[str, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    fitids_ausentes: list[dict[str, Any]] = []
    for demonstrativo in demonstrativos:
        identidade_chave = _chave(demonstrativo["identidade"])
        for transacao in demonstrativo["transacoes"]:
            if transacao["fitid"] is None:
                fitids_ausentes.append({"demonstrativo": demonstrativo["ordinal"], "ordinal": transacao["ordinal"]})
            elif demonstrativo["faltantes"]:
                incompletas[_chave(transacao["fitid"])].append((demonstrativo["ordinal"], transacao))
            else:
                grupos[(identidade_chave, _chave(transacao["fitid"]))].append((demonstrativo["ordinal"], transacao))
    identicas, conflitos, identidade_incompleta = [], [], []
    for grupo in grupos.values():
        if len(grupo) < 2:
            continue
        hashes = {item[1]["stmttrn_sha256"] for item in grupo}
        alvo = {"fitid": grupo[0][1]["fitid"],
                "demonstrativos": [item[0] for item in grupo],
                "ordinais": [item[1]["ordinal"] for item in grupo]}
        (identicas if len(hashes) == 1 else conflitos).append(alvo)
    for grupo in incompletas.values():
        if len(grupo) > 1:
            identidade_incompleta.append({"fitid": grupo[0][1]["fitid"],
                                          "demonstrativos": [item[0] for item in grupo],
                                          "ordinais": [item[1]["ordinal"] for item in grupo]})
    fitids: dict[str, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    for demonstrativo in demonstrativos:
        # Identidade parcial não prova que o FITID pertença a uma identidade
        # distinta; mantê-la fora desta comparação evita classificar
        # incompleto+completo como colisão entre contas.
        if demonstrativo["faltantes"]:
            continue
        for transacao in demonstrativo["transacoes"]:
            if transacao["fitid"] is not None:
                fitids[_chave(transacao["fitid"])][_chave(demonstrativo["identidade"])].add(demonstrativo["ordinal"])
    entre_identidades = []
    for valor, por_identidade in fitids.items():
        if len(por_identidade) > 1:
            entre_identidades.append({
                "fitid": next(t["fitid"] for d in demonstrativos for t in d["transacoes"]
                               if t["fitid"] is not None and _chave(t["fitid"]) == valor),
                "demonstrativos": sorted(ordinal for ordinais in por_identidade.values() for ordinal in ordinais),
                "identidades": [d["identidade"] for d in demonstrativos if _chave(d["identidade"]) in por_identidade],
            })
    return identicas, conflitos, entre_identidades, identidade_incompleta + fitids_ausentes


def perfilar_ofx(dados: bytes) -> dict[str, Any]:
    if not isinstance(dados, bytes) or not dados:
        raise _falha("ofx_vazio")
    if len(dados) > MAX_BYTES:
        raise _falha("ofx_acima_do_limite")
    texto = _decodificar(dados)
    if re.search(r"<!DOCTYPE|<!ENTITY|SYSTEM\s+['\"]", texto, re.IGNORECASE):
        raise _falha("ofx_entidade_nao_permitida")
    inicio = re.search(r"<OFX\b[^>]*>", texto, re.IGNORECASE)
    if not inicio:
        raise _falha("ofx_estrutura_nao_suportada")
    cabecalho = texto[:inicio.start()]
    ofx_header = re.search(r"^\s*OFXHEADER\s*:\s*(\d+)\s*$", cabecalho, re.IGNORECASE | re.MULTILINE)
    data_header = re.search(r"^\s*DATA\s*:\s*([^\r\n]+)", cabecalho, re.IGNORECASE | re.MULTILINE)
    xml_declarado = dados.startswith(b"\xef\xbb\xbf") or bool(re.match(r"\ufeff?\s*<\?xml\b", texto, re.IGNORECASE))
    sgml_declarado = False
    if ofx_header:
        if ofx_header.group(1) == "200":
            xml_declarado = True
        elif ofx_header.group(1) != "100":
            raise _falha("cabecalho_ofx_nao_suportado")
    if data_header:
        formato = data_header.group(1).strip().upper()
        if formato == "OFXXML":
            xml_declarado = True
        elif formato == "OFXSGML":
            sgml_declarado = True
        else:
            raise _falha("formato_ofx_nao_suportado")
    if xml_declarado and sgml_declarado:
        raise _falha("cabecalhos_ofx_contraditorios")
    corpo = texto[inicio.start():]
    if sgml_declarado:
        demonstrativos = _extrair_sgml(corpo)
    else:
        try:
            raiz = ET.fromstring(corpo)
        except ET.ParseError:
            if xml_declarado:
                raise _falha("xml_malformado")
            demonstrativos = _extrair_sgml(corpo)
        else:
            blocos = [item for item in raiz.iter() if _tag_nome(item) == "STMTRS"]
            if not blocos:
                raise _falha("stmttrrs_ausente")
            if len(blocos) > MAX_DEMONSTRATIVOS:
                raise _falha("demonstrativos_acima_do_limite")
            demonstrativos = [_extrair_xml(bloco, ordinal) for ordinal, bloco in enumerate(blocos, 1)]
    if len(demonstrativos) > MAX_DEMONSTRATIVOS:
        raise _falha("demonstrativos_acima_do_limite")
    total_transacoes = sum(len(item["transacoes"]) for item in demonstrativos)
    if total_transacoes > MAX_TRANSACOES:
        raise _falha("transacoes_acima_do_limite")
    identicas, conflitos, entre_identidades, incompletas = _perfil_repeticoes(demonstrativos)
    repeticoes_identidade_incompleta = [item for item in incompletas if "fitid" in item]
    fitids_ausentes = [item for item in incompletas if "fitid" not in item]
    identidades_incompletas = [{"demonstrativo": item["ordinal"], "faltantes": item["faltantes"]}
                               for item in demonstrativos if item["faltantes"]]
    return {
        "versao": 1,
        "fonte": "ofx",
        "somente_leitura": True,
        "sha256": _hash(dados),
        "demonstrativos": demonstrativos,
        "repeticoes_identicas": identicas,
        "conflitos_conteudo": conflitos,
        "fitids_multiplas_identidades": entre_identidades,
        "identidades_incompletas": identidades_incompletas,
        "repeticoes_identidade_incompleta": repeticoes_identidade_incompleta,
        "fitids_ausentes": fitids_ausentes,
        "resumo": {"demonstrativos": len(demonstrativos), "transacoes": total_transacoes,
                   "repeticoes_identicas": len(identicas), "conflitos_conteudo": len(conflitos),
                   "fitids_multiplas_identidades": len(entre_identidades),
                   "identidades_incompletas": len(identidades_incompletas),
                   "repeticoes_identidade_incompleta": len(repeticoes_identidade_incompleta),
                   "fitids_ausentes": len(fitids_ausentes)},
    }


def perfilar_conjunto(perfis: list[dict[str, Any]]) -> dict[str, Any]:
    """Classifica repetição/conflicto entre arquivos, com referências privadas."""

    if not isinstance(perfis, list) or not perfis:
        raise _falha("conjunto_vazio")
    grupos: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    incompletos: dict[str, list[dict[str, Any]]] = defaultdict(list)
    fitids_ausentes: list[dict[str, int]] = []
    identidades_incompletas: list[dict[str, Any]] = []
    identidades_por_fitid: dict[str, dict[str, list[dict[str, int]]]] = defaultdict(lambda: defaultdict(list))
    fitid_por_chave: dict[str, str] = {}
    for arquivo, perfil in enumerate(perfis, 1):
        if not isinstance(perfil, dict) or perfil.get("fonte") != "ofx" or not isinstance(perfil.get("demonstrativos"), list):
            raise _falha("perfil_invalido")
        for demonstrativo in perfil["demonstrativos"]:
            identidade = _chave(demonstrativo["identidade"])
            if demonstrativo.get("faltantes"):
                identidades_incompletas.append({"arquivo": arquivo, "demonstrativo": demonstrativo["ordinal"],
                                               "faltantes": demonstrativo["faltantes"]})
            for transacao in demonstrativo["transacoes"]:
                referencia = {"arquivo": arquivo, "demonstrativo": demonstrativo["ordinal"],
                              "transacao": transacao["ordinal"]}
                fitid = transacao.get("fitid")
                if fitid is None:
                    fitids_ausentes.append(referencia)
                    continue
                fitid_chave = _chave(fitid)
                fitid_por_chave[fitid_chave] = fitid
                if demonstrativo.get("faltantes"):
                    incompletos[fitid_chave].append({**referencia, "fitid": fitid})
                else:
                    # A referência precisa apontar para a ocorrência, não só
                    # para o arquivo: um arquivo pode conter vários
                    # demonstrativos e transações com o mesmo FITID.
                    identidades_por_fitid[fitid_chave][identidade].append(referencia)
                    grupos[(identidade, fitid_chave)].append({**referencia, "hash": transacao["stmttrn_sha256"], "fitid": fitid})
    identicas, conflitos, identidade_incompleta = [], [], []
    for grupo in grupos.values():
        if len(grupo) < 2:
            continue
        alvo = {"fitid": grupo[0]["fitid"], "referencias": [{k: item[k] for k in ("arquivo", "demonstrativo", "transacao")} for item in grupo]}
        (identicas if len({item["hash"] for item in grupo}) == 1 else conflitos).append(alvo)
    for grupo in incompletos.values():
        if len(grupo) > 1:
            identidade_incompleta.append({"fitid": grupo[0]["fitid"],
                                          "referencias": [{k: item[k] for k in ("arquivo", "demonstrativo", "transacao")} for item in grupo]})
    entre_identidades = []
    for fitid_chave, identidades in identidades_por_fitid.items():
        if len(identidades) > 1:
            fitid = fitid_por_chave[fitid_chave]
            referencias = [ref for valores in identidades.values() for ref in valores]
            referencias.sort(key=lambda ref: (ref["arquivo"], ref["demonstrativo"], ref["transacao"]))
            entre_identidades.append({"fitid": fitid, "demonstrativos": referencias})
    return {
        "versao": 1, "fontes": len(perfis),
        "repeticoes_identicas": identicas, "conflitos_conteudo": conflitos,
        "identidades_incompletas": identidades_incompletas,
        "repeticoes_identidade_incompleta": identidade_incompleta,
        "fitids_ausentes": fitids_ausentes,
        "fitids_multiplas_identidades": entre_identidades,
        "resumo": {"fontes": len(perfis), "repeticoes_identicas": len(identicas),
                   "conflitos_conteudo": len(conflitos), "identidades_incompletas": len(identidades_incompletas),
                   "repeticoes_identidade_incompleta": len(identidade_incompleta),
                   "fitids_ausentes": len(fitids_ausentes), "fitids_multiplas_identidades": len(entre_identidades)},
    }


def _destino_privado(saida: Path) -> bool:
    destino = saida.resolve()
    privado = any(destino.parts[i:i + 2] == ("docs", "privado") for i in range(len(destino.parts) - 1))
    temporario = any(destino.is_relative_to(raiz.resolve()) for raiz in (Path("/tmp"), Path("/private/tmp"), Path(tempfile.gettempdir())))
    return privado or temporario


def _salvar(relatorio: dict[str, Any], saida: Path) -> None:
    destino = saida.resolve()
    if not _destino_privado(destino):
        raise _falha("saida_deve_ser_privada")
    destino.mkdir(mode=0o700, parents=True, exist_ok=False)
    for nome, conteudo in (("analise.json", json.dumps(relatorio, ensure_ascii=False, indent=2) + "\n"),
                           ("analise.md", "# Perfil de identidade OFX\n\n" + json.dumps(relatorio["resumo"], ensure_ascii=False, indent=2) + "\n")):
        caminho = destino / nome
        with caminho.open("x", encoding="utf-8") as arquivo:
            os.chmod(caminho, 0o600)
            arquivo.write(conteudo)


def _ler_estavel(caminho: Path) -> tuple[bytes, bytes]:
    with caminho.open("rb") as arquivo:
        antes = arquivo.read(MAX_BYTES + 1)
    with caminho.open("rb") as arquivo:
        depois = arquivo.read(MAX_BYTES + 1)
    if len(antes) > MAX_BYTES or len(depois) > MAX_BYTES:
        raise _falha("ofx_acima_do_limite")
    if antes != depois:
        raise _falha("fonte_alterada_durante_leitura")
    return antes, depois


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ofx", action="append", required=True, type=Path)
    parser.add_argument("--saida", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        fontes = []
        for caminho in args.ofx:
            antes, depois = _ler_estavel(caminho)
            perfil = perfilar_ofx(antes)
            fontes.append({"ordinal": len(fontes) + 1, "caminho": caminho,
                           "sha256_antes": _hash(antes), "sha256_depois": _hash(depois),
                           "perfil": perfil})
        # Feche a janela de leitura relendo todas as fontes antes de criar a saída.
        for fonte in fontes:
            final_antes, final_depois = _ler_estavel(fonte["caminho"])
            if _hash(final_antes) != fonte["sha256_antes"]:
                raise _falha("fonte_alterada_durante_leitura")
            fonte["sha256_depois"] = _hash(final_depois)
            if fonte["sha256_depois"] != fonte["sha256_antes"]:
                raise _falha("fonte_alterada_durante_leitura")
            del fonte["caminho"]
        conjunto = perfilar_conjunto([fonte["perfil"] for fonte in fontes])
        relatorio = {"versao": 1, "modo": "somente_leitura", "fontes": fontes,
                     "conjunto": conjunto,
                     "resumo": {"fontes": len(fontes),
                                "demonstrativos": sum(item["perfil"]["resumo"]["demonstrativos"] for item in fontes),
                                "transacoes": sum(item["perfil"]["resumo"]["transacoes"] for item in fontes),
                                "sha256": [item["sha256_antes"] for item in fontes],
                                **conjunto["resumo"]},
                     "verificacao": {"fontes_inalteradas": True, "acessos_rede": 0,
                                     "consultas_diretas_banco": 0, "escritas_operacionais": 0}}
        _salvar(relatorio, args.saida)
        print(json.dumps({"resumo": relatorio["resumo"], "verificacao": relatorio["verificacao"]},
                         ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ValueError, UnicodeError, ET.ParseError, KeyError, TypeError):
        print("Perfil OFX não gerado: confira as fontes e a saída privada nova. Nenhuma importação ou escrita operacional foi executada.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
