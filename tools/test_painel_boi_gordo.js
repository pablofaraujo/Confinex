const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const fonte = fs.readFileSync('js/painel-boi-gordo.js', 'utf8');
const html = fs.readFileSync('painel-boi-gordo.html', 'utf8');
const artefato = JSON.parse(fs.readFileSync('dados/painel-boi-gordo.json', 'utf8'));
assert.ok(artefato.fonte && artefato.atualizadoEm, 'artefato sem fonte/data');
assert.ok(html.includes('dados/painel-boi-gordo.json'));
assert.ok(html.includes('cache: \'no-store\''));
assert.ok(html.includes('atualizarPainel'));
const contexto = { Promise, Date, Number, globalThis: {} };
vm.runInNewContext(fonte, contexto);
const api = contexto.globalThis.PainelBoiGordo;
assert.ok(api, 'API do painel ausente');
assert.strictEqual(api.normalizarDados(artefato), artefato);

const base = { atualizadoEm: '2026-07-20T10:00:00', fonte: 'teste', indicadores: [], curvaBGI: [] };
assert.strictEqual(api.normalizarDados(base), base);
assert.throws(() => api.normalizarDados({}), /data válida/);
assert.strictEqual(api.estaDefasado(base, new Date('2026-07-21T10:00:00'), 2), false);
assert.strictEqual(api.estaDefasado(base, new Date('2026-07-24T10:00:00'), 2), true);

let chamadas = 0;
let aplicacoes = 0;
const atualizador = api.criarAtualizador({
  buscar: () => { chamadas += 1; return new Promise(resolve => setTimeout(() => resolve(base), 1)); },
  aplicar: () => { aplicacoes += 1; }, fallback: base,
  agora: () => new Date('2026-07-20T12:00:00'), limiteDias: 2
});
Promise.all([atualizador.atualizar(), atualizador.atualizar()]).then(resultados => {
  assert.strictEqual(chamadas, 1, 'atualizações concorrentes não foram consolidadas');
  assert.strictEqual(aplicacoes, 1);
  assert.strictEqual(resultados[0].fonte, 'remota');
  console.log('Painel Boi Gordo: 9 verificações aprovadas.');
}).catch(error => { console.error(error); process.exitCode = 1; });

