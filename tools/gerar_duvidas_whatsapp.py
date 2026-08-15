#!/usr/bin/env python3
"""Gera pendências pesquisáveis no WhatsApp sem alterar dados operacionais.

Combina dúvidas privadas já conhecidas com abates cujo acerto ainda não está
completo. A saída alimenta a automação do WACLI e contém os contatos vinculados
ao confinamento. O fluxo é estritamente de leitura no Supabase.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    from .confinex_client import ConfinexClient, ConfinexError
except ImportError:  # execução direta na VPS
    from confinex_client import ConfinexClient, ConfinexError


STATUS_ACERTO_FINAL = {
    "conciliado", "finalizado", "liquidado", "pago", "recebido", "ressarcido",
}


def carregar_duvidas_base(caminho: Path | None) -> list[dict[str, Any]]:
    if not caminho or not caminho.exists():
        return []
    bruto = json.loads(caminho.read_text(encoding="utf-8"))
    itens = bruto.get("duvidas", bruto) if isinstance(bruto, dict) else bruto
    if not isinstance(itens, list):
        raise ValueError("arquivo-base de dúvidas deve conter uma lista")
    return [item for item in itens if isinstance(item, dict)]


def data_br(valor: Any) -> str | None:
    if not valor:
        return None
    try:
        return datetime.strptime(str(valor)[:10], "%Y-%m-%d").strftime("%d/%m/%Y")
    except ValueError:
        return str(valor)


def acerto_final(item: dict[str, Any]) -> bool:
    status = str(item.get("status") or "").strip().lower()
    if status in STATUS_ACERTO_FINAL:
        return True
    return bool(
        item.get("documento_id")
        and item.get("data_recebimento")
        and item.get("valor_recebido") is not None
    )


def contatos_do_confinamento(
    confinamento_id: str,
    vinculos: list[dict[str, Any]],
    contatos: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    resultado = []
    for vinculo in vinculos:
        if str(vinculo.get("confinamento_id")) != confinamento_id:
            continue
        contato = contatos.get(str(vinculo.get("contato_id")))
        if not contato:
            continue
        resultado.append({
            "nome": contato.get("nome"),
            "whatsapp": contato.get("whatsapp") or contato.get("telefone"),
            "papel": vinculo.get("papel"),
            "principal": bool(vinculo.get("principal")),
        })
    return sorted(
        resultado,
        key=lambda item: (not item["principal"], str(item.get("nome") or "")),
    )


def gerar_pendencias_acertos(
    cliente: ConfinexClient,
    *,
    hoje: date | None = None,
    janela_dias: int = 180,
) -> list[dict[str, Any]]:
    hoje = hoje or date.today()
    inicio = hoje - timedelta(days=max(1, janela_dias))
    operacoes = cliente.select(
        "operacoes", select="id,codigo,confinamento_id,status", limit=2000,
    )
    abates = cliente.select(
        "abates",
        select="id,operacao_id,data_abate,quantidade,romaneio,frigorifico",
        data_abate=f"gte.{inicio.isoformat()}",
        order="data_abate.desc",
        limit=2000,
    )
    acertos = cliente.select(
        "acertos",
        select=("id,operacao_id,status,data_emissao,data_recebimento,"
                "valor_recebido,documento_id"),
        limit=2000,
    )
    confinamentos = cliente.select("confinamentos", select="id,nome", limit=1000)
    vinculos = cliente.select(
        "confinamento_contatos",
        select="confinamento_id,contato_id,papel,principal",
        limit=2000,
    )
    linhas_contatos = cliente.select(
        "contatos", select="id,nome,telefone,whatsapp", limit=2000,
    )

    operacoes_id = {str(item["id"]): item for item in operacoes}
    confinamentos_id = {str(item["id"]): item for item in confinamentos}
    contatos_id = {str(item["id"]): item for item in linhas_contatos}
    acertos_por_operacao: dict[str, list[dict[str, Any]]] = {}
    for item in acertos:
        acertos_por_operacao.setdefault(str(item.get("operacao_id")), []).append(item)

    pendencias = []
    for abate in abates:
        operacao_id = str(abate.get("operacao_id") or "")
        operacao = operacoes_id.get(operacao_id)
        if not operacao or not operacao.get("confinamento_id"):
            continue
        existentes = acertos_por_operacao.get(operacao_id, [])
        if any(acerto_final(item) for item in existentes):
            continue
        data_abate = str(abate.get("data_abate") or "")
        quantidade = int(abate.get("quantidade") or 0)
        confinamento_id = str(operacao["confinamento_id"])
        confinamento = confinamentos_id.get(confinamento_id, {})
        codigo = str(operacao.get("codigo") or operacao_id)
        status = "acerto_ausente" if not existentes else "acerto_incompleto"
        termos = [
            str(quantidade) if quantidade else "",
            "acerto", "relatório", "romaneio", "abate",
            data_br(data_abate) or "", codigo,
            str(confinamento.get("nome") or ""),
        ]
        pendencias.append({
            "codigo": f"ACERTO-{codigo}-{data_abate}-{quantidade}",
            "operacao_codigo": codigo,
            "negocio": (
                f"{confinamento.get('nome') or 'Confinamento'} - "
                f"{quantidade} cabeças abatidas"
            ),
            "data": data_br(data_abate),
            "tipo": "acerto_confinamento",
            "status": status,
            "campos_faltantes": (
                "acerto do confinamento e confirmação do recebimento bancário"
            ),
            "quantidade": quantidade,
            "romaneio": abate.get("romaneio"),
            "frigorifico": abate.get("frigorifico"),
            "contatos": contatos_do_confinamento(
                confinamento_id, vinculos, contatos_id,
            ),
            "termos_busca": [termo for termo in dict.fromkeys(termos) if termo],
            "fonte_pendencia": "supabase_abate_sem_acerto_final",
        })
    return pendencias


def combinar_duvidas(
    base: list[dict[str, Any]], automaticas: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    por_codigo: dict[str, dict[str, Any]] = {}
    for item in [*base, *automaticas]:
        codigo = str(item.get("codigo") or "").strip()
        if not codigo:
            continue
        por_codigo[codigo] = item
    return list(por_codigo.values())


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Gera dúvidas de acertos para pesquisa privada no WhatsApp."
    )
    parser.add_argument("--base", type=Path)
    parser.add_argument("--saida", required=True, type=Path)
    parser.add_argument("--janela-dias", type=int, default=180)
    args = parser.parse_args()

    base = carregar_duvidas_base(args.base)
    status_supabase = "ok"
    erro_supabase = None
    try:
        automaticas = gerar_pendencias_acertos(
            ConfinexClient(), janela_dias=max(1, args.janela_dias),
        )
    except (ConfinexError, OSError, ValueError) as erro:
        automaticas = []
        status_supabase = "erro_leitura"
        erro_supabase = type(erro).__name__
    duvidas = combinar_duvidas(base, automaticas)
    saida = {
        "gerado_em": datetime.now().astimezone().isoformat(),
        "modo": "somente_leitura_sem_promocao_operacional",
        "status_supabase": status_supabase,
        "erro_supabase": erro_supabase,
        "contagens": {
            "base": len(base),
            "acertos_automaticos": len(automaticas),
            "total": len(duvidas),
        },
        "duvidas": duvidas,
        "controles": {
            "mensagens_enviadas": 0,
            "escritas_supabase": 0,
            "registros_operacionais_alterados": 0,
        },
    }
    args.saida.parent.mkdir(parents=True, exist_ok=True)
    args.saida.write_text(
        json.dumps(saida, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({
        "status_supabase": status_supabase,
        "contagens": saida["contagens"],
        "controles": saida["controles"],
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
