import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { mkdtemp, mkdir, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { join } from 'node:path';
import { tmpdir } from 'node:os';
import {
  ProvaRecusada,
  comandoConsulta,
  criarConsultaProtegida,
  executarProvaModelo,
} from './prova_modelo_continuidade.mjs';

const CHAVE = 'agent:juan:telegram:group:-700001';
const OUTRO_GRUPO = 'agent:juan:telegram:group:-700002';

const sha256 = (valor) => createHash('sha256').update(valor).digest('hex');

async function fixture() {
  const raiz = await mkdtemp(join(tmpdir(), 'prova-modelo-continuidad-'));
  const tools = join(raiz, 'tools');
  await mkdir(tools);
  const arquivos = {
    script: join(tools, 'consultar_continuidade_juan.py'),
    recuperar: join(tools, 'recuperar_contexto_juan.py'),
    ponte: join(raiz, 'ponte-real-ficticia.py'),
  };
  await writeFile(arquivos.recuperar, 'fixture recuperação sintética\n');
  await writeFile(arquivos.ponte, 'fixture ponte sintética\n');
  await writeFile(arquivos.script, `from pathlib import Path\nPONTE = Path('${arquivos.ponte}')\n# fixture sintética\n`);
  const hashes = {};
  for (const arquivo of Object.values(arquivos)) hashes[arquivo] = sha256(await readFile(arquivo));
  return { raiz, tools, ...arquivos, hashes };
}

function respostaLeitor(overrides = {}) {
  return JSON.stringify({
    escritas: 0, autoriza_escrita: false, candidatos: [], consultas: [
      { tabela: 'operation_drafts', status: 'ok' },
      { tabela: 'pending_actions', status: 'ok' },
    ],
    cobertura: { parcial: false, motivos: [] }, ...overrides,
  });
}

function consultaValida(fx, executar = () => ({ status: 0, stdout: respostaLeitor() })) {
  return criarConsultaProtegida({ script: fx.script, chave: CHAVE, hashes: fx.hashes, executar });
}

test('comandoConsulta só monta o comando canônico e recusa identidade/caminho inválidos', () => {
  assert.match(comandoConsulta('/tmp/fixture.py', CHAVE), /--entrada-stdin$/);
  for (const [script, chave] of [
    ['/tmp/fixture.py; touch /tmp/x', CHAVE],
    ['/tmp/fixture.py', OUTRO_GRUPO + ':extra'],
    ['relativo.py', CHAVE],
  ]) assert.throws(() => comandoConsulta(script, chave), ProvaRecusada);
});

test('consulta protegida rejeita exec arbitrário, escrita, mensagem, browser, agents, env, args e suffix shell', async () => {
  const fx = await fixture();
  try {
    const consulta = consultaValida(fx);
    const comandos = [
      { name: 'exec', arguments: { command: 'rm -rf /' } },
      { name: 'exec', arguments: { command: `${consulta.comando} && echo extra` } },
      { name: 'exec_command', arguments: { cmd: consulta.comando, env: { TOKEN: 'falso' } } },
      { name: 'message', arguments: { message: consulta.comando } },
      { name: 'browser', arguments: { url: 'https://example.invalid' } },
      { name: 'agents', arguments: { command: consulta.comando } },
      { name: 'exec', arguments: { command: consulta.comando, timeout: 1 } },
      { name: 'exec', arguments: { command: consulta.comando, args: [] } },
    ];
    for (const chamada of comandos) assert.throws(() => consulta.validar(chamada), ProvaRecusada);
  } finally { await rm(fx.raiz, { recursive: true, force: true }); }
});

test('consulta exige chave do grupo exata, rejeita symlink e hash alterado', async () => {
  const fx = await fixture();
  try {
    const consulta = criarConsultaProtegida({ script: fx.script, chave: CHAVE, hashes: fx.hashes });
    assert.throws(() => consulta.validar({ name: "exec", arguments: { command: comandoConsulta(fx.script, OUTRO_GRUPO) } }), ProvaRecusada);
    const link = fx.ponte;
    await rm(link);
    await symlink(fx.recuperar, link);
    assert.throws(() => criarConsultaProtegida({ script: fx.script, chave: CHAVE, hashes: fx.hashes }), ProvaRecusada);
    const fx2 = await fixture();
    try {
      await writeFile(fx2.script, 'arquivo alterado\n');
      assert.throws(() => criarConsultaProtegida({ script: fx2.script, chave: CHAVE, hashes: fx2.hashes }), ProvaRecusada);
    } finally { await rm(fx2.raiz, { recursive: true, force: true }); }
    const fx3 = await fixture();
    try {
      fx3.hashes[fx3.ponte] = '0'.repeat(64);
      assert.throws(() => criarConsultaProtegida({ script: fx3.script, chave: CHAVE, hashes: fx3.hashes }), ProvaRecusada);
    } finally { await rm(fx3.raiz, { recursive: true, force: true }); }
  } finally { await rm(fx.raiz, { recursive: true, force: true }); }
});

test('consulta rejeita JSON inválido, status parcial e não faz retry após timeout', async () => {
  const fx = await fixture();
  try {
    let execucoes = 0;
    const consulta = consultaValida(fx, () => { execucoes += 1; return { status: 0, stdout: '{invalido' }; });
    const chamada = { name: 'exec', arguments: { command: consulta.comando } };
    assert.throws(() => consulta.consultar(chamada), ProvaRecusada);
    assert.equal(execucoes, 1);
    assert.throws(() => consulta.consultar(chamada), ProvaRecusada);
    assert.equal(execucoes, 1);
  } finally { await rm(fx.raiz, { recursive: true, force: true }); }
});

test('consulta sem cada tabela obrigatória é recusada como não confirmada', async () => {
  const fx = await fixture();
  try {
    for (const consultas of [
      [{ tabela: 'pending_actions', status: 'ok' }],
      [{ tabela: 'operation_drafts', status: 'ok' }],
      [],
    ]) {
      const consulta = consultaValida(fx, () => ({ status: 0, stdout: respostaLeitor({ consultas }) }));
      assert.throws(() => consulta.consultar({ name: 'exec', arguments: { command: consulta.comando } }), ProvaRecusada);
    }
  } finally { await rm(fx.raiz, { recursive: true, force: true }); }
});

test('lote misto de leitura e escrita falha antes de executar qualquer chamada', async () => {
  const fx = await fixture();
  try {
    let execucoes = 0;
    const consulta = consultaValida(fx, () => { execucoes += 1; return { status: 0, stdout: respostaLeitor() }; });
    const contexto = { messages: [], tools: [{ name: 'exec' }] };
    const inferir = async () => ({ role: 'assistant', stopReason: 'toolUse', content: [
      { type: 'toolCall', id: 'leitura', name: 'exec', arguments: { command: consulta.comando } },
      { type: 'toolCall', id: 'escrita', name: 'message', arguments: { message: 'salvar' } },
    ] });
    await assert.rejects(executarProvaModelo({ contexto, inferir, consulta }), ProvaRecusada);
    assert.equal(execucoes, 0);
  } finally { await rm(fx.raiz, { recursive: true, force: true }); }
});

test('prova bem-sucedida consulta uma vez e para com resposta textual', async () => {
  const fx = await fixture();
  try {
    let execucoes = 0;
    const consulta = consultaValida(fx, () => { execucoes += 1; return { status: 0, stdout: respostaLeitor() }; });
    let turno = 0;
    const contexto = { messages: [], tools: [{ name: 'exec' }] };
    const inferir = async entrada => {
      if (turno++ === 0) {
        assert.deepEqual(entrada.tools, contexto.tools);
        return { role: 'assistant', stopReason: 'toolUse', content: [{ type: 'toolCall', id: 'consulta-1', name: 'exec', arguments: { command: consulta.comando } }] };
      }
      assert.deepEqual(entrada.tools, []);
      assert.equal(entrada.messages.at(-1).role, 'toolResult');
      return { role: 'assistant', stopReason: 'stop', content: [{ type: 'text', text: 'Consulta concluída para conferência.' }] };
    };
    const resultado = await executarProvaModelo({ contexto, inferir, consulta });
    assert.deepEqual(contexto.tools, [{ name: 'exec' }]);
    assert.equal(execucoes, 1);
    assert.equal(resultado.escritas, 0);
    assert.equal(resultado.entregas, 0);
    assert.equal(resultado.resposta, 'Consulta concluída para conferência.');
  } finally { await rm(fx.raiz, { recursive: true, force: true }); }
});

test('modelo sem consulta, sem texto, limite de turnos e timeout são recusados', async () => {
  const fx = await fixture();
  try {
    const casos = [
      [async () => ({ role: 'assistant', stopReason: 'stop', content: [{ type: 'text', text: 'não consultei' }] }), 'modelo_nao_consultou'],
      [async () => ({ role: 'assistant', stopReason: 'stop', content: [{ type: 'text', text: '   ' }] }), 'modelo_nao_consultou'],
      [async () => ({ role: 'assistant', stopReason: 'toolUse', content: [{ type: 'toolCall', id: 'um', name: 'exec', arguments: { command: 'x' } }] }), 'capacidade_nao_permitida'],
    ];
    for (const [inferir, motivo] of casos) {
      const consulta = consultaValida(fx);
      await assert.rejects(executarProvaModelo({ contexto: { messages: [], tools: [] }, inferir, consulta }), new RegExp(motivo));
    }
    const consulta = consultaValida(fx);
    await assert.rejects(executarProvaModelo({ contexto: { messages: [], tools: [] }, timeoutMs: 1, inferir: () => new Promise(() => {}), consulta }), /modelo_limite_de_tempo/);
  } finally { await rm(fx.raiz, { recursive: true, force: true }); }
});

test('configuração, chamada duplicada e resposta incompleta não escapam da prova', async () => {
  const fx = await fixture();
  try {
    const consulta = consultaValida(fx);
    await assert.rejects(executarProvaModelo({ contexto: { messages: [], tools: [] }, inferir: async () => ({}), consulta, timeoutMs: 0 }), /prova_configuracao_invalida/);
    let turno = 0;
    const inferir = async () => turno++ === 0
      ? { role: 'assistant', stopReason: 'toolUse', content: [{ type: 'toolCall', id: 'x', name: 'exec', arguments: { command: consulta.comando } }] }
      : { role: 'assistant', stopReason: 'toolUse', content: [{ type: 'toolCall', id: 'x', name: 'exec', arguments: { command: consulta.comando } }] };
    await assert.rejects(executarProvaModelo({ contexto: { messages: [], tools: [] }, inferir, consulta }), /excesso_de_ferramentas/);
  } finally { await rm(fx.raiz, { recursive: true, force: true }); }
});
