import assert from "node:assert/strict";
import {
  cotacaoBgiValida,
  criarCotacaoBgiManual,
  mesclarCotacoesBgiAutomaticas,
} from "../js/confinex-bgi.mjs";

const agora = "2026-07-26T12:00:00.000Z";

assert.equal(cotacaoBgiValida({ preco: "350" }), true);
assert.equal(cotacaoBgiValida({ preco: "" }), false);
assert.equal(cotacaoBgiValida({ preco: "0" }), false);
assert.equal(criarCotacaoBgiManual("", agora), null);
assert.throws(() => criarCotacaoBgiManual("0", agora), /maior que zero/);

const manual = criarCotacaoBgiManual("351,25", agora);
assert.equal(manual.preco, "351.25");
assert.equal(manual.modo, "manual");

const mescla = mesclarCotacoesBgiAutomaticas(
  {
    BGIX26: manual,
    BGIZ26: { preco: "340", fonte: "anterior", modo: "automatico" },
  },
  [
    { contrato: "BGIX26", cotacao: { preco: 360, fonte: "B3" } },
    { contrato: "BGIZ26", cotacao: { preco: 345, fonte: "B3" } },
    { contrato: "BGIF27", cotacao: { preco: null, fonte: "B3" } },
  ],
  agora,
);

assert.equal(mescla.cotacoes.BGIX26.preco, "351.25", "manual não pode ser sobrescrito");
assert.equal(mescla.cotacoes.BGIZ26.preco, "345", "automático pode ser atualizado");
assert.equal(mescla.cotacoes.BGIF27, undefined, "ausência não pode virar zero");
assert.deepEqual(mescla.preservados, ["BGIX26"]);
assert.deepEqual(mescla.atualizados, ["BGIZ26"]);

console.log("Mercado BGI: 12 verificações aprovadas.");
