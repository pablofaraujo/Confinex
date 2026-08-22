import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.exportar_snapshot_consolidacao import (
    LeitorSupabase,
    assinatura,
    gerar_snapshot,
)


class Resposta:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return json.dumps(self.payload).encode()


class ExportarSnapshotConsolidacaoTest(unittest.TestCase):
    def test_assinatura_independe_da_ordem(self):
        self.assertEqual(
            assinatura([{"id": "2"}, {"id": "1"}]),
            assinatura([{"id": "1"}, {"id": "2"}]),
        )

    def test_listar_usa_apenas_get_e_nao_expoe_chave_na_url(self):
        chamadas = []

        def abrir(requisicao, timeout):
            chamadas.append((requisicao, timeout))
            return Resposta([{"id": "1"}])

        leitor = LeitorSupabase("https://exemplo.invalid", "segredo", opener=abrir)
        self.assertEqual(leitor.listar("compras"), [{"id": "1"}])
        requisicao, timeout = chamadas[0]
        self.assertEqual(requisicao.get_method(), "GET")
        self.assertNotIn("segredo", requisicao.full_url)
        self.assertLessEqual(timeout, 20)

    def test_bloqueia_tabela_fora_do_contrato(self):
        leitor = LeitorSupabase("https://exemplo.invalid", "segredo")
        with self.assertRaisesRegex(ValueError, "tabela não permitida"):
            leitor.listar("usuarios")

    def test_snapshot_declara_zero_escritas(self):
        leitor = LeitorSupabase(
            "https://exemplo.invalid", "segredo", opener=lambda *_args, **_kwargs: Resposta([])
        )
        snapshot = gerar_snapshot(leitor)
        self.assertEqual(snapshot["modo"], "somente_leitura")
        self.assertEqual(snapshot["escritas_executadas"], 0)
        self.assertTrue(snapshot["resumo"])

    def test_fonte_nao_contem_metodos_de_escrita(self):
        fonte = Path(__file__).with_name("exportar_snapshot_consolidacao.py").read_text()
        for proibido in ('method="POST"', 'method="PATCH"', 'method="DELETE"'):
            self.assertNotIn(proibido, fonte)


if __name__ == "__main__":
    unittest.main()
