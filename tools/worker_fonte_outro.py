#!/usr/bin/env python3
"""Worker de fonte do adaptador ``outro`` — busca evidências e publica pelo broker.

Papel (passo 9 de docs/investigacoes-proativas.md, incremento 3): dar vida à
tarefa de fonte criada pelo planejador. O worker roda no sandbox SEM nenhuma
credencial do Supabase: ele fala apenas com o socket local do broker
(``assumir``/``adiar``/``publicar``) e lê um snapshot local somente leitura da
consolidação (gerado por ``tools/exportar_snapshot_consolidacao.py``).

Fronteiras invioláveis:
- nenhuma rede própria: só o socket UNIX do broker e arquivos locais;
- a consulta executada é EXATAMENTE a da tarefa durável, verificada por
  ``resolver_consulta_tarefa`` (hash/canônico divergentes abortam);
- correspondência determinística e conservadora: código do negócio que bate
  no snapshot vira pista nível "possível" (``tipo_correspondencia='nome'``) —
  o worker nunca declara vínculo, confiança forte, alternativa ou divergência
  (isso é papel da síntese; a RPC também recusa);
- cobertura honesta: snapshot ausente/ilegível/velho publica falha
  ``indisponivel`` (``erro_pre_resposta``), nunca "não encontrei";
  busca vazia com snapshot íntegro é ``vazio_com_cobertura``;
- o atestado HMAC é assinado localmente com o segredo da credencial vigente
  (arquivo 600); o segredo nunca é impresso nem sai do processo;
- dry-run é o padrão: monta e imprime o resultado sanitizado sem publicar.
  A publicação exige ``--executar`` (e o broker, se estiver em dry-run,
  recusa com ``dry_run_nao_publica`` de qualquer forma).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
from pathlib import Path
from typing import Any, Mapping

try:  # execução a partir da raiz do repositório (CI, testes)
    from tools import atestar_cobertura_adaptador as atestar
    from tools import investigacoes_revisao as biblioteca
except ImportError:  # execução direta (VPS/sandbox)
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import atestar_cobertura_adaptador as atestar  # type: ignore[no-redef]
    import investigacoes_revisao as biblioteca  # type: ignore[no-redef]

VERSAO_WORKER = "worker-fonte-outro-v1.0.0"
ADAPTADOR = "outro"
TABELA_FONTE = "negocios_candidatos"
LINHAGEM_SNAPSHOT = "snapshot_consolidacao"
LIMITE_CANDIDATOS = 50
IDADE_MAXIMA_HORAS_PADRAO = 24
# Coluna(s) do snapshot que respondem a cada campo obrigatório conhecido.
COLUNAS_POR_CAMPO = {
    "data": ("data_base",),
    "negocio": ("codigo_fonte",),
    "quantidade": ("quantidade",),
    "valor_total": ("valor_total",),
    "cabecas": ("quantidade",),
    "peso_carcaca_total": ("peso_total_kg",),
    "valor_bruto": ("valor_total",),
    "data_abate": ("data_base",),
    "peso_liquido_kg": ("peso_total_kg",),
    "lote": ("codigo_fonte",),
    "contraparte": ("nome",),
    "valor": ("valor_total",),
}
CAMPOS_PUBLICAVEIS_EVIDENCIA = (
    "id_logico", "fonte_tipo", "fonte_tabela", "fonte_registro_id",
    "registro_origem_ref", "snapshot_fonte_ref", "linhagem",
    "chave_natural_hash", "referencia_opaca", "fatos_normalizados",
    "provas_campos", "provas_campos_canonico", "provas_campos_hash",
    "resumo_sanitizado", "evidenciado_em",
)


def _sha256_bytes(dados: bytes) -> str:
    return hashlib.sha256(dados).hexdigest()


# ---------------------------------------------------------------------------
# Snapshot local (somente leitura)
# ---------------------------------------------------------------------------


def carregar_snapshot(caminho: str | Path) -> dict[str, Any]:
    """Lê o snapshot exportado; qualquer defeito vira falha de cobertura.

    Devolve {"ok": True, "snapshot": ..., "hash": sha256-do-arquivo} ou
    {"ok": False, "erro_codigo": ...} — nunca levanta exceção por defeito do
    arquivo, porque indisponibilidade é um resultado publicável, não um bug.
    """
    try:
        bruto = Path(caminho).read_bytes()
    except OSError:
        return {"ok": False, "erro_codigo": "snapshot_indisponivel"}
    try:
        snapshot = json.loads(bruto.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"ok": False, "erro_codigo": "snapshot_ilegivel"}
    if (
        not isinstance(snapshot, dict)
        or not isinstance(snapshot.get("tabelas"), dict)
        or not isinstance(snapshot.get("gerado_em"), str)
        or not isinstance(
            snapshot["tabelas"].get(TABELA_FONTE), list
        )
    ):
        return {"ok": False, "erro_codigo": "snapshot_sem_tabela_fonte"}
    return {"ok": True, "snapshot": snapshot, "hash": _sha256_bytes(bruto)}


def snapshot_dentro_da_idade(
    snapshot: Mapping[str, Any], agora_iso: str,
    idade_maxima_horas: int = IDADE_MAXIMA_HORAS_PADRAO,
) -> bool:
    """Compara instantes ISO-8601 UTC lexicograficamente após normalizar."""
    from datetime import datetime, timedelta

    try:
        gerado = datetime.fromisoformat(
            str(snapshot.get("gerado_em")).replace("Z", "+00:00")
        )
        agora = datetime.fromisoformat(agora_iso.replace("Z", "+00:00"))
    except ValueError:
        return False
    return gerado <= agora and (agora - gerado) <= timedelta(
        hours=idade_maxima_horas
    )


# ---------------------------------------------------------------------------
# Busca determinística (pura)
# ---------------------------------------------------------------------------


def buscar_candidatos(
    consulta_spec: Mapping[str, Any], snapshot: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Correspondência exata (case-insensitive) de termo contra o código.

    Cada linha correspondida rende um candidato por campo obrigatório coberto
    pelo snapshot; todos com ``tipo_correspondencia='nome'`` (nível possível)
    e a mesma ``chave_natural`` por linha, para os fatos ficarem juntos na
    mesma evidência. Sem termos, não há o que buscar (fonte vazia coberta).
    """
    termos = {str(t).strip().casefold()
              for t in (consulta_spec.get("termos") or []) if str(t).strip()}
    campos = [str(c) for c in (consulta_spec.get("campos") or [])]
    if not termos or not campos:
        return []
    candidatos: list[dict[str, Any]] = []
    linhas = sorted(
        (l for l in snapshot["tabelas"][TABELA_FONTE] if isinstance(l, dict)),
        key=lambda l: str(l.get("codigo_fonte") or ""),
    )
    for linha in linhas:
        referencias = {
            str(linha.get("codigo_fonte") or "").strip().casefold(),
            str(linha.get("chave_rastreio") or "").strip().casefold(),
        }
        if not (termos & referencias - {""}):
            continue
        chave_natural = {
            "tabela": TABELA_FONTE,
            "codigo_fonte": str(linha.get("codigo_fonte") or ""),
            "chave_rastreio": str(linha.get("chave_rastreio") or ""),
        }
        # Referência opaca (64 hex) exigida pelo selo: âncora do registro
        # inteiro, para os campos da mesma linha formarem uma evidência só.
        registro_ref = _sha256_bytes(
            atestar.json_canonico_postgres(chave_natural).encode("utf-8")
        )
        for campo in campos:
            valor = None
            for coluna in COLUNAS_POR_CAMPO.get(campo, ()):
                if linha.get(coluna) not in (None, ""):
                    valor = linha[coluna]
                    break
            if valor is None:
                continue
            candidatos.append({
                "campo": campo,
                "valor": valor,
                "tipo_correspondencia": "nome",
                "chave_natural": chave_natural,
                "referencia": chave_natural,
                "registro_ref": registro_ref,
                "fonte_tabela": TABELA_FONTE,
            })
            if len(candidatos) >= LIMITE_CANDIDATOS:
                return candidatos
    return candidatos


