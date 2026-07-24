const MODOS_PAGAMENTO_CONFINAMENTO = Object.freeze({
  adiantado: "Pagamento adiantado",
  mensal: "Pagamento mensal",
  final: "Pagamento no final",
});

function numeroNaoNegativo(valor, campo) {
  if (valor === "" || valor === null || valor === undefined) return 0;
  const numero = Number(valor);
  if (!Number.isFinite(numero) || numero < 0) {
    throw new RangeError(`${campo} deve ser um número maior ou igual a zero`);
  }
  return numero;
}

function normalizarModoPagamentoConfinamento(modo) {
  return Object.hasOwn(MODOS_PAGAMENTO_CONFINAMENTO, modo) ? modo : "final";
}

function criarParcelasMensais(valorTotal, diasCiclo) {
  if (valorTotal === 0) return [];
  if (diasCiclo === 0) return [{ dia: 0, valor: valorTotal }];

  const parcelas = [];
  let inicio = 0;
  let valorAcumulado = 0;
  while (inicio < diasCiclo) {
    const fim = Math.min(inicio + 30, diasCiclo);
    const ultima = fim === diasCiclo;
    const valor = ultima
      ? valorTotal - valorAcumulado
      : valorTotal * ((fim - inicio) / diasCiclo);
    parcelas.push({ dia: fim, valor });
    valorAcumulado += valor;
    inicio = fim;
  }
  return parcelas;
}

/**
 * Modela os pagamentos nominais do confinamento e seu custo financeiro.
 *
 * A taxa é efetiva mensal. Cada desembolso é capitalizado somente do dia em
 * que ocorre até o recebimento da venda. O valor presente é calculado em uma
 * trilha separada e não é somado ao lucro nominal.
 */
function calcularPagamentoConfinamento({
  valorTotal,
  diasCiclo,
  diasAteRecebimento,
  taxaMensal,
  modo,
} = {}) {
  const total = numeroNaoNegativo(valorTotal, "valorTotal");
  const ciclo = numeroNaoNegativo(diasCiclo, "diasCiclo");
  const recebimentoInformado = numeroNaoNegativo(
    diasAteRecebimento,
    "diasAteRecebimento",
  );
  const taxa = numeroNaoNegativo(taxaMensal, "taxaMensal");
  const recebimento = Math.max(recebimentoInformado, ciclo);
  const modoNormalizado = normalizarModoPagamentoConfinamento(modo);

  let parcelas;
  if (modoNormalizado === "adiantado") {
    parcelas = total > 0 ? [{ dia: 0, valor: total }] : [];
  } else if (modoNormalizado === "mensal") {
    parcelas = criarParcelasMensais(total, ciclo);
  } else {
    parcelas = total > 0 ? [{ dia: ciclo, valor: total }] : [];
  }

  const fluxos = parcelas.map((parcela, indice) => {
    const diasExposicao = Math.max(recebimento - parcela.dia, 0);
    const fatorAteRecebimento = Math.pow(1 + taxa, diasExposicao / 30);
    const fatorAteOrigem = Math.pow(1 + taxa, parcela.dia / 30);
    const valorNoRecebimento = parcela.valor * fatorAteRecebimento;
    return {
      parcela: indice + 1,
      dia: parcela.dia,
      valor: parcela.valor,
      diasExposicao,
      valorNoRecebimento,
      custoDinheiro: valorNoRecebimento - parcela.valor,
      valorPresente: parcela.valor / fatorAteOrigem,
    };
  });

  const somar = (campo) =>
    fluxos.reduce((totalFluxos, fluxo) => totalFluxos + fluxo[campo], 0);

  return {
    modo: modoNormalizado,
    rotulo: MODOS_PAGAMENTO_CONFINAMENTO[modoNormalizado],
    valorNominal: total,
    diasCiclo: ciclo,
    diasAteRecebimento: recebimento,
    taxaMensal: taxa,
    fluxos,
    quantidadeParcelas: fluxos.length,
    valorNoRecebimento: somar("valorNoRecebimento"),
    custoDinheiro: somar("custoDinheiro"),
    valorPresente: somar("valorPresente"),
  };
}

function rotuloModoPagamentoConfinamento(modo) {
  return MODOS_PAGAMENTO_CONFINAMENTO[
    normalizarModoPagamentoConfinamento(modo)
  ];
}

export {
  MODOS_PAGAMENTO_CONFINAMENTO,
  calcularPagamentoConfinamento,
  normalizarModoPagamentoConfinamento,
  rotuloModoPagamentoConfinamento,
};
