"""Cérebro de solução da rentabilidade executada.

Aplica a lógica F1 (fechamento) + F2 (nota/cascata de desvio) sobre as
operações elegíveis e PROPÕE soluções — nunca as executa. Toda proposta
nasce como `pending_actions` NÃO EXECUTÁVEL (executavel=false) mais um
`eventos` de rastro; a gravação de fechamentos e consolidações continua
exclusiva das ferramentas dedicadas, após o gate humano na fila.

Princípio (Pablo, 31/08/2026): quem soluciona é o Confinex e os agentes;
este módulo é a lógica que o sistema roda sozinho — determinística,
idempotente e sem inventar dados.

Decisões que o cérebro sabe tomar por operação:
- ``fechar``: operação elegível sem fechamento gravado.
- ``refechar``: fechamento gravado diverge do recalculado (números com
  tolerância de 1 centavo, ou conjunto de pendências mudou — ex.:
  recebimento comprovado depois do fechamento PARCIAL).
- ``consolidar``: avaliação com estimativa sem consolidação gravada.
- ``reconsolidar``: consolidação gravada com confirmação (sha256 da
  cascata) diferente da recalculada.
- ``em_dia``: nada a propor.

Idempotência: a chave de proposta é derivada do código da operação, da
decisão e das confirmações recalculadas. Se já existe QUALQUER
pending_action do cérebro com a mesma chave (inclusive rejeitada), a
proposta não é repetida — rejeição humana é respeitada até os dados
mudarem (o que muda a confirmação e, portanto, a chave).

Uso:
  python3 tools/cerebro_rentabilidade.py --varrer            # dry-run
  python3 tools/cerebro_rentabilidade.py --codigo CF-26-003  # dry-run
  ... --varrer --executar --confirmacao <hash>               # grava propostas
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
from typing import Any, Mapping

from tools import fechar_rentabilidade_operacao as f1
from tools import nota_desvio_operacao as f2

VERSAO_CEREBRO = "cerebro-rentabilidade-v1.0.0"
AGENTE = "cerebro_rentabilidade"
STATUS_ELEGIVEIS = ("abatida", "liquidada")
STATUS_PROPOSTA_VIVA = (
    "aguardando_confirmacao", "confirmado_telegram", "em_revisao",
    "aprovado_confinex", "rejeitado",
)
TOLERANCIA = 0.01
ESCRITA_PERMITIDA = frozenset({"pending_actions", "eventos"})


def _r2(valor: Any) -> float | None:
    if valor is None or valor == "":
        return None
    return round(float(valor) + 0.0, 2)


def _difere(a: Any, b: Any) -> bool:
    ra, rb = _r2(a), _r2(b)
    if ra is None or rb is None:
        return ra is not rb and ra != rb
    return abs(ra - rb) > TOLERANCIA


def _sha(objeto: Any) -> str:
    canonico = json.dumps(objeto, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def avaliar_operacao(
    dossie: Mapping[str, Any],
    fechamento_gravado: Mapping[str, Any] | None,
    consolidacao_gravada: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Decisão pura do cérebro para UMA operação. Não toca rede."""
    codigo = str(dossie["operacao"].get("codigo") or "?")
    fechamento = f1.fechar_operacao(dossie)
    cascata = f2.montar_cascata(dossie)
    realizado = fechamento["realizado"]
    previsto = (fechamento.get("previsto") or {})
    previsto_liquido = previsto.get("lucro_liquido_previsto")

    decisoes: list[dict[str, Any]] = []

    if fechamento_gravado is None:
        decisoes.append({
            "decisao": "fechar",
            "motivo": "operação elegível sem fechamento gravado",
        })
    else:
        diffs = []
        pares = (
            ("previsto", fechamento_gravado.get("previsto"), previsto_liquido),
            ("realizado_bruto", fechamento_gravado.get("realizado_bruto"),
             realizado["lucro_bruto"]),
            ("realizado_liquido", fechamento_gravado.get("realizado_liquido"),
             realizado["lucro_liquido"]),
            ("hedge_creditado", fechamento_gravado.get("hedge_creditado"),
             realizado["hedge_creditado"]),
        )
        for campo, gravado, atual in pares:
            if _difere(gravado, atual):
                diffs.append({"campo": campo, "gravado": _r2(gravado),
                              "recalculado": _r2(atual)})
        pendencias_novas = fechamento["pendencias"]
        explicacao_gravada = str(fechamento_gravado.get("explicacao") or "")
        pendencias_resolvidas = [
            "Recebimento da venda não comprovado"
            for _ in [0]
            if "Recebimento da venda não comprovado" in explicacao_gravada
            and not any("Recebimento" in p for p in pendencias_novas)
        ]
        if diffs or pendencias_resolvidas:
            decisoes.append({
                "decisao": "refechar",
                "motivo": ("números divergem do gravado" if diffs
                           else "pendência resolvida desde o fechamento"),
                "diffs": diffs,
                "pendencias_resolvidas": pendencias_resolvidas,
            })

    tem_avaliacao = bool(dossie.get("avaliacao"))
    tem_estimativa = bool(dossie.get("estimativas"))
    if tem_avaliacao and tem_estimativa:
        if consolidacao_gravada is None:
            decisoes.append({
                "decisao": "consolidar",
                "motivo": "avaliação com estimativa sem consolidação gravada",
            })
        else:
            gravada = str(
                (consolidacao_gravada.get("resultado_final") or {})
                .get("confirmacao") or ""
            )
            if gravada != cascata["confirmacao"]:
                decisoes.append({
                    "decisao": "reconsolidar",
                    "motivo": "cascata recalculada diverge da consolidada",
                    "confirmacao_gravada": gravada,
                })

    if not decisoes:
        decisoes.append({"decisao": "em_dia", "motivo": "nada a propor"})

    resumo_num = {
        "status_fechamento": fechamento["status_fechamento"],
        "previsto_liquido": _r2(previsto_liquido),
        "lucro_bruto": _r2(realizado["lucro_bruto"]),
        "lucro_liquido": _r2(realizado["lucro_liquido"]),
        "hedge_creditado": _r2(realizado["hedge_creditado"]),
        "resultado_total_com_hedge": _r2(
            realizado["resultado_total_com_hedge"]
        ),
        "desvio_total": cascata["desvio_total"],
    }
    chave = _sha({
        "versao": VERSAO_CEREBRO,
        "codigo": codigo,
        "decisoes": [d["decisao"] for d in decisoes],
        "confirmacao_f1": fechamento["confirmacao"],
        "confirmacao_f2": cascata["confirmacao"],
    })[:32]
    return {
        "versao": VERSAO_CEREBRO,
        "codigo": codigo,
        "operacao_id": dossie["operacao"].get("id"),
        "decisoes": decisoes,
        "acionavel": any(d["decisao"] != "em_dia" for d in decisoes),
        "resumo": resumo_num,
        "pendencias": fechamento["pendencias"],
        "nota": cascata["nota"],
        "confirmacao_f1": fechamento["confirmacao"],
        "confirmacao_f2": cascata["confirmacao"],
        "chave_proposta": chave,
    }