def politica_da_consulta(campos: list[str]) -> str:
    """Infere o tipo de assunto pela lista de campos obrigatórios do plano."""
    alvo = tuple(sorted(campos))
    for tipo, definidos in biblioteca.POLITICAS_CAMPOS_OBRIGATORIOS.items():
        if tuple(sorted(definidos)) == alvo:
            return tipo
    return "revisao"


# ---------------------------------------------------------------------------
# Montagem do resultado publicável (pura)
# ---------------------------------------------------------------------------


def montar_resultado(
    tarefa: Mapping[str, Any], leitura_snapshot: Mapping[str, Any],
    *, idade_maxima_horas: int = IDADE_MAXIMA_HORAS_PADRAO,
    agora_iso: str | None = None,
) -> dict[str, Any]:
    """Executa a consulta da tarefa contra o snapshot e sela o publicável."""
    consulta_spec = biblioteca.resolver_consulta_tarefa(tarefa)
    versao_adaptador = str(tarefa.get("adaptador_version") or "v1")
    if str(tarefa.get("adaptador") or "") != ADAPTADOR:
        raise ValueError("tarefa_de_outro_adaptador")

    falha = None
    if not leitura_snapshot.get("ok"):
        falha = str(leitura_snapshot.get("erro_codigo") or "snapshot_indisponivel")
    else:
        snapshot = leitura_snapshot["snapshot"]
        if agora_iso and not snapshot_dentro_da_idade(
            snapshot, agora_iso, idade_maxima_horas
        ):
            falha = "snapshot_fora_da_idade_maxima"
    if falha:
        return {
            "estado_cobertura": "indisponivel",
            "estado_resultado": "cobertura_incompleta",
            "bundle": {"evidencias": [], "alternativas": [],
                       "pendencias": [], "ligacoes": []},
            "resumo_sanitizado": None,
            "erro_codigo": falha,
            "erro_sanitizado": "fonte local indisponivel para a consulta",
            "cobertura": {
                "inicio_confirmado": False, "fim_confirmado": False,
                "paginas_confirmadas": 0, "registros_confirmados": 0,
                "paginacao_modo": "nao_iniciada",
                "artefato_cobertura_tipo": "erro_pre_resposta",
                "cursor_final_hash": None, "snapshot_fonte_hash": None,
            },
        }

    snapshot = leitura_snapshot["snapshot"]
    snapshot_hash = str(leitura_snapshot["hash"])
    candidatos = buscar_candidatos(consulta_spec, snapshot)
    cobertura = "completa" if candidatos else "vazio_com_cobertura"
    instante = str(snapshot["gerado_em"])
    fonte = biblioteca.selar_fonte_adaptador(
        adaptador=ADAPTADOR,
        versao_adaptador=versao_adaptador,
        consulta=consulta_spec,
        cobertura=cobertura,
        candidatos=candidatos,
        linhagem_registrada=LINHAGEM_SNAPSHOT,
        prova_cobertura={
            "estado": "concluida",
            "inicio_confirmado": True,
            "fim_confirmado": True,
            "consulta_hash": str(tarefa.get("consulta_hash") or ""),
        },
    )
    campos = [str(c) for c in (consulta_spec.get("campos") or [])]
    plano = biblioteca.planejar_investigacao(
        {
            "tipo": politica_da_consulta(campos),
            "titulo": "busca de fonte no snapshot da consolidacao",
            "contexto_nome": "",
        },
        {"canal": LINHAGEM_SNAPSHOT, "linhagem": LINHAGEM_SNAPSHOT},
        consulta_spec,
        fingerprint_base=snapshot_hash,
        cobertura=cobertura,
        instante_referencia=instante,
        campos_obrigatorios=campos,
        adaptador=ADAPTADOR,
        versao_adaptador=versao_adaptador,
        fontes=[fonte],
    )
    evidencias = []
    for registro in plano["registros"]["investigacao_evidencias"]:
        evidencia = {chave: registro.get(chave)
                     for chave in CAMPOS_PUBLICAVEIS_EVIDENCIA}
        evidencia["fonte_registro_id"] = None  # snapshot não carrega xmin
        evidencia["evidenciado_em"] = instante
        evidencias.append(evidencia)
    resumo = (
        f"{len(evidencias)} pista(s) no snapshot da consolidacao"
        if evidencias else "busca coberta sem pista no snapshot"
    )
    return {
        "estado_cobertura": cobertura,
        "estado_resultado": "evidencia_insuficiente",
        "bundle": {"evidencias": evidencias, "alternativas": [],
                   "pendencias": [], "ligacoes": []},
        "resumo_sanitizado": resumo,
        "erro_codigo": None,
        "erro_sanitizado": None,
        "cobertura": {
            "inicio_confirmado": True, "fim_confirmado": True,
            "paginas_confirmadas": 1,
            "registros_confirmados": len(evidencias),
            "paginacao_modo": "nao_paginado",
            "artefato_cobertura_tipo": "snapshot_fonte",
            "cursor_final_hash": None,
            "snapshot_fonte_hash": snapshot_hash,
        },
    }


