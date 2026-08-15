const assert = require('assert');
const fs = require('fs');
const path = require('path');
const {
  agruparPorConfinamento,
  aplicarInventarios,
  filtrarCoberturasAtivas,
  filtrarExposicaoAtiva,
  filtrarPendenciasConfinamento,
  resumirLotes,
} = require('../js/confinados-resumo.js');

const operacoes = [
  { id:'csap', codigo:'CF-26-009', status:'em_confinamento', tipo_negocio:'confinamento', sexo:'Femea', confinamento_id:'c1', confinamentos:{id:'c1',nome:'CSAP'} },
  { id:'guto', codigo:'CF-26-013', status:'comprada', tipo_negocio:'confinamento', sexo:'Boi', confinamento_id:'c2', confinamentos:{id:'c2',nome:'GUTO'} },
  { id:'bb', codigo:'BB2607-01', status:'comprada', tipo_negocio:'boi_balanca', sexo:'Boi', confinamento_id:null, confinamentos:null },
  { id:'encerrada', codigo:'CF-26-001', status:'abatida', tipo_negocio:'confinamento', sexo:'Femea', confinamento_id:'c3', confinamentos:{id:'c3',nome:'AGRORIBAS'} },
];
const lotes = resumirLotes(
  operacoes,
  [{operacao_id:'csap',cabecas:424,data_entrada:'2026-04-27',curral:'D-25'}],
  [{operacao_id:'csap',quantidade:424,data:'2026-04-27'},{operacao_id:'guto',quantidade:75,data:'2026-07-06'}],
  [{operacao_id:'csap',quantidade:141}],
);
assert.deepStrictEqual(lotes.map(l=>[l.codigo,l.cabecas_atuais,l.fonte_quantidade]), [
  ['CF-26-009',283,'entradas'],
  ['CF-26-013',75,'compras'],
]);
assert.deepStrictEqual(agruparPorConfinamento(lotes).map(g=>[g.nome,g.cabecas,g.machos,g.femeas,g.lotes_sem_entrada]), [
  ['CSAP',283,0,283,0],
  ['GUTO',75,75,0,1],
]);
const comInventario = aplicarInventarios(agruparPorConfinamento(lotes), [
  {confinamento_id:'c1',data_referencia:'2026-08-15',cabecas_total:285,machos:null,femeas:null,fonte:'contagem'},
  {confinamento_id:'c3',data_referencia:'2026-08-15',cabecas_total:105,machos:null,femeas:null,fonte:'contagem'},
], [{id:'c1',nome:'CSAP'},{id:'c2',nome:'GUTO'},{id:'c3',nome:'CLAUDIO ADRIANO'}]);
assert.deepStrictEqual(comInventario.map(g=>[g.nome,g.cabecas,g.machos,g.femeas,g.nao_informado,g.diferenca_ledger]), [
  ['CSAP',285,0,283,2,2],
  ['GUTO',75,75,0,0,undefined],
  ['CLAUDIO ADRIANO',105,0,0,105,105],
]);
assert.deepStrictEqual(filtrarExposicaoAtiva([
  {operacao_id:'csap',codigo:'CF-26-009'},
  {operacao_id:'bb',codigo:'BB2607-01'},
  {operacao_id:'encerrada',codigo:'CF-26-001'},
], operacoes).map(e=>e.codigo), ['CF-26-009']);
assert.deepStrictEqual(filtrarPendenciasConfinamento([
  {id:'p1',operacoes:operacoes[0]},
  {id:'p2',operacoes:operacoes[2]},
]).map(p=>p.id), ['p1']);
assert.deepStrictEqual(filtrarCoberturasAtivas([
  {status:'aberta',categoria:'hedge',negocio_rateio:'CF-26-009 5,2 cts'},
  {status:'encerrada',categoria:'hedge',negocio_rateio:'CF-26-009'},
  {status:'aberta',categoria:'especulacao',negocio_rateio:'CF-26-009'},
  {status:'aberta',categoria:'hedge',negocio_rateio:'BB2607-01'},
], operacoes).length, 1);

const htmlResumo = fs.readFileSync(path.join(__dirname,'..','confinados.html'),'utf8');
const htmlGeral = fs.readFileSync(path.join(__dirname,'..','index.html'),'utf8');
assert.ok(htmlResumo.includes("ConfinadosResumo.filtrarPendenciasConfinamento(pend.data)"));
assert.ok(htmlResumo.includes("ConfinadosResumo.filtrarExposicaoAtiva(exposicaoEfetiva,ops.data)"));
assert.ok(htmlResumo.includes("db.from('abates').select('operacao_id,quantidade,data_abate')"));
assert.ok(htmlResumo.includes('Posições abertas vinculadas aos confinados'));
assert.ok(!htmlResumo.includes('Posições na B3 — hedge × especulação'));
assert.ok(!htmlResumo.includes('Creditado (realizado)'));
assert.ok(htmlResumo.includes("db.from('ressarcimentos_operacionais')"));
assert.ok(htmlGeral.includes('Machos / fêmeas confinados'));

console.log('Resumo de confinados: 19 verificações aprovadas.');
