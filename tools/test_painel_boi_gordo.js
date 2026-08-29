const assert = require('assert');
const fs = require('fs');
const vm = require('vm');

const fonte = fs.readFileSync('js/painel-boi-gordo.js', 'utf8');
const html = fs.readFileSync('painel-boi-gordo.html', 'utf8');
const artefato = JSON.parse(fs.readFileSync('dados/painel-boi-gordo.json', 'utf8'));
assert.ok(artefato.fonte && artefato.atualizadoEm, 'artefato sem fonte/data');
assert.ok(html.includes('dados/painel-boi-gordo.json'));
assert.ok(html.includes('cache: \'no-store\''));
assert.ok(html.includes('js/painel-boi-gordo.js?v=20260829-1'), 'versão do script não invalida o cache anterior');
assert.ok(!html.includes('id="atualizarPainel"'), 'painel ainda repete a ação global Atualizar');
assert.ok(html.includes('atualizador.atualizar();'), 'painel deixou de atualizar automaticamente ao abrir');
assert.ok(html.includes('aplicar: renderizarPainel'), 'arquivo atualizado não redesenha o painel completo');
for (const alvo of ['cards', 'tbodyBGI', 'manchetes', 'contexto', 'chartBGI']) {
  assert.ok(html.includes(`getElementById('${alvo}')`), `renderização atualizada não cobre ${alvo}`);
}
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
assert.ok(api.resumoFontes({ atualizadoEmB3: '2026-08-28 16:29:00', referenciasAtualizadasEm: '2026-07-15 10:21:00' }).includes('demais referências'));
assert.ok(html.includes('Data da fonte'), 'curva não informa a data de cada cotação');
assert.ok(html.includes('sem base física atualizada'), 'ágio sem físico atual ainda parece calculado');
assert.ok(html.includes('Manchetes de referência'), 'manchetes antigas ainda parecem ser do dia');
assert.ok(html.includes('Sem manchetes atualizadas nesta fonte.'), 'bloco vazio de manchetes não foi explicado');
assert.ok(html.includes('Sem contexto atualizado nesta fonte.'), 'bloco vazio de contexto não foi explicado');

let chamadas = 0;
let aplicacoes = 0;
let defasagemAplicada = null;
const atualizador = api.criarAtualizador({
  buscar: () => { chamadas += 1; return new Promise(resolve => setTimeout(() => resolve(base), 1)); },
  aplicar: (_dados, _fallback, defasado) => { aplicacoes += 1; defasagemAplicada = defasado; }, fallback: base,
  agora: () => new Date('2026-07-24T12:00:00'), limiteDias: 2
});
Promise.all([atualizador.atualizar(), atualizador.atualizar()]).then(resultados => {
  assert.strictEqual(chamadas, 1, 'atualizações concorrentes não foram consolidadas');
  assert.strictEqual(aplicacoes, 1);
  assert.strictEqual(resultados[0].fonte, 'remota');
  assert.strictEqual(resultados[0].defasado, true, 'JSON remoto antigo foi tratado como atual');
  assert.strictEqual(defasagemAplicada, true, 'tela não recebeu o estado de defasagem');
  assert.ok(html.includes('dados defasados'), 'tela não informa dados defasados');
  console.log('Painel Boi Gordo: 24 verificações aprovadas.');
}).catch(error => { console.error(error); process.exitCode = 1; });