def montar_pedido_publicacao(
    tarefa: Mapping[str, Any], resultado: Mapping[str, Any],
    *, segredo: bytes, chave_id: str, artefato_hash: str,
) -> dict[str, Any]:
    """Assina o atestado de cobertura e monta o pedido ``publicar`` do socket."""
    cobertura = resultado["cobertura"]
    atestado = atestar.assinar_atestado_cobertura(
        segredo=segredo,
        chave_id=chave_id,
        adaptador=ADAPTADOR,
        adaptador_version=str(tarefa.get("adaptador_version") or "v1"),
        artefato_hash=artefato_hash,
        familia_fonte=biblioteca.REGISTRO_ADAPTADORES[ADAPTADOR]["familia_fonte"],
        consulta_hash=str(tarefa.get("consulta_hash") or ""),
        consulta_ref=str(tarefa.get("consulta_ref") or ""),
        tarefa_id=str(tarefa.get("id") or ""),
        investigacao_id=str(tarefa.get("investigacao_id") or ""),
        lease_token=str(tarefa.get("lease_token") or ""),
        fencing_token=int(tarefa.get("fencing_token") or 0),
        estado_cobertura=str(resultado["estado_cobertura"]),
        estado_resultado=str(resultado["estado_resultado"]),
        bundle=resultado["bundle"],
        inicio_confirmado=bool(cobertura["inicio_confirmado"]),
        fim_confirmado=bool(cobertura["fim_confirmado"]),
        paginas_confirmadas=int(cobertura["paginas_confirmadas"]),
        registros_confirmados=int(cobertura["registros_confirmados"]),
        paginacao_modo=str(cobertura["paginacao_modo"]),
        artefato_cobertura_tipo=str(cobertura["artefato_cobertura_tipo"]),
        cursor_final_hash=cobertura["cursor_final_hash"],
        snapshot_fonte_hash=cobertura["snapshot_fonte_hash"],
        resumo_sanitizado=resultado["resumo_sanitizado"],
        erro_codigo=resultado["erro_codigo"],
        erro_sanitizado=resultado["erro_sanitizado"],
    )
    return {
        "op": "publicar",
        "p_tarefa_id": str(tarefa.get("id") or ""),
        "p_lease_token": str(tarefa.get("lease_token") or ""),
        "p_fencing_token": int(tarefa.get("fencing_token") or 0),
        "p_estado_cobertura": str(resultado["estado_cobertura"]),
        "p_estado_resultado": str(resultado["estado_resultado"]),
        "p_bundle": resultado["bundle"],
        "p_atestado_cobertura": atestado,
        "p_resumo_sanitizado": resultado["resumo_sanitizado"],
        "p_erro_codigo": resultado["erro_codigo"],
        "p_erro_sanitizado": resultado["erro_sanitizado"],
    }