def proposta_payload(avaliacao: Mapping[str, Any]) -> dict[str, Any]:
    """Payload da pending_action — informativo, sem campos de promoção."""
    return {
        "versao": avaliacao["versao"],
        "chave_proposta": avaliacao["chave_proposta"],
        "decisoes": avaliacao["decisoes"],
        "resumo": avaliacao["resumo"],
        "pendencias": avaliacao["pendencias"],
        "nota": avaliacao["nota"],
        "confirmacao_f1": avaliacao["confirmacao_f1"],
        "confirmacao_f2": avaliacao["confirmacao_f2"],
        "aplicacao": (
            "Gate humano: aprovar executa as ferramentas dedicadas "
            "(fechar_rentabilidade_operacao / nota_desvio_operacao); "
            "esta ação não é executável pelo promotor legado."
        ),
    }


def resumo_texto(avaliacao: Mapping[str, Any]) -> str:
    decisoes = "+".join(d["decisao"] for d in avaliacao["decisoes"])
    r = avaliacao["resumo"]
    return (
        f"Cérebro rentabilidade — {avaliacao['codigo']}: {decisoes}. "
        f"Total com hedge R$ {r['resultado_total_com_hedge']:.2f}"
        + (f"; desvio R$ {r['desvio_total']:.2f}"
           if r.get("desvio_total") is not None else "")
        + f" ({r['status_fechamento']})."
    )


