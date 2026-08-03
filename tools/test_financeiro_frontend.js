'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const raiz = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(raiz, 'financeiro.html'), 'utf8');
const js = fs.readFileSync(path.join(raiz, 'js/financeiro.js'), 'utf8');
const componentes = fs.readFileSync(path.join(raiz, 'design/components.css'), 'utf8');

for(const id of ['kpis','obrigacoes','lembretes','dividas','transacoes','filtroSituacao','filtroTexto','filtroBanco','erroBanco']){
  assert.ok(html.includes(`id="${id}"`), `financeiro.html sem #${id}`);
}
for(const tabela of ['fluxo_caixa','emprestimos','promissorias','transacoes_banco']){
  assert.ok(js.includes(`db.from('${tabela}').select('*')`), `consulta ausente: ${tabela}`);
}
assert.ok(!/\.(insert|update|delete|upsert|rpc)\s*\(/.test(js), 'Financeiro deve permanecer somente leitura');
assert.ok(html.includes('nenhuma movimentação, baixa, parcela, renegociação ou conciliação é criada'));
assert.ok(html.includes('Preparado, mas não ativado'));
assert.ok(js.includes('As demais áreas continuam disponíveis.'));
assert.ok(!/\bgrupo_(?:id|origem_id)\b/.test(js), 'não exibir ID técnico de grupo');
assert.ok(html.includes('cfagro-gestao.js?v=20260723-3'));
assert.ok(html.includes('financeiro.js?v=20260803-1'));
assert.ok(html.includes('components.css?v=20260724-1'));
assert.ok(html.includes('class="kpis kpis-dinheiro"'));
assert.ok(componentes.includes('.kpis.kpis-dinheiro'));
assert.ok(componentes.includes('minmax(min(220px,100%),1fr)'));

console.log('Financeiro frontend: 24 verificações aprovadas.');
