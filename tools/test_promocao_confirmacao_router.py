import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from confinex_client import OperationalInsertResult
from promocao_confirmacao_router import parse_promote, route_confirmation

class FakeClient:
    def __init__(self):
        self.action={'id':'84bd463d-18d7-4b76-8fb4-4566afe59cc6','acao_tipo':'promover_revisao_operacional','status':'aguardando_confirmacao','entidade_tipo':'compras','entidade_codigo':'CF-TESTE','payload':{'source_draft_id':'draft-1','target_table':'compras','promovido_para_operacional':False,'dados_revisados':{'origem_conversa_id':'grupo-1','origem_mensagem_id':'msg-original'},'proposed_record':{'quantidade':'1','valor_total':'1','origem_registro':'teste'}}}
        self.ops=[]; self.audit=[]; self.updates=[]
    def select(self, table, **params): return [self.action] if table=='pending_actions' and params.get('id')=='eq.84bd463d-18d7-4b76-8fb4-4566afe59cc6' else []
    def insert_operational(self, table, payload, *, idempotency_key=None):
        self.ops.append((table,payload,idempotency_key))
        return OperationalInsertResult(status='inserted',record={'id':'op-1',**payload})
    def insert(self, table, payload): self.audit.append((table,payload)); return {'id':'ev-1',**payload}
    def update(self, table, filters, payload): self.updates.append((table,filters,payload)); return [payload]

class RouterTests(unittest.TestCase):
    def test_parse_promote(self):
        self.assertEqual(parse_promote('PROMOVER 84bd463d-18d7-4b76-8fb4-4566afe59cc6'), '84bd463d-18d7-4b76-8fb4-4566afe59cc6')
        self.assertIsNone(parse_promote('pode salvar'))
    def test_non_promotion_ignored(self):
        out=route_confirmation(FakeClient(), texto='pode salvar', grupo_id='grupo-1', mensagem_id='msg-2', usuario='pablo')
        self.assertFalse(out['handled'])
    def test_requires_message_id(self):
        with self.assertRaisesRegex(Exception, 'mensagem_id'):
            route_confirmation(FakeClient(), texto='PROMOVER 84bd463d-18d7-4b76-8fb4-4566afe59cc6', grupo_id='grupo-1', mensagem_id=None, usuario='pablo')
    def test_executes_controlled_path(self):
        client=FakeClient()
        out=route_confirmation(client, texto='PROMOVER 84bd463d-18d7-4b76-8fb4-4566afe59cc6', grupo_id='grupo-1', mensagem_id='msg-2', usuario='pablo')
        self.assertTrue(out['handled'])
        self.assertTrue(out['resultado']['executado'])
        self.assertEqual(client.ops[0][0], 'compras')

if __name__=='__main__': unittest.main()
