"""Perfil puro de campos e chaves para diagnóstico de normalização.

O módulo não conhece o Supabase e não faz inferências de negócio.  Ele apenas
conta a amostra recebida.  Em particular, a flag ``candidata_unica_na_amostra``
descreve a amostra e não constitui uma recomendação de chave operacional.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from fractions import Fraction
import math
import numbers
from typing import Any
import unicodedata


_TIPOS_ORDEM = {
    "bool": 0,
    "numero": 1,
    "texto": 2,
    "data": 3,
    "objeto": 4,
}


def _erro(mensagem: str) -> ValueError:
    """Cria erros de configuração sem incluir qualquer valor de registro."""

    return ValueError(mensagem)


def _validar_registros(registros: Any, nome: str) -> list[dict[str, Any]]:
    if not isinstance(registros, list):
        raise _erro(f"{nome} deve ser uma lista de registros")
    if any(not isinstance(registro, dict) for registro in registros):
        raise _erro(f"{nome} deve conter somente objetos de registro")
    return registros


def _validar_campos(campos: Any, nome: str) -> list[str]:
    if not isinstance(campos, list) or not campos:
        raise _erro(f"{nome} deve ser uma lista não vazia de campos")
    if any(not isinstance(campo, str) or not campo.strip() for campo in campos):
        raise _erro(f"{nome} contém nome de campo inválido")
    if len(set(campos)) != len(campos):
        raise _erro(f"{nome} não pode repetir campos")
    return campos


def _validar_chaves(chaves: Any, campos: list[str]) -> list[dict[str, Any]]:
    if chaves is None:
        return []
    if not isinstance(chaves, list):
        raise _erro("chaves deve ser uma lista ou None")

    resultado: list[dict[str, Any]] = []
    nomes: set[str] = set()
    for chave in chaves:
        if not isinstance(chave, dict):
            raise _erro("cada chave deve ser um objeto de configuração")
        nome = chave.get("nome")
        if not isinstance(nome, str) or not nome.strip():
            raise _erro("cada chave precisa de nome")
        if nome in nomes:
            raise _erro("nomes de chaves não podem se repetir")
        nomes.add(nome)
        campos_chave = chave.get("campos")
        _validar_campos(campos_chave, "campos da chave")
        if any(campo not in campos for campo in campos_chave):
            raise _erro("chave referencia campo fora do catálogo")
        resultado.append({"nome": nome, "campos": list(campos_chave)})
    return resultado


def _eh_nulo(valor: Any) -> bool:
    return valor is None or (isinstance(valor, str) and not valor.strip())


def _tipo(valor: Any) -> str:
    # bool precisa ser testado antes de números: bool é uma subclasse de int.
    if isinstance(valor, bool):
        return "bool"
    if isinstance(valor, (Decimal, numbers.Real)):
        return "numero"
    if isinstance(valor, str):
        return "texto"
    if isinstance(valor, (datetime, date)):
        return "data"
    return "objeto"


def _chave_numero(valor: Any) -> tuple[Any, ...]:
    """Representa números por igualdade exata, sem tolerância de ponto flutuante."""

    if isinstance(valor, Decimal):
        if valor.is_nan():
            return ("decimal-especial", "nan", str(valor.is_signed()))
        if valor.is_infinite():
            return ("decimal-especial", "-inf" if valor.is_signed() else "inf")
        return ("finito", Fraction(valor))
    if isinstance(valor, float):
        if math.isnan(valor):
            return ("float-especial", "nan", str(math.copysign(1.0, valor) < 0))
        if math.isinf(valor):
            return ("float-especial", "-inf" if valor < 0 else "inf")
        # as_integer_ratio preserva o valor binário real; não arredondamos 0.1.
        return ("finito", Fraction(*valor.as_integer_ratio()))
    if isinstance(valor, numbers.Real):
        try:
            return ("finito", Fraction(valor))
        except (TypeError, ValueError, OverflowError):
            return ("numero-especial", type(valor).__qualname__, repr(valor))
    return ("numero-especial", type(valor).__qualname__, repr(valor))


def _chave_exata(valor: Any) -> tuple[Any, ...]:
    """Chave hashável com marca de tipo para igualdade diagnóstica."""

    if valor is None:
        return ("nulo",)
    if isinstance(valor, bool):
        return ("bool", valor)
    if isinstance(valor, (Decimal, numbers.Real)):
        return ("numero", _chave_numero(valor))
    if isinstance(valor, str):
        return ("texto", valor)
    if isinstance(valor, datetime):
        return ("data", "datetime", valor.isoformat())
    if isinstance(valor, date):
        return ("data", "date", valor.isoformat())
    if isinstance(valor, list):
        return ("objeto", "lista", tuple(_chave_exata(item) for item in valor))
    if isinstance(valor, tuple):
        return ("objeto", "tupla", tuple(_chave_exata(item) for item in valor))
    if isinstance(valor, dict):
        itens = [(_chave_exata(key), _chave_exata(item)) for key, item in valor.items()]
        return ("objeto", "dicionario", tuple(sorted(itens, key=repr)))
    if isinstance(valor, set):
        return ("objeto", "conjunto", tuple(sorted((_chave_exata(item) for item in valor), key=repr)))
    # Valores fora do contrato (por exemplo, uma instância de classe) não são
    # emitidos no resultado. A representação serve só para manter o contador
    # total determinístico durante esta chamada.
    return ("objeto", type(valor).__module__, type(valor).__qualname__, repr(valor))


def _chave_normalizada(valor: Any) -> tuple[Any, ...]:
    """Normalização auxiliar, deliberadamente separada da chave exata."""

    if isinstance(valor, str):
        return ("texto", unicodedata.normalize("NFKC", valor).strip().casefold())
    if isinstance(valor, list):
        return ("objeto", "lista", tuple(_chave_normalizada(item) for item in valor))
    if isinstance(valor, tuple):
        return ("objeto", "tupla", tuple(_chave_normalizada(item) for item in valor))
    if isinstance(valor, dict):
        itens = [(_chave_normalizada(key), _chave_normalizada(item)) for key, item in valor.items()]
        return ("objeto", "dicionario", tuple(sorted(itens, key=repr)))
    if isinstance(valor, set):
        return ("objeto", "conjunto", tuple(sorted((_chave_normalizada(item) for item in valor), key=repr)))
    return _chave_exata(valor)


def _contagens(valores: list[Any]) -> tuple[int, int, int, int, int]:
    grupos: dict[tuple[Any, ...], int] = {}
    grupos_normalizados: dict[tuple[Any, ...], set[tuple[Any, ...]]] = {}
    for valor in valores:
        exata = _chave_exata(valor)
        grupos[exata] = grupos.get(exata, 0) + 1
        grupos_normalizados.setdefault(_chave_normalizada(valor), set()).add(exata)
    duplicados = [frequencia for frequencia in grupos.values() if frequencia > 1]
    colisoes = sum(
        1 for exatas in grupos_normalizados.values() if len(exatas) > 1
    )
    return (
        len(grupos),
        len(duplicados),
        sum(frequencia - 1 for frequencia in duplicados),
        colisoes,
        len(grupos_normalizados),
    )


def _tipos(valores: list[Any]) -> list[str]:
    return sorted({_tipo(valor) for valor in valores}, key=lambda tipo: _TIPOS_ORDEM[tipo])


def _perfil_valores(valores: list[Any]) -> dict[str, Any]:
    preenchidos_valores = [valor for valor in valores if not _eh_nulo(valor)]
    distintos, grupos_duplicados, repeticoes, _colisoes, _ = _contagens(preenchidos_valores)
    tipos = _tipos(preenchidos_valores)
    return {
        "preenchidos": len(preenchidos_valores),
        "nulos": len(valores) - len(preenchidos_valores),
        "distintos": distintos,
        "grupos_duplicados": grupos_duplicados,
        "repeticoes_excedentes": repeticoes,
        # Objetos podem ser comparados, mas não são candidatos automáticos a
        # uma chave simples sem uma decisão explícita do domínio.
        "candidata_unica_na_amostra": bool(
            valores
            and len(preenchidos_valores) == len(valores)
            and distintos == len(valores)
            and "objeto" not in tipos
        ),
        "tipos": tipos,
    }


def _valores_chave(registros: list[dict[str, Any]], campos: list[str]) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    exatos: list[tuple[Any, ...]] = []
    normalizados: list[tuple[Any, ...]] = []
    for registro in registros:
        valores = [registro.get(campo) for campo in campos]
        if any(_eh_nulo(valor) for valor in valores):
            continue
        exatos.append(tuple(_chave_exata(valor) for valor in valores))
        normalizados.append(tuple(_chave_normalizada(valor) for valor in valores))
    return exatos, normalizados


def _perfil_chave(
    registros: list[dict[str, Any]],
    nome: str,
    campos: list[str],
) -> dict[str, Any]:
    for campo in campos:
        if registros and not any(campo in registro for registro in registros):
            raise _erro("chave referencia coluna inexistente na tabela")
    exatos, normalizados = _valores_chave(registros, campos)
    grupos: dict[tuple[Any, ...], int] = {}
    por_normalizacao: dict[tuple[Any, ...], set[tuple[Any, ...]]] = {}
    for exata, normalizada in zip(exatos, normalizados):
        grupos[exata] = grupos.get(exata, 0) + 1
        por_normalizacao.setdefault(normalizada, set()).add(exata)
    duplicados = [frequencia for frequencia in grupos.values() if frequencia > 1]
    incompletos = len(registros) - len(exatos)
    # Chaves compostas podem conter objetos quando a configuração é explícita;
    # a restrição de objeto é aplicada ao campo simples no perfil acima.
    candidata = bool(registros and not incompletos and len(grupos) == len(registros))
    if len(campos) == 1:
        tipos = [_tipo(registro.get(campos[0])) for registro in registros if not _eh_nulo(registro.get(campos[0]))]
        candidata = candidata and "objeto" not in tipos
    return {
        "nome": nome,
        "campos": list(campos),
        "completos": len(exatos),
        "incompletos": incompletos,
        "distintos": len(grupos),
        "grupos_duplicados": len(duplicados),
        "repeticoes_excedentes": sum(frequencia - 1 for frequencia in duplicados),
        "candidata_unica_na_amostra": candidata,
        "colisoes_normalizacao": sum(1 for exatas in por_normalizacao.values() if len(exatas) > 1),
    }


def perfilar_tabela(
    registros: list[dict[str, Any]],
    campos: list[str],
    chaves: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Perfila campos e chaves sem alterar ou devolver os registros."""

    registros = _validar_registros(registros, "registros")
    campos = _validar_campos(campos, "campos")
    chaves_validadas = _validar_chaves(chaves, campos)
    perfil_campos = []
    for campo in campos:
        perfil = {"campo": campo}
        perfil.update(_perfil_valores([registro.get(campo) for registro in registros]))
        perfil_campos.append(perfil)
    return {
        "registros": len(registros),
        "campos": perfil_campos,
        "chaves": [
            _perfil_chave(registros, chave["nome"], chave["campos"])
            for chave in chaves_validadas
        ],
    }