class ClienteCerebro:
    """Leituras livres; escrita SÓ em pending_actions e eventos."""

    def __init__(self, url: str, chave: str, timeout: int = 20) -> None:
        if not url or not chave:
            raise ValueError("configuracao_incompleta")
        self.url = url.rstrip("/")
        self.chave = chave
        self.timeout = max(1, min(int(timeout), 20))
        self.fechador = f1.ClienteFechamento(url, chave, timeout)

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        base = {"apikey": self.chave, "Authorization": f"Bearer {self.chave}"}
        base.update(extra or {})
        return base

    def _get(self, tabela: str, consulta: dict[str, str]) -> list[dict[str, Any]]:
        return self.fechador._get(tabela, consulta)

    def _post(self, tabela: str, corpo: Any) -> None:
        if tabela not in ESCRITA_PERMITIDA:
            raise ValueError(f"escrita em {tabela} não permitida ao cérebro")
        dados = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        requisicao = urllib.request.Request(
            f"{self.url}/rest/v1/{tabela}", data=dados, method="POST",
            headers=self._headers({"Content-Type": "application/json",
                                   "Prefer": "return=minimal"}),
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=self.timeout):
                pass
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"escrita em {tabela} falhou com HTTP {exc.code}"
            ) from exc

    # -- consultas do estado gravado ------------------------------------
    def operacoes_elegiveis(self) -> list[dict[str, Any]]:
        return self._get("operacoes", {
            "status": "in.(" + ",".join(STATUS_ELEGIVEIS) + ")",
            "select": "id,codigo,status",
            "order": "codigo.asc",
        })

    def fechamento_mais_recente(self, operacao_id: str) -> dict[str, Any] | None:
        linhas = self._get("fechamentos_operacao", {
            "operacao_id": "eq." + operacao_id,
            "order": "fechado_em.desc",
            "limit": "1",
        })
        return linhas[0] if linhas else None

    def consolidacao_mais_recente(
        self, avaliacao_id: str
    ) -> dict[str, Any] | None:
        linhas = self._get("confinex_consolidacoes", {
            "avaliacao_id": "eq." + avaliacao_id,
            "order": "consolidado_em.desc",
            "limit": "1",
        })
        return linhas[0] if linhas else None

    def proposta_ja_existe(self, chave: str) -> bool:
        linhas = self._get("pending_actions", {
            "agente": "eq." + AGENTE,
            "payload->>chave_proposta": "eq." + chave,
            "status": "in.(" + ",".join(STATUS_PROPOSTA_VIVA) + ")",
            "select": "id",
            "limit": "1",
        })
        return bool(linhas)

    # -- escrita da proposta (única escrita do cérebro) -----------------
    def propor(self, avaliacao: Mapping[str, Any]) -> None:
        self._post("pending_actions", {
            "agente": AGENTE,
            "canal": "sistema",
            "acao_tipo": "proposta_rentabilidade",
            "entidade_tipo": "operacao",
            "entidade_id": avaliacao["operacao_id"],
            "entidade_codigo": avaliacao["codigo"],
            "resumo": resumo_texto(avaliacao),
            "payload": proposta_payload(avaliacao),
            "executavel": False,
        })
        self._post("eventos", {
            "tipo": "cerebro_rentabilidade_proposta",
            "agente": AGENTE,
            "entidade_tipo": "operacao",
            "entidade_id": avaliacao["operacao_id"],
            "entidade_codigo": avaliacao["codigo"],
            "origem": "sistema",
            "dados": {
                "chave_proposta": avaliacao["chave_proposta"],
                "decisoes": [d["decisao"] for d in avaliacao["decisoes"]],
                "confirmacao_f1": avaliacao["confirmacao_f1"],
                "confirmacao_f2": avaliacao["confirmacao_f2"],
            },
            "observacao": resumo_texto(avaliacao),
        })


