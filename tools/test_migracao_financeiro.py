import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRACAO = ROOT / "supabase/migrations/202607240001_financeiro_compromissos.sql"


class MigracaoFinanceiroTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRACAO.read_text(encoding="utf-8")
        cls.normalizado = re.sub(r"--.*", "", cls.sql).lower()

    def test_modelo_cobre_compromissos_e_eventos_financeiros(self):
        for tabela in (
            "financeiro_compromissos",
            "financeiro_parcelas",
            "financeiro_pagamentos",
            "financeiro_renegociacoes",
            "financeiro_lembretes",
        ):
            self.assertIn(f"create table if not exists public.{tabela}", self.normalizado)

    def test_migracao_nao_muda_dados_operacionais(self):
        for comando in ("insert into", "delete from", "truncate ", "drop table"):
            self.assertNotIn(comando, self.normalizado)
        self.assertNotRegex(self.normalizado, r"\bupdate\s+public\.")

    def test_rls_e_somente_leitura(self):
        for tabela in (
            "financeiro_compromissos",
            "financeiro_parcelas",
            "financeiro_pagamentos",
            "financeiro_renegociacoes",
            "financeiro_lembretes",
        ):
            self.assertIn(
                f"alter table public.{tabela} enable row level security",
                self.normalizado,
            )
        self.assertNotRegex(
            self.normalizado,
            r"create policy[\s\S]*?\bfor\s+(insert|update|delete|all)\b",
        )

    def test_view_respeita_rls_e_calcula_saldo(self):
        self.assertIn("with (security_invoker = true)", self.normalizado)
        self.assertIn("as saldo_aberto", self.normalizado)
        self.assertIn("as total_pago", self.normalizado)
        self.assertIn("as proximo_vencimento", self.normalizado)

    def test_validacoes_impedem_valores_e_estados_invalidos(self):
        self.assertGreaterEqual(self.normalizado.count("check (valor > 0)"), 2)
        self.assertIn("check (valor_original > 0)", self.normalizado)
        self.assertIn("'parcial'", self.normalizado)
        self.assertIn("'renegociado'", self.normalizado)


if __name__ == "__main__":
    unittest.main()
