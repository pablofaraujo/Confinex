"""Dossiê da operação (F3) — documento legível por confinamento/lote.

Renderiza, a partir do MESMO dossiê JSON que alimenta o F1/F2, um documento
Markdown determinístico com tudo que o Pablo pediu para o Drive: contrato e
trava (hedge), acerto de abate, pagamento e a rentabilidade projetada ×
executada com a nota explicativa do desvio. Nenhum número é inventado: o
que não está no dossiê aparece como "não informado".

Este módulo NÃO acessa rede e NÃO grava em lugar nenhum — quem coloca o
arquivo no Drive é o operador (ou uma missão futura). Ele é a LÓGICA do
documento, versionada e testada, dentro do princípio do cérebro de solução.

Uso:
  python3 tools/dossie_operacao.py --entrada dossie.json           # stdout
  python3 tools/dossie_operacao.py --entrada dossie.json --saida X.md
"""

from __future__ import annotations

import argparse
import sys
from typing import Any, Mapping

from tools import fechar_rentabilidade_operacao as f1
from tools import nota_desvio_operacao as f2

VERSAO_DOSSIE = "dossie-operacao-v1.0.0"


def _moeda(valor: Any) -> str:
    if valor is None or valor == "":
        return "não informado"
    texto = f"{float(valor):,.2f}"
    texto = texto.replace(",", "@").replace(".", ",").replace("@", ".")
    return f"R$ {texto}"


def _data(valor: Any) -> str:
    if not valor:
        return "não informado"
    return str(valor)[:10]


def _linha_tabela(colunas: list[str]) -> str:
    return "| " + " | ".join(colunas) + " |"


