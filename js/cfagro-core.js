/* ============================================================
   CFAgro core — client Supabase único, formatadores e auth.
   Carregar DEPOIS do CDN do supabase-js. Docs: DESIGN.md
   Uso típico no fim do script da página:
     CFAgro.authInit(carregar);
   ============================================================ */
(function(){
  'use strict';

  const SUPA_URL = 'https://fkmdzwjmjlmxqotznvgq.supabase.co';
  const SUPA_KEY = 'sb_publishable_mNwlWLAaJOVoXpmlD7ShYg_-Nqyy0bT'; // chave pública — RLS protege
  const db = window.supabase ? window.supabase.createClient(SUPA_URL, SUPA_KEY) : null;

  // ---------- Formatadores ----------
  const fmtR$  = v => v==null ? '—' : Number(v).toLocaleString('pt-BR',{style:'currency',currency:'BRL',maximumFractionDigits:0});
  const fmtR$2 = v => v==null ? '—' : Number(v).toLocaleString('pt-BR',{style:'currency',currency:'BRL',minimumFractionDigits:2});
  const fmtN   = (v,d=0) => v==null ? '—' : Number(v).toLocaleString('pt-BR',{maximumFractionDigits:d,minimumFractionDigits:d});
  const fmtD   = s => { if(!s) return '—'; const [y,m,d]=String(s).slice(0,10).split('-'); return `${d}/${m}/${y.slice(2)}`; };
  const fmtDT  = s => s ? new Date(s).toLocaleString('pt-BR',{dateStyle:'short',timeStyle:'short'}) : '—';
  const addDias = (iso,dias) => { if(!iso) return null; const d=new Date(iso+'T12:00:00'); d.setDate(d.getDate()+(Number(dias)||0)); return d.toISOString().slice(0,10); };
  const cls = v => v>0 ? 'pos' : (v<0 ? 'neg' : '');
  const esc = t => String(t??'').replace(/&/g,'&amp;').replace(/</g,'&lt;');
  const minAtras = s => s ? Math.round((Date.now()-new Date(s))/60000) : null;

  // ---------- Auth compartilhado ----------
  // Convenção de markup: #login (tela), #app (conteúdo), #email, #senha, #loginErr.
  // A sessão Supabase é uma só para todo o ecossistema.
  function authInit(onReady){
    window.entrar = async function(){
      const {error} = await db.auth.signInWithPassword({
        email: document.getElementById('email').value.trim(),
        password: document.getElementById('senha').value
      });
      if(error){ document.getElementById('loginErr').textContent = 'Falha no login: '+error.message; return; }
      iniciar();
    };
    window.sair = async function(){ await db.auth.signOut(); location.reload(); };
    function iniciar(){
      const l=document.getElementById('login'), a=document.getElementById('app');
      if(l) l.style.display='none';
      if(a) a.style.display='block';
      if(onReady) onReady();
    }
    db.auth.getSession().then(({data})=>{ if(data.session) iniciar(); });
  }

  // ---------- Exposição ----------
  window.CFAgro = { db, fmtR$, fmtR$2, fmtN, fmtD, fmtDT, addDias, cls, esc, minAtras, authInit };
  // Atalhos globais — as páginas usam os helpers sem prefixo.
  Object.assign(window, { db, fmtR$, fmtR$2, fmtN, fmtD, fmtDT, addDias, cls, esc, minAtras });
})();
