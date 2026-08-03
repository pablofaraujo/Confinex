import assert from "node:assert/strict";
import {
  atualizarContratoBgiPorPrazo,
  contratoB3PorData,
  cotacaoBgiValida,
  criarCotacaoBgiManual,
  mesclarCotacoesBgiAutomaticas,
} from "../js/confinex-bgi.mjs";

const agora = "2026-07-26T12:00:00.000Z";

assert.equal(contratoB3PorData("2026-09-30"), "BGIU26");
assert.equal(contratoB3PorData("2026-10-01"), "BGIV26", "V identifica outubro");
assert.equal(contratoB3PorData("2026-10-31"), "BGIV26");
assert.equal(contratoB3PorData("2026-11-01"), "BGIX26", "X identifica novembro");
assert.equal(contratoB3PorData("2026-11-30"), "BGIX26");
assert.equal(contratoB3PorData("2026-12-01"), "BGIZ26");
assert.equal(contratoB3PorData(""), "");

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

const cenario = {
  tipo: "confinamento",
  modoPreco: "bolsa",
  dataEntrada: "2026-07-01",
  diasCiclo: "100",
  contratoB3: "BGIV26",
  precoBolsa: "300",
};
const prazoAtualizado = atualizarContratoBgiPorPrazo({
  cenario,
  campoAlterado: "diasCiclo",
  valor: "130",
  contratoSugerido: "BGIX26",
  cotacoes: { BGIX26: { preco: "315", fonte: "B3", atualizadaEm: agora } },
});
assert.equal(prazoAtualizado.contratoB3, "BGIX26");
assert.equal(prazoAtualizado.precoBolsa, "315");
assert.equal(prazoAtualizado.cotacaoB3Fonte, "B3");

const semCotacao = atualizarContratoBgiPorPrazo({
  cenario,
  campoAlterado: "dataEntrada",
  valor: "2027-01-01",
  contratoSugerido: "BGIK27",
  cotacoes: {},
});
assert.equal(semCotacao.contratoB3, "BGIK27");
assert.equal(semCotacao.precoBolsa, "");

const edicaoManual = atualizarContratoBgiPorPrazo({
  cenario,
  campoAlterado: "precoBolsa",
  valor: "321",
  contratoSugerido: "BGIX26",
});
assert.equal(edicaoManual.contratoB3, "BGIV26");
assert.equal(edicaoManual.precoBolsa, "321");

console.log("Mercado BGI: 26 verificações aprovadas.");
