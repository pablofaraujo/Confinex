function numeroFinanceiro(valor, campo, { permitirNegativo = false } = {}) {
  if (valor === "" || valor === null || valor === undefined) return 0;
  const numero = Number(valor);
  if (!Number.isFinite(numero) || (!permitirNegativo && numero < 0)) {
    throw new RangeError(
      `${campo} deve ser um número ${permitirNegativo ? "finito" : "maior ou igual a zero"}`,
    );
  }
  return numero;
}

function calcularResultadoFinanceiro({
  receita,
  custosOperacionais,
  custosFinanceiros = [],
} = {}) {
  const receitaLiquida = numeroFinanceiro(receita, "receita");
  const custosOperacionaisTotal = numeroFinanceiro(
    custosOperacionais,
    "custosOperacionais",
  );
  const componentes = (custosFinanceiros || []).map((componente, indice) => ({
    nome: String(componente?.nome || `componente ${indice + 1}`),
    valor: numeroFinanceiro(
      componente?.valor,
      `custosFinanceiros[${indice}].valor`,
    ),
  }));
  const custoFinanceiro = componentes.reduce(
    (total, componente) => total + componente.valor,
    0,
  );
  const lucroBruto = receitaLiquida - custosOperacionaisTotal;
  const lucroLiquido = lucroBruto - custoFinanceiro;

  return {
    receita: receitaLiquida,
    custosOperacionais: custosOperacionaisTotal,
    lucroBruto,
    componentesFinanceiros: componentes,
    custoFinanceiro,
    lucroLiquido,
    diferencaBrutoLiquido: lucroBruto - lucroLiquido,
  };
}

function calcularRentabilidadeBruta({
  lucroBruto,
  capitalInvestido,
  mesesCapital,
} = {}) {
  const lucro = numeroFinanceiro(lucroBruto, "lucroBruto", {
    permitirNegativo: true,
  });
  const capital = numeroFinanceiro(capitalInvestido, "capitalInvestido");
  const meses = numeroFinanceiro(mesesCapital, "mesesCapital");
  const rentabilidadeTotalBruta = capital > 0 ? (lucro / capital) * 100 : 0;
  const baseComposta = Math.max(1 + rentabilidadeTotalBruta / 100, 0);
  const rentabilidadeMensalBruta = meses > 0
    ? (Math.pow(baseComposta, 1 / meses) - 1) * 100
    : 0;

  return {
    rentabilidadeTotalBruta,
    rentabilidadeMensalBruta,
  };
}

function calcularValorPresente({
  receita,
  diaReceita,
  desembolsos = [],
  taxaMensal,
} = {}) {
  const taxa = numeroFinanceiro(taxaMensal, "taxaMensal");
  const receitaNominal = numeroFinanceiro(receita, "receita");
  const diaDaReceita = numeroFinanceiro(diaReceita, "diaReceita");
  const fator = (dia) => Math.pow(1 + taxa, dia / 30);
  const receitaVP = receitaNominal / fator(diaDaReceita);
  const fluxos = (desembolsos || []).map((desembolso, indice) => {
    const valor = numeroFinanceiro(
      desembolso?.valor,
      `desembolsos[${indice}].valor`,
    );
    const dia = numeroFinanceiro(
      desembolso?.dia,
      `desembolsos[${indice}].dia`,
    );
    return {
      nome: String(desembolso?.nome || `desembolso ${indice + 1}`),
      valor,
      dia,
      valorPresente: valor / fator(dia),
    };
  });
  const custosVP = fluxos.reduce(
    (total, fluxo) => total + fluxo.valorPresente,
    0,
  );

  return {
    receitaNominal,
    receitaVP,
    desembolsos: fluxos,
    custosVP,
    resultadoVP: receitaVP - custosVP,
  };
}

export {
  calcularRentabilidadeBruta,
  calcularResultadoFinanceiro,
  calcularValorPresente,
};
