(function(){
'use strict';
var itens = [];
var el = function(id){ return document.getElementById(id); };
var esc = function(v){ return CFAgro.esc(v); };
// Pendências é uma projeção de leitura. Nunca use `*` nestas tabelas: o
// executor mantém colunas de lease, hashes e vínculos que não pertencem ao
// navegador nem à interface humana.
var DRAFT_PENDENCIAS_COLUNAS = 'id,criado_em,atualizado_em,status,tipo_operacao,codigo_sugerido,entidade_final_tipo,dados_extraidos,campos_pendentes,agente,origem_canal,contexto_nome,escopo';
var ACAO_PENDENCIAS_COLUNAS = 'id,criado_em,atualizado_em,status,acao_tipo,entidade_tipo,entidade_codigo,resumo,payload,erro,agente,usuario_solicitante,canal,origem_canal,contexto_nome,escopo,executavel';

function classeStatus(status){
  var s = String(status || '').toLowerCase();
  return /erro|falha/.test(s) ? 'b-red' : /conclu|execut|valid|corrigid/.test(s) ? 'b-green' : 'b-amber';
}

function linkDestino(item){
  return '<a class="btn mini sec" href="'+esc(item.destino.href)+'">'+esc(item.acao)+'</a>';
}

function render(){
  var origem = el('filtroOrigem').value;
  var busca = el('filtroTexto').value.trim().toLowerCase();
  var filtrados = itens.filter(function(item){
    var base = [item.resumo,item.contexto,item.status,item.origem].join(' ').toLowerCase();
    return (origem === 'todas' || item.origem === origem) && (!busca || base.includes(busca));
  });
  el('listaPendencias').innerHTML = filtrados.map(function(item){
    return '<tr><td>'+esc(item.origem)+'</td><td class="wrap">'+esc(item.resumo)+'</td><td class="wrap">'+esc(item.contexto)+'</td><td><span class="badge '+classeStatus(item.status)+'">'+esc(item.status)+'</span></td><td>'+CFAgro.fmtDT(item.data)+'</td><td>'+linkDestino(item)+'</td></tr>';
  }).join('') || '<tr><td colspan="6" class="wrap">Nenhuma pendência corresponde aos filtros.</td></tr>';

  var contagem = {Revisões:0,Ações:0,Documentos:0,Planejamento:0};
  itens.forEach(function(item){ contagem[item.origem] = (contagem[item.origem] || 0) + 1; });
  el('kpis').innerHTML = [
    ['Total',itens.length],
    ['Revisões',contagem.Revisões],
    ['Ações',contagem.Ações],
    ['Documentos',contagem.Documentos],
    ['Planejamento',contagem.Planejamento]
  ].map(function(k){ return '<div class="kpi"><div class="l">'+k[0]+'</div><div class="v">'+k[1]+'</div></div>'; }).join('');
}

async function carregar(){
  el('subtitle').textContent = 'Carregando itens que exigem ação…';
  el('erroFontes').textContent = '';
  var respostas = await Promise.all([
    db.from('operation_drafts').select(DRAFT_PENDENCIAS_COLUNAS).limit(200),
    db.from('pending_actions').select(ACAO_PENDENCIAS_COLUNAS).limit(200),
    db.from('pendencias_documentos').select('*').in('status',['aguardando_vendedor','revisao_necessaria']).limit(200),
    db.from('operacoes').select('id,codigo,status,tipo_negocio').limit(500),
    db.from('confinex_avaliacoes').select('id,operacao_id,codigo,status,confinex_estimativas(id,versao,premissas,resultado)').limit(500)
  ]);
  var fechadosRevisoes = new Set(['realizado','rejeitado','cancelado']);
  var fechadosAcoes = new Set(['executado','rejeitado','cancelado','expirado']);
  var abertos = function(lista, fechados){
    return (lista || []).filter(function(item){
      return !fechados.has(String(item.status || '').toLowerCase());
    });
  };
  var documentosAbertos = function(lista){
    return (lista || []).filter(function(item){
      return new Set(['aguardando_vendedor','revisao_necessaria']).has(String(item.status || '').toLowerCase());
    });
  };
  var falhas = respostas.filter(function(resposta){ return resposta.error; });
  var pendenciasOperacionais = CFAgroGestao.pendenciasLegiveis(
    respostas[0].error ? [] : abertos(respostas[0].data, fechadosRevisoes),
    respostas[1].error ? [] : abertos(respostas[1].data, fechadosAcoes),
    respostas[2].error ? [] : documentosAbertos(respostas[2].data)
  );
  var planejamentos = respostas[3].error || respostas[4].error ? [] :
    CFAgroGestao.planejamentosRentabilidadePendentes(respostas[3].data, respostas[4].data);
  itens = pendenciasOperacionais.concat(planejamentos).sort(function(a,b){
    return String(b.data || '').localeCompare(String(a.data || '')) ||
      String(a.contexto || '').localeCompare(String(b.contexto || ''), 'pt-BR');
  });
  if(falhas.length === respostas.length){
    el('subtitle').textContent = CFAgroGestao.erroLegivel(falhas[0].error);
  }else{
    el('subtitle').textContent = 'Itens que exigem conferência ou próxima ação';
    if(falhas.length){
      el('erroFontes').textContent = falhas.length+' fonte(s) não puderam ser carregadas. Os demais itens continuam disponíveis.';
    }
  }
  render();
}

el('filtroOrigem').addEventListener('change', render);
el('filtroTexto').addEventListener('input', render);
el('entrarBtn').addEventListener('click', function(){ entrar(); });
CFAgro.authInit(carregar);
})();
