import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tools.conciliar_whatsapp_pix import (
    centavos,
    gerar_plano,
    ler_mensagens_wacli,
    ler_mensagens,
    normalizar_referencia_b3,
    regex_referencia_b3,
    regex_valor,
    variantes_valor,
)


class ConciliarWhatsappPixTest(unittest.TestCase):
    def test_normaliza_valores_brasileiros(self):
        self.assertEqual(centavos("R$ 82.361,00"), 8_236_100)
        self.assertEqual(centavos("82361,00"), 8_236_100)
        self.assertEqual(centavos("475.020"), 47_502_000)
        self.assertIn("82.361,00", variantes_valor(8_236_100))
        self.assertRegex("PIX R$ 82.361,00 enviado", regex_valor(8_236_100))
        self.assertNotRegex("telefone 5533823610012", regex_valor(8_236_100))

    def test_normaliza_referencia_humana_de_bolsa(self):
        self.assertEqual(normalizar_referencia_b3("b3 26 7"), "B3-26-007")
        self.assertRegex("Fechamos B3-26-007 com a mesa", regex_referencia_b3("B3-26-007"))
        self.assertNotRegex("B3-27-007", regex_referencia_b3("B3-26-007"))

    def criar_sessao(self, pasta: Path, sessao: str, mensagens: list[dict]):
        caminho = pasta / f"{sessao}.jsonl"
        linhas = [{"type": "session", "id": sessao, "timestamp": "2026-08-01T00:00:00Z"}]
        for indice, item in enumerate(mensagens, start=1):
            linhas.append({
                "type": "message",
                "id": item.get("id", f"m{indice}"),
                "timestamp": item.get("timestamp", f"2026-08-0{indice}T12:00:00Z"),
                "message": {
                    "role": item.get("role", "user"),
                    "sourceChannel": item.get("sourceChannel", "whatsapp"),
                    "content": [{"type": "text", "text": item["text"]}],
                },
            })
        caminho.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in linhas) + "\n")
        return caminho

    def test_localiza_valor_e_prioriza_contexto_pix(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            sessao = "11111111-1111-1111-1111-111111111111"
            self.criar_sessao(pasta, sessao, [{
                "text": "[WhatsApp contato] Allan: PIX de R$ 82.361,00. Segue o comprovante.",
                "timestamp": "2026-08-12T12:00:00Z",
            }])
            mensagens = ler_mensagens(pasta, {sessao: "agent:wey:whatsapp:direct:+5533999991111"})
            plano = gerar_plano([{
                "codigo": "NEG-26-005",
                "negocio": "Allan",
                "valores": ["82.361,00"],
                "data": "12/08/2026",
            }], mensagens)
            resultado = plano["resultados"][0]
            self.assertEqual(resultado["status"], "encontrado_unico")
            self.assertGreaterEqual(resultado["candidatos"][0]["pontuacao"], 150)
            self.assertEqual(resultado["candidatos"][0]["remetente"], "Allan")

    def test_preserva_ambiguidade_em_conversas_distintas(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            s1 = "11111111-1111-1111-1111-111111111111"
            s2 = "22222222-2222-2222-2222-222222222222"
            self.criar_sessao(pasta, s1, [{"text": "PIX 35.050,00"}])
            self.criar_sessao(pasta, s2, [{"text": "Comprovante 35.050,00"}])
            mensagens = ler_mensagens(pasta, {
                s1: "agent:wey:whatsapp:direct:+5533999991111",
                s2: "agent:wey:whatsapp:direct:+5533999992222",
            })
            plano = gerar_plano([{"codigo": "NEG-X", "negocio": "", "valores": [35050]}], mensagens)
            self.assertEqual(plano["resultados"][0]["status"], "ambiguo")

    def test_referencia_b3_identifica_a_conversa_mesmo_sem_valor(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            sessao = "55555555-5555-5555-5555-555555555555"
            self.criar_sessao(pasta, sessao, [{
                "text": "Mesa confirmou o fechamento da B3-26-014.",
            }])
            mensagens = ler_mensagens(pasta, {
                sessao: "agent:wey:whatsapp:direct:+5533999994444",
            })
            plano = gerar_plano([{
                "codigo": "hedge",
                "referencia_bolsa": "B3-26-014",
                "valores": [],
            }], mensagens)
            resultado = plano["resultados"][0]
            self.assertEqual(resultado["status"], "encontrado_unico")
            self.assertEqual(resultado["referencia_bolsa"], "B3-26-014")
            self.assertEqual(resultado["candidatos"][0]["referencia_bolsa"], "B3-26-014")
            self.assertGreaterEqual(resultado["candidatos"][0]["pontuacao"], 200)

    def test_ignora_assistente_e_outros_canais(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            sessao = "33333333-3333-3333-3333-333333333333"
            self.criar_sessao(pasta, sessao, [
                {"text": "PIX 41.651,00", "role": "assistant"},
                {"text": "PIX 41.651,00", "sourceChannel": "telegram"},
            ])
            self.assertEqual(ler_mensagens(pasta, {}), [])

    def test_recupera_contexto_whatsapp_de_sessao_arquivada(self):
        with tempfile.TemporaryDirectory() as tmp:
            pasta = Path(tmp)
            sessao = "44444444-4444-4444-4444-444444444444"
            self.criar_sessao(pasta, sessao, [{
                "text": "Comprovante do PIX 72.835,50",
                "sourceChannel": None,
            }])
            (pasta / f"{sessao}.trajectory.jsonl").write_text(json.dumps({
                "sessionId": sessao,
                "sessionKey": "agent:wey:whatsapp:direct:+5533999993333",
            }) + "\n")
            mensagens = ler_mensagens(pasta, {})
            self.assertEqual(len(mensagens), 1)
            self.assertEqual(mensagens[0].conversa, "WhatsApp — contato final 3333")

    def test_controles_proibem_efeitos_externos(self):
        plano = gerar_plano([], [])
        self.assertEqual(plano["modo"], "somente_leitura")
        self.assertEqual(plano["controles"], {
            "mensagens_enviadas": 0,
            "escritas_supabase": 0,
            "registros_operacionais_alterados": 0,
            "promocoes_executadas": 0,
        })
        fonte = Path(__file__).with_name("conciliar_whatsapp_pix.py").read_text()
        for proibido in ("requests.post", "requests.patch", "requests.delete", "openclaw message send", "--executar"):
            self.assertNotIn(proibido, fonte)

    @patch("tools.conciliar_whatsapp_pix.subprocess.run")
    def test_pesquisa_wacli_somente_leitura_e_inclui_enviadas(self, executar):
        executar.return_value.stdout = json.dumps({
            "success": True,
            "data": {
                "fts": True,
                "messages": [{
                    "ChatJID": "5533999991111@s.whatsapp.net",
                    "MsgID": "pix-1",
                    "Timestamp": "2026-08-12T14:00:00Z",
                    "FromMe": True,
                    "Text": "Enviei o PIX de R$ 82.361,00. Segue o comprovante.",
                }],
            },
            "error": None,
        })
        mensagens = ler_mensagens_wacli(Path("/usr/local/bin/wacli"), Path("/privado/wacli"), [8_236_100])
        self.assertEqual(len(mensagens), 1)
        self.assertEqual(mensagens[0].remetente, "mensagem enviada pelo titular")
        comandos = [chamada.args[0] for chamada in executar.call_args_list]
        self.assertTrue(comandos)
        for comando in comandos:
            self.assertIn("--read-only", comando)
            self.assertEqual(comando[-2], "--limit")
            self.assertNotIn("send", comando)
        for chamada in executar.call_args_list:
            self.assertEqual(chamada.kwargs["env"]["WACLI_READONLY"], "1")

    @patch("tools.conciliar_whatsapp_pix.subprocess.run")
    def test_pesquisa_referencia_b3_no_cache_wey_sem_valor(self, executar):
        executar.return_value.stdout = json.dumps({
            "success": True,
            "data": {"messages": []},
            "error": None,
        })

        ler_mensagens_wacli(
            Path("/usr/local/bin/wacli"),
            Path("/privado/wacli"),
            [],
            ["B3-26-014"],
        )

        comando = executar.call_args.args[0]
        self.assertIn("B3-26-014", comando)
        self.assertIn("--read-only", comando)
        self.assertNotIn("send", comando)


if __name__ == "__main__":
    unittest.main()
