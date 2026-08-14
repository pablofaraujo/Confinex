'use strict';

const assert = require('node:assert/strict');
const { recursoExternoNaoCritico } = require('./auditoria_rede');

assert.equal(
  recursoExternoNaoCritico('https://fonts.gstatic.com/s/inter/v20/fonte.woff2'),
  true,
);
assert.equal(
  recursoExternoNaoCritico('https://fonts.gstatic.com/s/inter/v20/fonte.css'),
  false,
);
assert.equal(
  recursoExternoNaoCritico('https://pablofaraujo.github.io/Confinex/app.js'),
  false,
);
assert.equal(recursoExternoNaoCritico('url inválida'), false);

console.log('Auditoria de rede: fontes externas não críticas isoladas.');
