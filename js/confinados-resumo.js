(function (raiz, fabrica) {
  const api = fabrica();
  if (typeof module === 'object' && module.exports) module.exports = api;
  raiz.ConfinadosResumo = api;
}(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  const STATUS_ATIVOS = new Set(['comprada', 'em_confinamento']);

  function numero(valor) {
    const convertido = Number(valor);
    return Number.isFinite(convertido) ? convertido : 0;
  }

  function ehConfinamento(operacao) {
    return String(operacao?.tipo_negocio || '').toLowerCase() === 'confinamento'
      || Boolean(operacao?.confinamento_id || operacao?.confinamentos?.id);
  }

  function estaAtiva(operacao) {
    return STATUS_ATIVOS.has(String(operacao?.status || '').toLowerCase());
  }

  function sexoNormalizado(sexo) {
    const valor = String(sexo || '').toLowerCase();
    if (/f[eê]mea|novilha|vaca/.test(valor)) return 'femeas';
    if (/macho|boi|garrote|touro/.test(valor)) return 'machos';
    return 'nao_informado';
  }

  function somarPorOperacao(linhas, campo) {
    const totais = new Map();
    (linhas || []).forEach((linha) => {
      const operacaoId = linha.operacao_id;
      if (!operacaoId) return;
      totais.set(operacaoId, (totais.get(operacaoId) || 0) + numero(linha[campo]));
    });
    return totais;
  }

  function resumirLotes(operacoes, entradas, compras, abates) {
    const entradasPorOperacao = somarPorOperacao(entradas, 'cabecas');
    const comprasPorOperacao = somarPorOperacao(compras, 'quantidade');
    const abatesPorOperacao = somarPorOperacao(abates, 'quantidade');

    return (operacoes || [])
      .filter((operacao) => ehConfinamento(operacao) && estaAtiva(operacao))
      .map((operacao) => {
        const cabecasEntradas = entradasPorOperacao.get(operacao.id) || 0;
        const cabecasCompradas = comprasPorOperacao.get(operacao.id) || 0;
        const cabecasBase = cabecasEntradas > 0 ? cabecasEntradas : cabecasCompradas;
        const cabecasAbatidas = abatesPorOperacao.get(operacao.id) || 0;
        const linhasEntrada = (entradas || []).filter((item) => item.operacao_id === operacao.id);
        const linhasCompra = (compras || []).filter((item) => item.operacao_id === operacao.id);
        const datas = (cabecasEntradas > 0 ? linhasEntrada.map((item) => item.data_entrada) : linhasCompra.map((item) => item.data)).filter(Boolean).sort();
        const currais = [...new Set(linhasEntrada.map((item) => item.curral).filter((item) => item && item !== '-'))];
        return {
          operacao_id: operacao.id,
          codigo: operacao.codigo,
          confinamento_id: operacao.confinamento_id || operacao.confinamentos?.id || null,
          confinamento: operacao.confinamentos?.nome || 'Confinamento sem nome',
          sexo: sexoNormalizado(operacao.sexo),
          cabecas_base: cabecasBase,
          cabecas_abatidas: cabecasAbatidas,
          cabecas_atuais: Math.max(cabecasBase - cabecasAbatidas, 0),
          fonte_quantidade: cabecasEntradas > 0 ? 'entradas' : cabecasCompradas > 0 ? 'compras' : 'ausente',
          desde: datas[0] || null,
          currais,
        };
      });
  }

  function agruparPorConfinamento(lotes) {
    const grupos = new Map();
    (lotes || []).forEach((lote) => {
      const chave = lote.confinamento_id || lote.confinamento;
      const grupo = grupos.get(chave) || {
        id: lote.confinamento_id,
        nome: lote.confinamento,
        cabecas: 0,
        machos: 0,
        femeas: 0,
        nao_informado: 0,
        lotes: 0,
        lotes_sem_entrada: 0,
      };
      grupo.cabecas += numero(lote.cabecas_atuais);
      grupo[lote.sexo] += numero(lote.cabecas_atuais);
      grupo.lotes += 1;
      if (lote.fonte_quantidade === 'compras') grupo.lotes_sem_entrada += 1;
      grupos.set(chave, grupo);
    });
    return [...grupos.values()];
  }

  function aplicarInventarios(grupos, inventarios, confinamentos) {
    const porId = new Map((grupos || []).map((grupo) => [grupo.id, { ...grupo }]));
    (confinamentos || []).forEach((confinamento) => {
      if (!porId.has(confinamento.id)) porId.set(confinamento.id, {
        id: confinamento.id,
        nome: confinamento.nome || 'Confinamento sem nome',
        cabecas: 0,
        machos: 0,
        femeas: 0,
        nao_informado: 0,
        lotes: 0,
        lotes_sem_entrada: 0,
      });
    });
    const ultimos = new Map();
    (inventarios || []).slice().sort((a,b) => String(b.data_referencia || '').localeCompare(String(a.data_referencia || ''))).forEach((item) => {
      if (!ultimos.has(item.confinamento_id)) ultimos.set(item.confinamento_id, item);
    });
    ultimos.forEach((inventario, confinamentoId) => {
      const grupo = porId.get(confinamentoId);
      if (!grupo) return;
      const saldoCalculado = numero(grupo.cabecas);
      const total = Math.max(numero(inventario.cabecas_total), 0);
      const machos = inventario.machos == null ? numero(grupo.machos) : numero(inventario.machos);
      const femeas = inventario.femeas == null ? numero(grupo.femeas) : numero(inventario.femeas);
      grupo.cabecas = total;
      grupo.machos = Math.min(machos, total);
      grupo.femeas = Math.min(femeas, Math.max(total - grupo.machos, 0));
      grupo.nao_informado = Math.max(total - grupo.machos - grupo.femeas, 0);
      grupo.inventario_em = inventario.data_referencia;
      grupo.diferenca_ledger = total - saldoCalculado;
      grupo.fonte_inventario = inventario.fonte;
    });
    return [...porId.values()].filter((grupo) => grupo.cabecas > 0 || grupo.lotes > 0);
  }

  function idsAtivosConfinamento(operacoes) {
    return new Set((operacoes || [])
      .filter((operacao) => ehConfinamento(operacao) && estaAtiva(operacao))
      .map((operacao) => operacao.id));
  }

  function filtrarExposicaoAtiva(exposicao, operacoes) {
    const ids = idsAtivosConfinamento(operacoes);
    return (exposicao || []).filter((item) => ids.has(item.operacao_id));
  }

  function filtrarPendenciasConfinamento(pendencias) {
    return (pendencias || []).filter((item) => ehConfinamento(item.operacoes || item.operacao));
  }

  function codigosRateio(texto) {
    return [...String(texto || '').matchAll(/CF\s*-\s*\d{2}\s*-\s*\d{3}/gi)]
      .map((item) => item[0].replace(/\s/g, '').replace(/-(\d{3})$/, '-$1').toUpperCase());
  }

  function filtrarCoberturasAtivas(posicoes, operacoes) {
    const codigosAtivos = new Set((operacoes || [])
      .filter((operacao) => ehConfinamento(operacao) && estaAtiva(operacao))
      .map((operacao) => String(operacao.codigo || '').toUpperCase()));
    return (posicoes || []).filter((posicao) => {
      if (!['aberta', 'rolada'].includes(String(posicao.status || '').toLowerCase())) return false;
      if (String(posicao.categoria || '').toLowerCase() === 'especulacao') return false;
      const codigos = codigosRateio(posicao.negocio_rateio);
      return codigos.some((codigo) => codigosAtivos.has(codigo));
    });
  }

  return {
    agruparPorConfinamento,
    aplicarInventarios,
    ehConfinamento,
    filtrarCoberturasAtivas,
    filtrarExposicaoAtiva,
    filtrarPendenciasConfinamento,
    resumirLotes,
    sexoNormalizado,
  };
}));
