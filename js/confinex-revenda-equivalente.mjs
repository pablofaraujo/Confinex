function numero(valor, campo, { permitirNegativo = false } = {}) {
  if (valor === "" || valor === null || valor === undefined) return null;
  const convertido = Number(valor);
  if (!Number.isFinite(convertido) || (!permitirNegativo && convertido < 0)) {
    throw new RangeError(`${campo} inválido`);
  }
  return convertido;
}

export function compararRevendaComConfinamento({
  lucroLiquidoConfinamento,
  custosOperacionaisRevenda,
  custoFinanceiroRevenda,
  arrobasVendidas,
  tributosPercentual,
  precoDisponivel,
} = {}) {
  const alvo = numero(lucroLiquidoConfinamento, "lucroLiquidoConfinamento", {
    permitirNegativo: true,
  });
  const custos = numero(custosOperacionaisRevenda, "custosOperacionaisRevenda");
  const financeiro = numero(custoFinanceiroRevenda, "custoFinanceiroRevenda");
  const arrobas = numero(arrobasVendidas, "arrobasVendidas");
  const tributos = numero(tributosPercentual, "tributosPercentual");
  const disponivel = numero(precoDisponivel, "precoDisponivel");

  const ausentes = [];
  if (alvo === null) ausentes.push("lucro líquido do confinamento");
  if (custos === null) ausentes.push("custos da revenda");
  if (financeiro === null) ausentes.push("custo do dinheiro da revenda");
  if (arrobas === null || arrobas <= 0) ausentes.push("arrobas vendidas");
  if (tributos === null) ausentes.push("tributos da revenda");
  if (disponivel === null) ausentes.push("preço disponível da revenda");
  if (tributos !== null && tributos >= 1) ausentes.push("tributos abaixo de 100%");

  if (ausentes.length) {
    return {
      calculavel: false,
      motivo: `Não calculável: informe ${ausentes.join(", ")}.`,
    };
  }

  const receitaLiquidaNecessaria = alvo + custos + financeiro;
  const faturamentoBrutoNecessario = receitaLiquidaNecessaria / (1 - tributos);
  const precoTeorico = faturamentoBrutoNecessario / arrobas;
  const igualdadePossivel = precoTeorico >= 0;
  const precoMinimo = Math.max(precoTeorico, 0);
  const receitaLiquidaDisponivel = disponivel * arrobas * (1 - tributos);
  const lucroLiquidoRevenda = receitaLiquidaDisponivel - custos - financeiro;
  const diferencaPreco = disponivel - precoMinimo;
  const tolerancia = 0.005;
  const melhorAlternativa = Math.abs(diferencaPreco) <= tolerancia
    ? "Mesmo resultado"
    : diferencaPreco > 0
      ? "Revenda direta"
      : "Confinamento";

  return {
    calculavel: true,
    igualdadePossivel,
    lucroLiquidoConfinamento: alvo,
    precoMinimo,
    precoTeorico,
    precoDisponivel: disponivel,
    diferencaPreco,
    lucroLiquidoRevenda,
    melhorAlternativa,
    receitaLiquidaNecessaria,
    faturamentoBrutoNecessario,
    tributosPercentual: tributos,
    observacao: igualdadePossivel
      ? ""
      : "A revenda já supera o lucro-alvo mesmo com preço de venda igual a zero.",
  };
}
