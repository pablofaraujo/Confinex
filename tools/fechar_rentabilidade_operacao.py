#!/usr/bin/env python3
"""Fechamento de rentabilidade executada por operação — F1 do circuito.

Consolida, para UMA operação (CF/BB/parceria), o realizado de ponta a ponta —
compra, custos, venda/abate, acerto, caixa conciliado e hedge creditado — e o
compara com a estimativa CONGELADA do Confinex. O resultado alimenta
``fechamentos_operacao`` (previsto, realizado_bruto, realizado_liquido,
hedge_creditado, desvio, explicacao), que hoje está vazia.

Contrato financeiro (docs/regras-de-negocio.md):
- receita líquida = faturamento bruto − Funrural − Finpec − outros custos da
  venda; encargo não informado NUNCA é inventado — vira pendência explícita;
- lucro bruto = receita líquida − (compra + frete + custos de confinamento);
- lucro líquido = lucro bruto − custos financeiros (categorias financeiras de
  ``custos_operacao``), cada componente descontado exatamente uma vez;
- hedge creditado (``alocacoes_hedge.resultado_creditado``) é apresentado em
  linha própria; o desvio compara (líquido + hedge) com o previsto congelado.

Honestidade do fechamento:
- status COMPLETO exige recebimento comprovado (acerto recebido, venda
  ``recebido`` ou caixa conciliado); sem isso o fechamento é PARCIAL e lista
  exatamente o que falta — nunca um número silenciosamente errado;
- estimativa com homologação retrospectiva é rotulada como reconstrução
  (regra do repositório: nunca apresentá-la como previsão contemporânea);
- dry-run é o padrão; a gravação exige ``--executar --confirmacao <hash>``
  com o hash impresso pelo dry-run, e escreve SOMENTE em
  ``fechamentos_operacao`` (nunca em compra, venda, abate ou caixa).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping

VERSAO_FECHAMENTO = "fechamento-v1.0.0"
CATEGORIAS_FINANCEIRAS = frozenset({
    "financeiro", "adiantamento_juros", "juros", "custo_dinheiro",
})
TABELA_ESCRITA = "fechamentos_operacao"


def _num(valor: Any) -> float:
    if valor is None or valor == "":
        return 0.0
    return float(valor)


def _r2(valor: float) -> float:
    return round(valor + 0.0, 2)


# ---------------------------------------------------------------------------
# Núcleo puro — recebe o dossiê e devolve o fechamento (testável offline)
# ---------------------------------------------------------------------------


def estimativa_congelada(dossie: Mapping[str, Any]) -> dict[str, Any] | None:
    estimativas = dossie.get("estimativas") or []
    originais = [e for e in estimativas if e.get("tipo") == "original"]
    escolhida = (originais or sorted(
        estimativas, key=lambda e: e.get("versao") or 0
    ))[:1]
    if not escolhida:
        return None
    est = escolhida[0]
    premissas = est.get("premissas") or {}
    retrospectiva = (
        str(premissas.get("homologacao") or "") == "retrospectiva"
        or "reconstru" in str(est.get("motivo_revisao") or "").lower()
    )
    resultado = est.get("resultado") or {}
    return {
        "versao": est.get("versao"),
        "reconstrucao_retrospectiva": retrospectiva,
        "lucro_bruto_previsto": resultado.get("lucroBruto"),
        "lucro_liquido_previsto": resultado.get("lucroLiquido"),
        "receita_prevista": resultado.get("receita"),
        "motivo_revisao": est.get("motivo_revisao"),
    }


def fechar_operacao(dossie: Mapping[str, Any]) -> dict[str, Any]:
    operacao = dossie.get("operacao") or {}
    pendencias: list[str] = []

    # Receita realizada (vendas + abates vinculados)
    vendas = dossie.get("vendas") or []
    abates = dossie.get("abates") or []
    faturamento_bruto = sum(_num(v.get("valor_bruto")) for v in vendas)
    funrural = 0.0
    finpec = 0.0
    outros_venda = 0.0
    for venda in vendas:
        if venda.get("funrural") is None:
            pendencias.append(
                "Funrural não informado na venda de "
                + str(venda.get("data_abate") or "?")
                + " — encargo não foi estimado"
            )
        funrural += _num(venda.get("funrural"))
        finpec += _num(venda.get("finpec"))
        outros_venda += _num(venda.get("outros_custos"))
    # Abates com romaneio: usam o valor líquido do frigorífico quando a venda
    # correspondente não existir (nunca somam em dobro com a venda vinculada).
    vendas_ids = {str(v.get("id")) for v in vendas}
    for abate in abates:
        if str(abate.get("venda_id") or "") in vendas_ids:
            continue
        faturamento_bruto += _num(abate.get("valor_bruto"))
        funrural += _num(abate.get("funrural_valor"))
        outros_venda += _num(abate.get("outros_descontos")) + _num(
            abate.get("fundesa_valor")
        )
    receita_liquida = faturamento_bruto - funrural - finpec - outros_venda

    # Custos operacionais e financeiros
    compras = dossie.get("compras") or []
    custo_compra = sum(_num(c.get("valor_total")) for c in compras)
    custos = dossie.get("custos") or []
    custos_operacionais_extra = 0.0
    custo_financeiro = 0.0
    custos_por_categoria: dict[str, float] = {}
    for custo in custos:
        categoria = str(custo.get("categoria") or "outros")
        valor = _num(custo.get("valor"))
        custos_por_categoria[categoria] = _r2(
            custos_por_categoria.get(categoria, 0.0) + valor
        )
        if categoria in CATEGORIAS_FINANCEIRAS:
            custo_financeiro += valor
        else:
            custos_operacionais_extra += valor
    ressarcimentos = dossie.get("ressarcimentos") or []
    ressarcir_pendente = sum(
        max(_num(r.get("valor")) - _num(r.get("valor_ressarcido")), 0.0)
        for r in ressarcimentos
    )
    if ressarcir_pendente > 0:
        pendencias.append(
            f"Ressarcimentos em aberto: R$ {ressarcir_pendente:,.2f}"
        )

    lucro_bruto = receita_liquida - custo_compra - custos_operacionais_extra
    lucro_liquido = lucro_bruto - custo_financeiro

    # Hedge creditado à operação (linha própria, uma única vez)
    hedge_itens = dossie.get("hedge") or []
    hedge_creditado = sum(
        _num(item.get("alocacao", {}).get("resultado_creditado"))
        for item in hedge_itens
    )
    for item in hedge_itens:
        if str(item.get("posicao", {}).get("status") or "") not in (
            "encerrada", "fechada", "rolada",
        ):
            pendencias.append(
                "Posição de hedge ainda aberta ("
                + str(item.get("posicao", {}).get("referencia_bolsa") or "?")
                + ") — resultado creditado pode mudar"
            )

    # Recebimento comprovado?
    acertos = dossie.get("acertos") or []
    caixa = dossie.get("fluxo_caixa") or []
    recebimento_ok = (
        any(a.get("data_recebimento") for a in acertos)
        or any(v.get("recebido") for v in vendas)
        or any(
            f.get("realizado") and str(f.get("tipo") or "") == "entrada"
            for f in caixa
        )
    )
    if not recebimento_ok:
        pendencias.append(
            "Recebimento da venda não comprovado (acerto sem data de "
            "recebimento, venda não marcada como recebida e sem caixa "
            "conciliado) — o realizado é econômico, não de caixa"
        )
    for acerto in acertos:
        if str(acerto.get("status") or "") == "aguardando":
            pendencias.append("Acerto do confinamento ainda aguardando")

    previsto = estimativa_congelada(dossie)
    if previsto is None:
        pendencias.append(
            "Sem estimativa congelada no Confinex — comparativo indisponível"
        )
    elif previsto["reconstrucao_retrospectiva"]:
        pendencias.append(
            "Estimativa é RECONSTRUÇÃO retrospectiva (não é previsão "
            "contemporânea); o desvio comparativo tem valor limitado"
        )

    previsto_liquido = (
        None if previsto is None else previsto.get("lucro_liquido_previsto")
    )
    resultado_total = lucro_liquido + hedge_creditado
    desvio = (
        None if previsto_liquido is None
        else _r2(resultado_total - _num(previsto_liquido))
    )

    status = "COMPLETO" if recebimento_ok and previsto is not None else "PARCIAL"
    fechamento = {
        "versao": VERSAO_FECHAMENTO,
        "operacao_id": operacao.get("id"),
        "codigo": operacao.get("codigo"),
        "modalidade": operacao.get("modalidade"),
        "tipo_negocio": operacao.get("tipo_negocio"),
        "status_fechamento": status,
        "realizado": {
            "faturamento_bruto": _r2(faturamento_bruto),
            "funrural": _r2(funrural),
            "finpec": _r2(finpec),
            "outros_da_venda": _r2(outros_venda),
            "receita_liquida": _r2(receita_liquida),
            "custo_compra": _r2(custo_compra),
            "custos_por_categoria": custos_por_categoria,
            "custos_operacionais_extra": _r2(custos_operacionais_extra),
            "custo_financeiro": _r2(custo_financeiro),
            "lucro_bruto": _r2(lucro_bruto),
            "lucro_liquido": _r2(lucro_liquido),
            "hedge_creditado": _r2(hedge_creditado),
            "resultado_total_com_hedge": _r2(resultado_total),
        },
        "previsto": previsto,
        "desvio_vs_previsto_liquido": desvio,
        "pendencias": pendencias,
    }
    fechamento["confirmacao"] = hashlib.sha256(json.dumps(
        fechamento, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    ).encode("utf-8")).hexdigest()
    return fechamento


def explicacao_texto(fechamento: Mapping[str, Any]) -> str:
    realizado = fechamento["realizado"]
    partes = [
        f"Fechamento {fechamento['versao']} ({fechamento['status_fechamento']}).",
        (
            "Receita líquida R$ {receita_liquida:,.2f} − compra R$ "
            "{custo_compra:,.2f} − custos R$ {custos_operacionais_extra:,.2f} "
            "= lucro bruto R$ {lucro_bruto:,.2f}; − financeiro R$ "
            "{custo_financeiro:,.2f} = líquido R$ {lucro_liquido:,.2f}; "
            "hedge creditado R$ {hedge_creditado:,.2f} → total R$ "
            "{resultado_total_com_hedge:,.2f}."
        ).format(**realizado),
    ]
    if fechamento.get("desvio_vs_previsto_liquido") is not None:
        partes.append(
            "Desvio vs previsto líquido: R$ "
            f"{fechamento['desvio_vs_previsto_liquido']:,.2f}."
        )
    if fechamento["pendencias"]:
        partes.append("Pendências: " + "; ".join(fechamento["pendencias"]))
    return " ".join(partes)


# ---------------------------------------------------------------------------
# Coleta (PostgREST) e gravação
# ---------------------------------------------------------------------------


class ClienteFechamento:
    def __init__(self, url: str, chave: str, timeout: int = 20) -> None:
        if not url or not chave:
            raise ValueError("configuracao_incompleta")
        self.url = url.rstrip("/")
        self.chave = chave
        self.timeout = max(1, min(int(timeout), 20))

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        base = {"apikey": self.chave, "Authorization": f"Bearer {self.chave}"}
        base.update(extra or {})
        return base

    def _get(self, tabela: str, consulta: dict[str, str]) -> list[dict[str, Any]]:
        requisicao = urllib.request.Request(
            f"{self.url}/rest/v1/{tabela}?" + urllib.parse.urlencode(consulta),
            method="GET", headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=self.timeout) as r:
                dados = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"leitura de {tabela} falhou com HTTP {exc.code}"
            ) from exc
        if not isinstance(dados, list):
            raise RuntimeError(f"resposta inválida de {tabela}")
        return dados

    def dossie(self, codigo: str) -> dict[str, Any]:
        operacoes = self._get("operacoes", {"codigo": "eq." + codigo})
        if len(operacoes) != 1:
            raise RuntimeError(
                f"operação {codigo}: esperava 1 registro, achei {len(operacoes)}"
            )
        operacao = operacoes[0]
        oid = str(operacao["id"])
        avaliacoes = [
            a for a in self._get(
                "confinex_avaliacoes", {"operacao_id": "eq." + oid}
            ) if str(a.get("status") or "") not in ("cancelado", "cancelada")
        ]
        estimativas: list[dict[str, Any]] = []
        for avaliacao in avaliacoes[:1]:
            estimativas = self._get("confinex_estimativas", {
                "avaliacao_id": "eq." + str(avaliacao["id"]),
                "order": "versao.asc",
            })
        alocacoes = self._get("alocacoes_hedge", {"operacao_id": "eq." + oid})
        hedge = []
        for alocacao in alocacoes:
            posicoes = self._get(
                "posicoes_hedge", {"id": "eq." + str(alocacao["posicao_id"])}
            )
            hedge.append({
                "alocacao": alocacao,
                "posicao": posicoes[0] if posicoes else {},
            })
        return {
            "operacao": operacao,
            "avaliacao": avaliacoes[0] if avaliacoes else None,
            "estimativas": estimativas,
            "compras": self._get("compras", {"operacao_id": "eq." + oid}),
            "vendas": self._get("vendas", {"operacao_id": "eq." + oid}),
            "abates": self._get("abates", {"operacao_id": "eq." + oid}),
            "acertos": self._get("acertos", {"operacao_id": "eq." + oid}),
            "custos": self._get("custos_operacao", {"operacao_id": "eq." + oid}),
            "ressarcimentos": self._get(
                "ressarcimentos_operacionais", {"operacao_id": "eq." + oid}
            ),
            "entradas": self._get(
                "entradas_confinamento", {"operacao_id": "eq." + oid}
            ),
            "fluxo_caixa": self._get("fluxo_caixa", {"operacao_id": "eq." + oid}),
            "promissorias": self._get(
                "promissorias", {"operacao_id": "eq." + oid}
            ),
            "hedge": hedge,
            "participantes": self._get(
                "operacao_participantes", {"operacao_id": "eq." + oid}
            ),
        }

    def gravar_fechamento(self, fechamento: Mapping[str, Any]) -> None:
        """Um POST em fechamentos_operacao, sem retentativa automática."""
        previsto = fechamento.get("previsto") or {}
        # `desvio` é coluna GERADA no banco (realizado_liquido − previsto,
        # sem hedge) e nunca deve ser enviada; o desvio COM hedge fica na
        # explicação e no campo desvio_vs_previsto_liquido do dry-run.
        payload = {
            "operacao_id": fechamento["operacao_id"],
            "previsto": previsto.get("lucro_liquido_previsto"),
            "realizado_bruto": fechamento["realizado"]["lucro_bruto"],
            "realizado_liquido": fechamento["realizado"]["lucro_liquido"],
            "hedge_creditado": fechamento["realizado"]["hedge_creditado"],
            "explicacao": explicacao_texto(fechamento),
        }
        requisicao = urllib.request.Request(
            f"{self.url}/rest/v1/{TABELA_ESCRITA}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers=self._headers({
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            }),
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=self.timeout) as r:
                if r.status not in {200, 201, 204}:
                    raise RuntimeError(f"HTTP inesperado: {r.status}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"gravação do fechamento falhou com HTTP {exc.code}"
            ) from exc


def cliente_do_ambiente() -> ClienteFechamento:
    url = os.environ.get("SUPABASE_URL") or os.environ.get("CONFINEX_DB_URL") or ""
    chave = (
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("CONFINEX_DB_KEY")
        or ""
    )
    return ClienteFechamento(url, chave)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fechamento de rentabilidade por operação (dry-run padrão)"
    )
    parser.add_argument("--versao", action="store_true")
    parser.add_argument("--operacao", help="código humano, ex.: CF-26-003")
    parser.add_argument(
        "--entrada", help="dossiê JSON local (modo offline, sem rede)"
    )
    parser.add_argument("--executar", action="store_true")
    parser.add_argument("--confirmacao")
    args = parser.parse_args(argv)
    if args.versao:
        print(VERSAO_FECHAMENTO)
        return 0
    if args.entrada:
        with open(args.entrada, encoding="utf-8") as arquivo:
            dossie = json.load(arquivo)
    elif args.operacao:
        dossie = cliente_do_ambiente().dossie(args.operacao)
    else:
        parser.error("informe --operacao CODIGO ou --entrada dossie.json")
    fechamento = fechar_operacao(dossie)
    print(json.dumps(fechamento, ensure_ascii=False, indent=2, default=str))
    print("\n" + explicacao_texto(fechamento), file=sys.stderr)
    if not args.executar:
        print(
            "\nDRY-RUN: nada foi gravado. Para gravar em fechamentos_operacao: "
            f"--executar --confirmacao {fechamento['confirmacao']}",
            file=sys.stderr,
        )
        return 0
    if args.entrada:
        print("ERRO: gravação exige modo ao vivo (--operacao)", file=sys.stderr)
        return 2
    if args.confirmacao != fechamento["confirmacao"]:
        print(
            "ERRO: --confirmacao não corresponde ao fechamento atual "
            "(os dados mudaram desde o dry-run); rode o dry-run de novo.",
            file=sys.stderr,
        )
        return 2
    cliente_do_ambiente().gravar_fechamento(fechamento)
    print("Fechamento gravado em fechamentos_operacao.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
