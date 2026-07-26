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

assert.equal(r.perdaPeso.brutaKgCab, 28);
assert.equal(r.perdaPeso.brutaKgTotal, 2100);
assert.equal(r.perdaPeso.brutaArrobasEquivalentes, 70);
assert.equal(r.perdaPeso.recuperadaKgCab, 7);
assert.equal(r.perdaPeso.recuperadaKgTotal, 525);
assert.equal(r.perdaPeso.recuperadaArrobasEquivalentes, 17.5);
assert.equal(r.perdaPeso.liquidaKgCab, 21);
assert.equal(r.perdaPeso.liquidaKgTotal, 1575);
assert.equal(r.perdaPeso.liquidaArrobasEquivalentes, 52.5);

const vazio = calcularReferenciasTransporte({});
assert.equal(vazio.transporteNaEntrada.custoArrobaBase, null);
assert.equal(vazio.transporteNaProducao.custoArrobaProduzida, null);

const semPerda = calcularReferenciasTransporte({ cabecas: 10, pesoOrigem: 400, pesoChegada: 400, pesoProcessado: 400, carcacaSaidaKg: 240, custoCompra: 1000, custoFrete: 0, custoConfinamento: 500 });
assert.equal(semPerda.perdaPeso.brutaKgCab, 0);
assert.equal(semPerda.perdaPeso.liquidaKgCab, 0);
const semRecuperacao = calcularReferenciasTransporte({ cabecas: 10, pesoOrigem: 400, pesoChegada: 380, pesoProcessado: 380, carcacaSaidaKg: 240 });
assert.equal(semRecuperacao.perdaPeso.recuperadaKgCab, 0);
const recuperacaoTotal = calcularReferenciasTransporte({ cabecas: 10, pesoOrigem: 400, pesoChegada: 380, pesoProcessado: 400, carcacaSaidaKg: 240 });
assert.equal(recuperacaoTotal.perdaPeso.liquidaKgCab, 0);
assert.equal(semPerda.transporteNaEntrada.custoArrobaBase, 1000 / (400 * 0.5 / 15 * 10));
const freteAlto = calcularReferenciasTransporte({ cabecas: 10, pesoOrigem: 400, pesoChegada: 380, pesoProcessado: 390, carcacaSaidaKg: 240, custoCompra: 1000, custoFrete: 5000, custoConfinamento: 500 });
assert.ok(freteAlto.transporteNaEntrada.custoArrobaBase > semPerda.transporteNaEntrada.custoArrobaBase);
assert.ok(freteAlto.transporteNaProducao.custoArrobaProduzida > semPerda.transporteNaProducao.custoArrobaProduzida);
const semProducao = calcularReferenciasTransporte({ cabecas: 10, pesoOrigem: 500, pesoChegada: 500, pesoProcessado: 500, carcacaSaidaKg: 240, custoConfinamento: 500 });
assert.equal(semProducao.transporteNaEntrada.custoArrobaProduzida, null);
assert.equal(semProducao.transporteNaProducao.custoArrobaProduzida, null);
assert.ok(Object.values(freteAlto.transporteNaEntrada).every((valor) => valor === null || Number.isFinite(valor)));
assert.ok(Object.values(freteAlto.transporteNaProducao).every((valor) => valor === null || Number.isFinite(valor)));

console.log("Referências de transporte: 30 verificações aprovadas.");
