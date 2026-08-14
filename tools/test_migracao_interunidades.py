import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRACAO = ROOT / "supabase/migrations/202608140001_interunidades_e_componentes.sql"


class MigracaoInterunidadesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRACAO.read_text(encoding="utf-8").lower()
        cls.corpo = re.sub(r"--.*?$", "", cls.sql, flags=re.MULTILINE)

    def test_e_transacional_e_exclusivamente_estrutural(self):
        self.assertRegex(self.sql, r"(?m)^begin;$")
        self.assertRegex(self.sql, r"(?m)^commit;$")
        for padrao in (
            r"\binsert\s+into\b",
            r"\bupdate\s+public\.[a-z_]",
            r"\bdelete\s+from\b",
            r"\btruncate\b",
            r"\bdrop\s+table\b",
        ):
            self.assertNotRegex(self.corpo, padrao)

    def test_cria_as_quatro_entidades_e_vincula_ledger(self):
        for tabela in (
            "compras_componentes",
            "negocios_fazenda",
            "movimentacoes_interunidades",
            "operacao_participantes",
        ):
            self.assertIn(f"create table if not exists public.{tabela}", self.sql)
            self.assertIn(f"alter table public.{tabela} enable row level security", self.sql)
        self.assertIn("alter table public.fazenda_ametista", self.sql)
        self.assertIn("add column if not exists negocio_fazenda_id uuid", self.sql)

    def test_componentes_nao_substituem_compra_agregada(self):
        self.assertIn("compra_agregada_id uuid not null references public.compras(id)", self.sql)
        self.assertIn("componentes nunca são somados novamente", self.sql)
        self.assertIn("create or replace view public.v_compras_componentes_resumo", self.sql)

    def test_movimento_exige_tres_faces_e_mesma_operacao(self):
        for trecho in (
            "venda_fazenda_id uuid not null unique",
            "lancamento_fazenda_id uuid not null unique",
            "compra_confinamento_id uuid not null unique",
            "validar_movimentacao_interunidades",
            "mesma operação",
            "quantidades divergentes",
            "peso ou valor divergente",
        ):
            self.assertIn(trecho, self.sql)

    def test_idempotencia_e_limite_de_participacao(self):
        self.assertGreaterEqual(self.sql.count("unique (idempotency_key)"), 3)
        self.assertIn("fazenda_ametista_idempotency_key_unique", self.sql)
        self.assertIn("participação econômica da operação excede 100%%", self.sql)
        self.assertIn("pg_advisory_xact_lock", self.sql)
        self.assertIn("papel in ('proprietario', 'parceiro')", self.sql)

    def test_valor_da_fazenda_e_preco_interunidades_sao_conferidos(self):
        self.assertIn("validar_valor_negocio_fazenda", self.sql)
        self.assertIn("abs(new.valor_total - valor_calculado) > 0.01", self.sql)
        self.assertIn("compra.preco_arroba is distinct from new.preco_arroba", self.sql)

    def test_authenticated_somente_leitura_e_service_role_sem_delete(self):
        self.assertEqual(self.sql.count("for select to authenticated using (true)"), 4)
        self.assertNotRegex(self.sql, r"grant\s+(insert|update|delete|all).*to authenticated")
        self.assertNotRegex(self.sql, r"grant\s+[^;]*delete[^;]*to service_role")
        self.assertEqual(self.sql.count("with (security_invoker = true)"), 2)

    def test_nao_contem_fatos_privados(self):
        self.assertNotRegex(self.sql, r"\b\d{5,}(?:[.,]\d+)?\b")
        self.assertNotRegex(self.sql, r"\b\d{3}\.\d{3}\.\d{3}-\d{2}\b")
        self.assertNotIn("r$", self.sql)


if __name__ == "__main__":
    unittest.main()
