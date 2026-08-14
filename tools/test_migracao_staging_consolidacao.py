import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRACAO = ROOT / "supabase/migrations/202608130001_staging_consolidacao_privada.sql"


class MigracaoStagingConsolidacaoTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql = MIGRACAO.read_text(encoding="utf-8").lower()

    def test_transacao_e_tabelas_esperadas(self):
        self.assertRegex(self.sql, r"(?m)^begin;$")
        self.assertRegex(self.sql, r"(?m)^commit;$")
        for tabela in (
            "fontes_importacao",
            "negocios_candidatos",
            "negocio_versoes",
            "evidencias_negocio",
            "transacoes_banco_staging",
            "conciliacoes_candidatas",
            "vinculos_documentais_candidatos",
            "decisoes_consolidacao",
        ):
            self.assertIn(f"create table if not exists public.{tabela}", self.sql)
            self.assertIn(f"alter table public.{tabela} enable row level security", self.sql)

    def test_nao_muda_dados_operacionais(self):
        corpo = re.sub(r"--.*?$", "", self.sql, flags=re.MULTILINE)
        proibidos = (
            r"\binsert\s+into\s+public\.(operacoes|compras|vendas|fluxo_caixa|transacoes_banco|gtas|notas_fiscais_xml_raw)",
            r"\bupdate\s+public\.(operacoes|compras|vendas|fluxo_caixa|transacoes_banco|gtas|notas_fiscais_xml_raw)",
            r"\bdelete\s+from\b",
            r"\btruncate\b",
            r"\bdrop\s+table\b",
        )
        for padrao in proibidos:
            self.assertNotRegex(corpo, padrao)

    def test_idempotencia_das_fontes_e_do_ofx(self):
        self.assertIn("unique (tipo, hash_sha256)", self.sql)
        self.assertIn("unique (conta, fitid)", self.sql)
        self.assertIn("unique (chave_rastreio)", self.sql)
        self.assertIn("unique (negocio_candidato_id, versao_referencia)", self.sql)

    def test_conciliacao_e_muitos_para_muitos_sem_promocao(self):
        self.assertIn("valor_alocado numeric not null", self.sql)
        self.assertIn("num_nonnulls(negocio_candidato_id, operacao_id, fluxo_caixa_id) = 1", self.sql)
        self.assertIn("confirmação não altera fluxo_caixa", self.sql)

    def test_datas_valores_confianca_e_estados_sao_tipados(self):
        self.assertIn("data_base date", self.sql)
        self.assertIn("mensagem_em timestamptz", self.sql)
        self.assertIn("valor_total numeric", self.sql)
        self.assertGreaterEqual(self.sql.count("confianca >= 0 and confianca <= 1"), 3)
        self.assertIn("'rascunho', 'em_revisao', 'confirmado', 'rejeitado', 'incorporado'", self.sql)

    def test_authenticated_somente_leitura_e_sem_delete_service_role(self):
        self.assertNotRegex(self.sql, r"grant\s+(insert|update|delete|all).*to authenticated")
        self.assertNotRegex(self.sql, r"grant\s+[^;]*delete[^;]*to service_role")
        self.assertEqual(self.sql.count("for select to authenticated using (true)"), 8)

    def test_decisoes_preservam_auditoria_e_nao_podem_ser_alteradas(self):
        self.assertIn("estado_anterior text not null", self.sql)
        self.assertIn("estado_novo text not null", self.sql)
        self.assertIn("decidido_por text not null", self.sql)
        self.assertIn("evidencias_ids uuid[]", self.sql)
        self.assertIn("grant select, insert on public.decisoes_consolidacao to service_role", self.sql)
        self.assertNotIn("grant select, insert, update on public.decisoes_consolidacao", self.sql)


if __name__ == "__main__":
    unittest.main()
