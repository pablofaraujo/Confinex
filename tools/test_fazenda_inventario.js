const assert = require('assert');
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'fazenda-ametista.html'), 'utf8');

assert.ok(html.includes('Último inventário físico'));
assert.ok(html.includes("db.from('v_inventarios_fazenda_resumo')"));
assert.ok(html.includes("db.from('inventarios_fazenda')"));
assert.ok(html.includes(".eq('unidade_codigo','fazenda_ametista')"));
assert.ok(html.includes(".eq('data_referencia',referencia)"));
assert.ok(html.includes("['Estoque físico'"));
assert.ok(html.includes("['Saldo pelo histórico'"));
assert.ok(html.includes('entradas menos saídas registradas'));
assert.ok(html.includes('Peso estimado'));
assert.ok(html.includes('peso_medio_kg'));
assert.ok(html.includes('peso_total_kg'));
assert.ok(html.includes('Não informado'));
assert.ok(html.includes('Inventário indisponível'));
assert.ok(!/from\('inventarios_fazenda'\)[\s\S]{0,120}\.(insert|update|delete|upsert)\(/.test(html));
assert.ok(!html.includes('284'));
assert.ok(!html.includes('85.560'));

console.log('Inventário da Fazenda: 16 verificações aprovadas.');
