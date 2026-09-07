#!/usr/bin/env python3
"""Coleta uma prévia privada e somente leitura do Portfólio B3.

O módulo não conhece credenciais, não abre HTTP e não oferece comandos de
escrita. A fonte de banco é exclusivamente ``get_read`` da ponte local. As
funções puras aceitam callbacks para que o planejador e os testes não precisem
habilitar qualquer capacidade adicional.

Mensagens de WhatsApp são aceitas somente de um leitor/cache já normalizado ou
de um export privado no contrato ``mensagens-whatsapp-normalizadas-v1``. Isso
não comprova captura ativa, sincronização ou cobertura fora do período pedido.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlencode, urlsplit

from planejar_atualizacao_b3 import (
    ErroEntrada,
    _texto_normalizado,
    gerar_plano,
    gravar_privado_sem_sobrescrever,
    ler_json_privado,
)


PONTE = Path("/root/juan-severino/handlers/confinex_db_bridge.py")
SCHEMA_SNAPSHOT = "snapshot-b3-v1"
SCHEMA_MENSAGENS = "mensagens-whatsapp-normalizadas-v1"
SCHEMA_ORIGEM = "origem-pedido-telegram-v1"

# Campos declarados constituem exatamente o recorte cuja assinatura é provada.
# Campo desconhecido na base faz a consulta falhar; não há select=* nem fallback.
CAMPOS_TABELAS: dict[str, tuple[str, ...]] = {
    "posicoes_hedge": (
        "id", "referencia_bolsa", "contrato", "direcao", "categoria",
        "contratos_qtd", "preco_entrada", "data_entrada", "status",
        "preco_saida", "data_saida", "resultado_realizado",
        "custo_corretagem", "custo_finpec", "termo", "origem",
        "negocio_rateio", "detalhes", "obs", "mes", "rolada_para",
        "created_at", "updated_at",
    ),
    "alocacoes_hedge": (
        "id", "posicao_id", "operacao_id", "contratos_qtd",
        "resultado_creditado", "created_at",
    ),
}
TAMANHO_MAXIMO_RESPOSTA = 2_000_000
LIMITE_PAGINA_MAXIMO = 500
MAX_PAGINAS_MAXIMO = 50


class ColetaIndisponivel(RuntimeError):
    """Falha fechada sem incluir resposta bruta ou detalhes de infraestrutura."""


def _hash_json(valor: Any) -> str:
    serializado = json.dumps(
        valor, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def _id_opaco(valor: str) -> str:
    return "opq_" + hashlib.sha256(valor.encode("utf-8")).hexdigest()[:24]


def construir_rota(tabela: str, limite: int, offset: int) -> str:
    if tabela not in CAMPOS_TABELAS:
        raise ValueError("tabela_nao_permitida")
    if not isinstance(limite, int) or not 1 <= limite <= LIMITE_PAGINA_MAXIMO:
        raise ValueError("limite_invalido")
    if not isinstance(offset, int) or offset < 0:
        raise ValueError("offset_invalido")
    return tabela + "?" + urlencode({
        "select": ",".join(CAMPOS_TABELAS[tabela]),
        "order": "id.asc",
        "limit": str(limite),
        "offset": str(offset),
    })


def validar_rota_fixa(rota: str) -> None:
    if not isinstance(rota, str) or len(rota) > 4_096:
        raise ColetaIndisponivel("rota_recusada")
    partes = urlsplit(rota)
    tabela = partes.path
    if partes.scheme or partes.netloc or partes.fragment or tabela not in CAMPOS_TABELAS:
        raise ColetaIndisponivel("rota_recusada")
    try:
        parametros = parse_qs(partes.query, keep_blank_values=True, strict_parsing=True)
    except ValueError:
        raise ColetaIndisponivel("rota_recusada") from None
    if set(parametros) != {"select", "order", "limit", "offset"}:
        raise ColetaIndisponivel("rota_recusada")
    if any(len(valores) != 1 for valores in parametros.values()):
        raise ColetaIndisponivel("rota_recusada")
    if parametros["select"][0] != ",".join(CAMPOS_TABELAS[tabela]):
        raise ColetaIndisponivel("rota_recusada")
    if parametros["order"][0] != "id.asc":
        raise ColetaIndisponivel("rota_recusada")
    try:
        limite = int(parametros["limit"][0])
        offset = int(parametros["offset"][0])
    except ValueError:
        raise ColetaIndisponivel("rota_recusada") from None
    if not 1 <= limite <= LIMITE_PAGINA_MAXIMO or offset < 0:
        raise ColetaIndisponivel("rota_recusada")


class PonteLeitura:
    """Adaptador mínimo: somente subprocesso fixo ``get_read`` e ambiente limpo."""

    def __init__(
        self,
        caminho: Path = PONTE,
        segundos: float = 45,
        executar: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.caminho = Path(caminho)
        self._monotonic = monotonic
        self.fim = monotonic() + segundos
        self.executar = executar

    def __call__(self, rota: str) -> Any:
        validar_rota_fixa(rota)
        restante = self.fim - self._monotonic()
        if restante < 2:
            raise ColetaIndisponivel("limite_de_tempo")
        espera = min(12.0, restante - 1.0)
        try:
            processo = self.executar(
                [sys.executable, str(self.caminho), "--timeout", str(espera), "get_read", rota],
                capture_output=True,
                text=True,
                timeout=espera + 1.0,
                env={
                    "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                    "LANG": "C.UTF-8",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
            corpo, marcador, status = processo.stdout.rpartition("\nHTTP_STATUS:")
            if processo.returncode != 0 and not marcador:
                try:
                    erro = json.loads(processo.stdout)
                except (json.JSONDecodeError, TypeError):
                    erro = None
                recursos_b3 = {
                    "recurso_leitura_nao_permitido:posicoes_hedge",
                    "recurso_leitura_nao_permitido:alocacoes_hedge",
                }
                if (
                    isinstance(erro, dict)
                    and set(erro) == {"error"}
                    and isinstance(erro["error"], str)
                    and erro["error"] in recursos_b3
                ):
                    raise ColetaIndisponivel("ponte_sem_leitura_b3")
            if (
                processo.returncode != 0
                or not marcador
                or status.strip() != "200"
                or len(corpo.encode("utf-8")) > TAMANHO_MAXIMO_RESPOSTA
            ):
                raise ColetaIndisponivel("consulta_nao_confirmada")
            return json.loads(corpo)
        except ColetaIndisponivel:
            raise
        except (OSError, subprocess.SubprocessError, UnicodeError, ValueError):
            raise ColetaIndisponivel("ponte_indisponivel") from None


def _validar_linha(tabela: str, linha: Any) -> dict[str, Any]:
    campos = CAMPOS_TABELAS[tabela]
    if not isinstance(linha, dict) or set(linha) != set(campos):
        raise ColetaIndisponivel("resposta_fora_do_select_declarado")
    if not isinstance(linha["id"], str) or not linha["id"]:
        raise ColetaIndisponivel("identificador_invalido")
    return {campo: linha[campo] for campo in campos}


def _ler_tabela(
    tabela: str,
    ler: Callable[[str], Any],
    limite_pagina: int,
    max_paginas: int,
) -> list[dict[str, Any]]:
    registros: list[dict[str, Any]] = []
    ids: set[str] = set()
    for pagina in range(max_paginas):
        offset = pagina * limite_pagina
        resposta = ler(construir_rota(tabela, limite_pagina, offset))
        if not isinstance(resposta, list):
            raise ColetaIndisponivel("resposta_invalida")
        if len(resposta) > limite_pagina:
            raise ColetaIndisponivel("pagina_acima_do_limite")
        for item in resposta:
            linha = _validar_linha(tabela, item)
            if linha["id"] in ids:
                raise ColetaIndisponivel("identificador_repetido")
            ids.add(linha["id"])
            registros.append(linha)
        if len(resposta) < limite_pagina:
            return registros
    raise ColetaIndisponivel("paginacao_truncada")


def _amostra(
    ler: Callable[[str], Any], limite_pagina: int, max_paginas: int
) -> dict[str, list[dict[str, Any]]]:
    return {
        tabela: _ler_tabela(tabela, ler, limite_pagina, max_paginas)
        for tabela in ("posicoes_hedge", "alocacoes_hedge")
    }


def _snapshot_indisponivel(gerado_em: str, motivo: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_SNAPSHOT,
        "gerado_em": gerado_em,
        "cobertura": {"estado": "indisponivel", "motivo": motivo},
        "posicoes": [],
    }


def _montar_snapshot(
    amostra: dict[str, list[dict[str, Any]]],
    gerado_em: str,
    intervalo_inicio: str,
    intervalo_fim: str,
) -> dict[str, Any]:
    posicoes_raw = amostra["posicoes_hedge"]
    alocacoes_raw = amostra["alocacoes_hedge"]
    posicoes_por_id = {item["id"]: item for item in posicoes_raw}
    alocacoes_por_posicao: dict[str, list[dict[str, Any]]] = {
        item["id"]: [] for item in posicoes_raw
    }
    for alocacao in alocacoes_raw:
        posicao_id = alocacao["posicao_id"]
        if not isinstance(posicao_id, str) or posicao_id not in posicoes_por_id:
            raise ColetaIndisponivel("alocacao_orfa")
        alocacao_saida: dict[str, Any] = {
            "id_opaco": _id_opaco(alocacao["id"]),
            "posicao_ref": _id_opaco(posicao_id),
            # Privado e técnico: preserva integridade, mas não inventa código humano.
            "operacao_id_privado": alocacao["operacao_id"],
            "contratos_qtd": alocacao["contratos_qtd"],
            "resultado_creditado": alocacao["resultado_creditado"],
            "created_at": alocacao["created_at"],
        }
        alocacoes_por_posicao[posicao_id].append(alocacao_saida)

    posicoes: list[dict[str, Any]] = []
    for raw in posicoes_raw:
        item: dict[str, Any] = {
            "id_opaco": _id_opaco(raw["id"]),
            "referencia_bolsa": raw["referencia_bolsa"],
            "contrato": raw["contrato"],
            "direcao": raw["direcao"],
            "contratos_qtd": raw["contratos_qtd"],
            "preco_entrada": raw["preco_entrada"],
            "data_entrada": raw["data_entrada"],
            "status": raw["status"],
            "categoria": raw["categoria"],
            "custo_corretagem": raw["custo_corretagem"],
            "custo_finpec": raw["custo_finpec"],
            "termo": raw["termo"],
            "origem": raw["origem"],
            "negocio_rateio": raw["negocio_rateio"],
            "detalhes": raw["detalhes"],
            "mes": raw["mes"],
            "preco_saida": raw["preco_saida"],
            "data_saida": raw["data_saida"],
            "resultado_realizado": raw["resultado_realizado"],
            "rolada_para_ref": _id_opaco(raw["rolada_para"])
            if isinstance(raw["rolada_para"], str) and raw["rolada_para"]
            else None,
            "obs": raw["obs"],
            "observacao": raw["obs"],
            "created_at": raw["created_at"],
            "updated_at": raw["updated_at"],
            "alocacoes": sorted(
                alocacoes_por_posicao[raw["id"]], key=lambda valor: valor["id_opaco"]
            ),
        }
        posicoes.append(item)
    return {
        "schema_version": SCHEMA_SNAPSHOT,
        "gerado_em": gerado_em,
        "cobertura": {
            "estado": "completa",
            "escopo": "campos_selecionados_de_posicoes_hedge_e_alocacoes_hedge",
            "atomicidade": "nao_garantida",
            "atestado": True,
            "intervalo_inicio": intervalo_inicio,
            "intervalo_fim": intervalo_fim,
        },
        "posicoes": sorted(posicoes, key=lambda valor: valor["id_opaco"]),
    }


def coletar_snapshot(
    ler: Callable[[str], Any],
    *,
    limite_pagina: int = 100,
    max_paginas: int = 20,
    agora: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    """Retorna ``{snapshot, auditoria}``; qualquer incerteza esvazia o snapshot."""
    if not callable(ler):
        raise ValueError("leitor_invalido")
    if not isinstance(limite_pagina, int) or not 1 <= limite_pagina <= LIMITE_PAGINA_MAXIMO:
        raise ValueError("limite_pagina_invalido")
    if not isinstance(max_paginas, int) or not 1 <= max_paginas <= MAX_PAGINAS_MAXIMO:
        raise ValueError("max_paginas_invalido")
    relogio = agora or (lambda: datetime.now(timezone.utc))

    def instante_utc() -> str:
        instante = relogio()
        if not isinstance(instante, datetime) or instante.tzinfo is None:
            raise ValueError("relogio_sem_timezone")
        return instante.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

    intervalo_inicio = instante_utc()
    gerado_em = intervalo_inicio
    auditoria: dict[str, Any] = {
        "estado": "indisponivel",
        "escopo_assinatura": {tabela: list(campos) for tabela, campos in CAMPOS_TABELAS.items()},
        "atomicidade": "nao_garantida",
        "consultas": 0,
        "alteracao_concorrente": False,
    }
    chamadas = 0

    def ler_contado(rota: str) -> Any:
        nonlocal chamadas
        chamadas += 1
        return ler(rota)

    try:
        antes = _amostra(ler_contado, limite_pagina, max_paginas)
        depois = _amostra(ler_contado, limite_pagina, max_paginas)
        intervalo_fim = instante_utc()
        auditoria["consultas"] = chamadas
        assinaturas_antes = {t: _hash_json(antes[t]) for t in CAMPOS_TABELAS}
        assinaturas_depois = {t: _hash_json(depois[t]) for t in CAMPOS_TABELAS}
        contagens_antes = {t: len(antes[t]) for t in CAMPOS_TABELAS}
        contagens_depois = {t: len(depois[t]) for t in CAMPOS_TABELAS}
        auditoria.update({
            "assinaturas_antes": assinaturas_antes,
            "assinaturas_depois": assinaturas_depois,
            "contagens_antes": contagens_antes,
            "contagens_depois": contagens_depois,
        })
        if assinaturas_antes != assinaturas_depois or contagens_antes != contagens_depois:
            auditoria.update({"estado": "alteracao_concorrente", "alteracao_concorrente": True})
            return {
                "snapshot": _snapshot_indisponivel(gerado_em, "alteracao_concorrente_no_recorte"),
                "auditoria": auditoria,
            }
        snapshot = _montar_snapshot(depois, gerado_em, intervalo_inicio, intervalo_fim)
        auditoria["estado"] = "estavel_em_duas_amostras_no_recorte"
        conteudo_estavel = {chave: valor for chave, valor in snapshot.items() if chave != "gerado_em"}
        conteudo_estavel["cobertura"] = {
            chave: valor for chave, valor in snapshot["cobertura"].items()
            if chave not in {"intervalo_inicio", "intervalo_fim"}
        }
        auditoria["snapshot_hash"] = _hash_json(conteudo_estavel)
        return {"snapshot": snapshot, "auditoria": auditoria}
    except (ColetaIndisponivel, OSError, TimeoutError, ValueError) as exc:
        auditoria["consultas"] = chamadas
        motivo = str(exc) if isinstance(exc, ColetaIndisponivel) else "consulta_indisponivel"
        auditoria["motivo"] = motivo
        return {
            "snapshot": _snapshot_indisponivel(gerado_em, motivo),
            "auditoria": auditoria,
        }


def validar_origem(origem: Any) -> dict[str, Any]:
    obrigatorios = {"schema_version", "canal", "conversa_ref", "mensagem_ref"}
    opcionais = {"timestamp", "autor_ref", "contexto_nome"}
    if not isinstance(origem, dict) or not obrigatorios <= set(origem) <= obrigatorios | opcionais:
        raise ValueError("origem_invalida")
    if origem["schema_version"] != SCHEMA_ORIGEM or origem["canal"] != "telegram":
        raise ValueError("origem_invalida")
    if not all(isinstance(origem[chave], str) and origem[chave] for chave in ("conversa_ref", "mensagem_ref")):
        raise ValueError("origem_invalida")
    return dict(origem)


def normalizar_mensagens(
    dados: Any,
    *,
    conversa_ref: str,
    inicio: str,
    fim: str,
    limite: int = 200,
) -> dict[str, Any]:
    """Valida e limita um export/cache que já usa o contrato normalizado v1."""
    if not all(isinstance(valor, str) and valor for valor in (conversa_ref, inicio, fim)):
        raise ValueError("recorte_mensagens_invalido")
    if not isinstance(limite, int) or not 1 <= limite <= 1_000:
        raise ValueError("limite_mensagens_invalido")
    if not isinstance(dados, dict) or dados.get("schema_version") != SCHEMA_MENSAGENS:
        raise ValueError("export_mensagens_nao_normalizado")
    cobertura = dados.get("cobertura")
    mensagens = dados.get("mensagens")
    if not isinstance(cobertura, dict) or cobertura.get("estado") not in {"completa", "parcial", "indisponivel"}:
        raise ValueError("cobertura_mensagens_invalida")
    if not isinstance(mensagens, list):
        raise ValueError("mensagens_invalidas")
    def data_utc(valor: str) -> datetime:
        try:
            data = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("timestamp_mensagens_invalido") from None
        if data.tzinfo is None:
            raise ValueError("timestamp_mensagens_sem_timezone")
        return data.astimezone(timezone.utc)

    data_inicio = data_utc(inicio)
    data_fim = data_utc(fim)
    if data_inicio > data_fim:
        raise ValueError("intervalo_mensagens_invertido")
    inicio_utc = data_inicio.isoformat().replace("+00:00", "Z")
    fim_utc = data_fim.isoformat().replace("+00:00", "Z")
    saida: list[dict[str, Any]] = []
    vistos: set[tuple[str, str | None, str]] = set()
    timestamp_invalido = False
    identidade_pendente = False
    campos = {"conversa_ref", "mensagem_ref", "timestamp", "texto", "hash_conteudo"}
    for mensagem in mensagens:
        if not isinstance(mensagem, dict) or not set(mensagem) <= campos:
            raise ValueError("mensagem_invalida")
        if mensagem.get("conversa_ref") != conversa_ref or not isinstance(mensagem.get("texto"), str):
            raise ValueError("mensagem_fora_da_conversa_exata")
        timestamp = mensagem.get("timestamp")
        if not isinstance(timestamp, str) or not timestamp:
            timestamp_invalido = True
            continue
        try:
            instante = data_utc(timestamp)
        except ValueError:
            timestamp_invalido = True
            continue
        if instante < data_inicio or instante > data_fim:
            continue
        texto = _texto_normalizado(mensagem["texto"])
        if not texto:
            raise ValueError("mensagem_sem_texto")
        hash_conteudo = hashlib.sha256(texto.encode("utf-8")).hexdigest()
        hash_recebido = mensagem.get("hash_conteudo")
        if hash_recebido is not None and hash_recebido != hash_conteudo:
            raise ValueError("hash_mensagem_divergente")
        mensagem_ref = mensagem.get("mensagem_ref")
        if mensagem_ref is not None and (not isinstance(mensagem_ref, str) or not mensagem_ref):
            raise ValueError("mensagem_ref_invalida")
        if mensagem_ref is None:
            # Texto igual em horários diferentes pode representar operações reais
            # distintas; sem ID não há base para deduplicar as ocorrências.
            identidade_pendente = True
        else:
            identidade = (conversa_ref, mensagem_ref, hash_conteudo)
            if identidade in vistos:
                continue
            vistos.add(identidade)
        item = {
            "conversa_ref": conversa_ref,
            "mensagem_ref": mensagem_ref,
            "timestamp": instante.isoformat().replace("+00:00", "Z"),
            "texto": texto,
            "hash_conteudo": hash_conteudo,
        }
        saida.append(item)
    saida.sort(key=lambda item: (item.get("timestamp") or "", item.get("mensagem_ref") or item["hash_conteudo"]))
    truncada = len(saida) > limite
    inicio_fonte = cobertura.get("intervalo_inicio")
    fim_fonte = cobertura.get("intervalo_fim")
    fonte_atestada = False
    if cobertura["estado"] == "completa" and cobertura.get("atestado") is True:
        try:
            fonte_atestada = (
                data_utc(str(inicio_fonte)) == data_inicio
                and data_utc(str(fim_fonte)) == data_fim
            )
        except ValueError:
            fonte_atestada = False
    estado = cobertura["estado"]
    if estado == "completa" and not fonte_atestada:
        estado = "parcial"
    if (truncada or timestamp_invalido or identidade_pendente) and estado == "completa":
        estado = "parcial"
    return {
        "schema_version": SCHEMA_MENSAGENS,
        "cobertura": {
            "estado": estado,
            "fonte": "cache_ou_export_privado_previamente_normalizado",
            "captura_ativa_confirmada": False,
            "intervalo_inicio": inicio_utc,
            "intervalo_fim": fim_utc,
            "atestado": estado == "completa" and fonte_atestada,
            "identidade_pendente": identidade_pendente,
            **({"motivo": "timestamp_ausente_ou_invalido"} if timestamp_invalido else
               {"motivo": "limite_de_mensagens"} if truncada else
               {"motivo": "mensagem_sem_identidade_comprovada"} if identidade_pendente else
               {"motivo": "cobertura_do_export_nao_atestada"}
               if cobertura["estado"] == "completa" and not fonte_atestada else {}),
        },
        "mensagens": saida[:limite],
    }


def mensagens_indisponiveis() -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_MENSAGENS,
        "cobertura": {
            "estado": "indisponivel",
            "motivo": "cache_ou_export_normalizado_nao_fornecido",
            "captura_ativa_confirmada": False,
        },
        "mensagens": [],
    }


def coletar_previa_b3(
    ler: Callable[[str], Any],
    origem: Any,
    *,
    mensagens: dict[str, Any] | None = None,
    limite_pagina: int = 100,
    max_paginas: int = 20,
    agora: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    coleta = coletar_snapshot(
        ler, limite_pagina=limite_pagina, max_paginas=max_paginas, agora=agora
    )
    pacote = {
        "snapshot": coleta["snapshot"],
        "mensagens": mensagens if mensagens is not None else mensagens_indisponiveis(),
        "origem": validar_origem(origem),
        "metadados": {
            "modo": "somente_leitura",
            "autoriza_escrita": False,
            "escritas": 0,
            "atualizacao_operacional": False,
            "auditoria_snapshot": coleta["auditoria"],
        },
    }
    return pacote


def _ler_json_limitado(caminho: Path, limite_bytes: int = 1_000_000) -> Any:
    try:
        tamanho = caminho.lstat().st_size
    except OSError:
        raise ValueError("arquivo_json_indisponivel") from None
    if tamanho > limite_bytes:
        raise ValueError("arquivo_json_acima_do_limite")
    return ler_json_privado(caminho)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prévia B3 somente leitura; grava apenas o plano privado solicitado."
    )
    parser.add_argument("--origem-json", required=True, type=Path)
    parser.add_argument("--mensagens-json", type=Path)
    parser.add_argument("--conversa-ref")
    parser.add_argument("--intervalo-inicio")
    parser.add_argument("--intervalo-fim")
    parser.add_argument("--limite-mensagens", type=int, default=200)
    parser.add_argument("--saida", type=Path)
    parser.add_argument("--limite-pagina", type=int, default=100)
    parser.add_argument("--max-paginas", type=int, default=20)
    args = parser.parse_args()
    try:
        origem = _ler_json_limitado(args.origem_json)
        recorte_informado = all((args.conversa_ref, args.intervalo_inicio, args.intervalo_fim))
        if args.mensagens_json and not recorte_informado:
            raise ValueError("recorte_mensagens_obrigatorio")
        if not args.mensagens_json and any((args.conversa_ref, args.intervalo_inicio, args.intervalo_fim)):
            raise ValueError("mensagens_json_obrigatorio_para_recorte")
        mensagens = mensagens_indisponiveis()
        if args.mensagens_json:
            mensagens = normalizar_mensagens(
                _ler_json_limitado(args.mensagens_json, 8_000_000),
                conversa_ref=args.conversa_ref,
                inicio=args.intervalo_inicio,
                fim=args.intervalo_fim,
                limite=args.limite_mensagens,
            )
        pacote = coletar_previa_b3(
            PonteLeitura(),
            origem,
            mensagens=mensagens,
            limite_pagina=args.limite_pagina,
            max_paginas=args.max_paginas,
        )
        plano = gerar_plano(pacote["snapshot"], pacote["mensagens"], pacote["origem"])
        gravacao = None
        if args.saida:
            gravacao = gravar_privado_sem_sobrescrever(args.saida, plano)
        snapshot = pacote["snapshot"]
        auditoria = pacote["metadados"]["auditoria_snapshot"]
        print(json.dumps({
            "modo": "somente_leitura",
            "estado": snapshot["cobertura"]["estado"],
            "posicoes": len(snapshot["posicoes"]),
            "alocacoes": sum(len(item["alocacoes"]) for item in snapshot["posicoes"]),
            "snapshot_hash": auditoria.get("snapshot_hash"),
            "alteracao_concorrente": auditoria["alteracao_concorrente"],
            "mensagens_estado": pacote["mensagens"]["cobertura"]["estado"],
            "plano_id": plano["plano_id"],
            "saida": gravacao,
            "escritas": 0,
        }, ensure_ascii=False, sort_keys=True))
        return 0 if snapshot["cobertura"]["estado"] == "completa" else 2
    except (OSError, ValueError, json.JSONDecodeError, ErroEntrada):
        print(json.dumps({"estado": "indisponivel", "escritas": 0}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
