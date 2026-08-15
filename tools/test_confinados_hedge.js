const assert = require('assert');
const {
  calcularResultadoAberto,
  calcularResultadoRealizado,
  deduplicarPosicoes,
  extrairRateios,
  reconciliarExposicao,
  resumirCobertura,
} = require('../js/confinados-hedge.js');
const fs = require('fs');
const path = require('path');

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

const resumo = resumirCobertura([
  { ...base, contratos_qtd: 23.7, termo: 'bgp:q26', negocio_rateio: 'CF-26-009 5,2; CF-26-010 8,2; CF-26-011 10,3' },
  { ...base, contrato: 'BGIV26', contratos_qtd: 4, preco_entrada: 360, termo: 'bgp:v26', negocio_rateio: 'CF-26-013' },
  { ...base, contrato: 'BGIX26', contratos_qtd: 6, preco_entrada: 362, termo: 'bgp:x26', negocio_rateio: 'CF-26-014' },
], 35.47);
assert.ok(Math.abs(resumo.coberturaLiquida - 33.7) < 1e-9);
assert.ok(Math.abs(resumo.descobertos - 1.77) < 1e-9);
assert.ok(Math.abs(resumo.arrobasDescobertas - 584.1) < 1e-9);

const resumoComCompraEEspeculacao = resumirCobertura([
  { ...base, contratos_qtd: 10, termo: 'bgp:venda' },
  { ...base, direcao: 'comprado', contratos_qtd: 2, termo: 'bgp:compra' },
  { ...base, contratos_qtd: 5, categoria: 'especulacao', termo: 'bgp:espec' },
  { ...base, direcao: 'termo', contratos_qtd: 2.4, termo: 'bgp:termo' },
], 12);
assert.ok(Math.abs(resumoComCompraEEspeculacao.coberturaLiquida - 10.4) < 1e-9);
assert.ok(Math.abs(resumoComCompraEEspeculacao.descobertos - 1.6) < 1e-9);
assert.strictEqual(resumoComCompraEEspeculacao.contratosB3Brutos, 17);

const vendidoMarcado = calcularResultadoAberto({
  status: 'aberta', direcao: 'vendido', contratos_qtd: 4, preco_entrada: 360,
  custo_corretagem: 0, custo_finpec: 1320,
}, 353.6);
assert.ok(Math.abs(vendidoMarcado.bruto - 8448) < 1e-9);
assert.ok(Math.abs(vendidoMarcado.resultado - 7128) < 1e-9);
const compradoMarcado = calcularResultadoAberto({
  status: 'aberta', direcao: 'comprado', contratos_qtd: 2, preco_entrada: 340,
  custo_corretagem: 100, custo_finpec: 200,
}, 345);
assert.strictEqual(compradoMarcado.resultado, 3000);
assert.strictEqual(calcularResultadoAberto({ status: 'aberta', direcao: 'vendido', contratos_qtd: 1, preco_entrada: 350 }, null), null);
assert.strictEqual(calcularResultadoAberto({ status: 'encerrada', direcao: 'vendido', contratos_qtd: 1, preco_entrada: 350 }, 340), null);

const resultadoAtual = [
  { contrato: 'BGIQ26', contratos_qtd: 5.2, preco_entrada: 351, custo_finpec: 1716 },
  { contrato: 'BGIQ26', contratos_qtd: 8.2, preco_entrada: 351, custo_finpec: 2706 },
  { contrato: 'BGIQ26', contratos_qtd: 10.3, preco_entrada: 351 },
  { contrato: 'BGIV26', contratos_qtd: 4, preco_entrada: 360, custo_finpec: 1320 },
  { contrato: 'BGIX26', contratos_qtd: 3, preco_entrada: 362, custo_finpec: 990 },
  { contrato: 'BGIX26', contratos_qtd: 3, preco_entrada: 362, custo_finpec: 990 },
].reduce((total, posicao) => total + calcularResultadoAberto({
  ...posicao, status: 'aberta', direcao: 'vendido',
}, { BGIQ26: 348.15, BGIV26: 353.6, BGIX26: 356.5 }[posicao.contrato]).resultado, 0);
assert.ok(Math.abs(resultadoAtual - 33905.85) < 1e-9);
assert.strictEqual(Math.round(resultadoAtual), 33906);

