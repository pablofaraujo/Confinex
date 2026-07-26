export function cotacaoBgiValida(registro) {
  const preco = Number.parseFloat(registro?.preco);
  return Number.isFinite(preco) && preco > 0;
}

export function criarCotacaoBgiManual(preco, atualizadaEm) {
  const texto = String(preco ?? "").trim();
  if (!texto) return null;
  const valor = Number.parseFloat(texto.replace(",", "."));
  if (!Number.isFinite(valor) || valor <= 0) {
    throw new Error("Informe uma cotação maior que zero.");
  }
  return {
    preco: String(Math.round(valor * 100) / 100),
    fonte: "Valor informado manualmente",
    atualizadaEm,
    modo: "manual",
  };
}

export function mesclarCotacoesBgiAutomaticas(atuais, obtidas, atualizadaEm) {
  const cotacoes = { ...(atuais || {}) };
  const atualizados = [];
  const preservados = [];

  for (const item of obtidas || []) {
    const contrato = String(item?.contrato || "").trim().toUpperCase();
    const existente = cotacoes[contrato];
    if (!contrato || !cotacaoBgiValida({ preco: item?.cotacao?.preco })) continue;
    if (existente?.modo === "manual" && cotacaoBgiValida(existente)) {
      preservados.push(contrato);
      continue;
    }
    cotacoes[contrato] = {
      preco: String(Math.round(Number(item.cotacao.preco) * 100) / 100),
      fonte: item.cotacao.fonte || "Cotação automática",
      atualizadaEm: item.cotacao.data || atualizadaEm,
      modo: "automatico",
    };
    atualizados.push(contrato);
  }

  return { cotacoes, atualizados, preservados };
}
