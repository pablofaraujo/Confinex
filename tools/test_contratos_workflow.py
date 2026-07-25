import tempfile
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from contratos_workflow import analisar


class ContratosWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.documento = Path(self.temp.name) / "contrato-ficticio.pdf"
        self.documento.write_bytes(b"%PDF-1.4 fixture sem dados reais")
        self.negocio = {
            "referencia": "NEGOCIO-FICTICIO",
            "quantidade": 2,
            "peso_total_kg": 1000,
            "valor_total": 5000,
            "data_inicio": "2026-01-01",
            "data_fim": "2026-02-01",
            "pagamento": "mensal",
        }
        self.termos = {
            "partes": ["Parte A", "Parte B"],
            "obrigacoes": ["Cuidar dos animais"],
            "multas": "2%",
            "garantias": "nenhuma",
            "foro": "Cidade fictícia",
            "rescisao": "30 dias",
        }
        self.extraido = {**self.negocio, **self.termos}

    def tearDown(self):
        self.temp.cleanup()

    def analisar(self, **changes):
        return analisar(
            self.documento,
            extraido={**self.extraido, **changes},
            negocio=self.negocio,
            termos=self.termos,
            historico_hashes=set(),
        )

    def test_contrato_igual_fica_pronto_para_pedir_aprovacao(self):
        report = self.analisar()
        self.assertEqual(report["bloqueios"], [])
        self.assertTrue(report["pode_pedir_aprovacao"])
        self.assertFalse(report["pode_assinar"])
        self.assertEqual(report["acoes_externas_executadas"], 0)

    def test_divergencia_financeira_bloqueia(self):
        report = self.analisar(valor_total=5100)
        self.assertIn("dados_divergentes_do_negocio", report["bloqueios"])
        self.assertEqual(report["divergencias_negocio"][0]["campo"], "valor_total")

    def test_clausula_alterada_exige_revisao(self):
        report = self.analisar(multas="10%")
        self.assertIn("clausulas_alteradas", report["bloqueios"])
        self.assertTrue(
            report["triagem_juridica"]["aprovacao_especifica_necessaria"]
        )

    def test_hash_repetido_impede_duplicidade(self):
        first = self.analisar()
        repeated = analisar(
            self.documento,
            extraido=self.extraido,
            negocio=self.negocio,
            termos=self.termos,
            historico_hashes={first["documento"]["sha256"]},
        )
        self.assertTrue(repeated["documento"]["duplicado"])
        self.assertIn("documento_duplicado", repeated["bloqueios"])

    def test_finpec_sem_brincos_fica_pendente_e_nao_cria_garantia(self):
        report = self.analisar(finpec=True, alienacao_fiduciaria=True, brincos=[])
        self.assertEqual(report["finpec"]["estado"], "BRINCOS_PENDENTES")
        self.assertFalse(report["pode_criar_garantia"])

    def test_finpec_com_brincos_duplicados_fica_pendente(self):
        report = self.analisar(
            finpec=True,
            alienacao_fiduciaria=True,
            brincos=["A-1", "A-1"],
        )
        self.assertEqual(report["finpec"]["estado"], "BRINCOS_PENDENTES")
        self.assertEqual(report["finpec"]["brincos_duplicados"], ["A-1"])

    def test_finpec_identificado_ainda_exige_revisao_juridica(self):
        report = self.analisar(
            finpec=True,
            alienacao_fiduciaria=True,
            brincos=["A-1", "A-2"],
        )
        self.assertEqual(report["finpec"]["estado"], "REVISAO_JURIDICA")
        self.assertFalse(report["pode_criar_garantia"])

    def test_destino_drive_e_apenas_proposta(self):
        report = self.analisar()
        self.assertIn(
            "ClaudeCoWork/Contratos/NEGOCIO-FICTICIO/",
            report["documento"]["destino_drive_proposto"],
        )
        self.assertEqual(report["acoes_externas_executadas"], 0)


if __name__ == "__main__":
    unittest.main()
