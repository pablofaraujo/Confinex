#!/usr/bin/env python3
"""Propõe conciliações bancárias conservadoras sem promover dados operacionais."""

from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
import urllib.error
import urllib.request
import uuid
from collections import Counter
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

try:
    from exportar_snapshot_consolidacao import LeitorSupabase
    from identidade_bancaria import (
        assinatura, avaliar_presenca, chave_logica, decimal_assinado, identidade_registro,
    )
except ModuleNotFoundError:
    from tools.exportar_snapshot_consolidacao import LeitorSupabase
    from tools.identidade_bancaria import (
        assinatura, avaliar_presenca, chave_logica, decimal_assinado, identidade_registro,
    )


NAMESPACE = uuid.UUID("f2ba169e-4bce-4d4c-a66a-3c5d50056cdd")
TABELAS_ESCRITA = {"conciliacoes_candidatas"}
PALAVRAS_GENERICAS = {
    "banco", "compra", "compras", "confinamento", "documentos", "fazenda",
    "fiscais", "fornecedor", "gado", "operacional", "pagamento", "pagto",
    "pix", "recebido", "transferencia", "transferido", "valor", "ted", "doc",
    "sicoob",
}


def decimal_positivo(valor: Any) -> Decimal | None:
    numero = decimal_assinado(valor)
    if numero is None or not numero:
        return None
    magnitude = numero.copy_abs()  # abs() pode arredondar no contexto Decimal global.
    try:
        centavos = magnitude.quantize(Decimal("0.01"))
        return centavos if centavos == magnitude else None
    except InvalidOperation:
        return None


def data_iso(valor: Any) -> date | None:
    try:
        return date.fromisoformat(str(valor)[:10])
    except (TypeError, ValueError):
        return None


def normalizar(texto: Any) -> str:
    base = unicodedata.normalize("NFKD", str(texto or ""))
    base = "".join(ch for ch in base if not unicodedata.combining(ch)).casefold()
    return re.sub(r"[^a-z0-9]+", " ", base).strip()


def tokens_distintivos(*valores: Any) -> set[str]:
    tokens = set(normalizar(" ".join(str(v or "") for v in valores)).split())
    return {t for t in tokens if len(t) >= 4 and t not in PALAVRAS_GENERICAS and not t.isdigit()}


def chave_duplicidade_transacao(item: dict[str, Any]) -> tuple:
    texto = normalizar(" ".join(filter(None, (item.get("descricao"), item.get("memo")))))
    return (
        identidade_registro(item), str(item.get("data") or ""),
        decimal_assinado(item.get("valor")), texto,
    )


def correspondencia_textual_parcial(
    transacao: dict[str, Any], candidato: dict[str, Any],
) -> dict[str, Any] | None:
    # Candidatos não têm direção bancária explícita. Um crédito não prova
    # pagamento de compra; não o inverter para fabricar uma proposta de saída.
    if decimal_assinado(transacao.get("valor")) is None or decimal_assinado(transacao["valor"]) >= 0:
        return None
    dt, dc = data_iso(transacao.get("data")), data_iso(candidato.get("data_base"))
    if not dt or not dc or not (0 <= (dt - dc).days <= 120):
        return None
    tokens_banco = tokens_distintivos(transacao.get("descricao"), transacao.get("memo"))
    tokens_alvo = tokens_distintivos(candidato.get("nome"))
    if not tokens_banco & tokens_alvo:
        return None
    return {
        "classificacao": "possivel",
        "confianca": 0.60,
        "justificativa": (
            "Referência textual única e data posterior compatível; o valor pode ser parcial."
        ),
    }


def correspondencia(
    transacao: dict[str, Any], alvo: dict[str, Any], *, tipo_alvo: str,
) -> dict[str, Any] | None:
    valor_assinado = decimal_assinado(transacao.get("valor"))
    if valor_assinado is None or not valor_assinado:
        return None
    if tipo_alvo == "negocio" and valor_assinado > 0:
        return None
    if tipo_alvo == "fluxo":
        direcao = alvo.get("tipo")
        if direcao not in {"entrada", "saida"} or (valor_assinado > 0) != (direcao == "entrada"):
            return None
    valor_transacao = decimal_positivo(transacao.get("valor"))
    valor_alvo = decimal_positivo(alvo.get("valor_total") if tipo_alvo == "negocio" else alvo.get("valor"))
    if not valor_transacao or valor_transacao != valor_alvo:
        return None
    dt = data_iso(transacao.get("data"))
    da = data_iso(alvo.get("data_base") if tipo_alvo == "negocio" else alvo.get("data"))
    limite = 90 if tipo_alvo == "negocio" else 7
    diferenca = abs((dt - da).days) if dt and da else None
    if diferenca is None or diferenca > limite:
        return None
    tokens_banco = tokens_distintivos(transacao.get("descricao"), transacao.get("memo"))
    if tipo_alvo == "negocio":
        tokens_alvo = tokens_distintivos(alvo.get("nome"), alvo.get("codigo_fonte"), alvo.get("contexto"))
    else:
        tokens_alvo = tokens_distintivos(alvo.get("descricao"), alvo.get("categoria"))
    texto_coincide = bool(tokens_banco & tokens_alvo)
    forte = texto_coincide and diferenca <= (45 if tipo_alvo == "negocio" else 3)
    return {
        "classificacao": "forte" if forte else "provavel",
        "confianca": 0.92 if forte else 0.78,
        "justificativa": (
            "Valor exato, data compatível e referência textual coincidente."
            if forte else "Valor exato e data compatível em alvo único; confirmar a relação."
        ),
    }


