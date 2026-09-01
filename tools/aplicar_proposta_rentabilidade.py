"""Aplicador de propostas do cérebro de rentabilidade.

Fecha o ciclo do cérebro de solução: lê UMA proposta viva da fila
(pending_actions, agente cerebro_rentabilidade), remonta o dossiê ao vivo,
recalcula F1+F2 e — somente se as confirmações ainda baterem com as da
proposta — aplica a decisão pelas ferramentas dedicadas (upsert do F1 para
fechar/refechar; upsert do F2 para consolidar/reconsolidar), marca a
proposta como executada com o nome de quem aprovou e registra o rastro.

Guardas invioláveis:
- Dados mudaram desde a proposta (confirmação F1 ou F2 diverge) → ABORTA
  sem escrever nada: o cérebro proporá de novo com a chave nova.
- Dry-run por padrão com hash do plano; a execução exige --executar
  --confirmacao <hash> --aprovado-por <nome> (o gate humano é sempre
  externo a esta ferramenta — ela nunca decide sozinha).
- Escrita direta própria SÓ em pending_actions (marcar a proposta);
  fechamentos/consolidações passam pelas allowlists do F1/F2.

Uso:
  python3 tools/aplicar_proposta_rentabilidade.py --codigo CF-26-006
  python3 tools/aplicar_proposta_rentabilidade.py --proposta <uuid>
  ... --executar --confirmacao <hash> --aprovado-por pablo
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
from datetime import datetime, timezone
from typing import Any, Mapping

from tools import fechar_rentabilidade_operacao as f1
from tools import nota_desvio_operacao as f2

VERSAO_APLICADOR = "aplicador-rentabilidade-v1.0.0"
AGENTE_CEREBRO = "cerebro_rentabilidade"
ACAO_TIPO = "proposta_rentabilidade"
STATUS_APLICAVEIS = (
    "aguardando_confirmacao", "confirmado_telegram", "em_revisao",
    "aprovado_confinex",
)
ESCRITA_PROPRIA = frozenset({"pending_actions"})


def _sha(objeto: Any) -> str:
    canonico = json.dumps(objeto, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"))
    return hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def validar_proposta(
    proposta: Mapping[str, Any],
    fechamento: Mapping[str, Any],
    cascata: Mapping[str, Any],
) -> dict[str, Any]:
    """Validação pura: a proposta ainda descreve a realidade?

    Devolve o plano de aplicação; levanta ValueError com o motivo quando a
    aplicação não pode acontecer.
    """
    if str(proposta.get("agente") or "") != AGENTE_CEREBRO:
        raise ValueError("proposta não é do cérebro de rentabilidade")
    if str(proposta.get("acao_tipo") or "") != ACAO_TIPO:
        raise ValueError("acao_tipo inesperado para o aplicador")
    if str(proposta.get("status") or "") not in STATUS_APLICAVEIS:
        raise ValueError(
            f"proposta em status '{proposta.get('status')}' não é aplicável"
        )
    payload = proposta.get("payload") or {}
    conf_f1 = str(payload.get("confirmacao_f1") or "")
    conf_f2 = str(payload.get("confirmacao_f2") or "")
    if conf_f1 != fechamento["confirmacao"]:
        raise ValueError(
            "dados mudaram desde a proposta (confirmação F1 diverge) — "
            "aguarde a próxima rodada do cérebro"
        )
    if conf_f2 != cascata["confirmacao"]:
        raise ValueError(
            "dados mudaram desde a proposta (confirmação F2 diverge) — "
            "aguarde a próxima rodada do cérebro"
        )
    decisoes = [str(d.get("decisao") or "")
                for d in (payload.get("decisoes") or [])]
    aplicar_fechamento = any(d in ("fechar", "refechar") for d in decisoes)
    aplicar_consolidacao = any(
        d in ("consolidar", "reconsolidar") for d in decisoes
    )
    if not (aplicar_fechamento or aplicar_consolidacao):
        raise ValueError("proposta sem decisão aplicável")
    plano = {
        "proposta_id": proposta.get("id"),
        "codigo": fechamento.get("codigo"),
        "decisoes": decisoes,
        "aplicar_fechamento": aplicar_fechamento,
        "aplicar_consolidacao": aplicar_consolidacao,
        "confirmacao_f1": conf_f1,
        "confirmacao_f2": conf_f2,
    }
    plano["confirmacao"] = _sha(plano)
    return plano


def resultado_aplicacao(
    plano: Mapping[str, Any], modos: Mapping[str, str], aprovado_por: str
) -> dict[str, Any]:
    return {
        "aplicado_via": VERSAO_APLICADOR,
        "aprovado_por": aprovado_por,
        "decisoes": list(plano["decisoes"]),
        "fechamento": modos.get("fechamento"),
        "consolidacao": modos.get("consolidacao"),
        "confirmacao_f1": plano["confirmacao_f1"],
        "confirmacao_f2": plano["confirmacao_f2"],
    }


class ClienteAplicador:
    """Compõe os clientes F1/F2; escrita própria só em pending_actions."""

    def __init__(self, url: str, chave: str, timeout: int = 20) -> None:
        if not url or not chave:
            raise ValueError("configuracao_incompleta")
        self.url = url.rstrip("/")
        self.chave = chave
        self.timeout = max(1, min(int(timeout), 20))
        self.fechador = f1.ClienteFechamento(url, chave, timeout)
        self.notas = f2.ClienteNota(url, chave, timeout)

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        base = {"apikey": self.chave, "Authorization": f"Bearer {self.chave}"}
        base.update(extra or {})
        return base

    def _get(self, tabela: str, consulta: dict[str, str]) -> list[dict[str, Any]]:
        return self.fechador._get(tabela, consulta)

    def _patch(self, tabela: str, filtro: dict[str, str], payload: Any) -> None:
        if tabela not in ESCRITA_PROPRIA:
            raise ValueError(f"escrita em {tabela} não permitida ao aplicador")
        requisicao = urllib.request.Request(
            f"{self.url}/rest/v1/{tabela}?" + urllib.parse.urlencode(filtro),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            method="PATCH",
            headers=self._headers({
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            }),
        )
        try:
            with urllib.request.urlopen(requisicao, timeout=self.timeout) as r:
                if r.status not in {200, 201, 204}:
                    raise RuntimeError(f"HTTP inesperado em {tabela}: {r.status}")
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"escrita em {tabela} falhou com HTTP {exc.code}"
            ) from exc

    def buscar_proposta(self, proposta_id: str | None,
                        codigo: str | None) -> dict[str, Any]:
        consulta = {
            "agente": "eq." + AGENTE_CEREBRO,
            "acao_tipo": "eq." + ACAO_TIPO,
            "status": "in.(" + ",".join(STATUS_APLICAVEIS) + ")",
            "order": "criado_em.desc",
            "limit": "1",
        }
        if proposta_id:
            consulta["id"] = "eq." + proposta_id
        elif codigo:
            consulta["entidade_codigo"] = "eq." + codigo
        else:
            raise ValueError("informe --proposta ID ou --codigo CODIGO")
        linhas = self._get("pending_actions", consulta)
        if not linhas:
            raise RuntimeError("nenhuma proposta viva encontrada para o alvo")
        return linhas[0]

    def marcar_executada(self, proposta_id: str,
                         resultado: Mapping[str, Any],
                         aprovado_por: str) -> None:
        agora = datetime.now(timezone.utc).isoformat()
        self._patch("pending_actions", {"id": "eq." + proposta_id}, {
            "status": "executado",
            "confirmado_por": aprovado_por,
            "confirmado_em": agora,
            "executado_em": agora,
            "resultado": dict(resultado),
            "atualizado_em": agora,
        })

    def aplicar(self, plano: Mapping[str, Any],
                fechamento: Mapping[str, Any],
                cascata: Mapping[str, Any],
                dossie: Mapping[str, Any],
                aprovado_por: str) -> dict[str, str]:
        motivo = (
            f"aplicação da proposta {plano['proposta_id']} aprovada por "
            f"{aprovado_por}"
        )
        modos: dict[str, str] = {}
        if plano["aplicar_fechamento"]:
            modos["fechamento"] = self.fechador.gravar_fechamento(
                fechamento, motivo=motivo
            )
        if plano["aplicar_consolidacao"]:
            avaliacao = dossie.get("avaliacao") or {}
            estimativas = dossie.get("estimativas") or []
            if not avaliacao.get("id") or not estimativas:
                raise RuntimeError(
                    "proposta pede consolidação, mas a operação não tem "
                    "avaliação/estimativa"
                )
            modos["consolidacao"] = self.notas.gravar_consolidacao(
                cascata, str(avaliacao["id"]),
                int(estimativas[0].get("versao") or 1),
                motivo=motivo,
            )
        return modos


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Aplica UMA proposta do cérebro (dry-run padrão)"
    )
    parser.add_argument("--versao", action="store_true")
    parser.add_argument("--proposta")
    parser.add_argument("--codigo")
    parser.add_argument("--executar", action="store_true")
    parser.add_argument("--confirmacao")
    parser.add_argument("--aprovado-por", dest="aprovado_por")
    args = parser.parse_args(argv)
    if args.versao:
        print(VERSAO_APLICADOR)
        return 0

    cliente = ClienteAplicador(
        os.environ.get("SUPABASE_URL")
        or os.environ.get("CONFINEX_DB_URL") or "",
        os.environ.get("SUPABASE_SERVICE_KEY")
        or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        or os.environ.get("CONFINEX_DB_KEY") or "",
    )
    proposta = cliente.buscar_proposta(args.proposta, args.codigo)
    codigo = str(proposta.get("entidade_codigo") or "")
    dossie = cliente.fechador.dossie(codigo)
    fechamento = f1.fechar_operacao(dossie)
    cascata = f2.montar_cascata(dossie)
    try:
        plano = validar_proposta(proposta, fechamento, cascata)
    except ValueError as erro:
        print(f"ERRO: {erro}", file=sys.stderr)
        return 2
    print(json.dumps({
        "versao": VERSAO_APLICADOR,
        "proposta": plano["proposta_id"],
        "codigo": plano["codigo"],
        "decisoes": plano["decisoes"],
        "confirmacao": plano["confirmacao"],
    }, ensure_ascii=False, indent=1))

    if not args.executar:
        print("\nDry-run. Para aplicar: --executar --confirmacao "
              f"{plano['confirmacao']} --aprovado-por <nome>",
              file=sys.stderr)
        return 0
    if args.confirmacao != plano["confirmacao"]:
        print("ERRO: confirmação não confere com o plano atual",
              file=sys.stderr)
        return 2
    if not (args.aprovado_por or "").strip():
        print("ERRO: --aprovado-por é obrigatório na execução — o gate "
              "humano é externo a esta ferramenta", file=sys.stderr)
        return 2
    modos = cliente.aplicar(plano, fechamento, cascata, dossie,
                            args.aprovado_por.strip())
    cliente.marcar_executada(
        str(plano["proposta_id"]),
        resultado_aplicacao(plano, modos, args.aprovado_por.strip()),
        args.aprovado_por.strip(),
    )
    print(f"Proposta {plano['proposta_id']} aplicada: {modos}",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
