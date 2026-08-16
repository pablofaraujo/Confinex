import unittest

from agronota_nf import analisar_xml_nfe, campos_pendentes_documento, extrair_gtas_texto


def xml_nfe(informacao: str, produto: str = "30 BOVINOS", natureza: str = "") -> bytes:
    return f'''<?xml version="1.0"?>
    <nfeProc xmlns="http://www.portalfiscal.inf.br/nfe">
      <NFe><infNFe Id="NFe{'1' * 44}"><ide><natOp>{natureza}</natOp></ide>
        <det><prod><xProd>{produto}</xProd></prod><infAdProd>{informacao}</infAdProd></det>
        <infAdic><infCpl>{informacao}</infCpl></infAdic>
      </infNFe></NFe>
    </nfeProc>'''.encode()


class AgronotaNfTests(unittest.TestCase):
    def test_extrai_gta_da_informacao_complementar(self):
        resultado = analisar_xml_nfe(xml_nfe("GTA nº 123.456/2026"))
        self.assertEqual(resultado["gtas"], ["1234562026"])
        self.assertTrue(resultado["relacionada_a_gado"])
        self.assertFalse(resultado["gta_ambigua"])

    def test_deduplica_mesma_gta_em_campos_distintos(self):
        resultado = analisar_xml_nfe(xml_nfe("GTA: 123456-7"))
        self.assertEqual(resultado["gtas"], ["1234567"])

    def test_multiplas_gtas_exigem_revisao(self):
        resultado = analisar_xml_nfe(xml_nfe("GTA 123456 e GTA 654321"))
        self.assertEqual(resultado["gtas"], ["123456", "654321"])
        self.assertIsNone(resultado["gta"])
        self.assertTrue(resultado["gta_ambigua"])

    def test_gta_torna_documento_relevante_sem_inventar_categoria(self):
        resultado = analisar_xml_nfe(xml_nfe("GTA 123456", produto="LOTE COMERCIAL"))
        self.assertTrue(resultado["relacionada_a_gado"])
        self.assertEqual(resultado["gta"], "123456")

    def test_nao_inventa_gta_a_partir_de_data(self):
        self.assertEqual(extrair_gtas_texto("Emissão 14/08/2026 sem guia"), [])
        self.assertEqual(extrair_gtas_texto("GTA emitida em 14/08/2026"), [])

    def test_aceita_uf_e_texto_entre_rotulo_e_numero(self):
        self.assertEqual(extrair_gtas_texto("GTA/MG número: 123456"), ["123456"])

    def test_identifica_natureza_de_venda_sem_confundir_transferencia(self):
        self.assertTrue(analisar_xml_nfe(xml_nfe("GTA 123456", natureza="Venda de gado"))["eh_nota_venda"])
        self.assertFalse(analisar_xml_nfe(xml_nfe("GTA 123456", natureza="Transferência"))["eh_nota_venda"])

    def test_nota_de_venda_nao_procura_negocio_anterior(self):
        self.assertEqual(
            campos_pendentes_documento(tem_gta=True, operacao_vinculada=False, novo_negocio=True),
            ["extrato bancário ou comprovante"],
        )

    def test_lista_apenas_pendencias_reais(self):
        self.assertEqual(
            campos_pendentes_documento(tem_gta=True, operacao_vinculada=True),
            ["extrato bancário ou comprovante"],
        )
        self.assertEqual(
            campos_pendentes_documento(tem_gta=False, operacao_vinculada=False),
            ["número da GTA", "negócio correspondente", "extrato bancário ou comprovante"],
        )


if __name__ == "__main__":
    unittest.main()
