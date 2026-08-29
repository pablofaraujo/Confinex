/* Contrato de atualização do Painel Boi Gordo. Não grava Supabase. */
(function (global) {
  'use strict';

  function normalizarDados(payload) {
    if (!payload || typeof payload !== 'object' || !payload.atualizadoEm) {
      throw new Error('Resposta do painel sem data válida');
    }
    if (!payload.fonte || !Array.isArray(payload.indicadores) || !Array.isArray(payload.curvaBGI)) {
      throw new Error('Resposta do painel sem indicadores ou curva');
    }
    return payload;
  }

  function estaDefasado(data, agora, dias) {
    var dataAtualizacao = new Date(data.atualizadoEm.replace(' ', 'T'));
    return !Number.isFinite(dataAtualizacao.getTime()) ||
      agora.getTime() - dataAtualizacao.getTime() > dias * 86400000;
  }

  function formatarDataFonte(valor) {
    if (!valor) return 'data não informada';
    var normalizada = String(valor).replace(' ', 'T');
    var data = new Date(normalizada);
    if (!Number.isFinite(data.getTime())) return String(valor);
    return data.toLocaleString('pt-BR', { dateStyle: 'short', timeStyle: 'short' });
  }

  function resumoFontes(dados) {
    if (!dados.atualizadoEmB3) return 'fonte: ' + dados.fonte;
    return 'B3: ' + formatarDataFonte(dados.atualizadoEmB3) +
      ' · demais referências: ' + formatarDataFonte(dados.referenciasAtualizadasEm);
  }

  function criarAtualizador({ buscar, aplicar, fallback, agora, limiteDias }) {
    var emAndamento = null;
    var atual = fallback;
    return {
      atual: function () { return atual; },
      defasado: function () { return estaDefasado(atual, agora(), limiteDias); },
      atualizar: function () {
        if (emAndamento) return emAndamento;
        emAndamento = Promise.resolve().then(buscar).then(function (payload) {
          atual = normalizarDados(payload);
          var defasado = estaDefasado(atual, agora(), limiteDias);
          aplicar(atual, false, defasado);
          return { dados: atual, fonte: 'remota', defasado: defasado };
        }).catch(function () {
          var defasado = estaDefasado(atual, agora(), limiteDias);
          aplicar(atual, true, defasado);
          return { dados: atual, fonte: 'último dado válido', defasado: defasado };
        }).finally(function () { emAndamento = null; });
        return emAndamento;
      }
    };
  }

  global.PainelBoiGordo = {
    normalizarDados: normalizarDados,
    estaDefasado: estaDefasado,
    formatarDataFonte: formatarDataFonte,
    resumoFontes: resumoFontes,
    criarAtualizador: criarAtualizador
  };
}(typeof globalThis !== 'undefined' ? globalThis : this));
