import assert from "node:assert/strict";
import { compararRevendaComConfinamento } from "../js/confinex-revenda-equivalente.mjs";

const perto = (observado, esperado) => assert.ok(Math.abs(observado - esperado) < 1e-9);
const base = {
  lucroLiquidoConfinamento: 10_000,
  custosOperacionaisRevenda: 90_000,
  custoFinanceiroRevenda: 5_000,
  arrobasVendidas: 500,
  tributosPercentual: 0.05,
};
const precoEmpate = 105_000 / 0.95 / 500;

const empate = compararRevendaComConfinamento({ ...base, precoDisponivel: precoEmpate });
assert.equal(empate.calculavel, true);
perto(empate.precoMinimo, precoEmpate);
perto(empate.lucroLiquidoRevenda, 10_000);
assert.equal(empate.melhorAlternativa, "Mesmo resultado");

const revendaMelhor = compararRevendaComConfinamento({ ...base, precoDisponivel: precoEmpate + 10 });
assert.equal(revendaMelhor.melhorAlternativa, "Revenda direta");
assert.ok(revendaMelhor.lucroLiquidoRevenda > base.lucroLiquidoConfinamento);

const confinamentoMelhor = compararRevendaComConfinamento({ ...base, precoDisponivel: precoEmpate - 10 });
assert.equal(confinamentoMelhor.melhorAlternativa, "Confinamento");
assert.ok(confinamentoMelhor.lucroLiquidoRevenda < base.lucroLiquidoConfinamento);

const semTributos = compararRevendaComConfinamento({ ...base, tributosPercentual: 0, precoDisponivel: 210 });
assert.equal(semTributos.precoMinimo, 210);
const maisCustos = compararRevendaComConfinamento({ ...base, custosOperacionaisRevenda: 100_000, precoDisponivel: 250 });
assert.ok(maisCustos.precoMinimo > empate.precoMinimo);
const maisPrazo = compararRevendaComConfinamento({ ...base, custoFinanceiroRevenda: 15_000, precoDisponivel: 250 });
assert.ok(maisPrazo.precoMinimo > empate.precoMinimo);

for (const entrada of [
  { ...base, arrobasVendidas: 0, precoDisponivel: 300 },
  { ...base, arrobasVendidas: null, precoDisponivel: 300 },
  { ...base, tributosPercentual: 1, precoDisponivel: 300 },
  { ...base, precoDisponivel: null },
]) {
  const resultado = compararRevendaComConfinamento(entrada);
  assert.equal(resultado.calculavel, false);
  assert.match(resultado.motivo, /^Não calculável:/);
}

// A quantidade de arrobas já chega líquida do desconto de capim calculado pelo cenário.
const comCapim = compararRevendaComConfinamento({ ...base, arrobasVendidas: 480, precoDisponivel: 250 });
assert.ok(comCapim.precoMinimo > empate.precoMinimo);
const alvoMuitoNegativo = compararRevendaComConfinamento({ ...base, lucroLiquidoConfinamento: -200_000, precoDisponivel: 1 });
assert.equal(alvoMuitoNegativo.precoMinimo, 0);
assert.equal(alvoMuitoNegativo.igualdadePossivel, false);
assert.match(alvoMuitoNegativo.observacao, /preço de venda igual a zero/);
assert.throws(() => compararRevendaComConfinamento({ ...base, tributosPercentual: -0.01, precoDisponivel: 300 }), /tributosPercentual/);

console.log("Revenda equivalente: 21 verificações aprovadas.");
