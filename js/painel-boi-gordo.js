/* Contrato de atualização do Painel Boi Gordo. Não grava Supabase. */
(function (global) {
  'use strict';

  function normalizarDados(payload) {
    if (!payload || typeof payload !== 'object' || !payload.atualizadoEm) {
      throw new Error('Resposta do painel sem data válida');
    }
    if (!Array.isArray(payload.indicadores) || !Array.isArray(payload.curvaBGI)) {
      throw new Error('Resposta do painel sem indicadores ou curva');
    }
    return payload;
  }

  function estaDefasado(data, agora, dias) {
    var dataAtualizacao = new Date(data.atualizadoEm.replace(' ', 'T'));
    return !Number.isFinite(dataAtualizacao.getTime()) ||
      agora.getTime() - dataAtualizacao.getTime() > dias * 86400000;
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
          aplicar(atual, false);
          return { dados: atual, fonte: 'remota', defasado: false };
        }).catch(function () {
          aplicar(atual, true);
          return { dados: atual, fonte: 'último dado válido', defasado: estaDefasado(atual, agora(), limiteDias) };
        }).finally(function () { emAndamento = null; });
        return emAndamento;
      }
    };
  }

  global.PainelBoiGordo = { normalizarDados: normalizarDados, estaDefasado: estaDefasado, criarAtualizador: criarAtualizador };
}(typeof globalThis !== 'undefined' ? globalThis : this));