const resultadoRealizado = calcularResultadoRealizado([
  { ...base, status: 'encerrada', resultado_realizado: 57420, termo: null },
  { ...base, status: 'encerrada', resultado_realizado: 57420, termo: 'bgp:encerrada' },
  { ...base, contrato: 'BGIV26', status: 'fechada', resultado_realizado: 20708, termo: 'bgp:fechada' },
  { ...base, contrato: 'BGIU26', direcao: 'comprado', status: 'encerrada', resultado_realizado: -34980, termo: 'bgp:perda' },
  { ...base, contrato: 'BGIU26', direcao: 'comprado', status: 'encerrada', resultado_realizado: -818, termo: null },
  { ...base, contrato: 'BGIX26', status: 'aberta', resultado_realizado: 999999, termo: 'bgp:aberta' },
]);
assert.strictEqual(resultadoRealizado, 43148);

const htmlBgi = fs.readFileSync(path.join(__dirname, '..', 'bgi.html'), 'utf8');
assert.ok(htmlBgi.includes('./js/confinados-hedge.js?v=20260803-3'));
assert.ok(htmlBgi.includes('POS=ConfinadosHedge.deduplicarPosicoes(pos.data)'));
assert.ok(htmlBgi.includes('ConfinadosHedge.resumirCobertura(POS,nec)'));
assert.ok(htmlBgi.includes('ConfinadosHedge.reconciliarExposicao(expoConfinamento,POS)'));
assert.ok(htmlBgi.includes('ConfinadosHedge.calcularResultadoAberto(p,ultimas[p.contrato]?.preco)'));
assert.ok(htmlBgi.includes('ConfinadosHedge.calcularResultadoRealizado(POS)'));
assert.ok(htmlBgi.includes("['Contratos necessários',fmtN(nec)+' cts'],['Cobertura cts bolsa'"));
assert.ok(htmlBgi.indexOf('Resultado líquido em aberto') < htmlBgi.indexOf('Resultado realizado'));
assert.ok(!htmlBgi.includes("['Realizado creditado',fmtR$(cred)]"));
assert.ok(htmlBgi.includes("const ehLoteBoiBalanca = codigo => /^BB(?:-|\\d)/i.test"));
assert.ok(htmlBgi.includes("filter(e=>!ehLoteBoiBalanca(e.codigo))"));
assert.ok(htmlBgi.includes("filter(o=>!ehLoteBoiBalanca(o.codigo))"));
assert.ok(!htmlBgi.includes('const ab=expo.data.reduce'));
assert.ok(!htmlBgi.includes('POS=pos.data'));

const htmlConfinados = fs.readFileSync(path.join(__dirname, '..', 'confinados.html'), 'utf8');
assert.ok(htmlConfinados.indexOf('<h2>Saldo atual por confinamento</h2>')
  < htmlConfinados.indexOf('<h2>Lotes ativos em confinamento</h2>'));
assert.ok(htmlConfinados.indexOf('<h2>Lotes ativos em confinamento</h2>')
  < htmlConfinados.indexOf('<h2>Cobertura dos lotes ativos</h2>'));
assert.ok(htmlConfinados.indexOf('<h2>Cobertura dos lotes ativos</h2>')
  < htmlConfinados.indexOf('<h2>Posições abertas vinculadas aos confinados</h2>'));
assert.ok(htmlConfinados.includes(
  "a.valor_liquido!=null&&a.esperado_calculado!=null?fmtR$(a.divergencia):'—'",
));
assert.ok(!htmlConfinados.includes(
  "a.valor_liquido!=null?fmtR$(a.divergencia):'—'",
));

console.log('Confinados hedge e cabeçalho BGI: 40 verificações aprovadas.');
