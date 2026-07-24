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

// Pendências recuperam contexto humano aninhado e sempre ligam à origem segura.
const pendenciaAninhada = gestao.pendenciasLegiveis([{
  resumo:'{"bruto":"não mostrar"}',
  tipo_operacao:'compra_confinamento',
  status:'pendente',
  dados_extraidos:{
    resumo:'Conferir compra recebida',
    contexto_nome:'Grupo operacional'
  }
}], [], [])[0];
assert.strictEqual(pendenciaAninhada.resumo, 'Conferir compra recebida');
assert.strictEqual(pendenciaAninhada.contexto, 'Grupo operacional');
assert.deepStrictEqual(pendenciaAninhada.destino, {rotulo:'Revisões',href:'./revisoes.html'});
assert.strictEqual(pendenciaAninhada.acao, 'Revisar');

const documentoComCodigo = gestao.pendenciasLegiveis([], [], [{
  tipo:'nota_fiscal',
  status:'aguardando_vendedor',
  operacoes:{codigo:'BB-26-041'}
}])[0];
assert.strictEqual(documentoComCodigo.contexto, 'BB-26-041');
assert.deepStrictEqual(documentoComCodigo.destino, {rotulo:'Boi Balança',href:'./bb.html'});

// Eventos legados usam código/contexto humano, nunca UUID ou ID de grupo.
const eventosHumanos = gestao.eventosLegiveis([{
  tipo:'promocao_operacional_preparada',
  observacao:'Promoção 44444444-4444-4444-8444-444444444444 preparada',
  contexto_nome:'telegram:-1001234567890',
  entidade_codigo:'CF-26-018',
  entidade_tipo:'pending_action',
  origem:'confinex_revisoes',
  status:'registrado',
  usuario:'operador'
}]);
assert.strictEqual(eventosHumanos[0].contexto, 'CF-26-018');
assert.strictEqual(eventosHumanos[0].resumo, 'Promoção preparada');
assert.deepStrictEqual(eventosHumanos[0].origem, {rotulo:'Revisões',href:'./revisoes.html'});
assert.ok(!JSON.stringify(eventosHumanos).includes('44444444-4444-4444-8444-444444444444'));
assert.ok(!JSON.stringify(eventosHumanos).includes('telegram:-1001234567890'));

// Financeiro positivo: previsto/realizado, parcial, dívida, lembrete e origem.
const obrigacoes = gestao.obrigacoesFinanceiras([
  {tipo:'saida', descricao:'Parcela do trato', categoria:'confinamento', valor:1000, valor_pago:250, vencimento:'2026-07-25'},
  {tipo:'entrada', descricao:'Venda recebida', valor:500, realizado:true, data:'2026-07-20'}
], '2026-07-23');
assert.deepStrictEqual(
  {status:obrigacoes[1].status, pago:obrigacoes[1].valorPago, saldo:obrigacoes[1].saldo, origem:obrigacoes[1].origem.rotulo},
  {status:'Parcial', pago:250, saldo:750, origem:'Confinamento'}
);
assert.strictEqual(obrigacoes[0].status, 'Realizado');

const dividas = gestao.dividasFinanceiras([
  {numero_contrato:'1227617', valor_principal:1000, saldo_devedor:600, vencimento:'2026-07-30', taxa_juros_aa:18, status:'em_aberto', numero_parcelas:4, parcelas_pagas:2}
], [
  {numero:'001/2026', credor:'Fornecedor', valor:300, vencimento:'2026-07-22', status:'quitada'}
], '2026-07-23');
assert.deepStrictEqual(
  {referencia:dividas[1].referencia, saldo:dividas[1].saldo, parcelas:dividas[1].parcelas, status:dividas[1].status},
  {referencia:'1227617', saldo:600, parcelas:'2/4', status:'Parcial'}
);
assert.strictEqual(dividas[0].saldo, 0);