def id_conciliacao(transacao_id: str, tipo: str, alvo_id: str) -> str:
    return str(uuid.uuid5(NAMESPACE, f"{transacao_id}:{tipo}:{alvo_id}"))


def planejar(
    transacoes: list[dict[str, Any]],
    candidatos: list[dict[str, Any]],
    fluxos: list[dict[str, Any]],
    conciliacoes: list[dict[str, Any]],
    transacoes_operacionais: list[dict[str, Any]],
) -> dict[str, Any]:
    existentes = {
        (
            str(item.get("transacao_staging_id")),
            "negocio" if item.get("negocio_candidato_id") else "fluxo",
            str(item.get("negocio_candidato_id") or item.get("fluxo_caixa_id")),
        )
        for item in conciliacoes
    }
    duplicidades = Counter(chave_duplicidade_transacao(item) for item in transacoes)
    identidades_repetidas = Counter(chave_logica(item) for item in transacoes if chave_logica(item))
    propostas: list[dict[str, Any]] = []
    motivos = Counter()
    ambiguidades = Counter()
    for transacao in transacoes:
        if transacao.get("estado") not in {"nao_revisada", "em_revisao"}:
            motivos["estado_fora_da_revisao"] += 1
            continue
        if decimal_positivo(transacao.get("valor")) is None:
            ambiguidades["valor_ausente_zero_ou_precisao_a_conferir"] += 1
            continue
        presenca = avaliar_presenca(transacao, transacoes_operacionais)
        if presenca in {"presente_por_vinculo", "presente_por_identidade"}:
            motivos["ja_existe_no_banco_operacional"] += 1
            continue
        if presenca != "ausente_na_amostra":
            ambiguidades[presenca] += 1
            continue
        if identidades_repetidas[chave_logica(transacao)] > 1:
            ambiguidades["mesma_identidade_e_fitid_em_varias_linhas"] += 1
            continue
        if duplicidades[chave_duplicidade_transacao(transacao)] > 1:
            ambiguidades["duplicidade_aparente_entre_fontes"] += 1
            continue
        alvos: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
        for candidato in candidatos:
            if candidato.get("estado") not in {"rascunho", "em_revisao", "confirmado"}:
                continue
            regra = correspondencia(transacao, candidato, tipo_alvo="negocio")
            if regra:
                alvos.append(("negocio", candidato, regra))
        for fluxo in fluxos:
            regra = correspondencia(transacao, fluxo, tipo_alvo="fluxo")
            if regra:
                alvos.append(("fluxo", fluxo, regra))
        if not alvos:
            for candidato in candidatos:
                if candidato.get("estado") not in {"rascunho", "em_revisao", "confirmado"}:
                    continue
                regra = correspondencia_textual_parcial(transacao, candidato)
                if regra:
                    alvos.append(("negocio", candidato, regra))
        if not alvos:
            motivos["sem_alvo_compativel"] += 1
            continue
        if len(alvos) > 1:
            ambiguidades["mais_de_um_alvo_compativel"] += 1
            continue
        tipo, alvo, regra = alvos[0]
        chave = (str(transacao["id"]), tipo, str(alvo["id"]))
        if chave in existentes:
            motivos["proposta_deterministica_existente"] += 1
            continue
        registro = {
            "id": id_conciliacao(*chave),
            "transacao_staging_id": transacao["id"],
            "negocio_candidato_id": alvo["id"] if tipo == "negocio" else None,
            "operacao_id": None,
            "fluxo_caixa_id": alvo["id"] if tipo == "fluxo" else None,
            "valor_alocado": str(decimal_positivo(transacao.get("valor"))),
            "classificacao": regra["classificacao"],
            "confianca": regra["confianca"],
            "justificativa": regra["justificativa"],
            "estado": "pendente",
        }
        propostas.append(registro)
    plano = {
        "modo": "dry_run",
        "snapshot_sha256": assinatura({
            "transacoes": transacoes, "candidatos": candidatos, "fluxos": fluxos,
            "conciliacoes": conciliacoes, "operacionais": transacoes_operacionais,
        }),
        "propostas": propostas,
        "resumo": {
            "transacoes_lidas": len(transacoes),
            "propostas": len(propostas),
            "por_classificacao": dict(sorted(Counter(p["classificacao"] for p in propostas).items())),
            "por_tipo_alvo": {
                "negocio_candidato": sum(bool(p["negocio_candidato_id"]) for p in propostas),
                "fluxo_caixa": sum(bool(p["fluxo_caixa_id"]) for p in propostas),
            },
            "ignoradas_por_motivo": dict(sorted(motivos.items())),
            "ambiguidades_preservadas": dict(sorted(ambiguidades.items())),
            "escritas_executadas": 0,
            "tabelas_operacionais_alteradas": 0,
        },
    }
    plano["plano_id"] = assinatura(plano)[:12]
    return plano


