import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from tools.orquestrar_conciliacao_whatsapp import (
    buscar_evidencias,
    cobertura_suficiente,
    descobrir_candidatos,
    executar_backfills,
    jid_whatsapp,
    normalizar,
    pontuar_chat,
    pergunta_pendente,
    tokens_negocio,
)


class OrquestrarConciliacaoWhatsappTest(unittest.TestCase):
    def test_normaliza_e_remove_tokens_genericos(self):
        self.assertEqual(normalizar("Rogério São José"), "rogerio sao jose")
        self.assertEqual(tokens_negocio("PABLO FERREIRA ARAUJO"), [])
        self.assertEqual(tokens_negocio("Manoel Cazassa"), ["manoel", "cazassa"])

    def test_cobertura_precisa_alcancar_data_do_negocio(self):
        cobertura = {"oldest_ts": "2026-05-16T12:00:00Z"}
        self.assertFalse(cobertura_suficiente(cobertura, datetime(2026, 4, 28)))
        self.assertTrue(cobertura_suficiente(cobertura, datetime(2026, 6, 1)))

    def test_prioriza_nome_completo_e_alias_ortografico(self):
        tokens = tokens_negocio("Manoel Cazassa")
        self.assertGreater(
            pontuar_chat("Manuel Cazassa", tokens),
            pontuar_chat("Joao Manoel Rialma", tokens),
        )

    def test_normaliza_whatsapp_para_jid_sem_aceitar_valor_invalido(self):
        self.assertEqual(jid_whatsapp("+55 (17) 99999-1111"), "5517999991111@s.whatsapp.net")
        self.assertIsNone(jid_whatsapp("123"))

    @patch("tools.orquestrar_conciliacao_whatsapp.listar_chats", return_value=[])
    def test_contato_com_whatsapp_vira_candidato_direto(self, _listar):
        candidatos = descobrir_candidatos({
            "negocio": "CSAP - 141 cabeças",
            "contatos": [{"nome": "Vinicius Peron", "whatsapp": "+5517999991111"}],
        }, Path("/wacli"), Path("/store"))
        self.assertEqual(candidatos[0]["jid"], "5517999991111@s.whatsapp.net")
        self.assertEqual(candidatos[0]["origem"], "contato_supabase")

    @patch("tools.orquestrar_conciliacao_whatsapp.listar_mensagens_chat")
    def test_localiza_pdf_de_acerto_sem_enviar_mensagem(self, listar):
        listar.return_value = [{
            "ChatJID": "5517999991111@s.whatsapp.net",
            "MsgID": "msg-1",
            "Timestamp": "2026-08-12T10:59:55Z",
            "FromMe": False,
            "Text": "Sent document",
            "MediaType": "document",
            "Filename": "Acerto 141 cabecas - Abate 07.08.pdf",
        }]
        evidencias = buscar_evidencias({
            "data": "07/08/2026",
            "tipo": "acerto_confinamento",
            "termos_busca": ["141", "acerto", "abate"],
        }, [{"jid": "5517999991111@s.whatsapp.net", "name": "Contato CSAP"}], Path("/wacli"), Path("/store"))
        self.assertEqual(evidencias[0]["mensagem_id"], "msg-1")
        self.assertTrue(evidencias[0]["documento_candidato"])
        self.assertGreaterEqual(evidencias[0]["pontuacao"], 60)

    @patch("tools.orquestrar_conciliacao_whatsapp.listar_chats")
    def test_descobre_chat_por_nome_sem_aceitar_grupo(self, listar):
        listar.side_effect = [[
            {"jid": "5511999999999@s.whatsapp.net", "name": "Allan Casa do Produtor"},
            {"jid": "123@g.us", "name": "Grupo Allan"},
        ]]
        candidatos = descobrir_candidatos(
            {"negocio": "Allan"}, Path("/wacli"), Path("/store")
        )
        self.assertEqual(len(candidatos), 1)
        self.assertEqual(candidatos[0]["pontuacao"], 40)

    @patch("tools.orquestrar_conciliacao_whatsapp.subprocess.run")
    def test_backfill_e_serial_e_nao_envia_mensagem(self, executar):
        executar.return_value.stdout = ""
        plano = [{
            "codigo": "NEG-1",
            "candidatos": [{"jid": "5511999999999@s.whatsapp.net", "precisa_backfill": True}],
        }]
        resultado = executar_backfills(
            plano, Path("/wacli"), Path("/store"),
            maximo=1, requisicoes=2, quantidade=50, espera="45s",
        )
        self.assertEqual(resultado[0]["status"], "executado")
        comando = executar.call_args.args[0]
        self.assertIn("backfill", comando)
        self.assertNotIn("send", comando)
        self.assertNotIn("--read-only", comando)
        self.assertNotIn("WACLI_READONLY", executar.call_args.kwargs["env"])

    @patch("tools.orquestrar_conciliacao_whatsapp.subprocess.run")
    def test_backfill_distribui_codigos_e_deduplica_chat(self, executar):
        executar.return_value.stdout = ""
        plano = [
            {"codigo": "NEG-1", "candidatos": [
                {"jid": "1@s.whatsapp.net", "precisa_backfill": True},
                {"jid": "2@s.whatsapp.net", "precisa_backfill": True},
            ]},
            {"codigo": "NEG-2", "candidatos": [
                {"jid": "3@s.whatsapp.net", "precisa_backfill": True},
                {"jid": "1@s.whatsapp.net", "precisa_backfill": True},
            ]},
        ]
        resultado = executar_backfills(
            plano, Path("/wacli"), Path("/store"),
            maximo=3, requisicoes=1, quantidade=50, espera="45s",
        )
        self.assertEqual(len({item["jid"] for item in resultado}), 3)

    @patch("tools.orquestrar_conciliacao_whatsapp.subprocess.run")
    def test_backfill_prioriza_menor_numero_de_tentativas(self, executar):
        executar.return_value.stdout = ""
        plano = [{"codigo": "NEG-1", "candidatos": [
            {"jid": "repetido@s.whatsapp.net", "precisa_backfill": True, "pontuacao": 40},
            {"jid": "novo@s.whatsapp.net", "precisa_backfill": True, "pontuacao": 20},
        ]}]
        estado = {"backfills": {"repetido@s.whatsapp.net": {"tentativas": 3}}}
        resultado = executar_backfills(
            plano, Path("/wacli"), Path("/store"), maximo=1,
            requisicoes=1, quantidade=50, espera="10s", estado=estado,
        )
        self.assertEqual(resultado[0]["jid"], "novo@s.whatsapp.net")
        self.assertEqual(estado["backfills"]["novo@s.whatsapp.net"]["tentativas"], 1)

    @patch("tools.orquestrar_conciliacao_whatsapp.subprocess.run")
    def test_timeout_do_aparelho_vira_retentativa_e_nao_falha_generica(self, executar):
        executar.side_effect = __import__("subprocess").CalledProcessError(
            1, ["wacli"], stderr="timed out waiting for on-demand history sync response"
        )
        plano = [{"codigo": "NEG-1", "candidatos": [
            {"jid": "1@s.whatsapp.net", "precisa_backfill": True},
        ]}]
        resultado = executar_backfills(
            plano, Path("/wacli"), Path("/store"),
            maximo=1, requisicoes=1, quantidade=50, espera="10s",
        )
        self.assertEqual(resultado[0]["status"], "sem_resposta_aparelho")

    def test_gera_pergunta_pronta_sem_executar_acao(self):
        pergunta = pergunta_pendente({
            "negocio": "Exemplo",
            "campos_faltantes": "peso e data",
            "divergencias": "",
            "valores": ["R$ 10.000,00"],
        })
        self.assertIn("peso e data", pergunta)
        self.assertIn("R$ 10.000,00", pergunta)

    def test_fonte_nao_contem_integracoes_operacionais(self):
        fonte = Path(__file__).with_name("orquestrar_conciliacao_whatsapp.py").read_text()
        for proibido in ("openclaw message", "requests.post", "requests.patch", "wacli send"):
            self.assertNotIn(proibido, fonte.lower())


if __name__ == "__main__":
    unittest.main()