def resumo_para_terminal(resultado: Mapping[str, Any]) -> dict[str, Any]:
    """Versão de impressão: identidades e contagens, nunca fatos ou hmac."""
    return {
        "worker_version": VERSAO_WORKER,
        "estado_cobertura": resultado["estado_cobertura"],
        "estado_resultado": resultado["estado_resultado"],
        "evidencias": len(resultado["bundle"]["evidencias"]),
        "erro_codigo": resultado["erro_codigo"],
        "resumo_sanitizado": resultado["resumo_sanitizado"],
    }


# ---------------------------------------------------------------------------
# Cliente do socket do broker (uma linha JSON por conexão)
# ---------------------------------------------------------------------------


class ClienteBrokerLocal:
    def __init__(self, caminho_socket: str, timeout: int = 20) -> None:
        self.caminho = caminho_socket
        self.timeout = max(1, min(int(timeout), 60))

    def pedir(self, pedido: Mapping[str, Any]) -> dict[str, Any]:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conexao:
            conexao.settimeout(self.timeout)
            conexao.connect(self.caminho)
            conexao.sendall(
                (json.dumps(dict(pedido), ensure_ascii=False) + "\n")
                .encode("utf-8")
            )
            bruto = conexao.makefile("rb").readline(1024 * 1024)
        resposta = json.loads(bruto.decode("utf-8"))
        if not isinstance(resposta, dict):
            raise RuntimeError("resposta_do_broker_invalida")
        return resposta


