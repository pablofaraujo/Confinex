import re
import unittest
from pathlib import Path


RAIZ = Path(__file__).resolve().parents[1]
ATIVACAO = RAIZ / "supabase/migrations/202608290002_ativar_mediador_investigacoes.sql"
ROLLBACK = RAIZ / "supabase/rollbacks/202608290002_desativar_mediador_investigacoes.sql"
FUNDACAO = RAIZ / "supabase/migrations/202608290001_investigacoes_revisao.sql"


def normalizar(texto: str) -> str:
    return re.sub(r"\s+", " ", texto.lower()).strip()


class AtivacaoRollbackInvestigacoesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ativacao = ATIVACAO.read_text(encoding="utf-8")
        cls.rollback = ROLLBACK.read_text(encoding="utf-8")
        cls.fundacao = FUNDACAO.read_text(encoding="utf-8")
        cls.ativacao_n = normalizar(cls.ativacao)
        cls.rollback_n = normalizar(cls.rollback)
        cls.fundacao_n = normalizar(cls.fundacao)

    def test_outbox_fica_no_inventario_privado_sem_grant_direto(self):
        self.assertIn(
            "'public.investigacao_sucessoes_pendentes'::regclass",
            self.ativacao,
        )
        self.assertIn(
            "public.investigacao_sucessoes_pendentes in access exclusive mode",
            self.ativacao_n,
        )
        self.assertIn("not (select relrowsecurity", self.ativacao_n)
        self.assertIn("relforcerowsecurity", self.ativacao_n)
        self.assertIn("pg_policy where polrelid = v_tabela::oid", self.ativacao_n)
        self.assertNotRegex(
            self.ativacao_n,
            r"grant\s+(?:select|insert|update|delete|all)[^;]*"
            r"investigacao_sucessoes_pendentes",
        )

    def test_rpc_de_outbox_e_health_sao_exclusivas_do_service_role(self):
        assinaturas = (
            "public.consumir_sucessoes_promocao_terminal(uuid, text, text)",
            "public.listar_sucessoes_promocao_terminal_pendentes(integer)",
            "public.saude_investigacoes_proativas()",
        )
        for assinatura in assinaturas:
            self.assertIn(
                f"revoke all on function {assinatura} from public, anon, authenticated, service_role",
                self.ativacao_n,
            )
            self.assertIn(
                f"grant execute on function {assinatura} to service_role",
                self.ativacao_n,
            )
            self.assertIn(assinatura.replace(", ", ","), self.ativacao_n)

    def test_rpc_replanejamento_sao_service_only_em_todas_as_fases(self):
        assinaturas = (
            "public.obter_contexto_replanejamento_corretiva_stale(uuid, text, text)",
            "public.replanejar_investigacao_corretiva_stale(uuid, text, text, text, jsonb, text, text)",
            "public.obter_contexto_replanejamento_sucessoes_promocao_terminal(uuid, text)",
            "public.replanejar_sucessoes_promocao_terminal(uuid, text, text, jsonb, text)",
        )
        for assinatura in assinaturas:
            assinatura_n = assinatura.replace(", ", ",")
            for catalogo in (self.fundacao_n, self.ativacao_n):
                self.assertIn(
                    f"revoke all on function {assinatura}".lower()
                    + " from public, anon, authenticated, service_role",
                    catalogo,
                )
                self.assertIn(
                    f"grant execute on function {assinatura}".lower()
                    + " to service_role",
                    catalogo,
                )
                self.assertIn(assinatura_n, catalogo)
            self.assertIn(
                f"revoke all on function {assinatura}".lower()
                + " from public, anon, authenticated, service_role",
                self.rollback_n,
            )
            self.assertIn(assinatura_n, self.rollback_n)

    def test_replanejamento_tem_definicao_volatil_segura_e_rollback_fecha(self):
        for nome in (
            "obter_contexto_replanejamento_corretiva_stale",
            "replanejar_investigacao_corretiva_stale",
            "obter_contexto_replanejamento_sucessoes_promocao_terminal",
            "replanejar_sucessoes_promocao_terminal",
        ):
            bloco = self.fundacao_n.split(
                f"create or replace function public.{nome}", 1
            )[1].split("$$;", 1)[0]
            self.assertIn("volatile", bloco)
            self.assertIn("security definer", bloco)
            self.assertIn("set search_path = pg_catalog, public", bloco)
            self.assertIn(
                f"public.{nome}", self.ativacao_n
            )
            self.assertIn(
                f"public.{nome}", self.rollback_n
            )
        self.assertGreaterEqual(
            self.ativacao_n.count(
                "('public.investigacao_sucessoes_pendentes', 'investigacao_sucessoes_pendentes_imutavel', 'public.proteger_sucessao_promocao_terminal()', 27)"
            ),
            2,
        )
        self.assertIn(
            "has_function_privilege('service_role', v_funcao::oid, 'execute')",
            self.rollback_n,
        )
        tcb = self.ativacao_n.split(
            "v_internas_definer regprocedure[] := array[", 1
        )[1].split("];", 1)[0]
        self.assertIn(
            "'public.proteger_sucessao_promocao_terminal()'::regprocedure",
            tcb,
        )
        self.assertIn("funcao.provolatile = 'v'", self.ativacao_n)
        self.assertIn(
            "privilegio.grantee <> v_owner",
            self.ativacao_n,
        )
        for inventario in (
            "v_draft_select text[] := array[",
            "v_draft_insert text[] := array[",
            "v_draft_update text[] := array[",
            "v_acao_select text[] := array[",
            "v_acao_insert text[] := array[",
            "v_acao_update text[] := array[",
        ):
            self.assertIn(inventario, self.rollback_n)
        self.assertIn("aclexplode(coluna.attacl)", self.rollback_n)
        self.assertIn(
            "rollback: pós-condição de acl por coluna não foi satisfeita",
            self.rollback_n,
        )

    def test_authenticated_nao_recebe_colunas_internas(self):
        self.assertIn(
            "revoke select, insert, update on table public.pending_actions from public, anon, authenticated",
            self.ativacao_n,
        )
        for coluna in (
            "promocao_lease_token",
            "promocao_fencing_token",
            "promocao_preparacao_hash",
            "promocao_resultado_pedido_hash",
            "investigacao_origem_id",
            "promocao_origem_id",
            "entidade_final_id",
        ):
            self.assertIn(f"'{coluna}'", self.ativacao_n)
        self.assertIn("has_column_privilege('authenticated'", self.ativacao_n)

    def test_cutover_detecta_fila_velha_e_capacidades_orfas(self):
        self.assertIn("investigacao_sucessoes_pendentes", self.ativacao_n)
        self.assertIn("capacidade transitória residual impede o cutover", self.ativacao_n)
        self.assertIn("outbox terminal antiga impede o cutover", self.ativacao_n)
        self.assertIn(
            "estado in ( 'pendente', 'aguardando_reconciliacao', 'aguardando_planejamento' )",
            self.ativacao_n,
        )
        self.assertIn("criado_em < clock_timestamp() - interval '15 minutes'", self.ativacao_n)
        self.assertIn("estado = 'falha_permanente'", self.ativacao_n)
        self.assertIn("investigacao_sucessoes_pendentes_imutavel", self.ativacao_n)
        self.assertIn("proteger_sucessao_promocao_terminal()", self.ativacao_n)
        for gatilho in (
            "investigacao_tarefas_consulta_imutavel",
            "investigacoes_revisao_origem_imutavel",
            "investigacoes_revisao_obsolescencia_protegida",
            "investigacoes_revisao_atestacao_protegida",
        ):
            self.assertIn(gatilho, self.ativacao_n)
        self.assertIn("with ordinality as nome(attname, ordem)", self.ativacao_n)
        self.assertIn(
            "v_procedure = 'public.investigacao_registro_corresponde_promocao(text,uuid,uuid,jsonb)'::regprocedure and linguagem.lanname = 'sql' and funcao.provolatile = 'v' and funcao.proisstrict",
            self.ativacao_n,
        )
        self.assertNotIn("is distinct from all", self.ativacao_n)
        self.assertIn(
            "acao_tipo is distinct from 'promover_revisao_operacional' and acao_tipo is distinct from 'revisar_correcao_pos_gravacao'",
            self.ativacao_n,
        )
        self.assertIn("= v_policy_restrita", self.ativacao_n)
        self.assertIn("= v_policy_restrita", self.rollback_n)
        self.assertIn("v_draft_select text[]", self.ativacao_n)
        self.assertIn("v_draft_insert text[]", self.ativacao_n)
        self.assertIn("v_draft_update text[]", self.ativacao_n)
        self.assertIn("v_acao_select text[]", self.ativacao_n)
        self.assertIn("v_acao_insert text[]", self.ativacao_n)
        self.assertIn("v_acao_update text[]", self.ativacao_n)
        self.assertIn("cross join lateral aclexplode(coluna.attacl)", self.ativacao_n)
        self.assertIn("not coalesce(v_ativado_exato, false)", self.ativacao_n)

    def test_health_e_listagem_nao_expoem_payload(self):
        for nome in (
            "listar_sucessoes_promocao_terminal_pendentes",
            "saude_investigacoes_proativas",
        ):
            self.assertIn(f"create or replace function public.{nome}", self.fundacao_n)
        bloco_lista = self.fundacao_n.split(
            "create or replace function public.listar_sucessoes_promocao_terminal_pendentes",
            1,
        )[1].split("$$;", 1)[0]
        retorno_lista = bloco_lista.split("language plpgsql", 1)[0]
        self.assertIn("returns table ( promocao_id uuid, pedido_hash text )", retorno_lista)
        self.assertNotIn("outbox_id", retorno_lista)
        self.assertNotIn("estado text", retorno_lista)
        self.assertNotIn("criado_em", retorno_lista)
        self.assertIn("pedido_hash", bloco_lista)
        self.assertIn("promocao_id", bloco_lista)
        self.assertNotIn("resultado_terminal_hash", bloco_lista)
        self.assertNotIn("payload", bloco_lista)
        bloco_saude = self.fundacao_n.split(
            "create or replace function public.saude_investigacoes_proativas",
            1,
        )[1].split("$$;", 1)[0]
        self.assertIn("pendentes_antigas", bloco_saude)
        self.assertIn("outbox_aguardando_planejamento", bloco_saude)
        self.assertIn("capacidades_orfas", bloco_saude)
        self.assertIn("tarefas_lease_expirada", bloco_saude)
        self.assertIn("promocoes_lease_expirada", bloco_saude)
        self.assertIn("investigacao_autorizacoes_promocao", bloco_saude)
        self.assertIn("investigacao_autorizacoes_corretiva", bloco_saude)
        self.assertNotIn("pedido_hash", bloco_saude)
        self.assertNotIn("payload", bloco_saude)

    def test_rollback_fecha_rpcs_antes_de_retirar_guardioes(self):
        pos_revoke = self.rollback_n.index(
            "revoke all on function public.consumir_sucessoes_promocao_terminal"
        )
        pos_drop = self.rollback_n.index(
            "drop trigger pending_actions_bloqueia_investigacao"
        )
        self.assertLess(pos_revoke, pos_drop)
        for gatilho in (
            "compras_vinculo_promocao_protegido",
            "vendas_vinculo_promocao_protegido",
            "pesagens_vinculo_promocao_protegido",
            "abates_vinculo_promocao_protegido",
        ):
            self.assertNotIn(f"create trigger {gatilho}", self.fundacao_n)
            self.assertIn(f"create trigger {gatilho}", self.ativacao_n)
            self.assertIn(f"drop trigger {gatilho}", self.rollback_n)
            self.assertLess(pos_revoke, self.rollback_n.index(f"drop trigger {gatilho}"))
        for nome in (
            "consumir_sucessoes_promocao_terminal",
            "listar_sucessoes_promocao_terminal_pendentes",
            "saude_investigacoes_proativas",
        ):
            self.assertIn(f"revoke all on function public.{nome}", self.rollback_n)

    def test_rollback_bloqueia_trabalho_e_preserva_historico(self):
        self.assertIn(
            "estado <> 'concluida'",
            self.rollback_n,
        )
        self.assertIn("obsolescencia_motivo = 'complementar_promocao_ativa'", self.rollback_n)
        self.assertIn("drop trigger pending_actions_reativa_complementar", self.rollback_n)
        self.assertIn("sucessao_outbox_id", self.rollback_n)
        self.assertIn("filhas_quantidade is distinct from mapa.quantidade", self.rollback_n)
        self.assertIn("filhas_mapa_hash is distinct from encode", self.rollback_n)
        self.assertIn("histórico concluído do outbox possui linhagem divergente", self.rollback_n)
        self.assertIn("investigacao_autorizacoes_promocao", self.rollback_n)
        self.assertIn("investigacao_autorizacoes_corretiva", self.rollback_n)
        for comando in ("delete from", "truncate table", "drop table"):
            self.assertNotIn(
                f"{comando} public.investigacao_sucessoes_pendentes",
                self.rollback_n,
            )
        self.assertNotIn(
            "update public.investigacao_sucessoes_pendentes",
            self.rollback_n,
        )


if __name__ == "__main__":
    unittest.main()
