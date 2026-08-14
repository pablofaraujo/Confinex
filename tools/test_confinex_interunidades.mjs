import assert from 'node:assert/strict';
import {
  calcularNegocioPorPeso,
  consolidarComprasOperacao,
  validarMovimentacaoInterunidades,
  validarParticipacoes,
} from '../js/confinex-interunidades.mjs';

const calculo = calcularNegocioPorPeso({
  quantidade: 10,
  pesoTotalKg: 3000,
  precoArroba: 300,
});
assert.equal(calculo.pesoMedioKg, 300);
assert.equal(calculo.arrobas, 100);
assert.equal(calculo.valorTotal, 30000);
assert.throws(() => calcularNegocioPorPeso({ quantidade: 0, pesoTotalKg: 1, precoArroba: 1 }), /quantidade/);
assert.throws(() => calcularNegocioPorPeso({ quantidade: 1.5, pesoTotalKg: 1, precoArroba: 1 }), /inteira/);
assert.throws(() => calcularNegocioPorPeso({ quantidade: 1, pesoTotalKg: 1, precoArroba: 1, rendimentoCarnePct: 101 }), /exceder/);

const compras = [{ id: 'agregada', quantidade: 30, pesoTotalKg: 9000, valorTotal: 90000 }];
const componentes = [
  { compraAgregadaId: 'agregada', quantidade: 10, pesoTotalKg: 3000, valorTotal: 30000 },
  { compraAgregadaId: 'agregada', quantidade: 20, pesoTotalKg: 6000, valorTotal: 60000 },
];
const consolidado = consolidarComprasOperacao(compras, componentes);
assert.equal(consolidado.quantidade, 30);
assert.equal(consolidado.valorTotal, 90000);
assert.equal(consolidado.coberturaComponentes.quantidade, 30);
assert.throws(() => consolidarComprasOperacao(compras, [{ compraAgregadaId: 'outra' }]), /sem compra agregada/);

const movimento = { operacaoId: 'op-1', quantidade: 10, pesoTotalKg: 3000, precoArroba: 300, valorTotal: 30000 };
const valido = validarMovimentacaoInterunidades({
  venda: { id: 'venda-1', tipo: 'venda', estado: 'confirmado', ...movimento },
  lancamento: { tipo: 'saida', negocioFazendaId: 'venda-1', quantidade: 10 },
  compra: { ...movimento },
  movimento,
});
assert.equal(valido.ok, true);
const invalido = validarMovimentacaoInterunidades({
  venda: { id: 'venda-1', tipo: 'venda', estado: 'confirmado', ...movimento },
  lancamento: { tipo: 'saida', negocioFazendaId: 'venda-1', quantidade: 9 },
  compra: { ...movimento },
  movimento,
});
assert.equal(invalido.ok, false);
assert.deepEqual(invalido.erros, ['quantidade divergente']);
const precoInvalido = validarMovimentacaoInterunidades({
  venda: { id: 'venda-1', tipo: 'venda', estado: 'confirmado', ...movimento },
  lancamento: { tipo: 'saida', negocioFazendaId: 'venda-1', quantidade: 10 },
  compra: { ...movimento, precoArroba: 299 },
  movimento,
});
assert.deepEqual(precoInvalido.erros, ['preço divergente']);

assert.deepEqual(validarParticipacoes([
  { papel: 'proprietario', participacaoPct: 60 },
  { papel: 'parceiro', participacaoPct: 40 },
  { papel: 'corretor' },
]), { ok: true, total: 100 });
assert.deepEqual(validarParticipacoes([
  { papel: 'proprietario', participacaoPct: 70 },
  { papel: 'parceiro', participacaoPct: 40 },
]), { ok: false, total: 110 });

console.log('Interunidades: 19 verificações aprovadas');
