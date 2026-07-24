import assert from "node:assert/strict";
import {
  calcularResultadoFinanceiro,
  calcularValorPresente,
} from "../js/confinex-resultado-financeiro.mjs";

const perto = (observado, esperado, mensagem) =>
  assert.ok(
    Math.abs(observado - esperado) < 1e-9,
    `${mensagem}: esperado ${esperado}, observado ${observado}`,
  );

// Positivo: valores simples calculáveis sem a implementação.
const comFinanceiro = calcularResultadoFinanceiro({
  receita: 1500,
  custosOperacionais: 1000,
  custosFinanceiros: [
    { nome: "compra", valor: 60 },
    { nome: "frete", valor: 20 },
    { nome: "confinamento", valor: 10 },
  ],
});
assert.equal(comFinanceiro.lucroBruto, 500);
assert.equal(comFinanceiro.custoFinanceiro, 90);
assert.equal(comFinanceiro.lucroLiquido, 410);
assert.equal(comFinanceiro.diferencaBrutoLiquido, 90);

// Sem custo financeiro, bruto e líquido podem ser iguais e a causa é explícita.
const semFinanceiro = calcularResultadoFinanceiro({
  receita: 1500,
  custosOperacionais: 1000,
});
assert.equal(semFinanceiro.lucroBruto, 500);
assert.equal(semFinanceiro.custoFinanceiro, 0);
assert.equal(semFinanceiro.lucroLiquido, 500);

// Vazio é um contrato válido e não produz NaN.
assert.deepEqual(calcularResultadoFinanceiro(), {
  receita: 0,
  custosOperacionais: 0,
  lucroBruto: 0,
  componentesFinanceiros: [],
  custoFinanceiro: 0,
  lucroLiquido: 0,
  diferencaBrutoLiquido: 0,
});

// Negativo e falha de entrada não são mascarados.
assert.throws(
  () => calcularResultadoFinanceiro({ receita: -1 }),
  /receita/,
);
assert.throws(
  () => calcularResultadoFinanceiro({
    custosFinanceiros: [{ nome: "inválido", valor: "x" }],
  }),
  /custosFinanceiros/,
);

// VP independente: receita em 60 dias e desembolsos nos dias 0 e 30.
const vp = calcularValorPresente({
  receita: 1100,
  diaReceita: 60,
  taxaMensal: 0.1,
  desembolsos: [
    { nome: "compra", valor: 550, dia: 0 },
    { nome: "confinamento", valor: 330, dia: 30 },
  ],
});
perto(vp.receitaVP, 1100 / 1.1 ** 2, "receita VP");
perto(vp.custosVP, 550 + 330 / 1.1, "custos VP");
perto(
  vp.resultadoVP,
  1100 / 1.1 ** 2 - 550 - 330 / 1.1,
  "resultado VP",
);
assert.equal(vp.desembolsos[1].dia, 30);

assert.throws(
  () => calcularValorPresente({ taxaMensal: -0.01 }),
  /taxaMensal/,
);

console.log("Contrato bruto/líquido/VP: 15 verificações aprovadas.");
