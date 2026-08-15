import pathlib
import unittest


RAIZ = pathlib.Path(__file__).resolve().parents[1]
MIGRACAO = RAIZ / "supabase/migrations/202608150001_confinamentos_contatos_ressarcimentos.sql"


class MigracaoConfinamentosRessarcimentosTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRACAO.read_text(encoding="utf-8")

    def test_cria_tabelas_e_chaves_auditaveis(self):
        self.assertIn("CREATE TABLE IF NOT EXISTS public.confinamento_contatos", self.sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS public.ressarcimentos_operacionais", self.sql)
        self.assertIn("CREATE TABLE IF NOT EXISTS public.inventarios_confinamento", self.sql)
        self.assertIn("REFERENCES public.operacoes(id)", self.sql)
        self.assertIn("REFERENCES public.contatos(id)", self.sql)
        self.assertIn("UNIQUE (operacao_id, contato_id, tipo, descricao)", self.sql)

    def test_restringe_escrita_dos_clientes(self):
        self.assertIn("ENABLE ROW LEVEL SECURITY", self.sql)
        self.assertIn("FOR SELECT TO authenticated USING (true)", self.sql)
        self.assertIn("REVOKE INSERT, UPDATE, DELETE", self.sql)
        self.assertNotIn("FOR INSERT TO authenticated", self.sql)


if __name__ == "__main__":
    unittest.main()