def _validar_relacao_campos(
    registros: list[dict[str, Any]], campos: Any, lado: str
) -> list[str]:
    campos_validados = _validar_campos(campos, f"campos_{lado}")
    if registros and not all(any(campo in registro for registro in registros) for campo in campos_validados):
        raise _erro(f"relação referencia coluna inexistente no lado {lado}")
    return campos_validados


def _chaves_relacao(
    registros: list[dict[str, Any]], campos: list[str]
) -> tuple[list[tuple[Any, ...]], int]:
    completas: list[tuple[Any, ...]] = []
    incompletos = 0
    for registro in registros:
        valores = [registro.get(campo) for campo in campos]
        if any(_eh_nulo(valor) for valor in valores):
            incompletos += 1
        else:
            completas.append(tuple(_chave_exata(valor) for valor in valores))
    return completas, incompletos


def perfilar_relacao(
    origem: list[dict[str, Any]],
    campos_origem: list[str],
    destino: list[dict[str, Any]],
    campos_destino: list[str],
) -> dict[str, Any]:
    """Compara uma relação por igualdade exata e tipada.

    A cardinalidade considera apenas grupos presentes nos dois lados.  Um
    destino duplicado é exposto em ``grupos_ambiguos_destino`` e não é tratado
    como uma FK válida, embora suas correspondências exatas sejam contadas.
    """

    origem = _validar_registros(origem, "origem")
    destino = _validar_registros(destino, "destino")
    campos_origem = _validar_relacao_campos(origem, campos_origem, "origem")
    campos_destino = _validar_relacao_campos(destino, campos_destino, "destino")
    if len(campos_origem) != len(campos_destino):
        raise _erro("relação precisa do mesmo número de campos em cada lado")
    chaves_origem, incompletos_origem = _chaves_relacao(origem, campos_origem)
    chaves_destino, incompletos_destino = _chaves_relacao(destino, campos_destino)

    grupos_origem: dict[tuple[Any, ...], int] = {}
    grupos_destino: dict[tuple[Any, ...], int] = {}
    for chave in chaves_origem:
        grupos_origem[chave] = grupos_origem.get(chave, 0) + 1
    for chave in chaves_destino:
        grupos_destino[chave] = grupos_destino.get(chave, 0) + 1
    correspondidos = set(grupos_origem) & set(grupos_destino)
    correspondentes = sum(grupos_origem[chave] for chave in correspondidos)
    correspondentes_destino = sum(grupos_destino[chave] for chave in correspondidos)
    orfaos = sum(frequencia for chave, frequencia in grupos_origem.items() if chave not in grupos_destino)
    grupos_ambiguos_destino = sum(
        1 for chave in correspondidos if grupos_destino[chave] > 1
    )
    grupos_ambiguos_destino_total = sum(
        1 for frequencia in grupos_destino.values() if frequencia > 1
    )

    if not correspondidos:
        cardinalidade = "sem_correspondencia"
    else:
        origem_muitos = any(grupos_origem[chave] > 1 for chave in correspondidos)
        destino_muitos = any(grupos_destino[chave] > 1 for chave in correspondidos)
        if origem_muitos and destino_muitos:
            cardinalidade = "N:N"
        elif origem_muitos:
            cardinalidade = "N:1"
        elif destino_muitos:
            cardinalidade = "1:N"
        else:
            cardinalidade = "1:1"

    return {
        "registros_origem": len(origem),
        "registros_destino": len(destino),
        "incompletos_origem": incompletos_origem,
        "incompletos_destino": incompletos_destino,
        "correspondentes": correspondentes,
        "correspondentes_destino": correspondentes_destino,
        "orfaos": orfaos,
        "grupos_ambiguos_destino": grupos_ambiguos_destino_total,
        "grupos_ambiguos_destino_correspondidos": grupos_ambiguos_destino,
        "grupos_correspondidos": len(correspondidos),
        "cardinalidade_observada": cardinalidade,
        "fk_valida_na_amostra": bool(correspondidos) and not grupos_ambiguos_destino_total and not orfaos and not incompletos_origem,
    }


__all__ = ["perfilar_tabela", "perfilar_relacao"]