def executar_varredura(
    cliente: ClienteCerebro, codigos: list[str] | None
) -> dict[str, Any]:
    """Monta o plano da rodada (sem escrever nada)."""
    operacoes = cliente.operacoes_elegiveis()
    if codigos:
        alvo = set(codigos)
        operacoes = [o for o in operacoes if o["codigo"] in alvo]
    plano: list[dict[str, Any]] = []
    for operacao in operacoes:
        dossie = cliente.fechador.dossie(str(operacao["codigo"]))
        gravado = cliente.fechamento_mais_recente(str(operacao["id"]))
        consolidada = None
        if dossie.get("avaliacao"):
            consolidada = cliente.consolidacao_mais_recente(
                str(dossie["avaliacao"]["id"])
            )
        avaliacao = avaliar_operacao(dossie, gravado, consolidada)
        if not avaliacao["acionavel"]:
            plano.append({"codigo": avaliacao["codigo"],
                          "acao": "em_dia"})
            continue
        if cliente.proposta_ja_existe(avaliacao["chave_proposta"]):
            plano.append({"codigo": avaliacao["codigo"],
                          "acao": "ja_proposta",
                          "chave": avaliacao["chave_proposta"]})
            continue
        plano.append({"codigo": avaliacao["codigo"], "acao": "propor",
                      "avaliacao": avaliacao})
    return {
        "versao": VERSAO_CEREBRO,
        "plano": plano,
        "confirmacao": _sha([
            {"codigo": p["codigo"], "acao": p["acao"],
             "chave": p.get("avaliacao", {}).get("chave_proposta")
             or p.get("chave")}
            for p in plano
        ]),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Cérebro de rentabilidade (propõe; nunca executa)"
    )
    parser.add_argument("--versao", action="store_true")
    parser.add_argument("--varrer", action="store_true")
    parser.add_argument("--codigo", action="append")
    parser.add_argument("--executar", action="store_true")
    parser.add_argument("--confirmacao")
    args = parser.parse_args(argv)
    if args.versao:
        print(VERSAO_CEREBRO)
        return 0
    if not args.varrer and not args.codigo:
        parser.error("informe --varrer ou --codigo CODIGO")

    cliente = ClienteCerebro(
        os.environ.get("SUPABASE_URL")
        or os.environ.get("CONFINEX_DB_URL") or "",
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("CONFINEX_DB_KEY") or "",
    )
    rodada = executar_varredura(cliente, args.codigo)
    propostas = [p for p in rodada["plano"] if p["acao"] == "propor"]
    print(json.dumps({
        "versao": rodada["versao"],
        "resumo": {p["codigo"]: p["acao"] for p in rodada["plano"]},
        "propostas": [p["avaliacao"]["chave_proposta"] for p in propostas],
        "confirmacao": rodada["confirmacao"],
    }, ensure_ascii=False, indent=1))

    if not args.executar:
        print("\nDry-run. Para gravar as propostas: --executar "
              f"--confirmacao {rodada['confirmacao']}", file=sys.stderr)
        return 0
    if args.confirmacao != rodada["confirmacao"]:
        print("confirmação não confere com o plano atual", file=sys.stderr)
        return 2
    for proposta in propostas:
        cliente.propor(proposta["avaliacao"])
    print(f"{len(propostas)} proposta(s) gravada(s) na fila.",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
