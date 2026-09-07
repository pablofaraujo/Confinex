#!/usr/bin/env python3
"""Gera uma prévia privada e offline de possíveis atualizações do Portfólio B3.

Este módulo é deliberadamente incapaz de executar a atualização: não possui
cliente de rede, banco, subprocesso, fila ou capacidade de execução. Ele cruza
somente arquivos JSON já coletados, preserva ambiguidades e produz um contrato
determinístico que um futuro adaptador da central de investigações poderá ler.
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
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


VERSAO_PLANEJADOR = "planejador-atualizacao-b3-v1.0.0"
SCHEMA_SNAPSHOT = "snapshot-b3-v1"
SCHEMA_MENSAGENS = "mensagens-whatsapp-normalizadas-v1"
SCHEMA_ORIGEM = "origem-pedido-telegram-v1"
SCHEMA_PLANO = "previa-atualizacao-b3-v1"
ESTADOS_COBERTURA = frozenset({"completa", "parcial", "indisponivel", "vazia"})
REFERENCIA_B3_EXATA = re.compile(
    r"(?<![A-Z0-9_])B3\s*[-–—_/]\s*(\d{2})\s*[-–—_/]\s*(\d{1,6})(?![A-Z0-9_–—/-])",
    re.IGNORECASE,
)
REFERENCIA_B3_CANONICA = re.compile(r"^B3-\d{2}-\d{3,6}$")
LIMITE_TRECHO = 800
LIMITE_ARQUIVO_BYTES = 20 * 1024 * 1024
LIMITE_POSICOES = 10_000
LIMITE_MENSAGENS = 50_000
LIMITE_TEXTO_MENSAGEM = 20_000

ACOES_PADROES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("encerrar_parcial", re.compile(
        r"\b(?:fech|encerr)\w*\b.{0,35}\bparcial\w*\b|"
        r"\bparcial\w*\b.{0,35}\b(?:fech|encerr)\w*\b", re.IGNORECASE)),
    ("rolar", re.compile(r"\b(?:rolar|rolei|rolagem|rolad[ao])\b", re.IGNORECASE)),
    ("encerrar", re.compile(r"\b(?:fechar|fechei|fechou|fechado|fechamento|encerrar|encerrei|encerrad[ao]|liquidar|liquidei)\b", re.IGNORECASE)),
    ("abrir", re.compile(r"\b(?:abri|abrir|aberta|aberto|nova\s+posi[cç][aã]o|nova\s+opera[cç][aã]o)\b", re.IGNORECASE)),
)

CAMPOS_ACAO = {
    "abrir": ("contrato", "direcao", "contratos_qtd", "preco_entrada", "data_entrada"),
    "encerrar": ("preco_saida", "data_saida"),
    "encerrar_parcial": ("quantidade_encerrada", "preco_saida", "data_saida"),
    "rolar": ("novo_contrato", "preco_saida", "preco_entrada_nova", "data_saida"),
}

@dataclass
class ErroEntrada(Exception):
    codigo: str
    detalhe: str

    def __str__(self) -> str:
        return f"{self.codigo}: {self.detalhe}"


def _canonico(valor: Any) -> str:
    return json.dumps(valor, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def _sha(valor: Any) -> str:
    return hashlib.sha256(_canonico(valor).encode("utf-8")).hexdigest()


def _texto_normalizado(texto: Any) -> str:
    bruto = unicodedata.normalize("NFKC", str(texto or ""))
    limpo = "".join(c if c in "\n\t" or ord(c) >= 32 else " " for c in bruto)
    return re.sub(r"[ \t]+", " ", limpo).strip()


def _trecho_privado(texto: str) -> str:
    texto = re.sub(r"\s+", " ", _texto_normalizado(texto))
    texto = re.sub(r"(?i)\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", "[email_oculto]", texto)
    texto = re.sub(r"(?i)https?://\S+", "[url_oculta]", texto)
    texto = re.sub(
        r"(?i)\b(?:authorization\s*:\s*bearer|bearer)\s+\S+",
        "authorization=[segredo_oculto]", texto,
    )
    texto = re.sub(
        r"(?i)\b(token|api[_-]?key|apikey|authorization|bearer|secret|senha|password)\b\s*[:=]?\s*\S+",
        r"\1=[segredo_oculto]", texto,
    )
    texto = re.sub(r"\b[A-Za-z0-9_-]{48,}\b", "[segredo_opaco]", texto)
    return texto if len(texto) <= LIMITE_TRECHO else texto[:LIMITE_TRECHO - 1] + "…"


def normalizar_referencia_b3(valor: Any) -> str | None:
    texto = _texto_normalizado(valor)
    achado = REFERENCIA_B3_EXATA.search(texto)
    if not achado:
        return None
    return f"B3-{achado.group(1)}-{int(achado.group(2)):03d}"


def referencias_b3(texto: Any) -> list[str]:
    bruto = _texto_normalizado(texto)
    refs = {
        f"B3-{m.group(1)}-{int(m.group(2)):03d}"
        for m in REFERENCIA_B3_EXATA.finditer(bruto)
    }
    return sorted(refs)


def _objeto(valor: Any, codigo: str) -> dict[str, Any]:
    if not isinstance(valor, dict):
        raise ErroEntrada(codigo, "o documento deve ser um objeto JSON")
    return valor


def _lista_objetos(valor: Any, codigo: str) -> list[dict[str, Any]]:
    if not isinstance(valor, list) or not all(isinstance(item, dict) for item in valor):
        raise ErroEntrada(codigo, "esperada uma lista de objetos")
    return valor


def validar_cobertura(valor: Any, prefixo: str, *, historica: bool) -> dict[str, Any]:
    cobertura = _objeto(valor, f"{prefixo}_COBERTURA_INVALIDA")
    estado = str(cobertura.get("estado") or "")
    if estado not in ESTADOS_COBERTURA:
        raise ErroEntrada(f"{prefixo}_COBERTURA_INVALIDA", "estado desconhecido")
    resultado = {
        "estado": estado,
        "intervalo_inicio": cobertura.get("intervalo_inicio"),
        "intervalo_fim": cobertura.get("intervalo_fim"),
        "atestado": cobertura.get("atestado") is True,
        "detalhe_sanitizado": _trecho_privado(cobertura.get("detalhe_sanitizado") or ""),
    }
    if historica and estado == "completa" and not (
        resultado["atestado"] and resultado["intervalo_inicio"]
        and resultado["intervalo_fim"]
    ):
        raise ErroEntrada(
            f"{prefixo}_COBERTURA_NAO_ATESTADA",
            "completa exige intervalo_inicio, intervalo_fim e atestado=true",
        )
    return resultado


def validar_snapshot(valor: Any) -> dict[str, Any]:
    snapshot = _objeto(valor, "SNAPSHOT_INVALIDO")
    if snapshot.get("schema_version") != SCHEMA_SNAPSHOT:
        raise ErroEntrada("SNAPSHOT_SCHEMA_INVALIDO", f"esperado {SCHEMA_SNAPSHOT}")
    posicoes = _lista_objetos(snapshot.get("posicoes"), "SNAPSHOT_POSICOES_INVALIDAS")
    if len(posicoes) > LIMITE_POSICOES:
        raise ErroEntrada("SNAPSHOT_LIMITE_EXCEDIDO", "posições acima do limite")
    ids: set[str] = set()
    normalizadas: list[dict[str, Any]] = []
    for indice, posicao in enumerate(posicoes):
        identificador = str(posicao.get("id_opaco") or "").strip()
        referencia = str(posicao.get("referencia_bolsa") or "").strip().upper()
        if not identificador:
            raise ErroEntrada("SNAPSHOT_ID_AUSENTE", f"posição {indice}")
        if identificador in ids:
            raise ErroEntrada("SNAPSHOT_ID_DUPLICADO", f"posição {indice}")
        if referencia and not REFERENCIA_B3_CANONICA.fullmatch(referencia):
            raise ErroEntrada("SNAPSHOT_REFERENCIA_INVALIDA", f"posição {indice}")
        ids.add(identificador)
        alocacoes = _lista_objetos(posicao.get("alocacoes", []), "SNAPSHOT_ALOCACOES_INVALIDAS")
        atual = deepcopy(posicao)
        atual["id_opaco"] = identificador
        atual["referencia_bolsa"] = referencia or None
        atual["alocacoes"] = sorted(
            deepcopy(alocacoes), key=lambda item: str(item.get("id_opaco") or "")
        )
        normalizadas.append(atual)
    resultado = deepcopy(snapshot)
    resultado["schema_version"] = SCHEMA_SNAPSHOT
    resultado["cobertura"] = validar_cobertura(
        snapshot.get("cobertura"), "SNAPSHOT", historica=False
    )
    resultado["posicoes"] = sorted(normalizadas, key=lambda item: item["id_opaco"])
    return resultado


def validar_mensagens(valor: Any) -> dict[str, Any]:
    documento = _objeto(valor, "MENSAGENS_INVALIDAS")
    if documento.get("schema_version") != SCHEMA_MENSAGENS:
        raise ErroEntrada("MENSAGENS_SCHEMA_INVALIDO", f"esperado {SCHEMA_MENSAGENS}")
    mensagens = _lista_objetos(documento.get("mensagens"), "MENSAGENS_LISTA_INVALIDA")
    if len(mensagens) > LIMITE_MENSAGENS:
        raise ErroEntrada("MENSAGENS_LIMITE_EXCEDIDO", "mensagens acima do limite")
    normalizadas: list[dict[str, Any]] = []
    for indice, mensagem in enumerate(mensagens):
        conversa = str(mensagem.get("conversa_ref") or "").strip()
        texto = _texto_normalizado(mensagem.get("texto"))
        if not conversa or not texto:
            raise ErroEntrada("MENSAGEM_INCOMPLETA", f"mensagem {indice}")
        if len(texto) > LIMITE_TEXTO_MENSAGEM:
            raise ErroEntrada("MENSAGEM_TEXTO_EXCEDIDO", f"mensagem {indice}")
        hash_calculado = hashlib.sha256(texto.encode("utf-8")).hexdigest()
        hash_recebido = str(mensagem.get("hash_conteudo") or "").strip().lower()
        if hash_recebido and hash_recebido != hash_calculado:
            raise ErroEntrada("MENSAGEM_HASH_DIVERGENTE", f"mensagem {indice}")
        normalizadas.append({
            "conversa_ref": conversa,
            "mensagem_ref": str(mensagem.get("mensagem_ref") or "").strip() or None,
            "timestamp": mensagem.get("timestamp"),
            "texto": texto,
            "hash_conteudo": hash_calculado,
            # Campo recebido não concede autoridade. É preservado apenas como metadado.
            "marcada_confiavel_na_origem": mensagem.get("confiavel") is True,
        })
    return {
        "schema_version": SCHEMA_MENSAGENS,
        "cobertura": validar_cobertura(
            documento.get("cobertura"), "MENSAGENS", historica=True
        ),
        "mensagens": normalizadas,
    }


def validar_origem(valor: Any) -> dict[str, Any]:
    origem = _objeto(valor, "ORIGEM_INVALIDA")
    if origem.get("schema_version") != SCHEMA_ORIGEM or origem.get("canal") != "telegram":
        raise ErroEntrada("ORIGEM_SCHEMA_INVALIDO", f"esperado {SCHEMA_ORIGEM}/telegram")
    conversa = str(origem.get("conversa_ref") or "").strip()
    mensagem = str(origem.get("mensagem_ref") or "").strip()
    if not conversa or not mensagem:
        raise ErroEntrada("ORIGEM_INCOMPLETA", "conversa_ref e mensagem_ref são obrigatórios")
    return {
        "schema_version": SCHEMA_ORIGEM,
        "canal": "telegram",
        "conversa_ref": conversa,
        "mensagem_ref": mensagem,
        "timestamp": origem.get("timestamp"),
        "autor_ref": str(origem.get("autor_ref") or "").strip() or None,
        "contexto_nome": _trecho_privado(origem.get("contexto_nome") or "") or None,
    }


def agrupar_mensagens(mensagens: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    grupos: dict[tuple[str, str, str], dict[str, Any]] = {}
    hashes_por_id: dict[tuple[str, str], set[str]] = {}
    for indice, mensagem in enumerate(mensagens):
        conversa = str(mensagem["conversa_ref"])
        conteudo_hash = str(mensagem["hash_conteudo"])
        mensagem_ref = mensagem.get("mensagem_ref")
        if mensagem_ref:
            chave_id = (conversa, str(mensagem_ref))
            hashes_por_id.setdefault(chave_id, set()).add(conteudo_hash)
            identidade = str(mensagem_ref)
        else:
            # Hash/texto iguais sem ID não provam que se trata do mesmo evento.
            identidade = f"sem-id-{indice}"
        chave = (conversa, identidade, conteudo_hash)
        grupo = grupos.setdefault(chave, {
            "conversa_ref": conversa,
            "hash_conteudo": conteudo_hash,
            "mensagem_refs": [],
            "timestamps": [],
            "texto_privado_sanitizado": _trecho_privado(str(mensagem["texto"])),
            "referencias_b3": referencias_b3(mensagem["texto"]),
            "acoes_candidatas": detectar_acoes(str(mensagem["texto"])),
            "autoridade": "evidencia_nao_e_comando_nem_aceite",
            "identidade_suficiente_para_deduplicar": bool(mensagem_ref),
            "identidade_provisoria": None if mensagem_ref else identidade,
            "_ocorrencias": 0,
        })
        grupo["_ocorrencias"] += 1
        if mensagem_ref and mensagem_ref not in grupo["mensagem_refs"]:
            grupo["mensagem_refs"].append(mensagem_ref)
        if mensagem.get("timestamp") and mensagem["timestamp"] not in grupo["timestamps"]:
            grupo["timestamps"].append(mensagem["timestamp"])
    for grupo in grupos.values():
        grupo["mensagem_refs"].sort()
        grupo["timestamps"].sort()
        grupo["duplicatas_agregadas"] = max(0, grupo.pop("_ocorrencias") - 1)
        refs = grupo["mensagem_refs"]
        grupo["conflito_identidade"] = bool(
            refs and len(hashes_por_id.get((grupo["conversa_ref"], refs[0]), set())) > 1
        )
        grupo["evidencia_id"] = "evb3_" + _sha({
            "conversa": grupo["conversa_ref"], "refs": grupo["mensagem_refs"],
            "hash": grupo["hash_conteudo"],
            "identidade_provisoria": grupo["identidade_provisoria"],
        })[:24]
    return sorted(grupos.values(), key=lambda item: (
        item["conversa_ref"], item["timestamps"], item["hash_conteudo"]
    ))


def detectar_acoes(texto: str) -> list[str]:
    acoes = [nome for nome, padrao in ACOES_PADROES if padrao.search(texto)]
    if "encerrar_parcial" in acoes and "encerrar" in acoes:
        acoes.remove("encerrar")
    return sorted(set(acoes))


def _valor_presente(valor: Any) -> bool:
    return valor is not None and valor != "" and valor != []


def _candidato_acao(acao: str, posicao: Mapping[str, Any] | None,
                    evidencia_id: str) -> dict[str, Any]:
    necessarios = list(CAMPOS_ACAO[acao])
    presentes: list[str] = []
    if posicao:
        for campo in necessarios:
            if campo in posicao and _valor_presente(posicao.get(campo)):
                presentes.append(campo)
    return {
        "acao": acao,
        "estado": "candidata_nao_confirmada",
        "natureza": "acao_citada_ou_hipotese",
        "motivo": "a evidência menciona termo compatível; não comprova execução",
        "evidencia_ids": [evidencia_id],
        "campos_necessarios": necessarios,
        "campos_presentes_no_snapshot": presentes,
        "campos_faltantes": [campo for campo in necessarios if campo not in presentes],
        "pendencias": ["confirmacao_explicita_da_execucao"],
    }


def _snapshot_hash_estavel(snapshot: Mapping[str, Any]) -> str:
    conteudo = deepcopy(dict(snapshot))
    for campo in ("gerado_em", "capturado_em", "coletado_em"):
        conteudo.pop(campo, None)
    cobertura = conteudo.get("cobertura")
    if isinstance(cobertura, dict):
        cobertura = deepcopy(cobertura)
        cobertura.pop("intervalo_inicio", None)
        cobertura.pop("intervalo_fim", None)
        conteudo["cobertura"] = cobertura
    return _sha(conteudo)


def gerar_plano(snapshot_bruto: Any, mensagens_brutas: Any, origem_bruta: Any) -> dict[str, Any]:
    snapshot = validar_snapshot(snapshot_bruto)
    mensagens = validar_mensagens(mensagens_brutas)
    origem = validar_origem(origem_bruta)
    snapshot_hash = _snapshot_hash_estavel(snapshot)
    mensagens_hash = _sha(mensagens)
    origem_hash = _sha(origem)
    plano_id = "plb3_" + _sha({
        "versao": VERSAO_PLANEJADOR,
        "snapshot_hash": snapshot_hash,
        "mensagens_hash": mensagens_hash,
        "origem_hash": origem_hash,
    })
    evidencias = agrupar_mensagens(mensagens["mensagens"])
    por_referencia: dict[str, list[dict[str, Any]]] = {}
    sem_referencia: list[dict[str, Any]] = []
    for evidencia in evidencias:
        if not evidencia["referencias_b3"]:
            sem_referencia.append(evidencia)
        for referencia in evidencia["referencias_b3"]:
            por_referencia.setdefault(referencia, []).append(evidencia)

    posicoes_por_ref: dict[str, list[dict[str, Any]]] = {}
    for posicao in snapshot["posicoes"]:
        if posicao.get("referencia_bolsa"):
            posicoes_por_ref.setdefault(str(posicao["referencia_bolsa"]), []).append(posicao)

    previas: list[dict[str, Any]] = []
    for posicao in snapshot["posicoes"]:
        referencia = posicao.get("referencia_bolsa")
        candidatas = por_referencia.get(str(referencia), []) if referencia else []
        acoes: list[dict[str, Any]] = []
        for evidencia in candidatas:
            if len(evidencia["referencias_b3"]) != 1:
                continue
            for acao in evidencia["acoes_candidatas"]:
                acoes.append(_candidato_acao(acao, posicao, evidencia["evidencia_id"]))
        refs_posicoes = posicoes_por_ref.get(str(referencia), []) if referencia else []
        ambiguidades: list[str] = []
        if referencia and len(refs_posicoes) > 1:
            ambiguidades.append("referencia_b3_associada_a_multiplas_posicoes_no_snapshot")
        if len({acao["acao"] for acao in acoes}) > 1:
            ambiguidades.append("evidencias_indicam_acoes_diferentes")
        if any(item["conflito_identidade"] for item in candidatas):
            ambiguidades.append("mesmo_id_de_mensagem_com_conteudos_diferentes")
        if any(len(item["referencias_b3"]) > 1 for item in candidatas):
            ambiguidades.append("evidencia_multirreferencia_sem_atribuicao_de_acao")
        if referencia is None:
            ambiguidades.append("posicao_sem_referencia_b3")
        previas.append({
            "posicao_id_opaco": posicao["id_opaco"],
            "referencia_bolsa": referencia,
            "dados_atuais": deepcopy(posicao),
            "evidencia_ids": [item["evidencia_id"] for item in candidatas],
            "acoes_candidatas": acoes,
            "ambiguidades": ambiguidades,
            "situacao": (
                "ambiguo" if ambiguidades else
                "com_pistas" if acoes else "sem_pistas_referenciadas"
            ),
        })

    refs_sem_posicao = sorted(set(por_referencia) - set(posicoes_por_ref))
    evidencias_sem_posicao = [{
        "referencia_bolsa": referencia,
        "situacao": "referencia_sem_posicao_atual",
        "evidencia_ids": [item["evidencia_id"] for item in por_referencia[referencia]],
        "acoes_candidatas": [
            _candidato_acao(acao, None, item["evidencia_id"])
            for item in por_referencia[referencia]
            if len(item["referencias_b3"]) == 1
            for acao in item["acoes_candidatas"]
        ],
    } for referencia in refs_sem_posicao]
    possiveis_novas_sem_ref = [{
        "situacao": "possivel_nova_posicao_sem_referencia_a_conferir",
        "referencia_bolsa": None,
        "evidencia_id": item["evidencia_id"],
        "acoes_candidatas": [_candidato_acao("abrir", None, item["evidencia_id"])],
        "pendencias": ["confirmar_se_houve_abertura", "identificar_sem_inventar_referencia_b3"],
    } for item in sem_referencia if "abrir" in item["acoes_candidatas"]]

    cobertura_geral = "completa" if (
        snapshot["cobertura"]["estado"] == "completa"
        and mensagens["cobertura"]["estado"] == "completa"
    ) else "incompleta"
    return {
        "schema_version": SCHEMA_PLANO,
        "planejador_version": VERSAO_PLANEJADOR,
        "modo": "somente_leitura_offline",
        "estado": "somente_previa",
        "autoriza_escrita": False,
        "runtime_ativo": False,
        "plano_id": plano_id,
        "snapshot_hash": snapshot_hash,
        "mensagens_hash": mensagens_hash,
        "origem_hash": origem_hash,
        "origem_pedido": origem,
        "snapshot_recebido": snapshot,
        "cobertura": {
            "estado_geral": cobertura_geral,
            "snapshot": snapshot["cobertura"],
            "mensagens_whatsapp": mensagens["cobertura"],
            "regra": "completa somente quando ambas as fontes atestam o intervalo exato",
        },
        "previas_por_posicao": sorted(previas, key=lambda item: item["posicao_id_opaco"]),
        "evidencias_sem_posicao": evidencias_sem_posicao,
        "mensagens_sem_referencia": [item["evidencia_id"] for item in sem_referencia],
        "possiveis_novas_posicoes_sem_referencia": possiveis_novas_sem_ref,
        "evidencias_privadas": evidencias,
        "compatibilidade_central_investigacoes": {
            "adaptador_sugerido": "wey",
            "correlation_id": plano_id,
            "publicacao_solicitada": False,
            "nova_fila_criada": False,
        },
        "controles": {
            "rede": False,
            "subprocessos": False,
            "consultas_banco": 0,
            "escritas_operacionais": 0,
            "acoes_confirmadas": 0,
            "mensagens_enviadas": 0,
        },
        "resumo": {
            "posicoes": len(snapshot["posicoes"]),
            "mensagens_recebidas": len(mensagens["mensagens"]),
            "evidencias_deduplicadas": len(evidencias),
            "duplicatas_agregadas": len(mensagens["mensagens"]) - len(evidencias),
            "posicoes_com_pistas": sum(bool(item["acoes_candidatas"]) for item in previas),
            "posicoes_ambiguas": sum(item["situacao"] == "ambiguo" for item in previas),
            "referencias_sem_posicao": len(evidencias_sem_posicao),
            "mensagens_sem_referencia": len(sem_referencia),
            "possiveis_novas_posicoes_sem_referencia": len(possiveis_novas_sem_ref),
        },
    }


def ler_json_privado(caminho: Path) -> Any:
    try:
        info = caminho.lstat()
    except OSError as exc:
        raise ErroEntrada("ARQUIVO_ENTRADA_INDISPONIVEL", caminho.name) from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise ErroEntrada("ARQUIVO_ENTRADA_INSEGURO", caminho.name)
    if info.st_size > LIMITE_ARQUIVO_BYTES:
        raise ErroEntrada("ARQUIVO_ENTRADA_GRANDE", caminho.name)
    try:
        return json.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ErroEntrada("ARQUIVO_ENTRADA_INVALIDO", caminho.name) from exc


def _normalizar_alias_sistema_mac(
    caminho: Path,
    *,
    plataforma: str | None = None,
    resolver: Any = os.path.realpath,
) -> Path:
    """Normaliza apenas os dois aliases conhecidos e confirmados do macOS."""
    absoluto = caminho.absolute()
    plataforma = sys.platform if plataforma is None else plataforma
    if plataforma != "darwin":
        return absoluto
    aliases = {"tmp": "/private/tmp", "var": "/private/var"}
    if len(absoluto.parts) < 2:
        return absoluto
    primeiro = absoluto.parts[1]
    esperado = aliases.get(primeiro)
    if esperado and resolver("/" + primeiro) == esperado:
        return Path(esperado, *absoluto.parts[2:])
    return absoluto


def _ancestral_symlink(caminho: Path) -> bool:
    absoluto = _normalizar_alias_sistema_mac(caminho)
    atual = Path(absoluto.anchor)
    for parte in absoluto.parts[1:-1]:
        atual = atual / parte
        if atual.is_symlink():
            return True
    return False


def _em_repositorio_git(caminho: Path) -> bool:
    atual = caminho.resolve(strict=False).parent
    for pasta in (atual, *atual.parents):
        if (pasta / ".git").exists():
            return True
    return False


def gravar_privado_sem_sobrescrever(caminho: Path, plano: Mapping[str, Any]) -> str:
    if _ancestral_symlink(caminho):
        raise ErroEntrada("SAIDA_ANCESTRAL_SYMLINK_PROIBIDO", caminho.name)
    if _em_repositorio_git(caminho):
        raise ErroEntrada("SAIDA_NO_REPOSITORIO_PUBLICO", caminho.name)
    if caminho.is_symlink():
        raise ErroEntrada("SAIDA_SYMLINK_PROIBIDA", caminho.name)
    conteudo = (json.dumps(plano, ensure_ascii=False, indent=2, sort_keys=True)
                + "\n").encode("utf-8")
    if caminho.exists():
        if not caminho.is_file():
            raise ErroEntrada("SAIDA_INSEGURA", caminho.name)
        if caminho.stat().st_mode & 0o077:
            raise ErroEntrada("SAIDA_MODO_INSEGURO", caminho.name)
        existente = caminho.read_bytes()
        if existente == conteudo:
            return "ja_existente_identico"
        raise ErroEntrada("SAIDA_EXISTENTE_DIVERGENTE", caminho.name)
    caminho.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descritor = os.open(caminho, flags, 0o600)
        with os.fdopen(descritor, "wb") as arquivo:
            arquivo.write(conteudo)
            arquivo.flush()
            os.fsync(arquivo.fileno())
    except FileExistsError as exc:
        raise ErroEntrada("SAIDA_CRIADA_CONCORRENTEMENTE", caminho.name) from exc
    os.chmod(caminho, 0o600)
    return "criado"


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Gera prévia privada offline; nunca atualiza o Portfólio B3."
    )
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--mensagens", required=True, type=Path)
    parser.add_argument("--origem", required=True, type=Path)
    parser.add_argument("--saida", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = construir_parser().parse_args(argv)
    try:
        plano = gerar_plano(
            ler_json_privado(args.snapshot),
            ler_json_privado(args.mensagens),
            ler_json_privado(args.origem),
        )
        estado_saida = gravar_privado_sem_sobrescrever(args.saida, plano)
    except ErroEntrada as exc:
        print(json.dumps({"ok": False, "erro_codigo": exc.codigo},
                         ensure_ascii=False), file=sys.stderr)
        return 2
    print(json.dumps({
        "ok": True,
        "modo": plano["modo"],
        "plano_id": plano["plano_id"],
        "snapshot_hash": plano["snapshot_hash"],
        "saida": estado_saida,
        "cobertura": plano["cobertura"]["estado_geral"],
        "contagens": plano["resumo"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
