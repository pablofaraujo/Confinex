import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRACAO = ROOT / "supabase/migrations/202608120001_confinex_bases_online.sql"


class MigracaoBasesOnlineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRACAO.read_text(encoding="utf-8").lower()

    def test_migracao_e_aditiva_e_isolada(self):
        self.assertIn("create table if not exists public.confinex_bases", self.sql)
        for comando in ("drop table", "truncate", "alter table public.compras", "insert into public.compras"):
            self.assertNotIn(comando, self.sql)

    def test_rls_isola_cada_usuario(self):
        self.assertIn("enable row level security", self.sql)
        self.assertGreaterEqual(self.sql.count("criado_por = auth.uid()"), 2)
        self.assertNotIn("to anon", self.sql)

    def test_chave_e_nome_sao_obrigatorios(self):
        self.assertIn("primary key (criado_por, chave)", self.sql)
        self.assertIn("confinex_bases_chave_valida", self.sql)
        self.assertIn("confinex_bases_nome_valido", self.sql)

    def test_versao_antiga_nao_sobrescreve_a_online(self):
        self.assertIn("where excluded.atualizado_em >= atual.atualizado_em", self.sql)
        self.assertIn("salvar_base_confinex", self.sql)

    def test_migracao_nao_transporta_dados_locais(self):
        self.assertNotIn("localstorage", self.sql)
        self.assertNotIn("confinex_testes", self.sql)


if __name__ == "__main__":
    unittest.main()