assert.deepStrictEqual(
  gestao.resumoFinanceiroAmpliado(obrigacoes, dividas),
  {aReceber:0, aPagar:750, realizado:500, vencido:0, proximos30:750, dividaAberta:600}
);
const lembretes = gestao.lembretesFinanceiros(obrigacoes, dividas);
assert.strictEqual(lembretes.length, 2);
assert.strictEqual(lembretes[0].mensagem, 'Vence em 2 dia(s)');

const transacao = gestao.transacoesFinanceiras([
  {id:'22222222-2222-2222-2222-222222222222', data:'2026-07-23', descricao:'Pagamento', categoria:'compra de gado', lote_ref:'CF-26-012', valor:-100}
])[0];
assert.deepStrictEqual(
  {descricao:transacao.descricao, negocio:transacao.negocio, origem:transacao.origem.rotulo},
  {descricao:'Pagamento', negocio:'CF-26-012', origem:'Confinamento'}
);
assert.ok(!JSON.stringify(transacao).includes('22222222-2222-2222-2222-222222222222'));

// Vazio: cada projeção retorna um estado determinístico e renderizável.
assert.deepStrictEqual(gestao.pendenciasLegiveis([], [], []), []);
assert.deepStrictEqual(gestao.eventosLegiveis([]), []);
assert.deepStrictEqual(
  gestao.resumoFinanceiro([]),
  {previsto:0, realizado:0, aReceber:0, aPagar:0, quantidade:0}
);
assert.deepStrictEqual(gestao.obrigacoesFinanceiras([], '2026-07-23'), []);
assert.deepStrictEqual(gestao.dividasFinanceiras([], [], '2026-07-23'), []);
assert.deepStrictEqual(gestao.transacoesFinanceiras([]), []);
assert.deepStrictEqual(gestao.lembretesFinanceiros([], []), []);

// Entrada financeira inválida não contamina somas nem expõe UUID.
const invalida = gestao.obrigacoesFinanceiras([
  {tipo:'saida', valor:'inválido', origem_referencia:'33333333-3333-3333-3333-333333333333'}
], 'data inválida')[0];
assert.strictEqual(invalida.valorOriginal, 0);
assert.strictEqual(invalida.referencia, 'Não informada');
assert.ok(!JSON.stringify(invalida).includes('33333333-3333-3333-3333-333333333333'));

// Regressão exploratória: códigos legados seguros ainda precisam ser humanos.
const legados = gestao.eventosLegiveis([
  {
    tipo: 'frontend_revisoes_validacao',
    resumo: 'Ajustes salvos na revisao',
    entidade_codigo: 'juan_promover_pending_action',
    status: 'em_revisao',
    usuario: 'pablo',
  },
  {
    tipo: 'auditoria',
    resumo: 'Campos ausentes',
    entidade_codigo: 'compras_missing_fields',
    status: 'registrado',
  },
  {
    tipo: 'frontend',
    resumo: 'Tela atualizada',
    entidade_codigo: 'revisoes.html',
    status: 'registrado',
  },
]);
assert.strictEqual(legados[0].tipo, 'Frontend revisões validação');
assert.strictEqual(legados[0].resumo, 'Ajustes salvos na revisão');
assert.strictEqual(legados[0].contexto, 'Juan · ação pendente de promoção');
assert.strictEqual(legados[0].status, 'Em revisão');
assert.strictEqual(legados[1].contexto, 'Compras com campos faltantes');
assert.strictEqual(legados[2].contexto, 'Revisões');

// Falha: a mensagem é clara e não vaza detalhes internos da API.
const mensagem = gestao.erroLegivel(new Error('relation public.segredo does not exist'));
assert.strictEqual(mensagem, 'Não foi possível carregar os dados. Tente atualizar a página.');
assert.ok(!mensagem.includes('public.segredo'));

console.log('test_gestao_frontend: 44 verificações aprovadas');
