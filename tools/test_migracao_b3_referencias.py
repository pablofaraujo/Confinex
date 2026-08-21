import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRACAO = ROOT / "supabase/migrations/202608210001_b3_referencias_sequenciais.sql"


class MigracaoReferenciasB3Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRACAO.read_text(encoding="utf-8")

    def test_migracao_e_aditiva_e_nao_toca_tabelas_operacionais(self):
        sql = self.sql.lower()
        self.assertIn("add column if not exists referencia_bolsa text", sql)
        self.assertNotRegex(sql, r"\b(?:delete|truncate|drop table)\b")
        for tabela in ("compras", "vendas", "abates", "pesagens_caderno"):
            self.assertNotRegex(sql, rf"(?:insert\s+into|update|alter\s+table)\s+public\.{tabela}\b")

    def test_referencia_tem_formato_unico_e_imutavel(self):
        self.assertIn("^B3-[0-9]{2}-[0-9]{3,}$", self.sql)
        self.assertIn("create unique index if not exists", self.sql.lower())
        self.assertIn("não pode ser alterada", self.sql)

    def test_contador_anual_e_atomico(self):
        sql = self.sql.lower()
        self.assertIn("referencias_bolsa_contadores", sql)
        self.assertIn("on conflict (ano) do update", sql)
        self.assertIn("returning ultimo_numero into v_numero", sql)
        self.assertIn("security definer", sql)
        self.assertIn("enable row level security", sql)

    def test_legado_recebe_numero_deterministico(self):
        sql = self.sql.lower()
        self.assertIn("row_number() over", sql)
        self.assertIn("coalesce(p.data_entrada, p.created_at::date, current_date)", sql)
        self.assertRegex(sql, re.compile(r"where\s+p\.referencia_bolsa\s+is\s+null"))


if __name__ == "__main__":
    unittest.main()
