#!/usr/bin/env python3
"""Contrato estático do replanejamento seguro de sucessões terminais.

Este teste não acessa Supabase. Ele existe para impedir que uma edição futura
reduza o replanejamento a um clone de plano ou retire os gates de concorrência.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys


SQL = (
    Path(__file__).resolve().parents[1]
    / "supabase/migrations/202608290001_investigacoes_revisao.sql"
).read_text(encoding="utf-8")


def trecho(nome: str, proximo: str) -> str:
    inicio = SQL.index(nome)
    fim = SQL.index(proximo, inicio)
    return SQL[inicio:fim]


def exige(condicao: bool, mensagem: str) -> None:
    if not condicao:
        raise AssertionError(mensagem)


def main() -> int:
    leitura = trecho(
        "CREATE OR REPLACE FUNCTION public.obter_contexto_replanejamento_sucessoes_promocao_terminal(",
        "CREATE OR REPLACE FUNCTION public.replanejar_sucessoes_promocao_terminal(",
    )
    mutacao = trecho(
        "CREATE OR REPLACE FUNCTION public.replanejar_sucessoes_promocao_terminal(",
        "CREATE OR REPLACE FUNCTION public.listar_sucessoes_promocao_terminal_pendentes(",
    )
    consumidor = trecho(
        "CREATE OR REPLACE FUNCTION public.consumir_sucessoes_promocao_terminal(",
        "CREATE OR REPLACE FUNCTION public.obter_contexto_replanejamento_sucessoes_promocao_terminal(",
    )

    exige("VOLATILE" in leitura and "SECURITY DEFINER" in leitura,
           "leitura de contexto deve ser service-only e travar o retrato operacional")
    exige("contexto_cas_hash" in leitura and "resolucao_hash" in leitura,
           "leitura deve fechar CAS sobre resolução terminal")
    for campo in (
        "source_draft_atualizado_em",
        "source_candidato_atualizado_em",
        "policy_schema_hash",
        "campos_obrigatorios",
        "registro_operacional_origem_snapshot_ref",
    ):
        exige(campo in leitura, f"contexto deve incluir {campo}")

    exige("p_replanejamento - ARRAY[" in mutacao,
           "pedido de replanejamento precisa ter formato fechado")
    exige("Plano não cobre exatamente todas as complementares ativas" in mutacao,
           "mutação deve exigir cobertura total de predecessoras")
    exige("RETRY_CONJUNTO_FONTES_MUDOU" in mutacao,
           "mutação deve abortar se uma fonte/pai entrar durante os locks")
    exige("RETRY_CONTEXTO_CAS_DIVERGIU" in mutacao,
           "mutação deve validar o hash CAS após bloquear fontes")
    exige("contexto ->> 'contexto_hash'" in mutacao
           and "v_item ->> 'contexto_hash'" in mutacao,
           "cada predecessora precisa atestar seu próprio contexto CAS")
    exige("FOR SHARE" in mutacao and "FOR UPDATE" in mutacao,
           "mutação deve travar fontes e outbox/pais")
    exige(mutacao.index("v_ids_draft_pre") < mutacao.index("investigacao-promocao:")
           < mutacao.index("WHERE id = p_promocao_id FOR SHARE"),
           "ordem de locks deve ser D/C antes de promoção e pending")
    exige("PLANEJAMENTO_CORRETIVO_NAO_REPLANEJADO" in mutacao,
           "corretiva não pode clonar plano anterior")
    exige("fingerprint-replanejada-v1" in mutacao
           and "Não aceitamos fingerprint do planejador" in mutacao,
           "fingerprint precisa ser derivado no servidor, não recebido do planejador")
    exige("v_plano_hash IS NOT DISTINCT FROM v_pai.plano_hash" in mutacao,
           "alterar somente fingerprint não pode burlar o bloqueio de clone corretivo")
    exige("CASE WHEN v_tipo = 'pre_revisao' THEN v_pai.source_draft_id END" in mutacao,
           "corretiva deve nascer sem source_draft")
    exige("sucessao_outbox_id" in mutacao and "filhas_mapa_hash" in mutacao,
           "filhos e mapa terminal precisam ser derivados e selados no servidor")
    exige("estado = 'concluida'" in mutacao and "Outbox mudou antes do mapa atômico" in mutacao,
           "fechamento do outbox precisa ser atômico")
    exige("criar_sucessora_complementar" in mutacao and "consumir_complementar" in mutacao,
           "triggers devem receber capacidades transacionais, não GUCs")
    exige("investigacao_snapshot_registro_promocao" in leitura
           and "Registro operacional do replanejamento não corresponde ao outbox" in leitura
           and "Registro operacional do replanejamento não foi encontrado" in mutacao,
           "corretiva deve reler e travar o registro operacional atual")
    exige("identidade_valida" in leitura
           and "v_snapshot_operacional ->> 'corresponde'" not in leitura,
           "rodada corretiva deve exigir proveniência, sem exigir igualdade ao retrato antigo")
    exige("replanejamento_pedido_hash" in mutacao
           and "Retry de replanejamento diverge do pedido/mapa concluído" in mutacao,
           "retry concluído deve exigir o mesmo pedido fechado e o mesmo mapa")
    exige("replanejamento_pedido_hash = NULL" in SQL
           and "Outbox concluído exige hash do pedido de materialização" in SQL,
           "reabertura tardia deve limpar o selo e conclusão deve exigir novo selo")
    exige("ELSIF NEW.anexado_em IS NULL\n        AND EXISTS" in SQL
           and "pending_actions_reativa_complementar" in SQL,
           "evidência tardia não pode criar outbox enquanto o mediador estiver em sombra")

    exige("v_pais_pre" in consumidor and "v_pais_pos" in consumidor,
           "consumidor direto deve revalidar o conjunto de pais")
    exige("RETRY_CONJUNTO_FONTES_MUDOU: complementar criada" in consumidor,
           "consumidor direto deve abortar antes de alterar linha se o conjunto mudou")
    exige("v_outbox.classe_resolvida = 'com_gravacao'" in consumidor
           and "PLANEJAMENTO_FONTES_NECESSARIO" in consumidor,
           "caminho corretivo direto deve falhar fechado e pedir replanejamento")
    stale = trecho(
        "CREATE OR REPLACE FUNCTION public.substituir_investigacao_corretiva_stale(",
        "CREATE OR REPLACE FUNCTION public.decidir_promocao_operacional(",
    )
    exige(stale.index("PLANEJAMENTO_FONTES_NECESSARIO")
           < stale.index("INSERT INTO public.investigacao_autorizacoes_corretiva"),
           "substituição corretiva antiga deve falhar antes de capacidades/escritas")
    leitura_stale = trecho(
        "CREATE OR REPLACE FUNCTION public.obter_contexto_replanejamento_corretiva_stale(",
        "CREATE OR REPLACE FUNCTION public.replanejar_investigacao_corretiva_stale(",
    )
    mutacao_stale = trecho(
        "CREATE OR REPLACE FUNCTION public.replanejar_investigacao_corretiva_stale(",
        "CREATE OR REPLACE FUNCTION public.decidir_promocao_operacional(",
    )
    exige("VOLATILE" in leitura_stale and "planejamento_inputs" in leitura_stale,
           "getter stale deve travar e entregar insumos sanitizados ao planejador")
    exige("contexto_cas_hash" in leitura_stale
           and "source_candidatos_atualizados_em" in leitura_stale
           and "pending_action_revisao_atualizado_em" in leitura_stale,
           "getter stale deve selar fontes e eventual revisão humana")
    exige("RETRY_CONTEXTO_CORRETIVO_DIVERGIU" in mutacao_stale,
           "mutação stale deve recusar plano calculado sobre contexto antigo")
    exige("v_plano_hash IS NOT DISTINCT FROM v_pai.plano_hash" in mutacao_stale,
           "stale exige plano novo, não clone da rodada anterior")
    exige("revisao_corretiva_substituida" in mutacao_stale
           and "promovido_para_operacional', false" in mutacao_stale,
           "revisão humana antiga deve ser encerrada com auditoria não operacional")
    exige("v_tarefas_persistidas IS DISTINCT FROM v_plano_tarefas" in mutacao_stale,
           "retry stale deve validar também as tarefas persistidas")
    exige(mutacao_stale.index("investigacao-draft:")
           < mutacao_stale.index("investigacao-candidato:")
           < mutacao_stale.index("investigacao-promocao:")
           < mutacao_stale.index("FOR UPDATE"),
           "stale deve respeitar a ordem global D→C→P antes de row locks")

    exige(re.search(
        r"outbox\.estado IN \(\s*'pendente', 'aguardando_reconciliacao', 'aguardando_planejamento'",
        SQL,
    ) is not None, "listagem deve entregar itens aguardando planejamento")
    for assinatura in (
        "obter_contexto_replanejamento_sucessoes_promocao_terminal(uuid, text)",
        "replanejar_sucessoes_promocao_terminal(uuid, text, text, jsonb, text)",
        "obter_contexto_replanejamento_corretiva_stale(uuid, text, text)",
        "replanejar_investigacao_corretiva_stale(uuid, text, text, text, jsonb, text, text)",
    ):
        exige(f"REVOKE ALL ON FUNCTION public.{assinatura}" in SQL,
               f"RPC {assinatura} precisa revogar acesso público")
        exige(f"GRANT EXECUTE ON FUNCTION public.{assinatura}" in SQL,
               f"RPC {assinatura} precisa conceder somente ao serviço")
        exige(assinatura.replace(", ", ",") in SQL,
               f"inventário de ACL precisa conhecer {assinatura}")

    print("OK: contrato estático de replanejamento do outbox")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (AssertionError, ValueError) as erro:
        print(f"FALHOU: {erro}", file=sys.stderr)
        raise SystemExit(1)
