import tempfile
import unittest
from pathlib import Path

from tools.consolidar_historico_telegram import (
    agrupar_compras,
    cruzar_gtas,
    extrair_compra,
    gerar_plano,
    ler_exportacao,
    relatorio_markdown,
)


def html_exportacao(mensagens):
    blocos = []
    for indice, item in enumerate(mensagens, 1):
        autor = f'<div class="from_name">{item.get("autor", "")}</div>' if "autor" in item else ""
        texto = item.get("texto", "").replace("\n", "<br>")
        anexo = ""
        if item.get("anexo"):
            anexo = (
                '<div class="media_wrap clearfix">'
                f'<a class="photo_wrap" href="{item["anexo"]}">arquivo</a></div>'
            )
        blocos.append(
            f'<div class="message default clearfix" id="message{indice}">'
            f'<div class="date details" title="0{indice}.08.2026 10:00:00 UTC-03:00"></div>'
            f'{autor}{anexo}<div class="text">{texto}</div></div>'
        )
    return '<div class="text bold">Grupo de Teste</div>' + "".join(blocos)


def mensagem(texto, ordem=1, mensagem_id="m1"):
    return {
        "contexto": "Grupo de Teste",
        "texto": texto,
        "autor": "Agente",
        "ordem": ordem,
        "mensagem_id": mensagem_id,
        "data": f"0{ordem}.08.2026 10:00:00 UTC-03:00",
        "texto_sha256": f"hash-{mensagem_id}",
        "gtas": [],
        "categorias": [],
    }