class EscritorConciliacoes:
    def __init__(self, url: str, chave: str, timeout: int = 20) -> None:
        self.url, self.chave = url.rstrip("/"), chave
        self.timeout = max(1, min(int(timeout), 20))

    def inserir(self, tabela: str, payload: dict[str, Any]) -> None:
        if tabela not in TABELAS_ESCRITA:
            raise ValueError(f"escrita não permitida: {tabela}")
        requisicao = urllib.request.Request(
            f"{self.url}/rest/v1/{tabela}",
            data=json.dumps(payload, ensure_ascii=False).encode(), method="POST",
            headers={
                "apikey": self.chave, "Authorization": f"Bearer {self.chave}",
                "Content-Type": "application/json",
                "Prefer": "return=representation",
            },
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=self.timeout) as resposta:
                if resposta.status not in {200, 201}:
                    raise RuntimeError(f"HTTP inesperado: {resposta.status}")
                linhas = json.loads(resposta.read().decode("utf-8"))
                if not isinstance(linhas, list) or len(linhas) != 1 or not isinstance(linhas[0], dict):
                    raise RuntimeError("resultado de escrita não comprovado; conferir antes de retomar")
                for campo, valor in payload.items():
                    recebido = linhas[0].get(campo)
                    igual = decimal_assinado(recebido) == decimal_assinado(valor) if campo == "valor_alocado" else recebido == valor
                    if campo not in linhas[0] or not igual:
                        raise RuntimeError("conteúdo gravado não comprovado; conferir antes de retomar")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(f"escrita em {tabela} falhou com HTTP {exc.code}") from exc


def executar(plano: dict[str, Any], escritor: EscritorConciliacoes, limite: int) -> int:
    if assinatura({c: v for c, v in plano.items() if c != "plano_id"})[:12] != plano.get("plano_id"):
        raise ValueError("plano alterado; refazer a conferência")
    if limite <= 0:
        raise ValueError("execução exige limite positivo")
    propostas = plano["propostas"][:limite]
    for proposta in propostas:
        escritor.inserir("conciliacoes_candidatas", proposta)
    return len(propostas)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--executar", action="store_true")
    parser.add_argument("--limite", type=int, default=0)
    parser.add_argument("--confirmacao")
    args = parser.parse_args()
    url = os.environ.get("SUPABASE_URL") or os.environ.get("CONFINEX_DB_URL") or ""
    chave = os.environ.get("SUPABASE_SERVICE_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or os.environ.get("CONFINEX_DB_KEY") or ""
    leitor = LeitorSupabase(url, chave)
    plano = planejar(
        leitor.listar("transacoes_banco_staging"),
        leitor.listar("negocios_candidatos"),
        leitor.listar("fluxo_caixa"),
        leitor.listar("conciliacoes_candidatas"),
        leitor.listar("transacoes_banco"),
    )
    criadas = 0
    if args.executar:
        esperada = f"CRIAR CONCILIACOES {plano['plano_id']}"
        if args.confirmacao != esperada:
            raise SystemExit(f"confirmação inválida; use: {esperada}")
        criadas = executar(plano, EscritorConciliacoes(url, chave), args.limite)
        plano["modo"] = "executado"
    print(json.dumps({
        "plano_id": plano["plano_id"], "modo": plano["modo"],
        "resumo": plano["resumo"], "conciliacoes_criadas": criadas,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
