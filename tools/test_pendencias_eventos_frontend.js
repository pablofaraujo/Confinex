'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const raiz = path.resolve(__dirname, '..');
const pendenciasHtml = fs.readFileSync(path.join(raiz, 'pendencias.html'), 'utf8');
const pendenciasJs = fs.readFileSync(path.join(raiz, 'js/pendencias.js'), 'utf8');
const eventosHtml = fs.readFileSync(path.join(raiz, 'eventos.html'), 'utf8');
const eventosJs = fs.readFileSync(path.join(raiz, 'js/eventos.js'), 'utf8');
const auditoriaBrowser = fs.readFileSync(path.join(raiz, 'tools/auditar_ecossistema_browser.js'), 'utf8');

for(const id of ['listaPendencias','filtroOrigem','filtroTexto','erroFontes']){
  assert.ok(pendenciasHtml.includes(`id="${id}"`), `Pendências sem #${id}`);
}
for(const tabela of ['operation_drafts','pending_actions','pendencias_documentos']){
  assert.ok(pendenciasJs.includes(`db.from('${tabela}').select('*')`), `consulta ausente: ${tabela}`);
}
assert.ok(pendenciasHtml.includes('Próxima etapa'));
assert.ok(pendenciasJs.includes('Os demais itens continuam disponíveis.'));
assert.ok(pendenciasJs.includes("href=\"'+esc(item.destino.href)"));
assert.ok(!/\.(insert|update|delete|upsert|rpc)\s*\(/.test(pendenciasJs));

for(const id of ['listaEventos','filtroSituacao','filtroTipo','filtroPeriodo','filtroTexto']){
  assert.ok(eventosHtml.includes(`id="${id}"`), `Eventos sem #${id}`);
}
assert.ok(eventosJs.includes("db.from('eventos').select('*')"));
assert.ok(eventosHtml.includes('Todo o histórico'));
assert.ok(eventosHtml.includes('Origem'));
assert.ok(eventosJs.includes("href=\"'+esc(item.origem.href)"));
assert.ok(!/\.(insert|update|delete|upsert|rpc)\s*\(/.test(eventosJs));

for(const html of [pendenciasHtml,eventosHtml]){
  assert.ok(html.includes('cfagro-gestao.js?v=20260724-6'));
}
assert.ok(pendenciasHtml.includes('pendencias.js?v=20260724-3'));
assert.ok(eventosHtml.includes('eventos.js?v=20260724-3'));
assert.ok(auditoriaBrowser.includes('linhasRestauradas === 4'));
assert.ok(auditoriaBrowser.includes('linhasRestauradas === 3'));

console.log('Pendências e Eventos: 27 verificações estáticas aprovadas.');
