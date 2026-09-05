import subprocess
import unittest
from pathlib import Path

from patch_continuidade_juan import preparar, ANTES, DEPOIS, IMPORTACAO
from recuperar_contexto_juan import extrair, _envio, texto_prompt


class PatchContinuidadeTest(unittest.TestCase):
    def test_patch_minimo_idempotente_sem_modificar_body_atual(self):
        fonte = ('async function receber() { const rawBody = mensagemAtual; '
                 'const ctxPayload = await construir({agent: route.agentId, '
                 'session: route.sessionKey, rawBody, ' + ANTES + '}); }')
        novo = preparar(fonte)
        self.assertEqual(novo.count(DEPOIS), 1)
        self.assertEqual(novo.replace(IMPORTACAO, '').replace(DEPOIS, ANTES), fonte)
        self.assertEqual(preparar(novo), novo)
        self.assertIn('rawBody = mensagemAtual', novo)

    def test_runtime_desconhecido_ou_duplicado_bloqueia(self):
        for fonte in ('codigo qualquer', ANTES, ANTES + ANTES, 'confinex-continuidade-v1'):
            with self.assertRaises(ValueError):
                preparar(fonte)

    def test_node_integra_contexto_antes_do_modelo(self):
        raiz = Path(__file__).resolve().parents[1]
        for arquivo in ('continuidade_juan.mjs', 'test_continuidade_juan.mjs'):
            subprocess.run(['node', '--check', str(raiz / 'tools' / arquivo)], check=True, capture_output=True)
        subprocess.run(['node', str(raiz / 'tools/test_continuidade_juan.mjs')],
                       check=True, capture_output=True, timeout=20)

    def test_destino_topico_divergente_nao_entra(self):
        a = {'action': 'send', 'chatId': 'telegram:-999001', 'threadId': 8, 'message': 'texto indevido'}
        self.assertEqual(_envio(a, '-999001', '7'), '')
        self.assertEqual(_envio(a, '-999001', '8'), 'texto indevido')
        self.assertEqual(_envio(a, '-999001'), '')

    def test_estrutura_invalida_nao_derruba_extrator(self):
        for evento in (None, [], {'data': []}, {'message': []}, {'message': {'content': ['x'], 'role': 'user'}}):
            self.assertEqual(list(extrair(evento, False, '-999001')), [])

    def test_envelope_nao_e_reindexado_como_mensagem_nova(self):
        p = ('Group chat history context (untrusted, chronological, selected for current message):\n'
             '#1 anterior: compra fictícia\n\nInclua comissão')
        self.assertEqual(texto_prompt(p), 'Inclua comissão')
        e = {'message': {'role': 'user', 'content': p}, 'timestamp': '2026-09-01T00:00:00Z'}
        self.assertEqual(list(extrair(e, False, '-999001'))[0][1], 'Inclua comissão')


if __name__ == '__main__':
    unittest.main()
