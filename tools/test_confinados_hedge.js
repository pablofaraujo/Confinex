const assert = require('assert');
const {
  deduplicarPosicoes,
  extrairRateios,
  reconciliarExposicao,
} = require('../js/confinados-hedge.js');

const rateios = extrairRateios(
  'CF-26-009 5,2 cts CF-26-010 - 8,2 cts CF-26- 011 - 10,3',
  24,
);
assert.deepStrictEqual(rateios, [
  { codigo: 'CF-26-009', contratos: 5.2 },
  { codigo: 'CF-26-010', contratos: 8.2 },
  { codigo: 'CF-26-011', contratos: 10.3 },
]);

assert.deepStrictEqual(extrairRateios('CF-26-013', 4), [
  { codigo: 'CF-26-013', contratos: 4 },
]);

const base = {
  contrato: 'BGIQ26',
  direcao: 'vendido',
  contratos_qtd: 24,
  preco_entrada: 351,
  status: 'aberta',
  categoria: 'hedge',
};
const posicoes = deduplicarPosicoes([
  { ...base, id: 'legada', termo: null, negocio_rateio: null },
  { ...base, id: 'gerenciada', termo: 'bgp:gerenciada', negocio_rateio: 'CF-26-009 5,2 cts CF-26-010 - 8,2 cts CF-26- 011 - 10,3' },
]);
assert.strictEqual(posicoes.length, 1);
assert.strictEqual(posicoes[0].termo, 'bgp:gerenciada');

const exposicao = reconciliarExposicao([
  { codigo: 'CF-26-009', cts_necessarios: 14.55, cts_abertos: 5.2, cts_descobertos: 9.35 },
  { codigo: 'CF-26-010', cts_necessarios: 7.26, cts_abertos: 18.8, cts_descobertos: -11.54 },
  { codigo: 'CF-26-011', cts_necessarios: 9.4, cts_abertos: 0, cts_descobertos: 9.4 },
], posicoes);

assert.strictEqual(exposicao[0].cts_abertos, 5.2);
assert.strictEqual(exposicao[1].cts_abertos, 8.2);
assert.ok(Math.abs(exposicao[1].cts_descobertos - (-0.94)) < 1e-9);
assert.strictEqual(exposicao[2].cts_abertos, 10.3);
assert.ok(Math.abs(exposicao[2].cts_descobertos - (-0.9)) < 1e-9);

console.log('Confinados hedge: 12 verificações aprovadas.');