def ler_segredo(caminho: str | Path) -> bytes:
    """Lê o segredo HMAC do arquivo 600 (hex de 64+ caracteres)."""
    texto = Path(caminho).read_text(encoding="utf-8").strip()
    try:
        segredo = bytes.fromhex(texto)
    except ValueError as exc:
        raise ValueError("segredo_nao_e_hex") from exc
    if len(segredo) < 32:
        raise ValueError("segredo_hmac_invalido")
    return segredo


def artefato_hash_proprio() -> str:
    return _sha256_bytes(Path(os.path.abspath(__file__)).read_bytes())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Worker de fonte 'outro' (dry-run por padrão; não publica)"
    )
    parser.add_argument("--versao", action="store_true")
    parser.add_argument("--socket", default="/run/confinex-broker/broker.sock")
    parser.add_argument("--snapshot", help="arquivo JSON do snapshot local")
    parser.add_argument("--segredo", help="arquivo do segredo HMAC (600)")
    parser.add_argument("--lease-segundos", type=int, default=120)
    parser.add_argument("--max-tarefas", type=int, default=1)
    parser.add_argument("--idade-maxima-horas", type=int,
                        default=IDADE_MAXIMA_HORAS_PADRAO)
    parser.add_argument("--executar", action="store_true",
                        help="publica de verdade (padrão: monta e imprime)")
    args = parser.parse_args(argv)
    if args.versao:
        print(VERSAO_WORKER)
        return 0
    if not args.snapshot or not args.segredo:
        print("ERRO: --snapshot e --segredo são obrigatórios", file=sys.stderr)
        return 2
    if not 1 <= args.max_tarefas <= 10:
        print("ERRO: --max-tarefas deve ficar entre 1 e 10", file=sys.stderr)
        return 2

    from datetime import datetime, timezone

    segredo = ler_segredo(args.segredo)
    leitura = carregar_snapshot(args.snapshot)
    cliente = ClienteBrokerLocal(args.socket)
    artefato = artefato_hash_proprio()
    processadas = 0
    for _ in range(args.max_tarefas):
        resposta = cliente.pedir({
            "op": "assumir",
            "lease_segundos": max(30, min(args.lease_segundos, 900)),
        })
        tarefa = resposta.get("tarefa") if resposta.get("ok") else None
        if not tarefa:
            if resposta.get("erro"):
                print(f"ERRO do broker: {resposta['erro']}", file=sys.stderr)
                return 2
            break  # fila vazia
        chave_id = str(tarefa.get("lease_chave_id") or "")
        resultado = montar_resultado(
            tarefa, leitura,
            idade_maxima_horas=args.idade_maxima_horas,
            agora_iso=datetime.now(timezone.utc).isoformat(),
        )
        print(json.dumps(resumo_para_terminal(resultado),
                         ensure_ascii=False, indent=1))
        if not args.executar:
            print("\nDRY-RUN: nada publicado; a tarefa segue com o lease até "
                  "expirar e voltará à fila.", file=sys.stderr)
            return 0
        pedido = montar_pedido_publicacao(
            tarefa, resultado,
            segredo=segredo, chave_id=chave_id, artefato_hash=artefato,
        )
        resposta_pub = cliente.pedir(pedido)
        if not resposta_pub.get("ok"):
            print(f"ERRO ao publicar: {resposta_pub.get('erro')}",
                  file=sys.stderr)
            return 2
        processadas += 1
    print(f"tarefas publicadas: {processadas}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