class ConsolidarHistoricoTelegramTest(unittest.TestCase):
    def test_le_exportacao_herda_autor_e_confere_anexo(self):
        with tempfile.TemporaryDirectory() as pasta:
            raiz = Path(pasta)
            (raiz / "photos").mkdir()
            (raiz / "photos" / "foto.jpg").write_bytes(b"foto")
            caminho = raiz / "messages.html"
            caminho.write_text(html_exportacao([
                {"autor": "Pessoa", "texto": "primeira"},
                {"texto": "segunda", "anexo": "photos/foto.jpg"},
            ]))
            exportacao = ler_exportacao(caminho)
        self.assertEqual(exportacao["contexto"], "Grupo de Teste")
        self.assertEqual(exportacao["mensagens"][1]["autor"], "Pessoa")
        self.assertEqual(len(exportacao["anexos"]), 1)
        self.assertTrue(exportacao["anexos"][0]["existe"])
        self.assertEqual(len(exportacao["anexos"][0]["sha256"]), 64)

    def test_correcao_explicita_posterior_e_preferida_sem_confirmar(self):
        inicial = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 10 cabeças\nPeso bruto total: 3.000 kg\nNegociação: 01/08/2026",
            1, "m1"), {})
        correcao = extrair_compra(mensagem(
            "Correção final da Compra – Fornecedor\nQuantidade: 11 cabeças\nPeso bruto total: 3.300 kg\nNegociação: 01/08/2026",
            2, "m2"), {})
        grupo = agrupar_compras([inicial, correcao])[0]
        self.assertEqual(grupo["classificacao"], "correcao_explicita_mais_recente")
        self.assertEqual(grupo["versao_preferida"]["quantidade"], 11)
        self.assertFalse(grupo["confirmado"])
        self.assertTrue(grupo["requer_revisao"])
        self.assertEqual(grupo["situacao_revisao"], "conferir correção explícita")
        self.assertEqual(grupo["prioridade_revisao"], "alta")
        self.assertIn("cabeças", grupo["campos_divergentes_humanos"])

    def test_mesmo_fornecedor_e_data_com_dados_distintos_fica_ambiguo(self):
        primeira = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 10 cabeças\nPeso bruto total: 3.000 kg\nNegociação: 01/08/2026",
            1, "m1"), {})
        segunda = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 5 cabeças\nPeso bruto total: 1.500 kg\nNegociação: 01/08/2026",
            2, "m2"), {})
        grupo = agrupar_compras([primeira, segunda])[0]
        self.assertEqual(grupo["classificacao"], "ambiguo_multiplas_versoes")
        self.assertIsNone(grupo["versao_preferida"])
        self.assertFalse(grupo["confirmado"])
        self.assertEqual(grupo["situacao_revisao"], "escolher a versão correta")
        self.assertIn("peso total", grupo["campos_divergentes_humanos"])
        self.assertIn("valor total", grupo["campos_ausentes_humanos"])
        self.assertEqual(len(grupo["versoes_revisao"]), 2)

    def test_gta_exata_e_candidata_forte_mas_nao_confirmada(self):
        mensagens = [{
            "contexto": "Grupo", "mensagem_id": "m1", "data": "01.08.2026",
            "gtas": ["654321"],
        }]
        documentos = {"vinculos_nf_gta": [
            {"nf": "100", "gta": "654321", "linha_agronotas": 9},
            {"nf": "101", "gta": "999999", "linha_agronotas": 10},
        ]}
        resultado = cruzar_gtas(mensagens, documentos)
        self.assertEqual(resultado["vinculos_exatos"], 1)
        self.assertEqual(resultado["somente_documentos"], 1)
        self.assertEqual(resultado["classificacao"], "candidato_forte_documental_nao_confirmado")
        self.assertFalse(resultado["confirmado"])

    def test_teste_e_exemplo_nao_entram_nos_negocios_reais(self):
        compra_teste = extrair_compra(mensagem(
            "Compra – Teste Final\nQuantidade: 1 cabeça\nNegociação: 01/08/2026",
            1, "m1"), {})
        self.assertTrue(compra_teste["eh_teste"])
        exportacao = {
            "contexto": "Grupo", "arquivo": "messages.html", "arquivo_sha256": "a" * 64,
            "mensagens": [mensagem(compra_teste and "Compra – Teste Final\nQuantidade: 1 cabeça", 1, "m1")],
            "anexos": [], "anexos_omitidos": 0, "primeira_data": None, "ultima_data": None,
        }
        plano = gerar_plano([exportacao], {}, None, None)
        self.assertEqual(plano["resumo"]["blocos_compra_reais_deduplicados"], 0)

    def test_texto_igual_em_contextos_diferentes_permanece_como_evidencia(self):
        base = mensagem("Compra – Fornecedor\nQuantidade: 10 cabeças", 1, "m1")
        base["contexto"] = "Grupo A"
        outro = dict(base, contexto="Grupo B", mensagem_id="m2")
        exportacoes = []
        for contexto, item in (("Grupo A", base), ("Grupo B", outro)):
            exportacoes.append({
                "contexto": contexto, "arquivo": f"{contexto}.html",
                "arquivo_sha256": contexto * 8, "mensagens": [item], "anexos": [],
                "anexos_omitidos": 0, "primeira_data": None, "ultima_data": None,
            })
        plano = gerar_plano(exportacoes, {})
        self.assertEqual(plano["resumo"]["blocos_compra_reais_deduplicados"], 2)
        self.assertEqual(plano["resumo"]["grupos_compras"], 2)

    def test_cortes_sao_os_das_fontes_e_variacao_ima_nao_e_inferida(self):
        exportacao = {
            "contexto": "Grupo", "arquivo": "messages.html", "arquivo_sha256": "a" * 64,
            "mensagens": [], "anexos": [], "anexos_omitidos": 0,
            "primeira_data": None, "ultima_data": None,
        }
        documentos = {"fontes": {
            "agronotas": {"data_final": "2026-06-12"},
            "banco": {"data_final": "2026-07-24"},
            "ima": {"periodo_final": "2026-07-26", "saldo_rebanho": 253},
        }}
        plano = gerar_plano([exportacao], {}, documentos,
                            {"data": "2026-08-11", "saldo_rebanho": 263})
        self.assertEqual(plano["cortes"]["agronotas"], "2026-06-12")
        self.assertEqual(plano["cortes"]["banco"], "2026-07-24")
        self.assertEqual(plano["cortes"]["variacao_ima_sem_detalhamento"], 10)
        self.assertIn("saldo_ima_variou_sem_ficha_detalhada_correspondente", plano["pendencias"])
        self.assertEqual(plano["escritas_executadas"], 0)
        self.assertEqual(plano["tabelas_operacionais_alteradas"], 0)
        self.assertFalse(plano["plano_gera_escrita"])

    def test_plano_id_e_deterministico_e_nao_ha_opcao_executar(self):
        exportacao = {
            "contexto": "Grupo", "arquivo": "messages.html", "arquivo_sha256": "a" * 64,
            "mensagens": [], "anexos": [], "anexos_omitidos": 0,
            "primeira_data": None, "ultima_data": None,
        }
        primeiro = gerar_plano([exportacao], {})
        segundo = gerar_plano([exportacao], {})
        self.assertEqual(primeiro["plano_id"], segundo["plano_id"])
        fonte = Path(__file__).with_name("consolidar_historico_telegram.py").read_text()
        self.assertNotIn("--executar", fonte)
        self.assertNotIn("ConfinexClient", fonte)
        self.assertNotIn("requests.", fonte)

    def test_relatorio_traz_fila_humana_sem_combinar_versoes(self):
        primeira = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 10 cabeças\nNegociação: 01/08/2026",
            1, "m1"), {})
        segunda = extrair_compra(mensagem(
            "Compra – Fornecedor\nQuantidade: 12 cabeças\nNegociação: 01/08/2026",
            2, "m2"), {})
        exportacao = {
            "contexto": "Grupo de Teste", "arquivo": "messages.html",
            "arquivo_sha256": "a" * 64, "mensagens": [], "anexos": [],
            "anexos_omitidos": 0, "primeira_data": None, "ultima_data": None,
        }
        plano = gerar_plano([exportacao], {})
        plano["grupos_compras"] = agrupar_compras([primeira, segunda])
        plano["resumo"]["grupos_compras"] = 1
        plano["resumo"]["grupos_ambiguos"] = 1
        texto = relatorio_markdown(plano)
        self.assertIn("Fila privada de conferência por negócio", texto)
        self.assertIn("escolher a versão correta", texto)
        self.assertIn("não combina campos de versões diferentes", texto)
        self.assertNotIn("{\"", texto)


if __name__ == "__main__":
    unittest.main()
