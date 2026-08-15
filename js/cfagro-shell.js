/* ============================================================
   CFAgro shell — sidebar fixa de navegação (mesma em todo módulo).
   Carregar com defer em toda página do ecossistema (exceto as
   incluindo Confinex e OCR Pesagem), e também no
   boi-gordo-portfolio (repo separado) para manter a navegação
   consistente — por isso os links são absolutos, não relativos.
   Estilos: design/components.css (seção Shell). Docs: DESIGN.md
   ============================================================ */
(function(){
'use strict';

var BASE_PRODUCAO = 'https://pablofaraujo.github.io/Confinex/';
var LOCAL = /^(localhost|127\.0\.0\.1|\[::1\])$/.test(location.hostname);
var BASE = LOCAL ? new URL('./', location.href).href : BASE_PRODUCAO;

var NAV = [
  { href:'./',              rotulo:'Visão Geral',  icone:'grid' },
  { href:'./confinados.html', rotulo:'Resumo confinados', icone:'shield' },
  { sec:'Operações' },
  { href:'./confinex.html', rotulo:'Confinex',     icone:'calculator' },
  { href:'./confinamento.html', rotulo:'Operação confinamento', icone:'curral' },
  { href:'./fazenda-ametista.html', rotulo:'Fazenda Ametista', icone:'porteira' },
  // O portfólio é um app CFAgro separado, mas navega na mesma janela. Somente
  // ferramentas de terceiros usam ext:true e abrem outra aba.
  { href:'https://pablofaraujo.github.io/boi-gordo-portfolio/', rotulo:'Portfolio B3', icone:'briefcase' },
  { href:'./bgi.html',      rotulo:'BGI',           icone:'chart' },
  { href:'./bb.html',       rotulo:'Boi Balança',   icone:'scale' },
  { href:'./abate.html',    rotulo:'Abate',         icone:'scan' },
  { href:'./ocr-pesagem.html',rotulo:'OCR Pesagem', icone:'scan' },
  { sep:true },
  { href:'https://monitoring.livestock.datamars.com/', rotulo:'Datamars Livestock', icone:'tag', ext:true },
  { href:'https://app.agronota.com.br/', rotulo:'AgroNota', icone:'file', ext:true },
  { href:'https://www.sidagro.ima.mg.gov.br/portaldoprodutor/login.jsf', rotulo:'IMA / SIDAGRO', icone:'building', ext:true },
  { href:'./painel-boi-gordo.html', rotulo:'Painel Boi Gordo', icone:'chart' },
  { href:'./financeiro.html',rotulo:'Financeiro',    icone:'wallet' },
  { sec:'Parcerias' },
  { href:'./parcerias.html', rotulo:'Resumo',       icone:'handshake' },
  { href:'./parceria-ricardo.html',rotulo:'Ricardo', icone:'pessoa' },
  { href:'./parceria-xande.html',  rotulo:'Xande',   icone:'pessoa' },
  { sec:'Gestão' },
  { href:'./pendencias.html',rotulo:'Pendências',    icone:'checklist' },
  { href:'./revisoes.html', rotulo:'Revisões',      icone:'edit' },
  { href:'./eventos.html',  rotulo:'Eventos',       icone:'calendar' },
  { sec:'Sistema' },
  { href:'./ops.html',      rotulo:'Agentes / Ops', icone:'settings' }
];

var ICON_PATHS = { grid:'<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>', shield:'<path d="M12 3 20 6v5c0 5-3.4 8.3-8 10-4.6-1.7-8-5-8-10V6z"/><path d="m8.5 12 2.2 2.2 4.8-4.8"/>', calculator:'<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8 11h2m2 0h2m2 0h0M8 15h2m2 0h2m2 0h0M8 19h2m2 0h2m2 0h0"/>', curral:'<path d="M3 21V9l9-5 9 5v12M3 12h18M7 12v9m10-9v9M7 16h10"/>', porteira:'<path d="M4 21V6m16 15V6M4 8h16M4 18h16M6 9l12 8M18 9 6 17"/>', briefcase:'<rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M3 12h18M10 12v2h4v-2"/>', chart:'<path d="M4 19V5M4 19h17M8 15l3-4 3 2 5-7"/>', scale:'<path d="M12 3v18M5 6h14M7 6 3 14h8zM17 6l-4 8h8zM8 21h8"/>', scan:'<path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3"/><rect x="9" y="9" width="6" height="6" rx="1"/>', tag:'<path d="M20 13 13 20 4 11V4h7z"/><circle cx="8" cy="8" r="1"/>', file:'<path d="M6 3h8l4 4v14H6zM14 3v5h5M9 13h6M9 17h6"/>', building:'<path d="M4 21V4h16v17M8 8h2m2 0h2m2 0h2M8 12h2m2 0h2m2 0h2M8 16h2m2 0h2m2 0h2"/>', wallet:'<path d="M4 6h15a2 2 0 0 1 2 2v10H4a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h13"/><path d="M16 12h5"/>', handshake:'<path d="m3 12 4-4 5 2 5-4 4 4-4 4-4-2-4 4z"/>', pessoa:'<circle cx="12" cy="8" r="3"/><path d="M5 21c0-4 2.7-7 7-7s7 3 7 7"/>', checklist:'<rect x="4" y="3" width="16" height="18" rx="2"/><path d="m8 9 1 1 2-2m2 1h3m-6 5 1 1 2-2m2 1h3"/>', edit:'<path d="M4 20h4L19 9a2 2 0 0 0-3-3L5 16zM13 7l3 3"/>', calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M7 3v4m10-4v4M3 10h18"/>', settings:'<circle cx="12" cy="12" r="3"/><path d="M19 12h2M3 12h2M12 3v2m0 14v2"/>' };
function iconSvg(nome){ return '<svg class="shell-icon" aria-hidden="true" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round">'+(ICON_PATHS[nome]||ICON_PATHS.grid)+'</svg>'; }

function resolveHref(href){
  if(/^https?:\/\//.test(href)) return href;
  return BASE + href.replace(/^\.\//,'');
}

function montar(){
  if(document.body.classList.contains('has-shell')) return;

  // move o conteúdo atual da página para <main class="shell-content">
  var main = document.createElement('main');
  main.className = 'shell-content';
  while(document.body.firstChild) main.appendChild(document.body.firstChild);

  // detecta se estamos num repo/app externo (ex.: boi-gordo-portfolio) —
  // nesse caso nenhum link "interno" bate por nome de arquivo, e o próprio
  // item externo correspondente fica marcado como atual
  var onPortfolio = /\/boi-gordo-portfolio\//.test(location.pathname);
  var aqui = onPortfolio ? '' : (location.pathname.split('/').pop() || 'index.html');
  var ehVisaoGeral = !onPortfolio && aqui === 'index.html';

  var aside = document.createElement('aside');
  aside.className = 'shell-side';
  aside.innerHTML =
    NAV.map(function(n){
      if(n.sec) return '<div class="shell-sec">'+n.sec+'</div>';
      if(n.sep) return '<div class="shell-sep" aria-hidden="true"></div>';
      var full = resolveHref(n.href);
      var arquivo = full.split('#')[0].split('/').pop() || 'index.html';
      var portfolio = n.rotulo === 'Portfolio B3';
      var ativa = onPortfolio
        ? (portfolio ? ' ativa' : '')
        : (!portfolio && !n.ext && n.href.indexOf('#')<0 && arquivo===aqui ? ' ativa' : '');
      var alvo = n.ext ? ' target="_blank" rel="noopener"' : '';
      var ext = n.ext ? '<span class="ext">↗</span>' : '';
      return '<a class="shell-link'+ativa+'" href="'+full+'"'+alvo+'><span class="shell-icon-wrap">'+iconSvg(n.icone)+'</span>'+n.rotulo+ext+'</a>';
    }).join('');

  var topo = document.createElement('header');
  topo.className = 'shell-top';
  topo.innerHTML =
    '<a class="shell-brand" href="'+BASE+'"><img src="'+BASE+'confinex-logo.jpg" alt="Logo Confinex"><span>CONFINEX</span></a>'+
    '<div class="shell-context">Ecossistema pecuário CFAgro</div>'+
    (ehVisaoGeral ? '<div class="shell-actions" aria-label="Ações da sessão">'+
      '<button type="button" class="shell-action" data-shell-atualizar hidden>Atualizar</button>'+
      '<button type="button" class="shell-action" data-shell-sair hidden>Sair</button>'+
    '</div>' : '');

  document.body.prepend(topo, aside);
  document.body.appendChild(main);
  document.body.classList.add('has-shell');

  var atualizar = topo.querySelector('[data-shell-atualizar]');
  var sair = topo.querySelector('[data-shell-sair]');
  if(!atualizar || !sair) return;
  atualizar.addEventListener('click', function(){
    if(typeof window.carregar === 'function') window.carregar();
    else location.reload();
  });
  sair.addEventListener('click', function(){ if(typeof window.sair === 'function') window.sair(); });
  function revelarAcoesComSessao(tentativa){
    if(!(window.db && window.db.auth)){
      if(tentativa < 40) setTimeout(function(){ revelarAcoesComSessao(tentativa + 1); }, 250);
      return;
    }
    window.db.auth.getSession().then(function(resultado){
      if(resultado && resultado.data && resultado.data.session){
        atualizar.hidden = false;
        sair.hidden = false;
      }
    }).catch(function(){ /* Sem sessão confirmada, as ações continuam ocultas. */ });
  }
  revelarAcoesComSessao(0);
}

if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', montar);
else montar();
})();