def renderizar_dossie(dossie: Mapping[str, Any]) -> str:
    """Documento Markdown determinístico da operação. Puro; sem rede."""
    operacao = dossie["operacao"]
    codigo = str(operacao.get("codigo") or "?")
    fechamento = f1.fechar_operacao(dossie)
    cascata = f2.montar_cascata(dossie)
    realizado = fechamento["realizado"]
    previsto = fechamento.get("previsto") or {}

    partes: list[str] = []
    partes.append(f"# Dossiê {codigo}")
    partes.append("")
    partes.append(
        f"Gerado por {VERSAO_DOSSIE} sobre {f1.VERSAO_FECHAMENTO} + "
        f"{f2.VERSAO_NOTA}. Confirmações: fechamento "
        f"`{fechamento['confirmacao'][:16]}…`, cascata "
        f"`{cascata['confirmacao'][:16]}…`."
    )
    partes.append("")

    # -- 1. Identificação ------------------------------------------------
    partes.append("## 1. Identificação")
    partes.append("")
    partes.append(f"- Operação: **{codigo}** ({operacao.get('status') or '?'})")
    partes.append(f"- Modalidade: {operacao.get('modalidade') or 'não informado'}"
                  f" · Tipo: {operacao.get('tipo_negocio') or 'não informado'}"
                  f" · Sexo: {operacao.get('sexo') or 'não informado'}")
    if operacao.get("obs"):
        partes.append(f"- Observação: {operacao['obs']}")
    participantes = dossie.get("participantes") or []
    for p in participantes:
        pct = p.get("participacao_pct")
        partes.append(
            f"- Participante: {p.get('papel') or '?'}"
            + (f" ({pct}%)" if pct is not None else "")
            + (f" — {p['observacoes']}" if p.get("observacoes") else "")
        )
    partes.append("")

    # -- 2. Compra e entrada --------------------------------------------
    partes.append("## 2. Compra e entrada")
    partes.append("")
    for compra in dossie.get("compras") or []:
        pago = "paga" if compra.get("pago") else "NÃO paga"
        partes.append(
            f"- Compra de {compra.get('quantidade') or '?'} cab em "
            f"{_data(compra.get('data'))}: {_moeda(compra.get('valor_total'))}"
            f" ({pago}"
            + (f" em {_data(compra.get('data_pagamento'))}"
               if compra.get("data_pagamento") else "")
            + ")"
        )
    for entrada in dossie.get("entradas") or []:
        partes.append(
            f"- Entrada em {_data(entrada.get('data_entrada'))}: "
            f"{entrada.get('cabecas') or '?'} cab, curral "
            f"{entrada.get('curral') or '?'}"
            + (f", GTA {entrada['gta']}" if entrada.get("gta") else "")
            + (f", NF {entrada['nf']}" if entrada.get("nf") else "")
        )
    if not (dossie.get("compras") or dossie.get("entradas")):
        partes.append("- não informado")
    partes.append("")

    # -- 3. Contrato e trava (hedge) ------------------------------------
    partes.append("## 3. Contrato e trava (hedge)")
    partes.append("")
    if operacao.get("contrato_confinamento"):
        partes.append(
            f"- Contrato de confinamento: {operacao['contrato_confinamento']}"
        )
    itens_hedge = dossie.get("hedge") or []
    if itens_hedge:
        for item in itens_hedge:
            posicao = item.get("posicao") or {}
            alocacao = item.get("alocacao") or {}
            partes.append(
                f"- {posicao.get('contrato') or '?'} "
                f"{posicao.get('direcao') or '?'} "
                f"({posicao.get('referencia_bolsa') or '?'}): "
                f"{alocacao.get('contratos_qtd') or '?'} contrato(s) "
                f"alocado(s), entrada {posicao.get('preco_entrada') or '?'} → "
                f"saída {posicao.get('preco_saida') or '?'}, resultado "
                f"creditado {_moeda(alocacao.get('resultado_creditado'))} "
                f"[{posicao.get('status') or '?'}]"
            )
    else:
        partes.append("- Sem posição de hedge alocada a esta operação.")
    partes.append("")

    # -- 4. Abate, acerto e pagamento -----------------------------------
    partes.append("## 4. Abate, acerto e pagamento")
    partes.append("")
    for venda in dossie.get("vendas") or []:
        recebida = "recebida" if venda.get("recebido") else "NÃO recebida"
        partes.append(
            f"- Abate/venda em {_data(venda.get('data_abate'))}: "
            f"{venda.get('cabecas') or '?'} cab, bruto "
            f"{_moeda(venda.get('valor_bruto'))}, prazo "
            f"{_data(venda.get('prazo_recebimento'))} ({recebida})"
        )
        partes.append(
            f"  - Encargos: Funrural "
            f"{_moeda(venda.get('funrural')) if venda.get('funrural') is not None else 'não informado'}"
            f" · Finpec "
            f"{_moeda(venda.get('finpec')) if venda.get('finpec') is not None else 'não informado'}"
            f" · Outros {_moeda(venda.get('outros_custos') or 0)}"
        )
    for acerto in dossie.get("acertos") or []:
        partes.append(
            f"- Acerto: status **{acerto.get('status') or '?'}**"
            + (f", recebido em {_data(acerto['data_recebimento'])}"
               if acerto.get("data_recebimento")
               else ", sem data de recebimento")
            + (f", valor recebido {_moeda(acerto['valor_recebido'])}"
               if acerto.get("valor_recebido") is not None else "")
        )
    if not (dossie.get("vendas") or dossie.get("acertos")):
        partes.append("- não informado")
    partes.append("")

    # -- 5. Rentabilidade projetada × executada -------------------------
    partes.append("## 5. Rentabilidade projetada × executada")
    partes.append("")
    partes.append(_linha_tabela(["Indicador", "Projetado", "Executado"]))
    partes.append(_linha_tabela(["---", "---", "---"]))
    partes.append(_linha_tabela([
        "Receita bruta", _moeda(previsto.get("receita_prevista")),
        _moeda(realizado["faturamento_bruto"]),
    ]))
    partes.append(_linha_tabela([
        "Lucro bruto", _moeda(previsto.get("lucro_bruto_previsto")),
        _moeda(realizado["lucro_bruto"]),
    ]))
    partes.append(_linha_tabela([
        "Lucro líquido", _moeda(previsto.get("lucro_liquido_previsto")),
        _moeda(realizado["lucro_liquido"]),
    ]))
    partes.append(_linha_tabela([
        "Hedge creditado", "—", _moeda(realizado["hedge_creditado"]),
    ]))
    partes.append(_linha_tabela([
        "**Total com hedge**", "—",
        _moeda(realizado["resultado_total_com_hedge"]),
    ]))
    partes.append("")
    if cascata["desvio_total"] is not None:
        partes.append(f"Desvio total vs projetado (com hedge): "
                      f"**{_moeda(cascata['desvio_total'])}**.")
    else:
        partes.append("Sem estimativa congelada — comparativo indisponível.")
    if previsto.get("reconstrucao_retrospectiva"):
        partes.append("")
        partes.append("> ⚠️ A projeção é RECONSTRUÇÃO retrospectiva da "
                      "planilha, não previsão contemporânea.")
    partes.append("")

    # -- 6. Nota explicativa do desvio ----------------------------------
    partes.append("## 6. Nota explicativa do desvio")
    partes.append("")
    partes.append(cascata["nota"])
    partes.append("")

    # -- 7. Pendências ---------------------------------------------------
    partes.append("## 7. Pendências")
    partes.append("")
    pendencias = fechamento["pendencias"]
    if pendencias:
        for pendencia in pendencias:
            partes.append(f"- {pendencia}")
    else:
        partes.append("- Nenhuma. Fechamento COMPLETO.")
    partes.append("")
    partes.append(f"Status do fechamento: **{fechamento['status_fechamento']}**.")
    partes.append("")
    return "\n".join(partes)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Dossiê Markdown da operação (offline; não grava nada)"
    )
    parser.add_argument("--versao", action="store_true")
    parser.add_argument("--entrada", help="dossiê JSON (mesmo do F1/F2)")
    parser.add_argument("--saida", help="arquivo .md de saída (senão stdout)")
    args = parser.parse_args(argv)
    if args.versao:
        print(VERSAO_DOSSIE)
        return 0
    if not args.entrada:
        parser.error("informe --entrada dossie.json")
    import json
    with open(args.entrada, encoding="utf-8") as arquivo:
        dossie = json.load(arquivo)
    documento = renderizar_dossie(dossie)
    if args.saida:
        with open(args.saida, "w", encoding="utf-8") as arquivo:
            arquivo.write(documento)
        print(f"dossiê gravado em {args.saida}", file=sys.stderr)
    else:
        print(documento)
    return 0


if __name__ == "__main__":
    sys.exit(main())
