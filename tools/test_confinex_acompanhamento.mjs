import assert from 'node:assert/strict';
import { consolidarLote, fecharLote, normalizarEvento } from '../js/confinex-acompanhamento.mjs';

assert.deepEqual(normalizarEvento({ tipo: 'entrada', data: '2026-07-01', cabecas: 100 }).quantidade, 100);
assert.throws(() => normalizarEvento({ tipo: 'desconhecido', data: '2026-07-01', quantidade: 1 }), /tipo/);
assert.throws(() => normalizarEvento({ tipo: 'entrada', data: '01/07/2026', quantidade: 1 }), /data/);
assert.throws(() => normalizarEvento({ tipo: 'morte', data: '2026-07-01', quantidade: -1 }), /quantidade/);
const resumo = consolidarLote({ loteId: 'lote-1', eventos: [
  { tipo: 'entrada', data: '2026-07-01', cabecas: 100 },
  { tipo: 'materia_seca', data: '2026-07-02', materiaSecaKg: 250 },
  { tipo: 'pesagem', data: '2026-07-10', quantidade: 100, pesoKg: 420 },
  { tipo: 'morte', data: '2026-07-11', cabecas: 2 },
  { tipo: 'transferencia', data: '2026-07-12', cabecas: 3 },
  { tipo: 'cobranca', data: '2026-07-15', quantidade: 1000 },
  { tipo: 'pagamento', data: '2026-07-20', quantidade: 400 }
] });
assert.equal(resumo.cabecasAtuais, 95);
assert.equal(resumo.consumoMsKg, 250);
assert.equal(resumo.pesagens[0].pesoKg, 420);
assert.equal(resumo.saldoCobrancas, 600);
assert.equal(fecharLote(resumo, '2026-07-31').fechado, true);
assert.throws(() => fecharLote(resumo, '31/07/2026'), /data/);
console.log('Acompanhamento de lote: 11 verificações aprovadas.');

