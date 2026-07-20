/* ============================================================
   CFAgro shell — sidebar fixa de navegação (mesma em todo módulo).
   Carregar com defer em toda página do ecossistema (exceto as
   ainda fora do DS: confinex.html e ocr-pesagem.html), e também no
   boi-gordo-portfolio (repo separado) para manter a navegação
   consistente — por isso os links são absolutos, não relativos.
   Estilos: design/components.css (seção Shell). Docs: DESIGN.md
   ============================================================ */
(function(){
'use strict';

var BASE = 'https://pablofaraujo.github.io/Confinex/';

var NAV = [
  { href:'./',              rotulo:'Geral',        icone:'⌂' },
  { href:'./confinados.html', rotulo:'Confinados', icone:'🛡️' },
  { sec:'Operações' },
  { href:'./confinex.html', rotulo:'Confinex',     icone:'🧮' },
  { href:'./confinamento.html', rotulo:'Confinamento', icone:'🐮' },
  { href:'./fazenda-ametista.html', rotulo:'Fazenda Ametista', icone:'🌾' },
  { href:'https://pablofaraujo.github.io/boi-gordo-portfolio/', rotulo:'Portfolio B3', icone:'🗂', ext:true },
  { href:'./bgi.html',      rotulo:'BGI',           icone:'📈' },
  { href:'./bb.html',       rotulo:'Boi Balança',   icone:'⚖️' },
  { href:'./abate.html',    rotulo:'Abate',         icone:'🥩' },
  { href:'./ocr-pesagem.html',rotulo:'OCR Pesagem', icone:'📷' },
  { href:'https://monitoring.livestock.datamars.com/', rotulo:'Datamars Livestock', icone:'🏷️', ext:true },
  { href:'https://app.agronota.com.br/', rotulo:'AgroNota', icone:'🧾', ext:true },
  { href:'https://www.sidagro.ima.mg.gov.br/portaldoprodutor/login.jsf', rotulo:'IMA / SIDAGRO', icone:'🏛️', ext:true },
  { href:'./painel-boi-gordo.html', rotulo:'Painel Boi Gordo', icone:'📊' },
  { href:'./#fluxo',        rotulo:'Financeiro',    icone:'💰' },
  { sec:'Parcerias' },
  { href:'./parcerias.html', rotulo:'Resumo',       icone:'🤝' },
  { href:'./parceria-ricardo.html',rotulo:'Ricardo', icone:'🐂' },
  { href:'./parceria-xande.html',  rotulo:'Xande',   icone:'🐄' },
  { sec:'Gestão' },
  { href:'./#pendencias',   rotulo:'Pendências',    icone:'📋' },
  { href:'./#eventos',      rotulo:'Eventos',       icone:'📅' },
  { sec:'Sistema' },
  { href:'./ops.html',      rotulo:'Agentes / Ops', icone:'⚙️' }
];

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

  var aside = document.createElement('aside');
  aside.className = 'shell-side';
  aside.innerHTML =
    '<a class="shell-logo" href="'+BASE+'"><img src="'+BASE+'confinex-logo.jpg" alt=""><b>CFAgro</b></a>' +
    NAV.map(function(n){
      if(n.sec) return '<div class="shell-sec">'+n.sec+'</div>';
      var full = resolveHref(n.href);
      var arquivo = full.split('#')[0].split('/').pop() || 'index.html';
      var ativa = onPortfolio
        ? (n.ext ? ' ativa' : '')
        : (!n.ext && n.href.indexOf('#')<0 && arquivo===aqui ? ' ativa' : '');
      var alvo = n.ext ? ' target="_blank" rel="noopener"' : '';
      var ext = n.ext ? '<span class="ext">↗</span>' : '';
      return '<a class="shell-link'+ativa+'" href="'+full+'"'+alvo+'><span>'+n.icone+'</span>'+n.rotulo+ext+'</a>';
    }).join('');

  document.body.prepend(aside);
  document.body.appendChild(main);
  document.body.classList.add('has-shell');
}

if(document.readyState==='loading') document.addEventListener('DOMContentLoaded', montar);
else montar();
})();
