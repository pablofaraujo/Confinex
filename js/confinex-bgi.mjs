const CODIGOS_MESES_BGI = ["F", "G", "H", "J", "K", "M", "N", "Q", "U", "V", "X", "Z"];

export function contratoB3PorData(dataISO) {
  if (!dataISO) return "";
  const data = new Date(`${dataISO}T12:00:00`);
  if (Number.isNaN(data.getTime())) return "";
  return `BGI${CODIGOS_MESES_BGI[data.getMonth()]}${String(data.getFullYear()).slice(-2)}`;
}

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

export function atualizarContratoBgiPorPrazo({
  cenario,
  campoAlterado,
  valor,
  contratoSugerido,
  cotacoes = {},
} = {}) {
  const proximo = { ...(cenario || {}), [campoAlterado]: valor };
  const mudouPrazo = campoAlterado === "dataEntrada" || campoAlterado === "diasCiclo";
  if (
    !mudouPrazo
    || proximo.tipo === "revenda"
    || proximo.modoPreco !== "bolsa"
    || !contratoSugerido
  ) {
    return proximo;
  }

  const cotacao = cotacoes?.[contratoSugerido];
  return {
    ...proximo,
    contratoB3: contratoSugerido,
    precoBolsa: cotacaoBgiValida(cotacao) ? String(cotacao.preco) : "",
    cotacaoB3Fonte: cotacao?.fonte || "",
    cotacaoB3AtualizadaEm: cotacao?.atualizadaEm || "",
  };
}
