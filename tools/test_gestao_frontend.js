'use strict';
const assert = require('assert');
const gestao = require('../js/cfagro-gestao.js');

// Positivo: agrega caixa e apresenta estados operacionais em linguagem humana.
assert.deepStrictEqual(
  gestao.resumoFinanceiro([
    {tipo:'entrada', valor:1000, realizado:true},
    {tipo:'saida', valor:250, realizado:true},
    {tipo:'entrada', valor:400, realizado:false},
    {tipo:'saida', valor:150, realizado:false}
  ]),
  {previsto:1000, realizado:750, aReceber:400, aPagar:150, quantidade:4}
);
assert.strictEqual(gestao.statusHumano('aguardando_confirmacao'), 'Aguardando confirmação');
assert.strictEqual(
  gestao.pendenciasLegiveis([{tipo_operacao:'compra_confinamento',status:'pendente'}], [], [])[0].resumo,
  'Compra confinamento'
);

// Negativo: valores inválidos não contaminam totais e IDs técnicos não viram contexto.
assert.deepStrictEqual(
  gestao.resumoFinanceiro([{tipo:'saida', valor:'inválido', realizado:true}]),
  {previsto:0, realizado:0, aReceber:0, aPagar:0, quantidade:1}
);
const semContexto = gestao.pendenciasLegiveis(
  [{id:'11111111-1111-1111-1111-111111111111', resumo:'Conferir compra', status:'pendente'}],
  [],
  []
);
assert.strictEqual(semContexto[0].contexto, 'Contexto não informado');
assert.ok(!JSON.stringify(semContexto).includes('11111111-1111-1111-1111-111111111111'));

// Vazio: cada projeção retorna um estado determinístico e renderizável.
assert.deepStrictEqual(gestao.pendenciasLegiveis([], [], []), []);
assert.deepStrictEqual(gestao.eventosLegiveis([]), []);
assert.deepStrictEqual(
  gestao.resumoFinanceiro([]),
  {previsto:0, realizado:0, aReceber:0, aPagar:0, quantidade:0}
);

// Falha: a mensagem é clara e não vaza detalhes internos da API.
const mensagem = gestao.erroLegivel(new Error('relation public.segredo does not exist'));
assert.strictEqual(mensagem, 'Não foi possível carregar os dados. Tente atualizar a página.');
assert.ok(!mensagem.includes('public.segredo'));

console.log('test_gestao_frontend: 12 verificações aprovadas');
