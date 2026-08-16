import tempfile
import unittest
from pathlib import Path

from monitorar_agronota import executar, planejar
from test_agronota_nf import xml_nfe


class ClienteFalso:
    def __init__(self, notas, existentes=None):
        self.notas = notas
        self.existentes = dict(existentes or {})
        self.escritas = []

    def listar_notas(self, _desde):
        return self.notas

    def existe(self, tabela, identificador):
        return self.obter(tabela, identificador) is not None

    def obter(self, tabela, identificador):
        return self.existentes.get((tabela, identificador))

    def atualizar_nota(self, identificador, payload):
        self.escritas.append(("PATCH", "notas_fiscais_xml_raw", identificador, payload))

    def inserir(self, tabela, payload):
        self.escritas.append(("POST", tabela, payload["id"]))

    def atualizar(self, tabela, identificador, payload):
        self.escritas.append(("PATCH", tabela, identificador, payload))


class MonitorAgronotaTests(unittest.TestCase):
    def _nota(self):
        return {"id": "nf-1", "chave_acesso": "1" * 44, "numero": "10", "data": "2026-08-14",
                "valor": 100.0, "qtd_total_itens": 30, "descricao_itens": "BOVINOS",
                "operacao_id": "op-1", "gta": None, "alerta_gta_ausente": False}

    def test_dry_run_nao_escreve_e_prepara_apenas_revisao(self):
        cliente = ClienteFalso([self._nota()])
        with tempfile.TemporaryDirectory() as pasta:
            Path(pasta, f"{'1' * 44}-procNfe.xml").write_bytes(xml_nfe("GTA 123456"))
            plano = planejar(cliente, Path(pasta), "2026-08-13T00:00:00Z")
        self.assertEqual(cliente.escritas, [])
        self.assertEqual(plano["resumo"]["notas_atualizadas"], 1)
        self.assertEqual(plano["resumo"]["operation_drafts"], 1)
        self.assertEqual(plano["resumo"]["pending_actions"], 1)
        self.assertEqual(plano["resumo"]["eventos"], 1)
        self.assertEqual(plano["resumo"]["tabelas_operacionais_alteradas"], 0)
        draft = next(p for t, p in plano["criacoes"] if t == "operation_drafts")
        self.assertEqual(draft["campos_pendentes"], ["extrato bancário ou comprovante"])
        self.assertIsInstance(draft["confianca"], float)

    def test_execucao_idempotente_nao_ressuscita_registro_existente(self):
        nota = self._nota()
        primeira = ClienteFalso([nota])
        with tempfile.TemporaryDirectory() as pasta:
            Path(pasta, f"{'1' * 44}-procNfe.xml").write_bytes(xml_nfe("GTA 123456"))
            plano1 = planejar(primeira, Path(pasta), "x")
            existentes = {(t, p["id"]): p for t, p in plano1["criacoes"]}
            segunda = ClienteFalso([{**nota, "gta": "123456"}], existentes=existentes)
            plano2 = planejar(segunda, Path(pasta), "x")
        self.assertEqual(plano2["criacoes"], [])
        self.assertEqual(plano2["alteracoes_notas"], [])

    def test_execucao_restrita_ao_staging_revisao_e_evento(self):
        cliente = ClienteFalso([self._nota()])
        with tempfile.TemporaryDirectory() as pasta:
            Path(pasta, f"{'1' * 44}-procNfe.xml").write_bytes(xml_nfe("GTA 123456"))
            plano = planejar(cliente, Path(pasta), "x")
        executar(cliente, plano)
        tabelas = {item[1] for item in cliente.escritas}
        self.assertEqual(tabelas, {"notas_fiscais_xml_raw", "operation_drafts", "pending_actions", "eventos"})
        self.assertTrue({"compras", "vendas", "abates", "pesagens_caderno"}.isdisjoint(tabelas))
        posts = [item[1] for item in cliente.escritas if item[0] == "POST"]
        self.assertEqual(posts, ["pending_actions", "operation_drafts", "eventos"])

    def test_enriquece_revisao_aberta_quando_gta_passa_a_ser_reconhecida(self):
        nota = self._nota()
        with tempfile.TemporaryDirectory() as pasta:
            arquivo = Path(pasta, f"{'1' * 44}-procNfe.xml")
            arquivo.write_bytes(xml_nfe("documento sem guia"))
            inicial = ClienteFalso([nota])
            plano_inicial = planejar(inicial, Path(pasta), "x")
            existentes = {(t, p["id"]): p for t, p in plano_inicial["criacoes"]}
            arquivo.write_bytes(xml_nfe("GTA/MG número: 123456", produto="LOTE COMERCIAL"))
            enriquecido = ClienteFalso([nota], existentes=existentes)
            plano = planejar(enriquecido, Path(pasta), "x")
        self.assertEqual(plano["resumo"]["operation_drafts_atualizados"], 1)
        self.assertEqual(plano["resumo"]["pending_actions_atualizados"], 1)
        self.assertEqual(plano["resumo"]["eventos"], 1)
        draft_patch = next(p for t, _, p in plano["atualizacoes"] if t == "operation_drafts")
        self.assertNotIn("número da GTA", draft_patch["campos_pendentes"])
        self.assertEqual(draft_patch["dados_extraidos"]["gta"], "123456")

    def test_nota_de_venda_abre_novo_negocio_sem_exigir_vinculo_anterior(self):
        cliente = ClienteFalso([self._nota()])
        with tempfile.TemporaryDirectory() as pasta:
            Path(pasta, f"{'1' * 44}-procNfe.xml").write_bytes(
                xml_nfe("GTA 123456", natureza="Venda de animais")
            )
            plano = planejar(cliente, Path(pasta), "x")
        draft = next(p for t, p in plano["criacoes"] if t == "operation_drafts")
        action = next(p for t, p in plano["criacoes"] if t == "pending_actions")
        self.assertEqual(draft["tipo_operacao"], "novo_negocio_por_nota_fiscal")
        self.assertTrue(draft["dados_extraidos"]["novo_negocio"])
        self.assertEqual(draft["campos_pendentes"], ["extrato bancário ou comprovante"])
        self.assertEqual(action["acao_tipo"], "revisar_novo_negocio_fiscal")
        self.assertFalse(action["payload"]["promovido_para_operacional"])


if __name__ == "__main__":
    unittest.main()
