(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  root.ConfinadosHedge = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  function numero(valor) {
    if (valor === '' || valor === null || valor === undefined) return 0;
    const convertido = Number(String(valor).replace(',', '.'));
    return Number.isFinite(convertido) ? convertido : 0;
  }

  function codigoLote(texto) {
    const partes = String(texto || '').match(/CF\s*-\s*(\d{2})\s*-\s*(\d{3})/i);
    return partes ? `CF-${partes[1]}-${partes[2]}` : '';
  }

  function extrairRateios(texto, contratosTotal) {
    const fonte = String(texto || '');
    const regex = /(CF\s*-\s*\d{2}\s*-\s*\d{3})([\s\S]*?)(?=CF\s*-\s*\d{2}\s*-\s*\d{3}|$)/gi;
    const encontrados = [];
    let trecho;

    while ((trecho = regex.exec(fonte)) !== null) {
      const codigo = codigoLote(trecho[1]);
      const quantidade = trecho[2].match(/^\s*(?::|[-–—])?\s*(\d+(?:[.,]\d+)?)\s*(?:cts?|contratos?)?\b/i);
      encontrados.push({
        codigo,
        contratos: quantidade ? numero(quantidade[1]) : null,
      });
    }

    if (encontrados.length === 1 && encontrados[0].contratos === null) {
      encontrados[0].contratos = numero(contratosTotal);
    }

    return encontrados.filter((item) => item.codigo && item.contratos !== null && item.contratos >= 0);
  }

  function chaveOperacional(posicao) {
    return [
      String(posicao.contrato || '').toUpperCase(),
      String(posicao.direcao || '').toLowerCase(),
      numero(posicao.contratos_qtd),
      numero(posicao.preco_entrada),
      String(posicao.status || '').toLowerCase(),
    ].join('|');
  }

  function deduplicarPosicoes(posicoes) {
    const linhas = Array.isArray(posicoes) ? posicoes : [];
    const gerenciadas = linhas.filter((item) => String(item.termo || '').startsWith('bgp:'));
    const chavesGerenciadas = new Set(gerenciadas.map(chaveOperacional));

    return linhas.filter((item) => (
      String(item.termo || '').startsWith('bgp:')
      || !chavesGerenciadas.has(chaveOperacional(item))
    ));
  }

  function contratosAbertosPorLote(posicoes) {
    const totais = new Map();
    deduplicarPosicoes(posicoes)
      .filter((item) => ['aberta', 'rolada'].includes(item.status) && item.categoria !== 'especulacao')
      .forEach((item) => {
        extrairRateios(item.negocio_rateio, item.contratos_qtd).forEach((rateio) => {
          totais.set(rateio.codigo, (totais.get(rateio.codigo) || 0) + rateio.contratos);
        });
      });
    return totais;
  }

  function ehEspeculacao(posicao) {
    return String(posicao.categoria || '').toLowerCase() === 'especulacao'
      || /espec/i.test(String(posicao.negocio_rateio || posicao.obs || ''));
  }

  function resumirCobertura(posicoes, contratosNecessarios) {
    let vendidosHedge = 0;
    let compradosHedge = 0;
    let termosHedge = 0;
    let contratosB3Brutos = 0;

    deduplicarPosicoes(posicoes)
      .filter((item) => ['aberta', 'rolada'].includes(String(item.status || '').toLowerCase()))
      .forEach((item) => {
        const quantidade = Math.abs(numero(item.contratos_qtd));
        if (!quantidade) return;
        const direcao = String(item.direcao || '').toLowerCase();
        const termo = direcao === 'termo';
        if (!termo) contratosB3Brutos += quantidade;
        if (ehEspeculacao(item)) return;
        if (termo) termosHedge += quantidade;
        else if (direcao === 'comprado') compradosHedge += quantidade;
        else if (direcao === 'vendido') vendidosHedge += quantidade;
      });

    const necessarios = Math.max(numero(contratosNecessarios), 0);
    const coberturaLiquida = vendidosHedge + termosHedge - compradosHedge;
    const descobertos = Math.max(necessarios - coberturaLiquida, 0);
    return {
      necessarios,
      vendidosHedge,
      compradosHedge,
      termosHedge,
      coberturaLiquida,
      descobertos,
      arrobasDescobertas: descobertos * 330,
      contratosB3Brutos,
      arrobasB3Brutas: contratosB3Brutos * 330,
    };
  }

  function calcularResultadoAberto(posicao, cotacaoAtual) {
    if (String(posicao.status || '').toLowerCase() !== 'aberta') return null;
    const quantidade = Math.abs(numero(posicao.contratos_qtd));
    const entrada = numero(posicao.preco_entrada);
    const direcao = String(posicao.direcao || '').toLowerCase();
    const custos = numero(posicao.custo_corretagem) + numero(posicao.custo_finpec);
    if (direcao === 'termo') {
      return { atual: entrada, bruto: 0, custos, resultado: -custos };
    }
    const atual = numero(cotacaoAtual);
    if (!quantidade || !entrada || !atual) return null;
    const bruto = (direcao === 'vendido' ? entrada - atual : atual - entrada) * quantidade * 330;
    return {
      atual,
      bruto,
      custos,
      resultado: Math.round((bruto - custos) * 100) / 100,
    };
  }

  function reconciliarExposicao(exposicao, posicoes) {
    const abertosPorLote = contratosAbertosPorLote(posicoes);
    return (Array.isArray(exposicao) ? exposicao : []).map((item) => {
      if (!abertosPorLote.has(item.codigo)) return { ...item };
      const ctsAbertos = abertosPorLote.get(item.codigo);
      return {
        ...item,
        cts_abertos: ctsAbertos,
        cts_descobertos: numero(item.cts_necessarios) - ctsAbertos,
      };
    });
  }

  return {
    codigoLote,
    contratosAbertosPorLote,
    calcularResultadoAberto,
    deduplicarPosicoes,
    extrairRateios,
    reconciliarExposicao,
    resumirCobertura,
  };
}));
