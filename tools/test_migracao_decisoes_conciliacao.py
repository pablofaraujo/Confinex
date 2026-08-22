import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRACAO = ROOT / "supabase" / "migrations" / "202608220001_decisoes_conciliacao_financeira.sql"


class MigracaoDecisoesConciliacaoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRACAO.read_text(encoding="utf-8")
        cls.normalizado = " ".join(cls.sql.lower().split())

    def test_funcao_tem_transacao_e_acesso_restrito(self):
        self.assertRegex(self.sql, r"(?m)^BEGIN;$")
        self.assertRegex(self.sql, r"(?m)^COMMIT;$")
        self.assertIn("SECURITY DEFINER", self.sql)
        self.assertIn("SET search_path = public", self.sql)
        self.assertIn("auth.uid() IS NULL", self.sql)
        self.assertIn("FROM PUBLIC, anon", self.sql)
        self.assertIn("TO authenticated", self.sql)

    def test_exige_decisao_valida_e_motivo(self):
        self.assertIn("NOT IN ('confirmar', 'rejeitar')", self.sql)
        self.assertIn("IF v_motivo = ''", self.sql)
        self.assertIn("Informe o motivo da decisão", self.sql)
        self.assertIn("IF length(v_motivo) > 500", self.sql)

    def test_decisao_e_atomica_idempotente_e_preserva_conflito(self):
        self.assertIn("FOR UPDATE", self.sql)
        self.assertIn("IF v_registro.estado = v_estado_novo", self.sql)
        self.assertIn("'alterado', false", self.sql)
        self.assertIn("IF v_registro.estado <> 'pendente'", self.sql)
        self.assertIn("A sugestão já recebeu outra decisão", self.sql)

    def test_altera_somente_staging_e_historico(self):
        atualizadas = re.findall(r"(?i)UPDATE\s+public\.([a-z0-9_]+)", self.sql)
        inseridas = re.findall(r"(?i)INSERT\s+INTO\s+public\.([a-z0-9_]+)", self.sql)
        self.assertEqual(atualizadas, ["conciliacoes_candidatas"])
        self.assertEqual(inseridas, ["decisoes_consolidacao", "eventos"])
        for tabela in (
            "compras", "vendas", "abates", "pesagens_caderno", "operacoes",
            "fluxo_caixa", "transacoes_banco", "pending_actions", "operation_drafts",
        ):
            self.assertNotRegex(self.sql, rf"(?i)(?:UPDATE|INSERT\s+INTO|DELETE\s+FROM)\s+public\.{tabela}\b")

    def test_evento_deixa_claro_que_nao_promove(self):
        self.assertIn("'promovido_para_operacional', false", self.sql)
        self.assertIn("'registrado'", self.sql)
        self.assertIn("nenhum lançamento foi criado ou quitado", self.sql)


if __name__ == "__main__":
    unittest.main()
