#!/usr/bin/env python3
"""Nota explicativa de desvio de rentabilidade — F2 do circuito.

Recebe o mesmo dossiê do fechamento (F1), monta a CASCATA determinística que
liga o previsto congelado ao realizado e emite a nota em pt-BR sobre o que
fez a rentabilidade subir ou descer. Persiste em ``confinex_consolidacoes``
(a nota vai em ``comentario_geral``) e ``confinex_desvios`` (uma linha por
fator), no mesmo padrão dry-run → ``--executar --confirmacao <hash>``.

A cascata tem dois níveis e fecha aritmeticamente sempre:
- **componentes** (sempre computáveis): faturamento, encargos da venda,
  compra, frete, confinamento (trato/diária/etc.), demais custos, custo
  financeiro e hedge — cada um previsto × realizado QUANDO o lado previsto
  existir explicitamente na estimativa congelada;
- **resíduo não decomponível**: a diferença que os componentes com base
  prevista não explicam. Ele aparece como linha própria, nunca é distribuído
  nem escondido — em estimativas reconstruídas (retrospectivas) é onde mora
  a parte que a planilha não separou (ex.: hedge embutido no líquido).

Um componente sem base prevista mostra o realizado com estimado nulo e
classificação ``sem_base_prevista`` — informação, não chute. Regra do Pablo
(31/08/2026): Funrural é 0,2% em ~todos os casos; quando o encargo não
estiver informado, a nota apresenta o valor ESTIMADO a 0,2% claramente
rotulado, sem alterar os números oficiais do fechamento.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any, Mapping

try:
    from tools import fechar_rentabilidade_operacao as f1
except ImportError:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import fechar_rentabilidade_operacao as f1  # type: ignore[no-redef]

VERSAO_NOTA = "nota-desvio-v1.0.0"
FUNRURAL_PADRAO = 0.002  # 0,2% — regra declarada pelo Pablo em 31/08/2026
MATERIAL_ABS = 1000.0
MATERIAL_PCT_RECEITA = 0.005

# Vocabulário aceito pelos CHECKs de confinex_desvios (202607200001).
CLASSIFICACOES_BANCO = frozenset({"favoravel", "neutro", "desfavoravel"})
NATUREZA_BANCO = {
    "faturamento_bruto": "receita",
    "encargos_da_venda": "custo",
    "custo_compra": "custo",
    "frete": "custo",
    "custo_confinamento": "custo",
    "outros_custos_operacionais": "custo",
    "custo_financeiro": "custo",
    "hedge": "resultado",
    "residuo_nao_decomponivel": "resultado",
}


def _num(valor: Any) -> float | None:
    if valor is None or valor == "":
        return None
    return float(valor)


def _r2(valor: float) -> float:
    return round(valor + 0.0, 2)


# ---------------------------------------------------------------------------
# Bases previstas por componente (só o que existe explicitamente)
# ---------------------------------------------------------------------------


def bases_previstas(dossie: Mapping[str, Any]) -> dict[str, float | None]:
    """Extrai, da estimativa congelada, a base prevista de cada componente.

    Nada é derivado por subtração ou inferência: ou o número está declarado
    na estimativa (resultado ou premissas.dadosFonte), ou a base é ``None``.
    """
    estimativas = dossie.get("estimativas") or []
    originais = [e for e in estimativas if e.get("tipo") == "original"]
    est = (originais or estimativas)[:1]
    if not est:
        return {}
    resultado = est[0].get("resultado") or {}
    fonte = (est[0].get("premissas") or {}).get("dadosFonte") or {}
    custos_fonte = fonte.get("custos") or {}
    return {
        "faturamento": _num(resultado.get("receita")) or _num(fonte.get("fatTotal")),
        "compra": _num(resultado.get("custoCompra")) or _num(fonte.get("valorCompra")),
        "frete": _num(fonte.get("frete")),
        "confinamento": _num(custos_fonte.get("trato")),
        "lucro_bruto": _num(resultado.get("lucroBruto")),
        "lucro_liquido": _num(resultado.get("lucroLiquido")),
    }


# ---------------------------------------------------------------------------
# Cascata
# ---------------------------------------------------------------------------


def _classificar(desvio: float | None, favoravel_quando_positivo: bool) -> str:
    if desvio is None:
        return "sem_base_prevista"
    if abs(desvio) < 0.005:
        return "neutro"
    positivo = desvio > 0
    return (
        "favoravel"
        if positivo == favoravel_quando_positivo
        else "desfavoravel"
    )


def montar_cascata(dossie: Mapping[str, Any]) -> dict[str, Any]:
    fechamento = f1.fechar_operacao(dossie)
    realizado = fechamento["realizado"]
    bases = bases_previstas(dossie)
    receita_ref = bases.get("faturamento") or realizado["faturamento_bruto"]

    def material(desvio: float | None) -> bool:
        if desvio is None:
            return False
        limite = max(MATERIAL_ABS, abs(receita_ref) * MATERIAL_PCT_RECEITA)
        return abs(desvio) >= limite

    categorias = dict(realizado.get("custos_por_categoria") or {})
    frete_real = categorias.pop("frete", 0.0)
    confinamento_real = categorias.pop("trato", 0.0)
    financeiras = {
        nome: categorias.pop(nome)
        for nome in list(categorias)
        if nome in f1.CATEGORIAS_FINANCEIRAS
    }
    outros_custos_real = _r2(sum(categorias.values()))

    linhas: list[dict[str, Any]] = []

    def linha(indicador: str, natureza: str, estimado: float | None,
              realizado_v: float, favoravel_quando_positivo: bool,
              comentario: str) -> None:
        desvio = (
            None if estimado is None else _r2(realizado_v - estimado)
        )
        impacto = (
            None if desvio is None
            else _r2(desvio if favoravel_quando_positivo else -desvio)
        )
        pct = (
            None if desvio is None or not estimado
            else _r2(desvio / abs(estimado) * 100)
        )
        linhas.append({
            "indicador": indicador,
            "natureza": natureza,
            "estimado": estimado if estimado is None else _r2(estimado),
            "realizado": _r2(realizado_v),
            "desvio": desvio,
            "desvio_percentual": pct,
            "impacto_no_resultado": impacto,
            "classificacao": _classificar(impacto, True),
            "material": material(impacto),
            "comentario_automatico": comentario,
        })

    linha("faturamento_bruto", "componente", bases.get("faturamento"),
          realizado["faturamento_bruto"], True,
          "Receita bruta da venda/abate contra a receita prevista.")
    linha("encargos_da_venda", "componente", 0.0,
          realizado["funrural"] + realizado["finpec"]
          + realizado["outros_da_venda"], False,
          "Funrural + Finpec + outros descontos da venda (previsto explícito "
          "inexistente na estimativa ⇒ base 0 declarada).")
    linha("custo_compra", "componente", bases.get("compra"),
          realizado["custo_compra"], False,
          "Valor de compra do gado.")
    linha("frete", "componente", bases.get("frete"), frete_real, False,
          "Frete e transporte.")
    linha("custo_confinamento", "componente", bases.get("confinamento"),
          confinamento_real, False, "Trato/diária do confinamento.")
    if outros_custos_real:
        linha("outros_custos_operacionais", "componente", None,
              outros_custos_real, False,
              "Custos operacionais sem base prevista declarada.")
    linha("custo_financeiro", "componente", None,
          realizado["custo_financeiro"], False,
          "Custo do dinheiro (financeiro + juros de adiantamento); a "
          "estimativa congelada não o declara isolado do líquido.")
    linha("hedge", "componente", None, realizado["hedge_creditado"], True,
          "Resultado de hedge creditado à operação (linha própria; positivo "
          "soma, negativo subtrai).")

    previsto_liquido = bases.get("lucro_liquido")
    total_realizado = realizado["resultado_total_com_hedge"]
    desvio_total = (
        None if previsto_liquido is None
        else _r2(total_realizado - previsto_liquido)
    )
    impactos_explicados = _r2(sum(
        item["impacto_no_resultado"] for item in linhas
        if item["impacto_no_resultado"] is not None
    ))
    residuo = (
        None if desvio_total is None
        else _r2(desvio_total - impactos_explicados)
    )
    if residuo is not None:
        linhas.append({
            "indicador": "residuo_nao_decomponivel",
            "natureza": "residual",
            "estimado": None,
            "realizado": None,
            "desvio": residuo,
            "desvio_percentual": None,
            "impacto_no_resultado": residuo,
            "classificacao": _classificar(residuo, True),
            "material": material(residuo),
            "comentario_automatico": (
                "Parte do desvio que os componentes com base prevista não "
                "explicam — em estimativas reconstruídas, é onde mora o que "
                "a planilha embutiu no líquido sem separar (ex.: financeiro "
                "e hedge)."
            ),
        })

    cascata = {
        "versao": VERSAO_NOTA,
        "codigo": fechamento["codigo"],
        "operacao_id": fechamento["operacao_id"],
        "status_fechamento": fechamento["status_fechamento"],
        "previsto_liquido": previsto_liquido,
        "realizado_total_com_hedge": total_realizado,
        "desvio_total": desvio_total,
        "linhas": linhas,
        "pendencias": fechamento["pendencias"],
        "fechamento": fechamento,
    }
    cascata["nota"] = nota_pt_br(cascata)
    cascata["confirmacao"] = hashlib.sha256(json.dumps(
        {k: v for k, v in cascata.items() if k != "fechamento"},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        default=str,
    ).encode("utf-8")).hexdigest()
    return cascata


def _moeda(valor: float | None) -> str:
    if valor is None:
        return "—"
    texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


def nota_pt_br(cascata: Mapping[str, Any]) -> str:
    """Nota determinística: mesma cascata ⇒ mesma nota, sem prosa inventada."""
    partes: list[str] = []
    codigo = cascata["codigo"]
    if cascata["desvio_total"] is None:
        partes.append(
            f"{codigo}: sem estimativa congelada comparável — a nota "
            "apresenta apenas o realizado."
        )
    else:
        direcao = (
            "ACIMA" if cascata["desvio_total"] > 0 else
            "ABAIXO" if cascata["desvio_total"] < 0 else "IGUAL"
        )
        partes.append(
            f"{codigo}: resultado realizado (com hedge) de "
            f"{_moeda(cascata['realizado_total_com_hedge'])} contra "
            f"{_moeda(cascata['previsto_liquido'])} previstos — "
            f"{_moeda(abs(cascata['desvio_total']))} {direcao} do planejado."
        )
    materiais = [
        item for item in cascata["linhas"]
        if item["material"] and item["impacto_no_resultado"] is not None
    ]
    materiais.sort(key=lambda i: -abs(i["impacto_no_resultado"]))
    if materiais:
        fatores = []
        for item in materiais:
            sinal = "+" if item["impacto_no_resultado"] > 0 else "−"
            fatores.append(
                f"{item['indicador']} ({sinal}"
                f"{_moeda(abs(item['impacto_no_resultado']))[3:]})"
            )
        partes.append("Fatores materiais: " + "; ".join(fatores) + ".")
    sem_base = [
        item["indicador"] for item in cascata["linhas"]
        if item["classificacao"] == "sem_base_prevista"
        and item["natureza"] == "componente"
    ]
    if sem_base:
        partes.append(
            "Sem base prevista declarada (apenas realizado): "
            + ", ".join(sem_base) + "."
        )
    fechamento = cascata.get("fechamento") or {}
    realizado = fechamento.get("realizado") or {}
    if realizado.get("funrural") == 0.0 and realizado.get("faturamento_bruto"):
        estimado = _r2(realizado["faturamento_bruto"] * FUNRURAL_PADRAO)
        partes.append(
            "Funrural não informado no registro; a 0,2% (regra geral) seria "
            f"≈ {_moeda(estimado)} — valor ESTIMADO, fora dos números oficiais."
        )
    if cascata["pendencias"]:
        partes.append("Ressalvas: " + "; ".join(cascata["pendencias"]) + ".")
    return " ".join(partes)


# ---------------------------------------------------------------------------
# Persistência (confinex_consolidacoes + confinex_desvios)
# ---------------------------------------------------------------------------


class ClienteNota:
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

    def _post(self, tabela: str, payload: Any,
              representacao: bool = False) -> Any:
        if tabela not in ("confinex_consolidacoes", "confinex_desvios"):
            raise ValueError(f"escrita não permitida: {tabela}")
        prefer = "return=representation" if representacao else "return=minimal"
        requisicao = urllib.request.Request(
            f"{self.url}/rest/v1/{tabela}",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="POST",
            headers=self._headers({
                "Content-Type": "application/json", "Prefer": prefer,
            }),
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=self.timeout) as r:
                corpo = r.read().decode("utf-8")
                if r.status not in {200, 201, 204}:
                    raise RuntimeError(f"HTTP inesperado em {tabela}: {r.status}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"escrita em {tabela} falhou com HTTP {exc.code}"
            ) from exc
        return json.loads(corpo) if corpo else None


def payloads_persistencia(
    cascata: Mapping[str, Any], avaliacao_id: str, estimativa_versao: int
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    fechamento = cascata["fechamento"]
    consolidacao = {
        "avaliacao_id": avaliacao_id,
        "estimativa_versao": estimativa_versao,
        "realizado": fechamento["realizado"],
        "resultado_final": {
            "versao_nota": cascata["versao"],
            "status_fechamento": cascata["status_fechamento"],
            "previsto_liquido": cascata["previsto_liquido"],
            "realizado_total_com_hedge": cascata["realizado_total_com_hedge"],
            "desvio_total": cascata["desvio_total"],
            "confirmacao": cascata["confirmacao"],
        },
        "comentario_geral": cascata["nota"],
    }
    # confinex_desvios calcula `desvio` e `desvio_percentual` como colunas
    # GERADAS (realizado − estimado); enviá-las é erro 428C9. A linha
    # residual persiste estimado=0 (o resíduo esperado é sempre zero) e
    # realizado=<resíduo apurado>, para que a coluna gerada reproduza o
    # desvio sem inventar um "realizado" de componente.
    #
    # O vocabulário interno da cascata é mais rico que os CHECKs do banco
    # (migração 202607200001): natureza ∈ {custo, receita, resultado, prazo,
    # zootecnico} e classificacao ∈ {favoravel, neutro, desfavoravel}. A
    # persistência mapeia sem perder informação: "sem_base_prevista" vira
    # classificacao NULL com o fato explícito no comentário; a cascata
    # completa segue íntegra em resultado_final/comentario_geral.
    desvios = []
    for item in cascata["linhas"]:
        estimado, realizado = item["estimado"], item["realizado"]
        if item["natureza"] == "residual":
            estimado, realizado = 0.0, item["desvio"]
        classificacao = item["classificacao"]
        comentario = item["comentario_automatico"]
        if classificacao not in CLASSIFICACOES_BANCO:
            comentario = f"SEM BASE PREVISTA — {comentario}"
            classificacao = None
        desvios.append({
            "indicador": item["indicador"],
            "natureza": NATUREZA_BANCO.get(item["indicador"], "resultado"),
            "estimado": estimado,
            "realizado": realizado,
            "classificacao": classificacao,
            "comentario_automatico": comentario,
            "material": item["material"],
        })
    return consolidacao, desvios


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Nota explicativa de desvio (dry-run padrão)"
    )
    parser.add_argument("--versao", action="store_true")
    parser.add_argument("--operacao")
    parser.add_argument("--entrada")
    parser.add_argument("--executar", action="store_true")
    parser.add_argument("--confirmacao")
    args = parser.parse_args(argv)
    if args.versao:
        print(VERSAO_NOTA)
        return 0
    if args.entrada:
        with open(args.entrada, encoding="utf-8") as arquivo:
            dossie = json.load(arquivo)
    elif args.operacao:
        dossie = f1.cliente_do_ambiente().dossie(args.operacao)
    else:
        parser.error("informe --operacao CODIGO ou --entrada dossie.json")
    cascata = montar_cascata(dossie)
    saida = {k: v for k, v in cascata.items() if k != "fechamento"}
    print(json.dumps(saida, ensure_ascii=False, indent=2, default=str))
    print("\nNOTA: " + cascata["nota"], file=sys.stderr)
    if not args.executar:
        print(
            "\nDRY-RUN: nada foi gravado. Para gravar consolidação+desvios: "
            f"--executar --confirmacao {cascata['confirmacao']}",
            file=sys.stderr,
        )
        return 0
    if args.entrada:
        print("ERRO: gravação exige modo ao vivo (--operacao)", file=sys.stderr)
        return 2
    if args.confirmacao != cascata["confirmacao"]:
        print(
            "ERRO: --confirmacao não corresponde à cascata atual; rode o "
            "dry-run de novo.",
            file=sys.stderr,
        )
        return 2
    avaliacao = dossie.get("avaliacao") or {}
    estimativas = dossie.get("estimativas") or []
    if not avaliacao.get("id") or not estimativas:
        print("ERRO: operação sem avaliação/estimativa — nada a consolidar",
              file=sys.stderr)
        return 2
    consolidacao, desvios = payloads_persistencia(
        cascata, str(avaliacao["id"]),
        int(estimativas[0].get("versao") or 1),
    )
    cliente = ClienteNota(
        os.environ.get("SUPABASE_URL") or os.environ.get("CONFINEX_DB_URL") or "",
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("CONFINEX_DB_KEY") or "",
    )
    criada = cliente._post("confinex_consolidacoes", consolidacao, True)
    consolidacao_id = str(criada[0]["id"])
    for desvio in desvios:
        desvio["consolidacao_id"] = consolidacao_id
    cliente._post("confinex_desvios", desvios)
    print(f"Consolidação {consolidacao_id} gravada com "
          f"{len(desvios)} desvios.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
