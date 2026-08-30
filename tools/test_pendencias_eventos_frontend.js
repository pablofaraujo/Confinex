'use strict';
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const raiz = path.resolve(__dirname, '..');
const pendenciasHtml = fs.readFileSync(path.join(raiz, 'pendencias.html'), 'utf8');
const pendenciasJs = fs.readFileSync(path.join(raiz, 'js/pendencias.js'), 'utf8');
const eventosHtml = fs.readFileSync(path.join(raiz, 'eventos.html'), 'utf8');
const eventosJs = fs.readFileSync(path.join(raiz, 'js/eventos.js'), 'utf8');
const confinadosHtml = fs.readFileSync(path.join(raiz, 'confinados.html'), 'utf8');
const confinamentoHtml = fs.readFileSync(path.join(raiz, 'confinamento.html'), 'utf8');
const bbHtml = fs.readFileSync(path.join(raiz, 'bb.html'), 'utf8');
const auditoriaBrowser = fs.readFileSync(path.join(raiz, 'tools/auditar_ecossistema_browser.js'), 'utf8');

for(const id of ['listaPendencias','filtroOrigem','filtroTexto','erroFontes']){
  assert.ok(pendenciasHtml.includes(`id="${id}"`), `Pendências sem #${id}`);
}
for(const [tabela, projection] of [
  ['operation_drafts','DRAFT_PENDENCIAS_COLUNAS'],
  ['pending_actions','ACAO_PENDENCIAS_COLUNAS'],
]){
  assert.ok(pendenciasJs.includes(`db.from('${tabela}').select(${projection})`), `projeção ausente: ${tabela}`);
}
assert.ok(pendenciasJs.includes("db.from('pendencias_documentos').select('*')"), 'consulta ausente: pendencias_documentos');
for(const coluna of ['investigacao_origem_id','promocao_origem_id','promocao_lease_token','promocao_fencing_token']){
  assert.ok(!pendenciasJs.match(new RegExp(`(?:DRAFT|ACAO)_PENDENCIAS_COLUNAS[^\\n]*${coluna}`)), `pendências não pode projetar ${coluna}`);
}
for(const tabela of ['operacoes','confinex_avaliacoes']){
  assert.ok(pendenciasJs.includes(`db.from('${tabela}').select(`), `consulta ausente: ${tabela}`);
}
assert.ok(pendenciasHtml.includes('Próxima etapa'));
assert.ok(pendenciasHtml.includes('<option>Planejamento</option>'));
assert.ok(pendenciasJs.includes('planejamentosRentabilidadePendentes'));
assert.ok(confinamentoHtml.includes('id="tbPlanejamento"'));
assert.ok(confinamentoHtml.includes('Planejamento de rentabilidade pendente'));
assert.ok(confinamentoHtml.includes('planejamentosRentabilidadePendentes(ops.data, avaliacoes.data||[])'));
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

assert.ok(pendenciasHtml.includes('cfagro-gestao.js?v=20260815-2'));
assert.ok(eventosHtml.includes('cfagro-gestao.js?v=20260814-1'));
assert.ok(confinamentoHtml.includes('cfagro-gestao.js?v=20260815-2'));
assert.ok(pendenciasHtml.includes('pendencias.js?v=20260815-2'));
assert.ok(eventosHtml.includes('eventos.js?v=20260803-1'));
assert.ok(pendenciasJs.includes("new Set(['realizado','rejeitado','cancelado'])"));
assert.ok(pendenciasJs.includes("new Set(['executado','rejeitado','cancelado','expirado'])"));
assert.ok(pendenciasJs.includes("new Set(['aguardando_vendedor','revisao_necessaria'])"));
for(const estadoFechado of ['recebido','dispensado']){
  const registros = [
    {status: estadoFechado, tipo: 'nf_entrada'},
    {status: 'aguardando_vendedor', tipo: 'gta'}
  ];
  const ativos = registros.filter(item => new Set(['aguardando_vendedor','revisao_necessaria']).has(String(item.status || '').toLowerCase()));
  assert.deepStrictEqual(ativos.map(item => item.tipo), ['gta'], `documento ${estadoFechado} não pode aparecer como pendência`);
}
for(const [pagina, html] of [['Confinados', confinadosHtml], ['Boi Balança', bbHtml]]){
  assert.ok(
    html.includes(".in('status',['aguardando_vendedor','revisao_necessaria'])"),
    `${pagina} deve consultar documentos ausentes ou em revisão`
  );
  assert.ok(
    html.includes("String(p.tipo||'').toLowerCase()==='gta'"),
    `${pagina} deve reconhecer GTA sem depender de maiúsculas/minúsculas`
  );
  assert.ok(
    html.includes('Negócio encerrado · documento pendente'),
    `${pagina} deve manter o alerta documental após o encerramento do negócio`
  );
  assert.ok(
    html.includes('Revisar vínculo documental'),
    `${pagina} deve distinguir revisão documental de documento ausente`
  );
}
assert.ok(auditoriaBrowser.includes('linhasRestauradas === 6'));
assert.ok(auditoriaBrowser.includes('estado.linhas === 5'));

console.log('Pendências e Eventos: 48 verificações estáticas aprovadas.');
