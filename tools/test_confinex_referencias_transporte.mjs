import assert from "node:assert/strict";
import { calcularReferenciasTransporte } from "../js/confinex-referencias-transporte.mjs";

const r = calcularReferenciasTransporte({
  cabecas: 75,
  pesoOrigem: 400,
  pesoChegada: 372,
  pesoProcessado: 379,
  carcacaSaidaKg: 240,
  custoCompra: 300000,
  custoFrete: 15000,
  custoConfinamento: 90000,
});

assert.equal(r.transporteNaEntrada.arrobasBaseCab, 379 * 0.5 / 15);
assert.equal(r.transporteNaEntrada.arrobasProduzidasCab, 240 / 15 - 379 * 0.5 / 15);
assert.equal(r.transporteNaEntrada.custoArrobaBase, 315000 / (379 * 0.5 / 15 * 75));
assert.equal(r.transporteNaEntrada.custoArrobaProduzida, 90000 / ((240 / 15 - 379 * 0.5 / 15) * 75));

assert.equal(r.transporteNaProducao.arrobasBaseCab, 400 * 0.5 / 15);
assert.equal(r.transporteNaProducao.arrobasProduzidasCab, 240 / 15 - 400 * 0.5 / 15);
assert.equal(r.transporteNaProducao.custoArrobaBase, 300000 / (400 * 0.5 / 15 * 75));
assert.equal(r.transporteNaProducao.custoArrobaProduzida, 105000 / ((240 / 15 - 400 * 0.5 / 15) * 75));

assert.deepEqual(r.perdaPeso, { brutaKgCab: 28, recuperadaKgCab: 7, liquidaKgCab: 21 });

const vazio = calcularReferenciasTransporte({});
assert.equal(vazio.transporteNaEntrada.custoArrobaBase, null);
assert.equal(vazio.transporteNaProducao.custoArrobaProduzida, null);

console.log("Referências de transporte: 12 verificações aprovadas.");
