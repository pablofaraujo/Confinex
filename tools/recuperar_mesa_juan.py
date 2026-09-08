#!/usr/bin/env python3
"""Prepara trechos privados da mesa para contexto não confiável do Juan.

O módulo não envia mensagens, não consulta modelo, não acessa rede e não possui
capacidade operacional. A autorização da fonte é externa a texto do pedido e
deve vir de uma identidade autenticada comparada a uma allowlist privada.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from coletar_previa_b3 import normalizar_mensagens
from ler_cache_wey_b3 import SCHEMA_MANIFESTO, ler_cache_wey_b3_manifesto
from recuperar_textos_mesa import recuperar_textos_mesa


SCHEMA_RECUPERACAO = "textos-mesa-recuperados-v1"
SCHEMA_SAIDA = "contexto-mesa-juan-v1"
SCHEMA_CONFIG = "allowlist-recuperacao-mesa-juan-v1"
CONFIG_PADRAO = Path("/etc/confinex/recuperacao-mesa-juan.json")
LIMITE_TRECHOS_MAXIMO = 16
LIMITE_STRING = 1_800
LIMITE_BYTES_TOTAL_MAXIMO = 48_000
TERMOS_DOMINIO = ("b3", "portfolio", "portfólio", "hedge")
TERMOS_FONTE = ("whatsapp", "conversa", "wey")
AUTORIAS = frozenset({"titular", "interlocutor", "nao_informada"})
CHAVES_TELEGRAM = frozenset({
    "senderId", "chatId", "kind", "threadId", "accountId",
    "routeSessionKey", "mainSessionKey",
})


class RecuperacaoMesaInvalida(ValueError):
    """Falha fechada identificada somente por código sanitizado."""


def _falhar(codigo: str) -> None:
    raise RecuperacaoMesaInvalida(codigo)


def _normalizar(texto: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", texto).casefold().split())


def _contem_literal(texto: str, literal: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(literal) + r"(?!\w)", texto) is not None


def selecionar_alias_pedido(texto: Any, aliases: Any) -> str | None:
    """Retorna um único alias explicitamente pedido, sem inferência fuzzy."""
    if not isinstance(texto, str) or not texto or len(texto.encode("utf-8")) > 16_000:
        _falhar("texto_pedido_invalido")
    if not isinstance(aliases, (list, tuple)) or not 1 <= len(aliases) <= 20:
        _falhar("aliases_invalidos")
    normalizado = _normalizar(texto)
    aliases_normalizados: dict[str, str] = {}
    for alias in aliases:
        if (
            not isinstance(alias, str) or not alias.strip()
            or len(alias.encode("utf-8")) > 160
        ):
            _falhar("alias_invalido")
        chave = _normalizar(alias)
        if chave in aliases_normalizados:
            _falhar("alias_duplicado")
        aliases_normalizados[chave] = alias
    tem_dominio = any(_contem_literal(normalizado, _normalizar(termo)) for termo in TERMOS_DOMINIO)
    tem_fonte = any(_contem_literal(normalizado, _normalizar(termo)) for termo in TERMOS_FONTE)
    if not tem_dominio or not tem_fonte:
        return None
    encontrados = [
        alias for chave, alias in aliases_normalizados.items()
        if _contem_literal(normalizado, chave)
    ]
    if len(encontrados) > 1:
        _falhar("contato_ambiguo")
    return encontrados[0] if encontrados else None


def _resposta_nao_aplicavel() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_SAIDA,
        "status": "nao_aplicavel",
        "autoriza_escrita": False,
        "escritas": 0,
        "modelo_acionado": False,
        "atualizacao_operacional": False,
    }


def _normalizar_alias_macos(caminho: Path) -> Path:
    absoluto = caminho.absolute()
    if sys.platform != "darwin" or len(absoluto.parts) < 2:
        return absoluto
    esperado = {"tmp": "/private/tmp", "var": "/private/var"}.get(absoluto.parts[1])
    if esperado and os.path.realpath("/" + absoluto.parts[1]) == esperado:
        return Path(esperado, *absoluto.parts[2:])
    return absoluto


def _caminho_config_seguro(caminho: Path) -> os.stat_result:
    if not isinstance(caminho, Path) or not caminho.is_absolute():
        _falhar("config_privada_invalida")
    atual = _normalizar_alias_macos(caminho)
    while True:
        try:
            if stat.S_ISLNK(atual.lstat().st_mode):
                _falhar("config_privada_invalida")
        except OSError:
            _falhar("config_privada_indisponivel")
        if atual.parent == atual:
            break
        atual = atual.parent
    atual = caminho.parent
    while True:
        try:
            if (atual / ".git").exists():
                _falhar("config_privada_invalida")
        except OSError:
            _falhar("config_privada_invalida")
        if atual.parent == atual:
            break
        atual = atual.parent
    try:
        dados = caminho.lstat()
    except OSError:
        _falhar("config_privada_indisponivel")
    if (
        not stat.S_ISREG(dados.st_mode) or dados.st_uid != os.geteuid()
        or stat.S_IMODE(dados.st_mode) != 0o600
    ):
        _falhar("config_privada_invalida")
    if dados.st_size > 64_000:
        _falhar("config_privada_acima_limite")
    return dados


def _validar_telegram(valor: Any) -> dict[str, str | None]:
    if not isinstance(valor, dict) or set(valor) != CHAVES_TELEGRAM:
        _falhar("identidade_telegram_invalida")
    saida: dict[str, str | None] = {}
    for chave in CHAVES_TELEGRAM:
        item = valor.get(chave)
        if chave == "threadId" and item is None:
            saida[chave] = None
            continue
        if not isinstance(item, str) or not item or len(item.encode("utf-8")) > 512:
            _falhar("identidade_telegram_invalida")
        saida[chave] = item
    if saida["kind"] not in {"direct", "group"}:
        _falhar("identidade_telegram_invalida")
    return saida


def validar_configuracao(config: Any) -> list[dict[str, Any]]:
    if not isinstance(config, dict) or set(config) != {"schema_version", "vinculos"}:
        _falhar("config_invalida")
    vinculos = config.get("vinculos")
    if config.get("schema_version") != SCHEMA_CONFIG or not isinstance(vinculos, list) or not 1 <= len(vinculos) <= 20:
        _falhar("config_invalida")
    saida: list[dict[str, Any]] = []
    identidades: set[str] = set()
    aliases: set[str] = set()
    for vinculo in vinculos:
        if not isinstance(vinculo, dict) or set(vinculo) != {"alias", "telegram", "wey", "janela_dias"}:
            _falhar("config_invalida")
        alias = vinculo.get("alias")
        janela = vinculo.get("janela_dias")
        wey = vinculo.get("wey")
        if (
            not isinstance(alias, str) or not alias.strip() or len(alias.encode("utf-8")) > 160
            or isinstance(janela, bool) or not isinstance(janela, int) or not 1 <= janela <= 31
            or not isinstance(wey, dict) or set(wey) != {"db_path", "chat_jid"}
            or not isinstance(wey.get("db_path"), str) or not wey["db_path"]
            or not isinstance(wey.get("chat_jid"), str) or not wey["chat_jid"]
        ):
            _falhar("config_invalida")
        telegram = _validar_telegram(vinculo.get("telegram"))
        identidade = json.dumps(telegram, sort_keys=True, separators=(",", ":"))
        alias_normalizado = _normalizar(alias)
        if identidade in identidades or alias_normalizado in aliases:
            _falhar("config_duplicada")
        identidades.add(identidade)
        aliases.add(alias_normalizado)
        saida.append({
            "alias": alias.strip(), "telegram": telegram, "janela_dias": janela,
            "wey": {"db_path": wey["db_path"], "chat_jid": wey["chat_jid"]},
        })
    return saida


def ler_configuracao_privada(caminho: Path) -> list[dict[str, Any]]:
    _caminho_config_seguro(caminho)
    try:
        config = json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        _falhar("config_invalida")
    return validar_configuracao(config)


def selecionar_vinculo(
    entrada: Any, vinculos: list[dict[str, Any]],
) -> tuple[dict[str, Any], str] | None:
    if not isinstance(entrada, dict) or set(entrada) != {"chave_sessao", "texto", "telegram"}:
        _falhar("entrada_invalida")
    chave = entrada.get("chave_sessao")
    texto = entrada.get("texto")
    if not isinstance(chave, str) or not chave or not isinstance(texto, str):
        _falhar("entrada_invalida")
    telegram = _validar_telegram(entrada.get("telegram"))
    if telegram["routeSessionKey"] != chave:
        _falhar("identidade_telegram_invalida")
    correspondentes = [item for item in vinculos if item["telegram"] == telegram]
    if len(correspondentes) > 1:
        _falhar("identidade_ambigua")
    if not correspondentes:
        return None
    vinculo = correspondentes[0]
    alias = selecionar_alias_pedido(texto, [vinculo["alias"]])
    return (vinculo, texto) if alias is not None else None


def _timestamp(valor: Any) -> datetime:
    if not isinstance(valor, str) or not valor or len(valor) > 64:
        _falhar("timestamp_trecho_invalido")
    try:
        data = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError:
        _falhar("timestamp_trecho_invalido")
    if data.tzinfo is None:
        _falhar("timestamp_trecho_invalido")
    return data


def _validar_recuperacao(
    recuperacao: Any,
) -> tuple[list[dict[str, Any]], set[str], dict[str, Any], dict[str, Any]]:
    if not isinstance(recuperacao, dict) or recuperacao.get("schema_version") != SCHEMA_RECUPERACAO:
        _falhar("recuperacao_invalida")
    if recuperacao.get("autoriza_escrita") is not False or recuperacao.get("atualizacao_operacional") is not False:
        _falhar("recuperacao_invalida")
    textos = recuperacao.get("textos")
    blocos = recuperacao.get("blocos")
    cobertura = recuperacao.get("cobertura")
    if not isinstance(textos, list) or len(textos) > 1_000 or not isinstance(blocos, list) or len(blocos) > 1_000:
        _falhar("recuperacao_invalida")
    if not isinstance(cobertura, dict) or cobertura.get("estado") not in {"parcial", "completa", "indisponivel"}:
        _falhar("cobertura_invalida")
    inicio = cobertura.get("intervalo_inicio")
    fim = cobertura.get("intervalo_fim")
    _timestamp(inicio)
    _timestamp(fim)
    diagnostico_fonte = cobertura.get("diagnostico_sanitizado", {})
    chaves_diagnostico = {
        "omitidas_sem_texto", "omitidas_tamanho", "omitidas_anexo_sem_texto",
        "omitidas_por_estado", "editadas_sem_historico", "truncada_quantidade",
        "truncada_bytes",
    }
    if not isinstance(diagnostico_fonte, dict) or not set(diagnostico_fonte) <= chaves_diagnostico:
        _falhar("diagnostico_fonte_invalido")
    for chave, valor in diagnostico_fonte.items():
        valido = isinstance(valor, bool) if chave.startswith("truncada_") else (
            isinstance(valor, int) and not isinstance(valor, bool) and 0 <= valor <= 1_000_000
        )
        if not valido:
            _falhar("diagnostico_fonte_invalido")
    for chave in (
        "atestado", "identidade_pendente", "truncada", "captura_ativa_confirmada",
        "corte_por_limite", "omissoes_fonte_declaradas",
    ):
        if chave in cobertura and not isinstance(cobertura[chave], bool):
            _falhar("cobertura_invalida")
    cobertura_fonte = {
        "estado": cobertura["estado"],
        "intervalo_inicio": inicio,
        "intervalo_fim": fim,
        "atestado": cobertura.get("atestado") is True,
        "identidade_pendente": cobertura.get("identidade_pendente") is True,
        "truncada": cobertura.get("truncada") is True,
        "captura_ativa_confirmada": cobertura.get("captura_ativa_confirmada") is True,
    }
    campos_texto = {
        "texto_ref", "mensagem_ref", "timestamp", "texto", "hash_conteudo",
        "origem_autoria", "realces",
    }
    validados: list[dict[str, Any]] = []
    refs: set[str] = set()
    bytes_entrada = 0
    for item in textos:
        if not isinstance(item, dict) or set(item) != campos_texto:
            _falhar("trecho_invalido")
        texto_ref = item.get("texto_ref")
        texto = item.get("texto")
        autoria = item.get("origem_autoria")
        realces = item.get("realces")
        mensagem_ref = item.get("mensagem_ref")
        hash_conteudo = item.get("hash_conteudo")
        texto_bytes = len(texto.encode("utf-8")) if isinstance(texto, str) else 0
        if (
            not isinstance(texto_ref, str) or not texto_ref or len(texto_ref) > 128
            or texto_ref in refs or not isinstance(texto, str) or not texto
            or texto_bytes > 64_000 or bytes_entrada + texto_bytes > 4_000_000
            or (mensagem_ref is not None and (not isinstance(mensagem_ref, str) or not mensagem_ref))
            or not isinstance(hash_conteudo, str)
            or hash_conteudo != hashlib.sha256(texto.encode("utf-8")).hexdigest()
            or not isinstance(autoria, str) or autoria not in AUTORIAS
            or not isinstance(realces, list) or len(realces) > 40
        ):
            _falhar("trecho_invalido")
        for realce in realces:
            if (
                not isinstance(realce, dict) or set(realce) != {"termo", "origem"}
                or not isinstance(realce.get("termo"), str) or not realce["termo"]
                or realce.get("origem") not in {"informal", "fornecido"}
            ):
                _falhar("trecho_invalido")
        refs.add(texto_ref)
        bytes_entrada += texto_bytes
        validados.append({
            "texto_ref": texto_ref,
            "timestamp": item["timestamp"],
            "origem_autoria": autoria,
            "texto": texto,
            "_instante": _timestamp(item["timestamp"]),
        })
    prioritarias: set[str] = set()
    for bloco in blocos:
        if not isinstance(bloco, dict) or set(bloco) != {"bloco_ref", "texto_refs", "realces_refs"}:
            _falhar("bloco_invalido")
        texto_refs = bloco.get("texto_refs")
        realces_refs = bloco.get("realces_refs")
        if (
            not isinstance(texto_refs, list) or not isinstance(realces_refs, list)
            or len(texto_refs) > 1_000 or len(realces_refs) > len(texto_refs)
            or len(set(texto_refs)) != len(texto_refs) or len(set(realces_refs)) != len(realces_refs)
            or any(not isinstance(ref, str) or ref not in refs for ref in texto_refs + realces_refs)
            or not set(realces_refs) <= set(texto_refs)
        ):
            _falhar("bloco_invalido")
        prioritarias.update(texto_refs)
    return validados, prioritarias, cobertura_fonte, dict(sorted(diagnostico_fonte.items()))


def selecionar_trechos(
    recuperacao: Any,
    *,
    limite_trechos: int = 16,
    limite_bytes_total: int = 24_000,
) -> dict[str, Any]:
    """Prioriza blocos realçados e completa com mensagens recentes inteiras."""
    if (
        isinstance(limite_trechos, bool) or not isinstance(limite_trechos, int)
        or not 1 <= limite_trechos <= LIMITE_TRECHOS_MAXIMO
        or isinstance(limite_bytes_total, bool) or not isinstance(limite_bytes_total, int)
        or not 1 <= limite_bytes_total <= LIMITE_BYTES_TOTAL_MAXIMO
    ):
        _falhar("limites_saida_invalidos")
    textos, prioritarias, cobertura_fonte, diagnostico_fonte = _validar_recuperacao(recuperacao)
    cronologicos = sorted(textos, key=lambda item: (item["_instante"], item["texto_ref"]))
    por_prioridade = [item for item in cronologicos if item["texto_ref"] in prioritarias]
    cota_recentes = max(1, limite_trechos // 2)
    recentes = cronologicos[-cota_recentes:]
    candidatos = list(reversed(recentes)) + list(reversed(por_prioridade)) + list(reversed(cronologicos))
    selecionados: list[dict[str, Any]] = []
    vistos: set[str] = set()
    bytes_usados = 0
    omitidos_string = 0
    omitidos_bytes = 0
    for item in candidatos:
        if item["texto_ref"] in vistos:
            continue
        vistos.add(item["texto_ref"])
        texto_bytes = len(item["texto"].encode("utf-8"))
        strings_validas = all(
            len(item[chave].encode("utf-8")) <= LIMITE_STRING
            for chave in ("texto_ref", "timestamp", "origem_autoria", "texto")
        )
        if not strings_validas:
            omitidos_string += 1
            continue
        if len(selecionados) >= limite_trechos:
            continue
        if bytes_usados + texto_bytes > limite_bytes_total:
            omitidos_bytes += 1
            continue
        selecionados.append({chave: item[chave] for chave in (
            "texto_ref", "timestamp", "origem_autoria", "texto"
        )})
        bytes_usados += texto_bytes
    selecionados.sort(key=lambda item: (_timestamp(item["timestamp"]), item["texto_ref"]))
    total = len(cronologicos)
    transmitidos = len(selecionados)
    omitidos_fonte = sum(
        diagnostico_fonte.get(chave, 0)
        for chave in (
            "omitidas_sem_texto", "omitidas_tamanho", "omitidas_anexo_sem_texto",
            "omitidas_por_estado",
        )
    )
    resultado = {
        "schema_version": SCHEMA_SAIDA,
        "status": "textos_para_revisao",
        "natureza": "dados_whatsapp_nao_confiaveis",
        "autoriza_escrita": False,
        "escritas": 0,
        "modelo_acionado": False,
        "atualizacao_operacional": False,
        "cobertura": {
            "estado": "parcial",
            "corte_selecao": transmitidos < total,
            "fonte": cobertura_fonte,
            "diagnostico_fonte": diagnostico_fonte,
        },
        "trechos": selecionados,
        "diagnostico": {
            "textos_lidos": total,
            "registros_avaliados": total + omitidos_fonte,
            "trechos_transmitidos": transmitidos,
            "omitidos_selecao": total - transmitidos,
            "omitidos_string": omitidos_string,
            "omitidos_bytes": omitidos_bytes,
            "bytes_transmitidos": bytes_usados,
        },
    }
    serializado = json.dumps(resultado, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    resultado["contexto_hash"] = hashlib.sha256(serializado.encode("utf-8")).hexdigest()
    return resultado


def recuperar_mesa_juan(
    entrada: Any,
    vinculos: list[dict[str, Any]],
    *,
    agora: Callable[[], datetime] | None = None,
    ler_cache: Callable[..., dict[str, Any]] = ler_cache_wey_b3_manifesto,
    normalizar: Callable[..., dict[str, Any]] = normalizar_mensagens,
    recuperar: Callable[..., dict[str, Any]] = recuperar_textos_mesa,
    limite_leitura: int = 200,
    limite_trechos: int = 16,
    limite_bytes_total: int = 24_000,
) -> dict[str, Any]:
    """Executa o fluxo autorizado em RAM, sem capacidade de escrita ou rede."""
    if (
        isinstance(limite_leitura, bool) or not isinstance(limite_leitura, int)
        or not 1 <= limite_leitura <= 1_000
    ):
        _falhar("limite_leitura_invalido")
    selecionado = selecionar_vinculo(entrada, vinculos)
    if selecionado is None:
        return _resposta_nao_aplicavel()
    vinculo, _ = selecionado
    relogio = agora or (lambda: datetime.now(timezone.utc))
    fim_data = relogio()
    if not isinstance(fim_data, datetime) or fim_data.tzinfo is None:
        _falhar("relogio_invalido")
    fim_data = fim_data.astimezone(timezone.utc)
    inicio_data = fim_data - timedelta(days=vinculo["janela_dias"])
    inicio = inicio_data.isoformat().replace("+00:00", "Z")
    fim = fim_data.isoformat().replace("+00:00", "Z")
    leitura = ler_cache(
        {
            "schema_version": SCHEMA_MANIFESTO,
            "db_path": vinculo["wey"]["db_path"],
            "chat_jid": vinculo["wey"]["chat_jid"],
        },
        inicio=inicio,
        fim=fim,
        limite=limite_leitura,
        segundos=5.0,
        max_bytes_mensagem=16_000,
        max_bytes_total=1_000_000,
    )
    if not isinstance(leitura, dict) or set(leitura) != {"documento", "conversa_ref", "metadados"}:
        _falhar("leitura_cache_invalida")
    conversa_ref = leitura.get("conversa_ref")
    if not isinstance(conversa_ref, str) or not conversa_ref:
        _falhar("leitura_cache_invalida")
    mensagens = normalizar(
        leitura["documento"], conversa_ref=conversa_ref,
        inicio=inicio, fim=fim, limite=limite_leitura,
    )
    recuperacao = recuperar(
        mensagens, conversa_ref=conversa_ref,
        inicio=inicio, fim=fim, limite=limite_leitura,
        contexto_adjacente=2, termos=(),
    )
    return selecionar_trechos(
        recuperacao, limite_trechos=limite_trechos,
        limite_bytes_total=limite_bytes_total,
    )


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Recuperação privada e somente leitura de textos da mesa para Juan."
    )
    parser.add_argument("--entrada-stdin", action="store_true", required=True)
    parser.add_argument("--config", type=Path, default=CONFIG_PADRAO)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    try:
        bruto = sys.stdin.buffer.read(64_001)
        if len(bruto) > 64_000:
            _falhar("entrada_acima_limite")
        try:
            entrada = json.loads(bruto.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            _falhar("entrada_invalida")
        vinculos = ler_configuracao_privada(args.config)
        resultado = recuperar_mesa_juan(entrada, vinculos)
    except Exception as erro:
        codigo = str(erro) if isinstance(erro, RecuperacaoMesaInvalida) else "recuperacao_indisponivel"
        resultado = {
            "schema_version": SCHEMA_SAIDA,
            "status": "recuperacao_indisponivel",
            "codigo": codigo if re.fullmatch(r"[a-z0-9_]+", codigo) else "recuperacao_indisponivel",
            "autoriza_escrita": False,
            "escritas": 0,
            "modelo_acionado": False,
            "atualizacao_operacional": False,
            "cobertura": {"estado": "indisponivel"},
        }
    print(json.dumps(resultado, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
