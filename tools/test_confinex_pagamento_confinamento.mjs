import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import {
  calcularPagamentoConfinamento,
  normalizarModoPagamentoConfinamento,
} from "../js/confinex-pagamento-confinamento.mjs";

const perto = (observado, esperado, mensagem) =>
  assert.ok(
    Math.abs(observado - esperado) < 1e-8,
    `${mensagem}: esperado ${esperado}, observado ${observado}`,
  );

// Safari 14 não oferece Object.hasOwn. A regressão precisa exercitar o módulo
// nessas condições e também impedir que a API reapareça nos bundles servidos.
const descritorHasOwn = Object.getOwnPropertyDescriptor(Object, "hasOwn");
Object.defineProperty(Object, "hasOwn", {
  configurable: true,
  value: undefined,
  writable: true,
});
try {
  assert.equal(normalizarModoPagamentoConfinamento("mensal"), "mensal");
  assert.equal(normalizarModoPagamentoConfinamento("legado"), "final");
} finally {
  if (descritorHasOwn) Object.defineProperty(Object, "hasOwn", descritorHasOwn);
  else delete Object.hasOwn;
}

for (const arquivo of ["confinex-app.latest.js", "confinex-app.mobile.js"]) {
  const bundle = await readFile(new URL(`../${arquivo}`, import.meta.url), "utf8");
  assert.doesNotMatch(
    bundle,
    /\bObject\.hasOwn\s*\(/,
    `${arquivo} deve executar no Safari 14 sem Object.hasOwn`,
  );
}

// Positivo, calculado sem reutilizar a função sob teste:
// R$ 1.000 por 90 dias a 2% a.m.
const adiantado = calcularPagamentoConfinamento({
  valorTotal: 1000,
  diasCiclo: 90,
  diasAteRecebimento: 90,
  taxaMensal: 0.02,
  modo: "adiantado",
});
perto(adiantado.custoDinheiro, 1000 * (1.02 ** 3 - 1), "adiantado");
assert.deepEqual(
  adiantado.fluxos.map(({ dia, valor }) => [dia, valor]),
  [[0, 1000]],
);

const mensal = calcularPagamentoConfinamento({
  valorTotal: 1000,
  diasCiclo: 90,
  diasAteRecebimento: 90,
  taxaMensal: 0.02,
  modo: "mensal",
});
const parcela = 1000 / 3;
const custoMensalEsperado =
  parcela * (1.02 ** 2 - 1) +
  parcela * (1.02 - 1);
perto(mensal.custoDinheiro, custoMensalEsperado, "mensal");
assert.deepEqual(mensal.fluxos.map(({ dia }) => dia), [30, 60, 90]);
perto(
  mensal.fluxos.reduce((soma, fluxo) => soma + fluxo.valor, 0),
  1000,
  "soma das parcelas",
);

const final = calcularPagamentoConfinamento({
  valorTotal: 1000,
  diasCiclo: 90,
  diasAteRecebimento: 90,
  taxaMensal: 0.02,
  modo: "final",
});
perto(final.custoDinheiro, 0, "pagamento final sem prazo pós-abate");
assert.deepEqual(final.fluxos.map(({ dia, valor }) => [dia, valor]), [[90, 1000]]);

const finalComPrazo = calcularPagamentoConfinamento({
  valorTotal: 1000,
  diasCiclo: 90,
  diasAteRecebimento: 120,
  taxaMensal: 0.02,
  modo: "final",
});
perto(finalComPrazo.custoDinheiro, 20, "pagamento final com prazo pós-abate");

// Ciclo quebrado: parcelas proporcionais a 30, 30, 30 e 10 dias.
const cicloQuebrado = calcularPagamentoConfinamento({
  valorTotal: 1000,
  diasCiclo: 100,
  diasAteRecebimento: 100,
  taxaMensal: 0,
  modo: "mensal",
});
assert.deepEqual(
  cicloQuebrado.fluxos.map(({ dia, valor }) => [dia, valor]),
  [[30, 300], [60, 300], [90, 300], [100, 100]],
);

// Vazio mantém o cenário antigo e não cria custo ou parcela fantasma.
const vazio = calcularPagamentoConfinamento();
assert.equal(vazio.modo, "final");
assert.equal(vazio.valorNominal, 0);
assert.deepEqual(vazio.fluxos, []);
assert.equal(normalizarModoPagamentoConfinamento("legado"), "final");

// Negativo e falha de entrada são rejeitados explicitamente.
assert.throws(
  () => calcularPagamentoConfinamento({ valorTotal: -1 }),
  /valorTotal/,
);
assert.throws(
  () => calcularPagamentoConfinamento({ taxaMensal: "não é número" }),
  /taxaMensal/,
);

// Valor presente não se mistura ao custo futuro.
const trilhasSeparadas = calcularPagamentoConfinamento({
  valorTotal: 1000,
  diasCiclo: 90,
  diasAteRecebimento: 90,
  taxaMensal: 0.02,
  modo: "final",
});
perto(
  trilhasSeparadas.valorPresente,
  1000 / 1.02 ** 3,
  "valor presente separado",
);
perto(
  trilhasSeparadas.valorNoRecebimento,
  1000,
  "valor no recebimento separado",
);

console.log("Pagamento do confinamento: 21 verificações aprovadas, incluindo Safari 14.");
