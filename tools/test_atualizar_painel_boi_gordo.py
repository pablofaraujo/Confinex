import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from atualizar_painel_boi_gordo import atualizar, contexto_ssl, contratos_a_partir, cotacao_b3, montar_atualizacao_b3, validar


BASE = {
    "atualizadoEm": "2026-07-15 10:21:00",
    "fonte": "referência anterior",
    "indicadores": [{"label": "Físico", "valor": "R$ 1", "delta": "", "dir": "up"}],
    "curvaBGI": [{"venc": "Físico (CEPEA)", "mes": "jul/26", "valor": 328.1, "agio": 0}],
}
ROOT = Path(__file__).resolve().parents[1]


def resposta_b3(contrato="BGIU26", data="2026-08-28", valores=(357.8, 358.65)):
    return {
        "BizSts": {"cd": "OK"},
        "TradgFlr": {
            "date": data,
            "scty": {"symb": contrato, "lstQtn": [{"closPric": valor, "dtTm": f"16:{indice:02d}:00"} for indice, valor in enumerate(valores)]},
        },
    }


class AtualizadorPainelTest(unittest.TestCase):
    def test_valida_schema_completo(self):
        self.assertIs(validar(BASE), BASE)

    def test_rejeita_resposta_vazia(self):
        with self.assertRaisesRegex(ValueError, "campos"):
            validar({})

    def test_tls_permanece_com_verificacao_de_certificado(self):
        self.assertNotEqual(contexto_ssl().verify_mode.name, "CERT_NONE")

    def test_gera_doze_contratos_em_ordem_incluindo_virada_do_ano(self):
        contratos = contratos_a_partir(dt.date(2026, 8, 29))
        self.assertEqual(contratos[0], ("BGIQ26", "ago/26"))
        self.assertEqual(contratos[4], ("BGIZ26", "dez/26"))
        self.assertEqual(contratos[5], ("BGIF27", "jan/27"))
        self.assertEqual(len(contratos), 12)

    def test_cotacao_usa_ultimo_negocio_e_data_do_pregao(self):
        item = cotacao_b3("BGIU26", "set/26", abrir_json=lambda _url, _timeout: resposta_b3())
        self.assertEqual(item["valor"], 358.65)
        self.assertEqual(item["dataFonte"], "2026-08-28 16:01:00")
        self.assertEqual(item["tipo"], "futuro")

    def test_resposta_nok_nao_vira_cotacao(self):
        self.assertIsNone(cotacao_b3("BGIJ27", "abr/27", abrir_json=lambda _url, _timeout: {"BizSts": {"cd": "NOK"}}))

    def test_atualiza_b3_sem_fingir_que_referencias_antigas_sao_novas(self):
        def abrir(url, _timeout):
            contrato = url.rsplit("/", 1)[-1]
            return resposta_b3(contrato=contrato)

        resultado = montar_atualizacao_b3(json.loads(json.dumps(BASE)), dt.date(2026, 8, 29), abrir)
        self.assertEqual(resultado["atualizadoEmB3"], "2026-08-28 16:01:00")
        self.assertEqual(resultado["referenciasAtualizadasEm"], BASE["atualizadoEm"])
        self.assertEqual(resultado["indicadores"][0]["dataFonte"], BASE["atualizadoEm"])
        self.assertEqual(resultado["curvaBGI"][0]["tipo"], "fisico")
        self.assertIsNone(resultado["curvaBGI"][0]["agio"])
        self.assertTrue(all(item["agio"] is None for item in resultado["curvaBGI"]))

    def test_pregao_muito_antigo_preserva_arquivo(self):
        with self.assertRaisesRegex(ValueError, "defasado"):
            montar_atualizacao_b3(BASE, dt.date(2026, 8, 29), lambda _url, _timeout: resposta_b3(data="2026-08-01"))

    def test_falha_nao_sobrescreve_artefato(self):
        with tempfile.TemporaryDirectory() as pasta:
            destino = Path(pasta) / "painel.json"
            destino.write_text(json.dumps(BASE), encoding="utf-8")
            with patch("atualizar_painel_boi_gordo.montar_atualizacao_b3", side_effect=ValueError("vazia")):
                with self.assertRaises(ValueError):
                    atualizar(destino=destino)
            self.assertEqual(json.loads(destino.read_text(encoding="utf-8")), BASE)

    def test_agendamento_usa_b3_oficial_sem_variavel_externa(self):
        workflow = (ROOT / ".github" / "workflows" / "atualizar-painel-boi-gordo.yml").read_text(encoding="utf-8")
        self.assertIn("python3 tools/atualizar_painel_boi_gordo.py", workflow)
        self.assertNotIn("PAINEL_BOI_GORDO_SOURCE_URL", workflow)
        self.assertIn("::error::Painel não atualizado", workflow)
        self.assertIn("timeout-minutes: 5", workflow)


if __name__ == "__main__":
    unittest.main()
