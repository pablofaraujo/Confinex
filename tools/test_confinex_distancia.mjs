import assert from 'node:assert/strict';
import { calcularFrete, congelarDistancia, normalizarDistancia } from '../js/confinex-distancia.mjs';
import fs from 'node:fs';

const bundle = fs.readFileSync('confinex-app.latest.js', 'utf8');
assert.match(bundle, /distanciaFonte/);
assert.match(bundle, /distanciaCongeladaEm/);
assert.match(bundle, /congelada neste estudo/);
assert.match(bundle, /Local do confinamento/);
assert.match(bundle, /Fica salvo junto da base do confinamento/);
assert.match(bundle, /Ver no Maps/);
assert.match(bundle, /preservada neste estudo/);
assert.match(bundle, /"destinoFrete",/);

const base = normalizarDistancia({ origem: 'Fazenda A', destino: 'Cocho B', km: 420, fonte: 'proxy homologado', calculadaEm: '2026-07-24T09:00:00Z', ajusteKm: 10 });
assert.equal(base.km, 430);
assert.equal(base.ajusteKm, 10);
assert.throws(() => normalizarDistancia({ origem: '', destino: 'B', km: 1, fonte: 'x', calculadaEm: 'x' }), /origem/);
assert.throws(() => normalizarDistancia({ origem: 'A', destino: 'B', km: 0, fonte: 'x', calculadaEm: 'x' }), /distância/);
assert.throws(() => normalizarDistancia({ origem: 'A', destino: 'B', km: 1, fonte: '', calculadaEm: 'x' }), /fonte/);
const congelada = congelarDistancia(base, 'estudo-1', '2026-07-24T10:00:00Z');
assert.equal(congelada.estudoId, 'estudo-1');
assert.equal(congelarDistancia(congelada, 'estudo-1').km, 430);
assert.deepEqual(calcularFrete({ distanciaKm: 100, precoPorKm: 2, pedagios: 50, carretas: 2 }), { bruto: 900, total: 900, porCabeca: 450 });
assert.equal(calcularFrete({ distanciaKm: 100, precoPorKm: 2, carretas: 2, responsabilidade: 'confinamento' }).total, 0);
assert.equal(calcularFrete({ distanciaKm: 100, precoPorKm: 2, carretas: 2, responsabilidade: 'dividido' }).total, 400);
assert.throws(() => calcularFrete({ distanciaKm: -1, precoPorKm: 2, carretas: 1 }), /frete/);
console.log('Distância e frete: 16 verificações aprovadas.');
