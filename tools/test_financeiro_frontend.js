'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const raiz = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(raiz, 'financeiro.html'), 'utf8');
const js = fs.readFileSync(path.join(raiz, 'js/financeiro.js'), 'utf8');
const componentes = fs.readFileSync(path.join(raiz, 'design/components.css'), 'utf8');

for(const id of ['kpis','obrigacoes','lembretes','dividas','transacoes','conciliacoesPendentes','filtroSituacao','filtroTexto','filtroBanco','erroBanco','erroConciliacoes','mensagemConciliacoes']){
  assert.ok(html.includes(`id="${id}"`), `financeiro.html sem #${id}`);
}
for(const tabela of ['fluxo_caixa','emprestimos','promissorias','transacoes_banco','conciliacoes_candidatas','transacoes_banco_staging','negocios_candidatos']){
  assert.ok(js.includes(`db.from('${tabela}').select('*')`), `consulta ausente: ${tabela}`);
}
assert.ok(!/\.(insert|update|delete|upsert)\s*\(/.test(js), 'Financeiro não pode escrever diretamente em tabelas');
assert.strictEqual((js.match(/db\.rpc\(/g) || []).length, 1, 'somente uma RPC controlada');
assert.ok(js.includes("db.rpc('decidir_conciliacao_candidata'"));
assert.ok(html.includes('Confirmar aceita a relação no histórico de consolidação'));
assert.ok(html.includes('não quita, movimenta caixa nem cria lançamento operacional'));
assert.ok(js.includes('Informe o motivo antes de confirmar ou rejeitar.'));
assert.ok(js.includes('Nenhum lançamento, pagamento ou negócio operacional será alterado.'));
assert.ok(html.includes('Preparado, mas não ativado'));
assert.ok(js.includes('As demais áreas continuam disponíveis.'));
assert.ok(!/\bgrupo_(?:id|origem_id)\b/.test(js), 'não exibir ID técnico de grupo');
assert.ok(html.includes('cfagro-gestao.js?v=20260822-2'));
assert.ok(html.includes('financeiro.js?v=20260822-2'));
assert.ok(html.includes('components.css?v=20260815-1'));
assert.ok(html.includes('class="kpis kpis-dinheiro"'));
assert.ok(componentes.includes('.kpis.kpis-dinheiro'));
assert.ok(componentes.includes('minmax(min(220px,100%),1fr)'));

console.log('Financeiro frontend: 31 verificações aprovadas.');
