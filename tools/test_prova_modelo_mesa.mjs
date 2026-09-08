import assert from 'node:assert/strict';
import {
  CLASSIFICACAO,
  executarProvaModeloMesa,
  ProvaMesaRecusada,
} from './prova_modelo_mesa.mjs';

const trechos = [{
  texto_ref: 'txt_0123456789abcdef01234567',
  timestamp: '2026-09-08T10:15:00Z',
  origem_autoria: 'interlocutor',
  texto: 'Exemplo fictício de conversa escrita para conferência.',
}];
const respostaOk = {
  role: 'assistant', stopReason: 'stop',
  content: [{ type: 'text', text: 'Há uma pista textual, ainda ambígua. Nenhuma atualização foi realizada.' }],
};

async function executar(alteracoes = {}) {
  let inferencias = 0;
  let revisoes = 0;
  return executarProvaModeloMesa({
    provider: 'openai', model: 'gpt-5.5', pedido: 'Revise a conversa fictícia.', trechos,
    inferir: async entrada => {
      inferencias++;
      assert.equal(inferencias, 1);
      assert.deepEqual(entrada.tools, []);
      assert.equal(entrada.tool_choice, 'none');
      assert.equal(entrada.provider, 'openai');
      assert.equal(entrada.model, 'gpt-5.5');
      assert.deepEqual(entrada.messages.map(item => item.role), ['system', 'user']);
      return respostaOk;
    },
    recebimentoPrivado: async privado => {
      revisoes++;
      assert.equal(revisoes, 1);
      assert.equal(privado.classificacao, CLASSIFICACAO);
      assert.deepEqual(privado.trechos, trechos);
      return { recebido: true };
    },
    ...alteracoes,
  });
}

const ok = await executar();
assert.equal(ok.classificacao, 'A_CONFERIR');
assert.equal(ok.privado.resposta, respostaOk.content[0].text);
assert.equal(ok.resumo_sanitizado.ferramentas_oferecidas, 0);
assert.equal(ok.resumo_sanitizado.chamadas_ferramenta, 0);
assert.equal(ok.resumo_sanitizado.escritas_operacionais, 0);
assert.equal(ok.resumo_sanitizado.mensagens_externas, 0);
assert.ok(!JSON.stringify(ok.resumo_sanitizado).includes(trechos[0].texto));

const comThinking = await executar({ inferir: async () => ({
  role: 'assistant', stopReason: 'stop',
  content: [{ type: 'thinking', thinking: 'não persistir' }, ...respostaOk.content],
}) });
assert.equal(comThinking.privado.resposta, respostaOk.content[0].text);
assert.ok(!JSON.stringify(comThinking).includes('não persistir'));

for (const resposta of [
  { role: 'assistant', stopReason: 'toolUse', content: [{ type: 'toolCall', id: 't1' }] },
  { role: 'assistant', stopReason: 'stop', content: [
    { type: 'text', text: 'Nenhuma atualização foi realizada.' }, { type: 'toolCall', id: 't1' },
  ] },
]) {
  await assert.rejects(executar({ inferir: async () => resposta }),
    erro => erro instanceof ProvaMesaRecusada && erro.codigo === 'modelo_tentou_ferramenta');
}

for (const resposta of [
  null,
  { role: 'assistant', stopReason: 'length', content: [{ type: 'text', text: 'Incompleta' }] },
  { role: 'assistant', stopReason: 'stop', content: [] },
  { role: 'assistant', stopReason: 'stop', content: [{ type: 'text', text: '' }] },
  { role: 'assistant', stopReason: 'stop', content: [{ type: 'desconhecido', text: 'x' }] },
]) {
  await assert.rejects(executar({ inferir: async () => resposta }), ProvaMesaRecusada);
}

for (const [provider, model] of [['outro', 'gpt-5.5'], ['openai', 'outro']]) {
  await assert.rejects(executar({ provider, model }),
    erro => erro.codigo === 'modelo_ou_provedor_invalido');
}

for (const alteracoes of [
  { pedido: '' }, { trechos: [] }, { timeoutMs: 90_001 }, { maxTokens: 0 },
  { trechos: [{ ...trechos[0], extra: 'não permitido' }] },
  { trechos: [{ ...trechos[0], origem_autoria: 'suposta' }] },
  { tools: [{ name: 'exec' }] },
]) {
  await assert.rejects(executar(alteracoes), ProvaMesaRecusada);
}

await assert.rejects(executar({ inferir: async () => { throw Error('segredo-ficticio'); } }),
  erro => erro.codigo === 'modelo_indisponivel' && !erro.message.includes('segredo-ficticio'));
await assert.rejects(executar({
  inferir: async () => { throw new ProvaMesaRecusada('segredo-tipado-ficticio'); },
}), erro => erro.codigo === 'modelo_indisponivel' && !erro.message.includes('segredo-tipado-ficticio'));

let abortado = false;
await assert.rejects(executar({
  timeoutMs: 5,
  inferir: (_entrada, { signal }) => new Promise(resolve => {
    signal.addEventListener('abort', () => { abortado = true; resolve(respostaOk); });
  }),
}), erro => erro.codigo === 'modelo_limite_de_tempo');
assert.equal(abortado, true);

for (const texto of [
  'Há uma pista a conferir.',
  'Eu atualizei o portfólio. Nenhuma atualização foi realizada.',
  'O portfólio foi atualizado. Nenhuma atualização foi realizada.',
]) {
  await assert.rejects(executar({ inferir: async () => ({
    role: 'assistant', stopReason: 'stop', content: [{ type: 'text', text: texto }],
  }) }), ProvaMesaRecusada);
}

await assert.rejects(executar({ inferir: async () => ({
  role: 'assistant', stopReason: 'stop',
  content: [{ type: 'text', text: `Nenhuma atualização foi realizada. ${'x'.repeat(16_000)}` }],
}) }), erro => erro.codigo === 'modelo_resposta_acima_do_limite');

await assert.rejects(executar({ recebimentoPrivado: async () => false }),
  erro => erro.codigo === 'recebimento_privado_nao_confirmado');
await assert.rejects(executar({
  recebimentoPrivado: async () => { throw new ProvaMesaRecusada('nota-privada'); },
}), erro => erro.codigo === 'recebimento_privado_indisponivel' && !erro.message.includes('nota-privada'));

console.log('Prova modelo mesa: completion única, sem ferramentas e A_CONFERIR aprovados.');
