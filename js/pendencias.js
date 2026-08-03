(function(){
'use strict';
var itens = [];
var el = function(id){ return document.getElementById(id); };
var esc = function(v){ return CFAgro.esc(v); };

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

  var contagem = {Revisões:0,Ações:0,Documentos:0};
  itens.forEach(function(item){ contagem[item.origem] = (contagem[item.origem] || 0) + 1; });
  el('kpis').innerHTML = [
    ['Total',itens.length],
    ['Revisões',contagem.Revisões],
    ['Ações',contagem.Ações],
    ['Documentos',contagem.Documentos]
  ].map(function(k){ return '<div class="kpi"><div class="l">'+k[0]+'</div><div class="v">'+k[1]+'</div></div>'; }).join('');
}

async function carregar(){
  el('subtitle').textContent = 'Carregando itens que exigem ação…';
  el('erroFontes').textContent = '';
  var respostas = await Promise.all([
    db.from('operation_drafts').select('*').limit(200),
    db.from('pending_actions').select('*').limit(200),
    db.from('pendencias_documentos').select('*').limit(200)
  ]);
  var fechados = new Set(['executado','rejeitado','cancelado','validado','dispensado']);
  var abertos = function(lista){ return (lista || []).filter(function(item){ return !fechados.has(String(item.status || '').toLowerCase()); }); };
  var falhas = respostas.filter(function(resposta){ return resposta.error; });
  itens = CFAgroGestao.pendenciasLegiveis(
    respostas[0].error ? [] : abertos(respostas[0].data),
    respostas[1].error ? [] : abertos(respostas[1].data),
    respostas[2].error ? [] : abertos(respostas[2].data)
  );
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
