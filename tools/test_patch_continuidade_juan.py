import subprocess
import tempfile
import unittest
import hashlib
import json
from pathlib import Path

from patch_continuidade_juan import (
    preparar, ANTES, DEPOIS, DEPOIS_V1, IMPORTACAO, MARCADOR_V1, MARCADOR_V2,
)
from recuperar_contexto_juan import extrair, _envio, texto_prompt


class PatchContinuidadeTest(unittest.TestCase):
    def test_patch_minimo_idempotente_sem_modificar_body_atual(self):
        fonte = self.fonte_original()
        novo = preparar(fonte)
        self.assertEqual(novo.count(DEPOIS), 1)
        self.assertEqual(novo.replace(IMPORTACAO, '').replace(DEPOIS, ANTES), fonte)
        self.assertEqual(preparar(novo), novo)
        self.assertIn('rawBody = mensagemAtual', novo)
        self.assertIn('senderId', novo)
        self.assertIn('chatId: String(chatId)', novo)
        self.assertIn('threadSpec.id != null', novo)
        self.assertIn(MARCADOR_V2, novo)

    @staticmethod
    def fonte_original():
        return ('async function receber() { const rawBody = mensagemAtual; '
                'const senderId = msg.from.id; const chatId = msg.chat.id; '
                'const conversationKind = peer.kind; const threadSpec = topico; '
                'const topic = threadSpec.id; '
                'const account = route.accountId; const main = route.mainSessionKey; '
                'const ctxPayload = await construir({agent: route.agentId, '
                'session: route.sessionKey, rawBody, ' + ANTES + '}); }')

    def test_patch_migra_v1_para_v2_sem_duplicar_hook_ou_import(self):
        original = self.fonte_original()
        v1 = (f'import {{ enriquecerContextoJuan }} from "./continuidade_juan.mjs"; {MARCADOR_V1}\n'
              + original.replace(ANTES, DEPOIS_V1, 1))
        v2 = preparar(v1)
        self.assertNotIn(DEPOIS_V1, v2)
        self.assertEqual(v2.count(DEPOIS), 1)
        self.assertEqual(v2.count(MARCADOR_V2), 1)
        self.assertNotIn(MARCADOR_V1, v2)
        self.assertEqual(preparar(v2), v2)

    def test_runtime_desconhecido_ou_duplicado_bloqueia(self):
        for fonte in ('codigo qualquer', ANTES, ANTES + ANTES, MARCADOR_V1, MARCADOR_V2):
            with self.assertRaises(ValueError):
                preparar(fonte)

    def test_patch_recusa_envelope_sem_cada_variavel_autenticada(self):
        fonte = self.fonte_original()
        for variavel in (
            'senderId', 'chatId', 'conversationKind', 'threadSpec.id',
            'route.accountId', 'route.mainSessionKey',
        ):
            with self.subTest(variavel=variavel):
                with self.assertRaisesRegex(ValueError, 'ponto de entrada desconhecido'):
                    preparar(fonte.replace(variavel, 'ausente', 1))

    def test_v2_duplicado_parcial_ou_import_divergente_falha_fechado(self):
        v2 = preparar(self.fonte_original())
        casos = (
            v2 + IMPORTACAO,
            v2 + DEPOIS,
            v2.replace(IMPORTACAO, 'import { enriquecerContextoJuan } from "outro"; '
                       + MARCADOR_V2 + '\n'),
            v2.replace(DEPOIS, ANTES),
        )
        for fonte in casos:
            with self.subTest(tamanho=len(fonte)):
                with self.assertRaisesRegex(ValueError, 'runtime divergente'):
                    preparar(fonte)

    def test_cli_recusa_sha_divergente_sem_emitir_proposta(self):
        with tempfile.TemporaryDirectory() as pasta:
            runtime = Path(pasta) / 'runtime.js'
            runtime.write_text(self.fonte_original(), encoding='utf-8')
            resultado = subprocess.run([
                'python3', str(Path(__file__).with_name('patch_continuidade_juan.py')),
                str(runtime), '--sha256-esperado', '0' * 64,
            ], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(resultado.returncode, 0)
        self.assertEqual(resultado.stdout, '')
        self.assertIn('SHA-256 não confere', resultado.stderr)
        self.assertNotIn(str(runtime), resultado.stderr)

    def test_cli_confere_bytes_crlf_e_recusa_utf8_invalido(self):
        script = str(Path(__file__).with_name('patch_continuidade_juan.py'))
        with tempfile.TemporaryDirectory() as pasta:
            runtime = Path(pasta) / 'runtime.js'
            conteudo = self.fonte_original().replace('; ', ';\r\n').encode('utf-8')
            runtime.write_bytes(conteudo)
            sha = hashlib.sha256(conteudo).hexdigest()
            resultado = subprocess.run([
                'python3', script, str(runtime), '--sha256-esperado', sha,
            ], capture_output=True, text=True, timeout=10)
            self.assertEqual(resultado.returncode, 0, resultado.stderr)
            self.assertEqual(json.loads(resultado.stdout)['sha256_antes'], sha)

            invalido = b'\xff\xfe\x80'
            runtime.write_bytes(invalido)
            resultado = subprocess.run([
                'python3', script, str(runtime), '--sha256-esperado',
                hashlib.sha256(invalido).hexdigest(),
            ], capture_output=True, text=True, timeout=10)
        self.assertNotEqual(resultado.returncode, 0)
        self.assertEqual(resultado.stdout, '')
        self.assertIn('conteúdo não é UTF-8', resultado.stderr)
        self.assertNotIn(str(runtime), resultado.stderr)

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
