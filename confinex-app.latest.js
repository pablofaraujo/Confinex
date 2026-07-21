// src/confinex-entry.jsx
import React from "react";
import { createRoot } from "react-dom/client";

// confinex_work.jsx
import { useEffect, useState, useMemo, useRef } from "react";
import { Fragment, jsx, jsxs } from "react/jsx-runtime";
var T = {
  bg: "#F5F6F8",
  surface: "#FFFFFF",
  card: "#FFFFFF",
  border: "#DFE4EA",
  accent: "#142B42",
  accentDim: "#FFF8D9",
  text: "#172536",
  muted: "#9AA5B1",
  label: "#5F6D7A",
  green: "#16A34A",
  red: "#DC2626",
  sc: ["#142B42", "#2563EB", "#D97706", "#7C3AED", "#DC2626"]
};
var APP_STORAGE_KEY = "confinex:last-state:v3";
var RESTORE_STORAGE_KEY = "confinex:restore-before-reset:v1";
var VERSION_STORAGE_KEY = "confinex:named-versions:v1";
var LEGACY_STORAGE_KEYS = ["confinex:last-state:v2"];
var css = `
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700&family=DM+Mono:wght@300;400;500&display=swap');
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{background:${T.bg};color:${T.text};font-family:'Plus Jakarta Sans',sans-serif;font-size:13px;line-height:1.55;min-height:100vh;overflow-x:hidden}
.app{max-width:1180px;min-width:0;margin:0 auto;padding:28px 16px 100px}
.hdr{display:flex;align-items:flex-start;gap:20px;margin-bottom:32px;padding-bottom:20px;border-bottom:1px solid ${T.border}}
.logo{font-family:'Plus Jakarta Sans',sans-serif;font-size:24px;font-weight:700;color:${T.accent};letter-spacing:-.5px;line-height:1}
.logo-sub{font-size:10px;color:${T.muted};letter-spacing:2px;text-transform:uppercase;margin-top:3px;font-weight:400}
.sec{min-width:0;max-width:100%;background:${T.card};border:1px solid ${T.border};border-top:3px solid #F2C500;border-radius:14px;padding:22px;margin-bottom:12px;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.sec-t{font-family:'Plus Jakarta Sans',sans-serif;font-weight:600;font-size:10px;letter-spacing:1.5px;text-transform:uppercase;color:${T.accent};margin-bottom:18px;display:flex;align-items:center;gap:10px}
.sec-t::after{content:'';flex:1;height:1px;background:${T.border}}
.sec-t.nm::after{display:none}
.g2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
.g3{display:grid;grid-template-columns:1fr 1fr 1fr;gap:14px}
.g4{display:grid;grid-template-columns:1fr 1fr 1fr 1fr;gap:14px}
.g2>*,.g3>*,.g4>*{min-width:0}
@media(max-width:680px){.g2,.g3,.g4{grid-template-columns:1fr 1fr}}
@media(max-width:680px){.hdr{flex-direction:column;gap:14px}.hdr>div:last-child{margin-left:0!important;width:100%;flex-wrap:wrap}}
@media(max-width:520px){.g2,.g3,.g4{grid-template-columns:1fr}.g2>.fld,.g3>.fld,.g4>.fld{grid-column:auto!important}.app{padding-left:8px;padding-right:8px}.sec{padding:18px}.logo{font-size:21px}}
.fld{display:flex;min-width:0;flex-direction:column;gap:5px}
.lbl{font-size:10px;font-weight:600;color:${T.label};letter-spacing:.5px;text-transform:uppercase}
input,select{min-width:0;max-width:100%;background:${T.surface};border:1px solid ${T.border};border-radius:8px;color:${T.text};font-family:'DM Mono',monospace;font-size:13px;padding:9px 12px;width:100%;outline:none;transition:border-color .15s;box-shadow:0 1px 2px rgba(0,0,0,.04)}
input:focus,select:focus{border-color:${T.accent};box-shadow:0 0 0 3px ${T.accentDim}}
input[readonly]{color:${T.accent};font-weight:600;cursor:default;background:${T.accentDim};border-color:transparent}
select option{background:white}
.hint{font-size:10px;color:${T.muted};margin-top:3px}
.dvdr{height:1px;background:${T.border};margin:18px 0}
.tg{display:flex;gap:6px;flex-wrap:wrap}
.tb{background:${T.surface};border:1.5px solid ${T.border};border-radius:8px;color:${T.label};cursor:pointer;font-family:'Plus Jakarta Sans',sans-serif;font-size:11px;font-weight:500;padding:7px 13px;transition:all .15s;line-height:1.2}
.tb.on{background:${T.accentDim};border-color:${T.accent};color:${T.accent};font-weight:700}
.tb:hover:not(.on){border-color:#BDC3CC;color:${T.text}}
.sc-bar{display:flex;align-items:stretch;border-bottom:1px solid ${T.border};overflow-x:auto;gap:2px;padding:0 22px}
.sc-tab{background:transparent;border:none;border-bottom:2.5px solid transparent;color:${T.muted};cursor:pointer;font-family:'Plus Jakarta Sans',sans-serif;font-size:12px;font-weight:600;padding:10px 14px;transition:all .15s;white-space:nowrap;display:flex;align-items:center;gap:5px;margin-bottom:-1px}
.sc-tab.on{border-bottom-color:var(--c);color:var(--c)}
.sc-tab:hover:not(.on){color:${T.label}}
.sc-del{background:none;border:none;color:${T.red};cursor:pointer;font-size:14px;opacity:.35;padding:0 2px;transition:opacity .15s}
.sc-del:hover{opacity:1}
.sc-add{background:none;border:1.5px dashed ${T.border};border-radius:8px;color:${T.muted};cursor:pointer;font-size:18px;padding:2px 12px;transition:all .15s;align-self:center;margin-left:4px;margin-bottom:4px;line-height:1.5}
.sc-add:hover{border-color:${T.accent};color:${T.accent}}
.sc-body{padding:22px}
.warn{background:#FEF9EC;border:1px solid #F0D58C;border-radius:8px;color:#92640A;font-size:11px;padding:9px 13px;margin-top:10px}
.ck{display:flex;align-items:center;gap:9px;cursor:pointer;user-select:none}
.ck input[type=checkbox]{width:15px;height:15px;accent-color:${T.accent};cursor:pointer;flex-shrink:0}
.ck span{font-size:12px;color:${T.label}}
.calc-btn{background:${T.accent};border:none;border-radius:10px;color:#fff;cursor:pointer;font-family:'Plus Jakarta Sans',sans-serif;font-size:14px;font-weight:700;padding:15px;width:100%;margin-top:18px;transition:opacity .15s,transform .1s;letter-spacing:.2px}
.calc-btn:hover{opacity:.9;transform:translateY(-1px)}
.calc-btn:active{transform:none}
.res-wrap{margin-top:30px}
.res-ttl{font-family:'Plus Jakarta Sans',sans-serif;font-size:20px;font-weight:700;color:${T.text};margin-bottom:4px}
.res-sub{font-size:11px;color:${T.muted};margin-bottom:20px}
.rank-row{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:20px}
.rcard{flex:1;min-width:155px;background:${T.surface};border-radius:12px;padding:16px;border:1.5px solid ${T.border};box-shadow:0 1px 4px rgba(0,0,0,.05)}
.rcard.best{border-color:var(--c);box-shadow:0 2px 12px rgba(0,0,0,.08)}
.rn{font-family:'Plus Jakarta Sans',sans-serif;font-size:26px;font-weight:700;color:var(--c);line-height:1}
.rname{font-size:12px;font-weight:600;color:${T.text};margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.rtype{font-size:9px;color:${T.muted};letter-spacing:1px;text-transform:uppercase;margin-top:1px}
.rval{font-family:'DM Mono',monospace;font-size:18px;font-weight:500;margin-top:9px}
.rval.pos{color:${T.green}}
.rval.neg{color:${T.red}}
.rsub{font-size:10px;color:${T.muted};font-family:'DM Mono',monospace;margin-top:2px}
.tbl-wrap{overflow-x:auto}
.cmp-tbl{width:100%;border-collapse:collapse;font-size:12px}
.cmp-tbl th{padding:9px 13px;font-size:9px;font-weight:600;color:${T.muted};letter-spacing:.8px;text-transform:uppercase;border-bottom:1px solid ${T.border};text-align:right;white-space:nowrap;background:${T.bg}}
.cmp-tbl th:first-child{text-align:left;min-width:190px}
.cmp-tbl th.sc-th{color:var(--c);font-family:'Plus Jakarta Sans',sans-serif;font-size:11px;font-weight:700}
.cmp-tbl td{padding:9px 13px;border-bottom:1px solid ${T.border};font-family:'DM Mono',monospace;text-align:right;color:${T.text};white-space:nowrap}
.cmp-tbl td:first-child{text-align:left;font-family:'Plus Jakarta Sans',sans-serif;color:${T.label};font-size:11px}
.cmp-tbl tr:last-child td{border-bottom:none}
.cmp-tbl tr.grp td{padding-top:14px;border-top:2px solid ${T.border};color:${T.accent};font-weight:600;font-size:10px;letter-spacing:.8px;text-transform:uppercase;font-family:'Plus Jakarta Sans',sans-serif;background:${T.bg}}
.cmp-tbl tr.tot td{font-size:13px;font-weight:700;background:#F9FAFB}
.pos{color:${T.green}}
.neg{color:${T.red}}
.hi{font-weight:700}
`;
var fN = (n, d = 2) => !isFinite(n) || isNaN(n) ? "\u2014" : n.toFixed(d).replace(".", ",");
var fR = (n) => !isFinite(n) || isNaN(n) ? "\u2014" : "R$\xA0" + n.toLocaleString("pt-BR", { minimumFractionDigits: 0, maximumFractionDigits: 0 });
var fP = (n) => !isFinite(n) || isNaN(n) ? "\u2014" : fN(n, 1) + "%";
var fAt = (n) => !isFinite(n) || isNaN(n) ? "\u2014" : fN(n, 1) + "\xA0@";
var pctInput = (value, fallbackPct = 0) => {
  const parsed = parseFloat(value);
  return Number.isFinite(parsed) ? parsed / 100 : fallbackPct;
};
function divisorCapim(modo) {
  if (modo === "700g") return 15 / (15 - 0.7) * 30;
  if (modo === "800g") return 15 / (15 - 0.8) * 30;
  if (modo === "1kg") return 15 / (15 - 1) * 30;
  return null;
}
function calcArrobas({ peso, sexo, modoCapim, limCapim, descBezerro, limBezerro }) {
  const limC = parseFloat(limCapim) || 300;
  const limB = parseFloat(limBezerro) || 280;
  const bezDesc = sexo === "femea" && descBezerro && peso >= limB ? 10 / 15 : 0;
  if (modoCapim === "sem") {
    return Math.max(0, peso / 2 / 15 - bezDesc);
  }
  if (modoCapim === "10kg") {
    const aplicar = peso >= limC;
    const carcaca = peso * 0.5;
    const capimKg = aplicar ? 10 : 0;
    return Math.max(0, (carcaca - capimKg) / 15 - bezDesc);
  }
  const div = divisorCapim(modoCapim);
  if (peso < limC) {
    return Math.max(0, peso / 2 / 15 - bezDesc);
  }
  return Math.max(0, peso / div - bezDesc);
}
function taxaMensalFluxos(fluxos) {
  const validos = (fluxos || []).filter((f) => Number.isFinite(f.valor) && Number.isFinite(f.meses));
  if (!validos.some((f) => f.valor < 0) || !validos.some((f) => f.valor > 0)) return null;
  const vp = (taxa) => validos.reduce((total, fluxo) => total + fluxo.valor / Math.pow(1 + taxa, Math.max(Number(fluxo.meses) || 0, 0)), 0);
  let baixo = -0.9999;
  let alto = 1;
  let vpBaixo = vp(baixo);
  let vpAlto = vp(alto);
  while (vpBaixo * vpAlto > 0 && alto < 1e6) {
    alto *= 2;
    vpAlto = vp(alto);
  }
  if (!Number.isFinite(vpBaixo) || !Number.isFinite(vpAlto) || vpBaixo * vpAlto > 0) return null;
  for (let i = 0; i < 100; i += 1) {
    const meio = (baixo + alto) / 2;
    const vpMeio = vp(meio);
    if (Math.abs(vpMeio) < 1e-7) return meio;
    if (vpBaixo * vpMeio <= 0) {
      alto = meio;
      vpAlto = vpMeio;
    } else {
      baixo = meio;
      vpBaixo = vpMeio;
    }
  }
  return (baixo + alto) / 2;
}
function calcImpactoOperacaoFinanceira(sc, { custoDinheiroMensal, dataRecebimento, mesesCapital, baseRentabilidade, lucroLiquidoSemOperacao }) {
  const tipoAdiantamento = sc.tipoAdiantamento === "recebimento" ? "recebimento" : "capital";
  const valorSolicitado = sc.simularAdiantamento ? Math.max(parseFloat(sc.valorAdiantamento) || 0, 0) : 0;
  const baseCalculo = Math.max(baseRentabilidade || 0, 0) || (tipoAdiantamento === "capital" ? valorSolicitado : 0);
  const diasAdiantamento = valorSolicitado > 0 ? diasEntreISO(sc.dataAdiantamento, dataRecebimento) : 0;
  const fatorCusto = custoDinheiroMensal * diasAdiantamento / 30;
  const valorTerminalSemOperacao = Math.max(baseCalculo + lucroLiquidoSemOperacao, 0);
  const valorMaximoAntecipacao = tipoAdiantamento === "recebimento" && fatorCusto >= 0 ? valorTerminalSemOperacao / (1 + fatorCusto) : valorSolicitado;
  const valorAdiantamento = tipoAdiantamento === "recebimento" ? Math.min(valorSolicitado, valorMaximoAntecipacao) : valorSolicitado;
  const custoAdiantamento = valorAdiantamento * fatorCusto;
  const lucroLiquido = lucroLiquidoSemOperacao - custoAdiantamento;
  const rTliqSemAdiantamento = baseCalculo > 0 ? lucroLiquidoSemOperacao / baseCalculo * 100 : 0;
  const rTliq = baseCalculo > 0 ? lucroLiquido / baseCalculo * 100 : 0;
  const rMliqSemAdiantamento = mesesCapital > 0 ? (Math.pow(Math.max(1 + rTliqSemAdiantamento / 100, 0), 1 / mesesCapital) - 1) * 100 : 0;
  let rMliq = mesesCapital > 0 ? (Math.pow(Math.max(1 + rTliq / 100, 0), 1 / mesesCapital) - 1) * 100 : 0;
  let valorRecebidoAntecipado = 0;
  let saldoRecebimentoFinal = valorTerminalSemOperacao;
  let mesesAteAntecipacao = mesesCapital;
  if (tipoAdiantamento === "recebimento" && valorAdiantamento > 0 && baseCalculo > 0) {
    valorRecebidoAntecipado = valorAdiantamento;
    saldoRecebimentoFinal = Math.max(valorTerminalSemOperacao - valorAdiantamento - custoAdiantamento, 0);
    mesesAteAntecipacao = Math.max(mesesCapital - diasAdiantamento / 30, 0);
    const taxaFluxo = taxaMensalFluxos([
      { valor: -baseCalculo, meses: 0 },
      { valor: valorRecebidoAntecipado, meses: mesesAteAntecipacao },
      { valor: saldoRecebimentoFinal, meses: mesesCapital }
    ]);
    if (Number.isFinite(taxaFluxo)) rMliq = taxaFluxo * 100;
  }
  return {
    tipoAdiantamento,
    valorAdiantamentoSolicitado: valorSolicitado,
    valorAdiantamento,
    dataAdiantamento: sc.dataAdiantamento || "",
    dataRecebimentoAdiantamento: dataRecebimento,
    diasAdiantamento,
    mesesAteAntecipacao,
    custoAdiantamento,
    valorMaximoAntecipacao,
    valorRecebidoAntecipado,
    saldoRecebimentoFinal,
    lucroLiquidoSemAdiantamento: lucroLiquidoSemOperacao,
    rTliqSemAdiantamento,
    rMliqSemAdiantamento,
    lucroLiquido,
    rTliq,
    rMliq,
    impactoAdiantamentoMensal: rMliq - rMliqSemAdiantamento
  };
}
function perdaKm(km) {
  if (km <= 0) return 0;
  return 0.07 + (km > 300 ? Math.ceil((km - 300) / 100) * 5e-3 : 0);
}
function boisPorCarretaPadrao(sexo) {
  return sexo === "femea" ? "70" : "65";
}
function isoHoje() {
  const agora = /* @__PURE__ */ new Date();
  const ano = agora.getFullYear();
  const mes = String(agora.getMonth() + 1).padStart(2, "0");
  const dia = String(agora.getDate()).padStart(2, "0");
  return `${ano}-${mes}-${dia}`;
}
function addDiasISO(dataISO, dias) {
  if (!dataISO) return "";
  const data = new Date(`${dataISO}T12:00:00`);
  if (Number.isNaN(data.getTime())) return "";
  data.setDate(data.getDate() + Math.max(0, Math.round(Number(dias) || 0)));
  return data.toISOString().slice(0, 10);
}
function diasEntreISO(dataInicial, dataFinal) {
  if (!dataInicial || !dataFinal) return 0;
  const inicio = new Date(`${dataInicial}T12:00:00`);
  const fim = new Date(`${dataFinal}T12:00:00`);
  if (Number.isNaN(inicio.getTime()) || Number.isNaN(fim.getTime())) return 0;
  return Math.max(0, Math.round((fim.getTime() - inicio.getTime()) / 864e5));
}
function fmtData(dataISO) {
  if (!dataISO) return "\u2014";
  const [ano, mes, dia] = dataISO.split("-");
  return ano && mes && dia ? `${dia}/${mes}/${ano}` : "\u2014";
}
var B3_MONTH_CODES = ["F", "G", "H", "J", "K", "M", "N", "Q", "U", "V", "X", "Z"];
var B3_MONTH_LABELS = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"];
function compararContratosB3(a, b) {
  const contratoA = String(a || "").toUpperCase().match(/^BGI([FGHJKMNQUVXZ])(\d{2})$/);
  const contratoB = String(b || "").toUpperCase().match(/^BGI([FGHJKMNQUVXZ])(\d{2})$/);
  if (!contratoA || !contratoB) return String(a || "").localeCompare(String(b || ""), "pt-BR");
  const ordemA = parseInt(contratoA[2], 10) * 12 + B3_MONTH_CODES.indexOf(contratoA[1]);
  const ordemB = parseInt(contratoB[2], 10) * 12 + B3_MONTH_CODES.indexOf(contratoB[1]);
  return ordemA - ordemB;
}
function contratoB3PorData(dataISO) {
  if (!dataISO) return "";
  const data = new Date(`${dataISO}T12:00:00`);
  if (Number.isNaN(data.getTime())) return "";
  const mes = data.getMonth();
  const ano = data.getFullYear();
  return `BGI${B3_MONTH_CODES[mes]}${String(ano).slice(-2)}`;
}
function mesSaidaLabel(dataISO) {
  if (!dataISO) return "\u2014";
  const data = new Date(`${dataISO}T12:00:00`);
  if (Number.isNaN(data.getTime())) return "\u2014";
  return `${B3_MONTH_LABELS[data.getMonth()]}/${String(data.getFullYear()).slice(-2)}`;
}
function googleMapsUrl(origem, destino) {
  const base = "https://www.google.com/maps/dir/?api=1&travelmode=driving";
  const params = new URLSearchParams();
  if (origem) params.set("origin", origem);
  if (destino) params.set("destination", destino);
  return `${base}&${params.toString()}`;
}
var SHEETS_BACKEND_STORAGE_KEY = "confinex:sheets-backend-url";
var sheetsJsonpSeq = 0;
function getStoredSheetsBackendUrl() {
  try {
    return window.CONFINEX_SHEETS_API_URL || localStorage.getItem(SHEETS_BACKEND_STORAGE_KEY) || "";
  } catch {
    return window.CONFINEX_SHEETS_API_URL || "";
  }
}
function storeSheetsBackendUrl(url) {
  try {
    localStorage.setItem(SHEETS_BACKEND_STORAGE_KEY, String(url || "").trim());
  } catch {
  }
}
function sheetsJsonp(url, params = {}) {
  const endpoint = String(url || "").trim();
  if (!endpoint) return Promise.reject(new Error("missing-backend-url"));
  return new Promise((resolve, reject) => {
    const callbackName = `__confinexSheetsCb${Date.now()}_${sheetsJsonpSeq++}`;
    const script = document.createElement("script");
    const cleanup = () => {
      try {
        delete window[callbackName];
      } catch {
        window[callbackName] = void 0;
      }
      script.remove();
    };
    const timer = setTimeout(() => {
      cleanup();
      reject(new Error("backend-timeout"));
    }, 12e3);
    window[callbackName] = (data) => {
      clearTimeout(timer);
      cleanup();
      resolve(data);
    };
    const query = new URLSearchParams({ ...params, callback: callbackName, t: String(Date.now()) });
    script.onerror = () => {
      clearTimeout(timer);
      cleanup();
      reject(new Error("backend-load-error"));
    };
    script.src = `${endpoint}${endpoint.includes("?") ? "&" : "?"}${query.toString()}`;
    document.head.appendChild(script);
  });
}
// PATCH Fase 0 (R2): resposta legível — sem no-cors. O Apps Script publicado
// como "Anyone" responde com CORS após o redirect; agora erro é erro de verdade.
var CONFINEX_DEVICE_KEY = "confinex:device-id:v1";
function confinexDeviceId() {
  try {
    let id = localStorage.getItem(CONFINEX_DEVICE_KEY);
    if (!id) {
      id = `dev-${Math.random().toString(36).slice(2, 8)}`;
      localStorage.setItem(CONFINEX_DEVICE_KEY, id);
    }
    return id;
  } catch {
    return "dev-anon";
  }
}
async function sheetsPost(url, action, payload) {
  const endpoint = String(url || "").trim();
  if (!endpoint) throw new Error("missing-backend-url");
  const body = new URLSearchParams();
  body.set("action", action);
  body.set("payload", JSON.stringify(payload || {}));
  const response = await fetch(endpoint, { method: "POST", body, redirect: "follow" });
  let data = null;
  try {
    data = await response.json();
  } catch {
    throw new Error(`backend-bad-response (HTTP ${response.status})`);
  }
  if (!data || data.ok !== true) {
    const err = new Error((data && data.error) || `backend-error (HTTP ${response.status})`);
    err.backend = data;
    throw err;
  }
  return data;
}
// Envio síncrono ao fechar a página (pagehide) — melhor esforço
function sheetsBeacon(url, action, payload) {
  try {
    const endpoint = String(url || "").trim();
    if (!endpoint || !navigator.sendBeacon) return false;
    const body = new URLSearchParams();
    body.set("action", action);
    body.set("payload", JSON.stringify(payload || {}));
    return navigator.sendBeacon(endpoint, body);
  } catch {
    return false;
  }
}
function normalizeSheetsState(data) {
  const state = data?.state || data?.payload || data || {};
  return {
    lote: state.lote,
    cenarios: state.cenarios,
    confinamentos: state.confinamentos,
    historico: state.historico,
    scAtivo: state.scAtivo,
    resultados: state.resultados
  };
}
function numeroBR(texto) {
  if (typeof texto === "number") return Number.isFinite(texto) ? texto : NaN;
  if (!texto) return NaN;
  const limpo = String(texto).replace(/[^\d,.-]/g, "").replace(/\./g, "").replace(",", ".");
  const valor = parseFloat(limpo);
  return Number.isFinite(valor) ? valor : NaN;
}
function extrairNumeroB3(payload) {
  const candidatos = [];
  const visitar = (valor) => {
    if (valor == null) return;
    if (typeof valor === "number" || typeof valor === "string") {
      const numero = numeroBR(valor);
      if (Number.isFinite(numero) && numero > 100 && numero < 800) candidatos.push(numero);
      return;
    }
    if (Array.isArray(valor)) {
      valor.forEach(visitar);
      return;
    }
    if (typeof valor === "object") {
      Object.entries(valor).forEach(([chave, item]) => {
        if (/price|prc|last|ultimo|ult|close|ajuste|settlement|valor/i.test(chave)) visitar(item);
      });
    }
  };
  visitar(payload);
  return candidatos.find(Number.isFinite);
}
function extrairCepeaBoi(html) {
  const texto = String(html || "").replace(/\s+/g, " ");
  const match = texto.match(/INDICADOR DO BOI GORDO CEPEA\/ESALQ\s+(\d{2}\/\d{2}\/\d{4})\s+([\d.,]+)/i);
  if (!match) return null;
  const preco = numeroBR(match[2]);
  if (!Number.isFinite(preco)) return null;
  const [dia, mes, ano] = match[1].split("/");
  return {
    preco,
    fonte: "CEPEA/ESALQ B3 (referencia fisica)",
    symbol: "CEPEA_BOI_GORDO",
    data: `${ano}-${mes}-${dia}T12:00:00.000Z`,
    referenciaFisica: true
  };
}
async function fetchComTimeout(url, options = {}, timeoutMs = 4500) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}
async function buscarB3Publica(contrato) {
  const url = `https://cotacao.b3.com.br/mds/api/v1/InstrumentPriceFluctuation/${encodeURIComponent(contrato)}`;
  const response = await fetchComTimeout(url);
  if (!response.ok) throw new Error("B3 nao respondeu");
  const data = await response.json();
  if (data?.BizSts?.cd === "NOK") throw new Error(data?.BizSts?.desc || "Contrato indisponivel na B3");
  const preco = extrairNumeroB3(data);
  if (!Number.isFinite(preco)) throw new Error("Preco B3 indisponivel");
  return {
    preco,
    fonte: "B3",
    symbol: contrato,
    data: (data?.Msg?.dtTm ? new Date(data.Msg.dtTm.replace(" ", "T")).toISOString() : (/* @__PURE__ */ new Date()).toISOString())
  };
}
async function buscarB3HistoricoIntradiario(contrato) {
  const url = `https://cotacao.b3.com.br/mds/api/v1/DailyFluctuationHistory/${encodeURIComponent(contrato)}`;
  const response = await fetchComTimeout(url);
  if (!response.ok) throw new Error("Historico B3 nao respondeu");
  const data = await response.json();
  if (data?.BizSts?.cd === "NOK") throw new Error(data?.BizSts?.desc || "Historico B3 indisponivel");
  const cotacoes = data?.TradgFlr?.scty?.lstQtn || [];
  const ultima = [...cotacoes].reverse().find((item) => Number.isFinite(item?.closPric));
  const preco = ultima?.closPric;
  if (!Number.isFinite(preco)) throw new Error("Historico B3 sem preco");
  const dataBase = data?.TradgFlr?.date || (/* @__PURE__ */ new Date()).toISOString().slice(0, 10);
  return {
    preco,
    fonte: "B3 historico intradiario",
    symbol: data?.TradgFlr?.scty?.symb || contrato,
    data: `${dataBase}T${ultima?.dtTm || "12:00:00"}`
  };
}
async function buscarYahooB3(contrato) {
  const symbol = `${contrato}.SA`;
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${encodeURIComponent(symbol)}?interval=1d&range=5d`;
  const response = await fetchComTimeout(url);
  if (!response.ok) throw new Error("Yahoo nao respondeu");
  const data = await response.json();
  const result = data?.chart?.result?.[0];
  const meta = result?.meta || {};
  const close = result?.indicators?.quote?.[0]?.close || [];
  const ultimoClose = [...close].reverse().find((v) => Number.isFinite(v));
  const preco = Number.isFinite(meta.regularMarketPrice) ? meta.regularMarketPrice : ultimoClose;
  if (!Number.isFinite(preco)) throw new Error("Cotacao Yahoo indisponivel");
  return {
    preco,
    fonte: "Yahoo Finance",
    symbol,
    data: new Date((meta.regularMarketTime || Date.now() / 1e3) * 1e3).toISOString()
  };
}
async function buscarCepeaBoiGordo() {
  const alvo = "https://www.cepea.org.br/br/indicador/boi-gordo.aspx";
  const urls = [
    alvo,
    `https://api.allorigins.win/raw?url=${encodeURIComponent(alvo)}`
  ];
  let ultimoErro = null;
  for (const url of urls) {
    try {
      const response = await fetchComTimeout(url, {}, 3500);
      if (!response.ok) throw new Error("CEPEA nao respondeu");
      const cotacao = extrairCepeaBoi(await response.text());
      if (cotacao) return cotacao;
    } catch (err) {
      ultimoErro = err;
    }
  }
  throw ultimoErro || new Error("CEPEA indisponivel");
}
async function buscarPrecoB3PorContrato(contrato) {
  const erros = [];
  for (const buscar of [buscarB3Publica, buscarB3HistoricoIntradiario, buscarYahooB3]) {
    try {
      return await buscar(contrato);
    } catch (err) {
      erros.push(err?.message || String(err));
    }
  }
  try {
    const cepea = await buscarCepeaBoiGordo();
    return {
      ...cepea,
      fonte: `${cepea.fonte}; ${contrato} sem cotacao futura automatica`
    };
  } catch (err) {
    erros.push(err?.message || String(err));
  }
  throw new Error(erros.join(" | "));
}
function calcCenario(lote, sc) {
  const N = parseFloat(lote.qtd) || 0;
  const pm = parseFloat(lote.pesoMedio) || 0;
  const precoCompra = parseFloat(lote.precoCompra) || 0;
  const baldeio = parseFloat(lote.baldeio) || 0;
  const arrobasCompra = calcArrobas({
    peso: pm,
    sexo: lote.sexo,
    modoCapim: lote.modoCapim,
    limCapim: lote.limCapim,
    descBezerro: lote.descBezerro,
    limBezerro: lote.limBezerro
  });
  const custoCompra = arrobasCompra * precoCompra * N + (parseFloat(lote.baldeio) || 0);
  const km = parseFloat(sc.km) || 0;
  const pKm = parseFloat(sc.precoPorKm) || 0;
  const pedIda = parseFloat(sc.pedIda) || 0;
  const pedVol = parseFloat(sc.pedVolta) || 0;
  const boisPorCarreta = Math.max(parseFloat(sc.boisPorCarreta) || parseFloat(boisPorCarretaPadrao(lote.sexo)), 1);
  const qtdCarretas = N > 0 ? Math.ceil(N / boisPorCarreta) : 0;
  const freteDeles = sc.respFrete === "confinamento";
  const fretePorCarretaBruto = km * 2 * pKm + pedIda + pedVol;
  const fretePorCarreta = freteDeles ? 0 : fretePorCarretaBruto;
  const freteBrutoTotal = fretePorCarretaBruto * qtdCarretas;
  const freteTotal = freteDeles ? 0 : sc.respFrete === "dividido" ? freteBrutoTotal / 2 : freteBrutoTotal;
  const fPorCab = N > 0 ? freteTotal / N : 0;
  const pctPerda = sc.perdaManual !== "" && sc.perdaManual !== void 0 ? parseFloat(sc.perdaManual) / 100 : perdaKm(km);
  const pesoChegada = pm * (1 - pctPerda);
  const rec = parseFloat(sc.recuperacao) || 0;
  const pesoProc = pesoChegada + (pm - pesoChegada) * (rec / 100);
  const prazoPagtoCompra = parseFloat(lote.prazoPagtoCompra) || 0;
  if (sc.tipo === "revenda") {
    const precoVenda2 = parseFloat(sc.precoRevenda) || 0;
    const arrobasVenda = calcArrobas({
      peso: pm,
      sexo: lote.sexo,
      modoCapim: sc.modoCapimVenda || "sem",
      limCapim: lote.limCapim,
      descBezerro: lote.descBezerro,
      limBezerro: lote.limBezerro
    });
    const receita2 = arrobasVenda * precoVenda2 * N;
    const custos2 = custoCompra + freteTotal;
    const lucro2 = receita2 - custos2;
    const diasVenda = parseFloat(sc.diasPagamento) || 0;
    const diasTotal2 = Math.max(diasVenda, 1);
    const meses2 = Math.max(diasTotal2 / 30, 0.05);
    const diasCapital2 = Math.max(diasTotal2 - prazoPagtoCompra, 0);
    const freteCapital2 = sc.freteNoAcerto ? 0 : freteTotal;
    const investInicial2 = custoCompra + freteCapital2;
    const diasFreteCapital2 = freteCapital2 > 0 ? diasTotal2 : 0;
    const diasCapitalPonderado2 = investInicial2 > 0 ? (custoCompra * diasCapital2 + freteCapital2 * diasFreteCapital2) / investInicial2 : 0;
    const mesesCapital2 = Math.max(diasCapitalPonderado2 / 30, 0.05);
    const rT2 = investInicial2 > 0 ? lucro2 / investInicial2 * 100 : 0;
    const rM2 = mesesCapital2 > 0 ? (Math.pow(1 + rT2 / 100, 1 / mesesCapital2) - 1) * 100 : 0;
    const custoDinheiroMensal2 = pctInput(lote.custoDinheiro, 0);
    const custoDinheiroCompra2 = custoCompra * (Math.pow(1 + custoDinheiroMensal2, diasCapital2 / 30) - 1);
    const custoDinheiroFrete2 = freteCapital2 * (Math.pow(1 + custoDinheiroMensal2, diasFreteCapital2 / 30) - 1);
    const dataRecebimento2 = addDiasISO(sc.dataEntrada, diasTotal2);
    const custoDinheiroOperacao2 = custoDinheiroCompra2 + custoDinheiroFrete2;
    const lucroLiquidoSemAdiantamento2 = lucro2 - custoDinheiroOperacao2;
    const impactoFinanceiro2 = calcImpactoOperacaoFinanceira(sc, {
      custoDinheiroMensal: custoDinheiroMensal2,
      dataRecebimento: dataRecebimento2,
      mesesCapital: mesesCapital2,
      baseRentabilidade: investInicial2,
      lucroLiquidoSemOperacao: lucroLiquidoSemAdiantamento2
    });
    const custoDinheiroTotal2 = custoDinheiroOperacao2 + impactoFinanceiro2.custoAdiantamento;
    const fatorVP2 = custoDinheiroMensal2 > 0 ? Math.pow(1 + custoDinheiroMensal2, mesesCapital2) : 1;
    const vpArroba2 = precoVenda2 / fatorVP2;
    const receitaVP2 = receita2 / fatorVP2;
    const arrobasCompraTotal2 = arrobasCompra * N;
    const baldeioTotal2 = parseFloat(lote.baldeio) || 0;
    const precoCompraVpMax2 = arrobasCompraTotal2 > 0 ? (receitaVP2 - freteTotal - baldeioTotal2) / arrobasCompraTotal2 : 0;
    const resultadoVP2 = receitaVP2 - custos2;
    const margemCompraVp2 = precoCompraVpMax2 - precoCompra;
    const arrobasPostasCab2 = pesoProc * 0.5 / 15;
    const arrobasPostasTotal2 = arrobasPostasCab2 * N;
    const custoArrobaPosta2 = arrobasPostasTotal2 > 0 ? (custoCompra + freteTotal) / arrobasPostasTotal2 : 0;
    return {
      N,
      pm,
      arrobasCompra,
      arrobasEntrega: arrobasVenda,
      arrobasAbate: arrobasVenda,
      pesoChegada,
      pesoProc,
      pesoAbate: pm,
      carcacaKg: 0,
      rcFinal: 0,
      pctPerda: pctPerda * 100,
      precoVendaLiq: precoVenda2,
      vpArroba: vpArroba2,
      faturamentoBruto: receita2,
      valorFunrural: 0,
      valorFinpec: 0,
      receita: receita2,
      receitaVP: receitaVP2,
      custoCompra,
      freteTotal,
      fretePorCarreta,
      boisPorCarreta,
      qtdCarretas,
      respFrete: sc.respFrete,
      custoCont: 0,
      custos: custos2,
      lucro: lucro2,
      investInicial: investInicial2,
      capitalCompra: custoCompra,
      capitalFrete: freteCapital2,
      rentTotal: rT2,
      rentMensal: rM2,
      diasTotal: diasTotal2,
      diasPag: diasVenda,
      meses: meses2,
      diasCapital: diasCapital2,
      mesesCapital: mesesCapital2,
      fretePorCab: fPorCab,
      ganhoTotal: 0,
      arrobasPostasCab: arrobasPostasCab2,
      custoArrobaPosta: custoArrobaPosta2,
      kgCarcacaProduzidaCab: 0,
      arrobasProduzidasCab: 0,
      custoArrobaLiquidaProduzida: 0,
      custoArrobaMarginal: 0,
      fretePorArrobaProduzida: 0,
      custoProducaoFretePorArroba: 0,
      custoDinheiroTotal: custoDinheiroTotal2,
      custoDinheiroOperacao: custoDinheiroOperacao2,
      custoDinheiroCompra: custoDinheiroCompra2,
      custoDinheiroFrete: custoDinheiroFrete2,
      ...impactoFinanceiro2,
      precoCompraVpMax: precoCompraVpMax2,
      resultadoVP: resultadoVP2,
      margemCompraVp: margemCompraVp2,
      tipo: "revenda",
      _id: sc.id
    };
  }
  const dias = parseFloat(sc.diasCiclo) || 90;
  const diasPag = parseFloat(sc.diasPagamento) || 0;
  const gmd = parseFloat(sc.gmd) || 0;
  const pesoBase = sc.refGanho === "origem" ? pm : sc.refGanho === "proc" ? pesoProc : pesoChegada;
  const ganhoTotal = gmd * dias;
  const pesoAbate = pesoBase + ganhoTotal;
  const rcF = parseFloat(sc.rcFinal) / 100 || 0.53;
  const carcacaKg = pesoAbate * rcF;
  const arrobasAbate = carcacaKg / 15;
  const rcEnt = parseFloat(sc.rcEntrada) / 100 || 0.5;
  const pesoRefEnt = sc.refEntrada === "chegada" ? pesoChegada : pm;
  const arrobasEntrada = pesoRefEnt * rcEnt / 15;
  let custoCont = 0;
  if (sc.modalidade === "arroba") {
    custoCont = (parseFloat(sc.custoArrobaProd) || 0) * Math.max(0, arrobasAbate - arrobasEntrada);
  } else if (sc.modalidade === "ms") {
    const cMs = parseFloat(sc.custoMS) || 0;
    const pctMS2 = parseFloat(sc.consumoMS) / 100 || 0.023;
    const cadm = parseFloat(sc.custoAdm) || 0;
    const cprot = parseFloat(sc.protocolo) || 0;
    const pesoMedioConf2 = (pesoChegada + pesoAbate) / 2;
    const consumoDiarioKg = pesoMedioConf2 * pctMS2;
    const tonsMS = consumoDiarioKg * dias / 1e3;
    custoCont = tonsMS * cMs + cadm * dias + cprot;
  } else if (sc.modalidade === "diaria") {
    custoCont = (parseFloat(sc.custoDiaria) || 0) * dias;
  }
  const custoContTotal = custoCont * N;
  const fur = pctInput(sc.funrural, 2e-3);
  const finpec = pctInput(sc.finpec, 0);
  let precoVendaBruto;
  if (sc.modoPreco === "balcao") {
    precoVendaBruto = parseFloat(sc.precoBalcao) || 0;
  } else {
    const bolsa = parseFloat(sc.precoBolsa) || 0;
    const basePct = parseFloat(sc.baseDesc) || 0;
    precoVendaBruto = bolsa * (1 - basePct / 100);
  }
  const arrobasRef = sc.modalidade === "parceria" ? arrobasEntrada : arrobasAbate;
  const faturamentoBruto = arrobasRef * precoVendaBruto * N;
  const valorFunrural = faturamentoBruto * fur;
  const valorFinpec = faturamentoBruto * finpec;
  const receita = faturamentoBruto - valorFunrural - valorFinpec;
  const precoVenda = arrobasRef > 0 && N > 0 ? receita / (arrobasRef * N) : 0;
  const custos = custoCompra + freteTotal + custoContTotal;
  const lucro = receita - custos;
  const diasTotal = dias + diasPag;
  const meses = Math.max(diasTotal / 30, 0.05);
  const diasCapital = Math.max(diasTotal - prazoPagtoCompra, 0);
  const freteCapital = sc.freteNoAcerto ? 0 : freteTotal;
  const investInicial = custoCompra + freteCapital;
  const diasFreteCapital = freteCapital > 0 ? diasTotal : 0;
  const diasCapitalPonderado = investInicial > 0 ? (custoCompra * diasCapital + freteCapital * diasFreteCapital) / investInicial : 0;
  const mesesCapital = Math.max(diasCapitalPonderado / 30, 0.05);
  const rT = investInicial > 0 ? lucro / investInicial * 100 : 0;
  const rM = mesesCapital > 0 ? (Math.pow(1 + rT / 100, 1 / mesesCapital) - 1) * 100 : 0;
  const custoDinheiroMensal = pctInput(lote.custoDinheiro, 0);
  const custoDinheiroCompra = custoCompra * (Math.pow(1 + custoDinheiroMensal, diasCapital / 30) - 1);
  const custoDinheiroFrete = freteCapital * (Math.pow(1 + custoDinheiroMensal, diasFreteCapital / 30) - 1);
  const dataRecebimentoAdiantamento = addDiasISO(sc.dataEntrada, diasTotal);
  const custoDinheiroOperacao = custoDinheiroCompra + custoDinheiroFrete;
  const lucroLiquidoSemAdiantamento = lucro - custoDinheiroOperacao;
  const impactoFinanceiro = calcImpactoOperacaoFinanceira(sc, {
    custoDinheiroMensal,
    dataRecebimento: dataRecebimentoAdiantamento,
    mesesCapital,
    baseRentabilidade: investInicial,
    lucroLiquidoSemOperacao: lucroLiquidoSemAdiantamento
  });
  const custoDinheiroTotal = custoDinheiroOperacao + impactoFinanceiro.custoAdiantamento;
  const fatorVP = custoDinheiroMensal > 0 ? Math.pow(1 + custoDinheiroMensal, mesesCapital) : 1;
  const vpArroba = precoVenda / fatorVP;
  const receitaVP = receita / fatorVP;
  const arrobasCompraTotal = arrobasCompra * N;
  const baldeioTotal = parseFloat(lote.baldeio) || 0;
  const precoCompraVpMax = arrobasCompraTotal > 0 ? (receitaVP - freteTotal - custoContTotal - baldeioTotal) / arrobasCompraTotal : 0;
  const resultadoVP = receitaVP - custos;
  const margemCompraVp = precoCompraVpMax - precoCompra;
  const pesoMedioConf = (pesoChegada + pesoAbate) / 2;
  const pctMS = parseFloat(sc.consumoMS) / 100 || 0;
  const msTotalKgCab = pesoMedioConf * pctMS * dias;
  const arrobasPostasCab = pesoProc * 0.5 / 15;
  const arrobasPostasTotal = arrobasPostasCab * N;
  const custoArrobaPosta = arrobasPostasTotal > 0 ? (custoCompra + freteTotal) / arrobasPostasTotal : 0;
  const kgCarcacaInicialCab = pesoProc * 0.5;
  const kgCarcacaProduzidaCab = Math.max(carcacaKg - kgCarcacaInicialCab, 0);
  const arrobasProduzidasCab = kgCarcacaProduzidaCab / 15;
  const arrobasProduzidasTotal = arrobasProduzidasCab * N;
  const custoArrobaLiquidaProduzida = arrobasProduzidasTotal > 0 ? custoContTotal / arrobasProduzidasTotal : 0;
  const custoDiarioCab = dias > 0 && N > 0 ? custoContTotal / N / dias : 0;
  const kgCarcacaMarginalDia = Math.max(gmd * rcF, 0);
  const custoArrobaMarginal = kgCarcacaMarginalDia > 0 ? custoDiarioCab / kgCarcacaMarginalDia * 15 : 0;
  const fretePorArrobaProduzida = arrobasProduzidasTotal > 0 ? freteTotal / arrobasProduzidasTotal : 0;
  const custoProducaoFretePorArroba = arrobasProduzidasTotal > 0 ? (custoContTotal + freteTotal) / arrobasProduzidasTotal : 0;
  return {
    N,
    pm,
    arrobasCompra,
    arrobasEntrega: arrobasEntrada,
    arrobasAbate,
    pesoChegada,
    pesoProc,
    pesoAbate,
    carcacaKg,
    rcFinal: rcF * 100,
    pctPerda: pctPerda * 100,
    precoVendaLiq: precoVenda,
    vpArroba,
    faturamentoBruto,
    valorFunrural,
    valorFinpec,
    receita,
    receitaVP,
    custoCompra,
    freteTotal,
    fretePorCarreta,
    boisPorCarreta,
    qtdCarretas,
    respFrete: sc.respFrete,
    custoCont: custoContTotal,
    custos,
    lucro,
    investInicial,
    capitalCompra: custoCompra,
    capitalFrete: freteCapital,
    rentTotal: rT,
    rentMensal: rM,
    diasTotal,
    diasPag,
    meses,
    diasCapital,
    mesesCapital,
    fretePorCab: fPorCab,
    ganhoTotal,
    arrobasPostasCab,
    custoArrobaPosta,
    kgCarcacaProduzidaCab,
    arrobasProduzidasCab,
    custoArrobaLiquidaProduzida,
    custoArrobaMarginal,
    fretePorArrobaProduzida,
    custoProducaoFretePorArroba,
    msTotalKgCab: sc.modalidade === "ms" ? msTotalKgCab : 0,
    custoDinheiroTotal,
    custoDinheiroOperacao,
    custoDinheiroCompra,
    custoDinheiroFrete,
    ...impactoFinanceiro,
    precoCompraVpMax,
    resultadoVP,
    margemCompraVp,
    tipo: "confinamento",
    _id: sc.id
  };
}
var defaultSc = (i) => ({
  id: Date.now() + i,
  nome: i === 4 ? "Revenda" : `Confinamento ${i + 1}`,
  tipo: i === 4 ? "revenda" : "confinamento",
  km: "800",
  precoPorKm: "7.5",
  boisPorCarreta: "65",
  pedIda: "0",
  pedVolta: "0",
  perdaManual: "",
  respFrete: i === 4 ? "confinamento" : "meu",
  freteNoAcerto: false,
  simularAdiantamento: false,
  tipoAdiantamento: "capital",
  dataAdiantamento: isoHoje(),
  valorAdiantamento: "",
  recuperacao: "3",
  modalidade: "parceria",
  rcEntrada: "50",
  refEntrada: "origem",
  custoArrobaProd: "270",
  custoMS: "1450",
  consumoMS: "2.3",
  custoAdm: "2.50",
  protocolo: "20",
  custoDiaria: "17",
  refGanho: "chegada",
  gmd: "1.3",
  rcFinal: "53",
  dataEntrada: isoHoje(),
  diasCiclo: "110",
  diasPagamento: "0",
  modoPreco: "bolsa",
  precoBolsa: "350",
  contratoB3: "",
  cotacaoB3Fonte: "",
  cotacaoB3AtualizadaEm: "",
  origemFrete: "",
  destinoFrete: "",
  baseDesc: "0",
  precoBalcao: "300",
  funrural: "0.2",
  finpec: "0.0",
  finpecConfigurado: false,
  precoRevenda: "310",
  modoCapimVenda: "sem"
});
var CAMPOS_MODELO_CONFINAMENTO = [
  "tipo",
  "km",
  "precoPorKm",
  "boisPorCarreta",
  "pedIda",
  "pedVolta",
  "perdaManual",
  "respFrete",
  "freteNoAcerto",
  "simularAdiantamento",
  "tipoAdiantamento",
  "dataAdiantamento",
  "valorAdiantamento",
  "recuperacao",
  "modalidade",
  "rcEntrada",
  "refEntrada",
  "custoArrobaProd",
  "custoMS",
  "consumoMS",
  "custoAdm",
  "protocolo",
  "custoDiaria",
  "refGanho",
  "gmd",
  "rcFinal",
  "dataEntrada",
  "diasCiclo",
  "diasPagamento",
  "modoPreco",
  "origemFrete",
  "destinoFrete",
  "baseDesc",
  "precoBalcao",
  "funrural",
  "finpec",
  "finpecConfigurado",
  "precoRevenda",
  "modoCapimVenda"
];
function modeloFromSc(sc, nome) {
  const modelo = { id: Date.now(), nome: nome || sc.nome || "Confinamento" };
  CAMPOS_MODELO_CONFINAMENTO.forEach((k) => {
    modelo[k] = sc[k];
  });
  return modelo;
}
function scFromModelo(sc, modelo) {
  const next = { ...sc };
  CAMPOS_MODELO_CONFINAMENTO.forEach((k) => {
    if (modelo[k] !== void 0) next[k] = modelo[k];
  });
  next.nome = modelo.nome || sc.nome;
  return next;
}
var defaultLote = {
  codigoNegocio: "",
  grupoOrigemNome: "Confinamento",
  origemNome: "Fazenda Ametista",
  sexo: "macho",
  qtd: "65",
  pesoMedio: "350",
  precoCompra: "305",
  baldeio: "0",
  modoCapim: "10kg",
  limCapim: "300",
  descBezerro: false,
  limBezerro: "280",
  prazoPagtoCompra: "0",
  custoDinheiro: "2.0",
  cotacoesB3: {},
  cotacoesB3AtualizadasEm: ""
};
function contratoB3DoCenario(sc) {
  if (!sc || sc.tipo === "revenda" || sc.modoPreco !== "bolsa") return "";
  const dataSaida = addDiasISO(sc.dataEntrada, parseFloat(sc.diasCiclo) || 0);
  const sugerido = contratoB3PorData(dataSaida);
  return sc.modalidade === "parceria" ? sugerido : sc.contratoB3 || sugerido;
}
function contratosB3DaEvolucao(cenarios) {
  const contratos = /* @__PURE__ */ new Set();
  (cenarios || []).forEach((sc) => {
    if (!sc || sc.tipo === "revenda" || sc.modoPreco !== "bolsa") return;
    for (let dias = 60; dias <= 240; dias += 15) {
      const contrato = contratoB3PorData(addDiasISO(sc.dataEntrada, dias));
      if (contrato) contratos.add(contrato);
    }
  });
  return [...contratos];
}
function calcEvolucaoTempo(lote, sc) {
  if (!sc || sc.tipo === "revenda") return [];
  const diasAtual = Math.round(parseFloat(sc.diasCiclo) || 0);
  const prazos = /* @__PURE__ */ new Set();
  for (let dias = 60; dias <= 240; dias += 15) prazos.add(dias);
  if (diasAtual >= 60 && diasAtual <= 240) prazos.add(diasAtual);
  return [...prazos].sort((a, b) => a - b).map((dias) => {
    const dataSaida = addDiasISO(sc.dataEntrada, dias);
    const contrato = contratoB3PorData(dataSaida);
    const cotacao = lote.cotacoesB3?.[contrato];
    if (sc.modoPreco === "bolsa" && !(cotacao && parseFloat(cotacao.preco) > 0)) {
      return { dias, dataSaida, contrato, cotacao: null, resultado: null };
    }
    const scTeste = {
      ...sc,
      diasCiclo: String(dias),
      ...(sc.modoPreco === "bolsa" ? {
        contratoB3: contrato,
        precoBolsa: String(cotacao.preco),
        cotacaoB3Fonte: cotacao.fonte || "",
        cotacaoB3AtualizadaEm: cotacao.atualizadaEm || ""
      } : {})
    };
    try {
      return {
        dias,
        dataSaida,
        contrato: sc.modoPreco === "bolsa" ? contrato : "Balcão",
        cotacao: sc.modoPreco === "bolsa" ? parseFloat(cotacao.preco) : parseFloat(sc.precoBalcao) || 0,
        resultado: calcCenario(lote, scTeste)
      };
    } catch {
      return { dias, dataSaida, contrato, cotacao: null, resultado: null };
    }
  });
}
function normalizarMercadoB3(loteInformado, cenariosInformados) {
  const loteNovo = {
    ...defaultLote,
    ...loteInformado || {},
    cotacoesB3: { ...loteInformado?.cotacoesB3 || {} }
  };
  const cenariosNovos = (cenariosInformados || []).map((sc) => ({ ...sc }));
  cenariosNovos.forEach((sc) => {
    const contrato = contratoB3DoCenario(sc);
    if (!contrato) return;
    if (sc.modalidade === "parceria") sc.contratoB3 = contrato;
    const existente = loteNovo.cotacoesB3[contrato];
    const atualizadaEm = sc.cotacaoB3AtualizadaEm || "";
    if (!existente || atualizadaEm > (existente.atualizadaEm || "")) {
      loteNovo.cotacoesB3[contrato] = {
        preco: String(sc.precoBolsa || existente?.preco || "350"),
        fonte: sc.cotacaoB3Fonte || existente?.fonte || "Valor migrado do cenário",
        atualizadaEm
      };
    }
  });
  cenariosNovos.forEach((sc) => {
    const contrato = contratoB3DoCenario(sc);
    const cotacao = loteNovo.cotacoesB3[contrato];
    if (!contrato || !cotacao) return;
    sc.contratoB3 = contrato;
    sc.precoBolsa = String(cotacao.preco);
    sc.cotacaoB3Fonte = cotacao.fonte || "";
    sc.cotacaoB3AtualizadaEm = cotacao.atualizadaEm || "";
  });
  return { lote: loteNovo, cenarios: cenariosNovos };
}
function estadoPadraoLimpo() {
  const cenarios = [defaultSc(0)];
  const normalizado = normalizarMercadoB3({ ...defaultLote, cotacoesB3: {} }, cenarios);
  return {
    lote: normalizado.lote,
    cenarios: normalizado.cenarios,
    confinamentos: [],
    historico: [],
    scAtivo: 0,
    resultados: []
  };
}
function carregarVersoesNomeadas() {
  try {
    const parsed = JSON.parse(localStorage.getItem(VERSION_STORAGE_KEY) || "[]");
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}
function loadSavedState() {
  const fallback = estadoPadraoLimpo();
  try {
    const raw = localStorage.getItem(APP_STORAGE_KEY) || LEGACY_STORAGE_KEYS.map((k) => localStorage.getItem(k)).find(Boolean);
    if (!raw) return fallback;
    const saved = JSON.parse(raw);
    const savedLote = { ...defaultLote, ...saved.lote || {}, cotacoesB3: { ...saved.lote?.cotacoesB3 || {} } };
    const defaultBois = boisPorCarretaPadrao(savedLote.sexo);
    const savedCenarios = Array.isArray(saved.cenarios) && saved.cenarios.length ? saved.cenarios.slice(0, 5).map((sc, i) => {
      const next = { ...defaultSc(i), ...sc };
      if (!next.boisPorCarreta || next.boisPorCarreta === "35") next.boisPorCarreta = defaultBois;
      if (!next.funrural || next.funrural === "1.2") next.funrural = "0.2";
      if (!next.finpecConfigurado) next.finpec = "0.0";
      return next;
    }) : fallback.cenarios.map((sc) => ({ ...sc, boisPorCarreta: defaultBois }));
    const mercadoNormalizado = normalizarMercadoB3(savedLote, savedCenarios);
    const confinamentos = Array.isArray(saved.confinamentos) ? saved.confinamentos : [];
    const historico = Array.isArray(saved.historico) ? saved.historico : [];
    const resultados = Array.isArray(saved.resultados) && saved.resultados.length ? mercadoNormalizado.cenarios.map((sc) => {
      try {
        return calcCenario(mercadoNormalizado.lote, sc);
      } catch {
        return null;
      }
    }) : [];
    LEGACY_STORAGE_KEYS.forEach((k) => localStorage.removeItem(k));
    return {
      lote: mercadoNormalizado.lote,
      cenarios: mercadoNormalizado.cenarios,
      confinamentos,
      historico,
      scAtivo: Math.min(Math.max(parseInt(saved.scAtivo, 10) || 0, 0), savedCenarios.length - 1),
      resultados
    };
  } catch {
    return fallback;
  }
}
function F({ label, hint, children, span }) {
  return /* @__PURE__ */ jsxs("div", { className: "fld", style: span ? { gridColumn: `span ${span}` } : {}, children: [
    /* @__PURE__ */ jsx("label", { className: "lbl", children: label }),
    children,
    hint && /* @__PURE__ */ jsx("div", { className: "hint", children: hint })
  ] });
}
function Tg({ opts, val, set }) {
  return /* @__PURE__ */ jsx("div", { className: "tg", children: opts.map((o) => /* @__PURE__ */ jsx("button", { className: `tb ${val === o.v ? "on" : ""}`, onClick: () => set(o.v), children: o.l }, o.v)) });
}
function Ck({ checked, onChange, label }) {
  return /* @__PURE__ */ jsxs("label", { className: "ck", children: [
    /* @__PURE__ */ jsx("input", { type: "checkbox", checked, onChange: (e) => onChange(e.target.checked) }),
    /* @__PURE__ */ jsx("span", { children: label })
  ] });
}
function ScPanel({ sc, upd, sexo, custoDinheiro, resultado, confinamentos, modeloSelecionado, setModeloSelecionado, aplicarModelo, salvarModelo, atualizarModelo, apagarModelo, calcularDistancia, statusDistancia }) {
  const u = (k) => (v) => upd(k, v && v.target ? v.target.value : v);
  const isRev = sc.tipo === "revenda";
  const freteDeles = sc.respFrete === "confinamento";
  const isParceria = sc.modalidade === "parceria";
  const antecipacaoRecebimento = sc.tipoAdiantamento === "recebimento";
  const dataSaida = addDiasISO(sc.dataEntrada, parseFloat(sc.diasCiclo) || 0);
  const dataRecebimento = addDiasISO(sc.dataEntrada, (isRev ? 0 : parseFloat(sc.diasCiclo) || 0) + (parseFloat(sc.diasPagamento) || 0));
  const contratoSugerido = contratoB3PorData(dataSaida);
  useEffect(() => {
    if (isParceria && contratoSugerido && sc.contratoB3 !== contratoSugerido) {
      upd("contratoB3", contratoSugerido);
    } else if (!isParceria && contratoSugerido && !sc.contratoB3) {
      upd("contratoB3", contratoSugerido);
    }
  }, [contratoSugerido, isParceria]);
  return /* @__PURE__ */ jsxs("div", { children: [
    !isRev && /* @__PURE__ */ jsxs(Fragment, { children: [
      /* @__PURE__ */ jsx("div", { className: "sec-t nm", children: "Base Salva do Confinamento" }),
      /* @__PURE__ */ jsxs("div", { className: "g4", children: [
        /* @__PURE__ */ jsx(F, { label: "Selecionar base", children: /* @__PURE__ */ jsxs("select", { value: modeloSelecionado, onChange: (e) => setModeloSelecionado(e.target.value), children: [
          /* @__PURE__ */ jsx("option", { value: "", children: "Nova base / sem modelo" }),
          confinamentos.map((m) => /* @__PURE__ */ jsx("option", { value: m.id, children: m.nome }, m.id))
        ] }) }),
        /* @__PURE__ */ jsx(F, { label: "Aplicar no cen\xE1rio", children: /* @__PURE__ */ jsx("button", { className: "tb", style: { width: "100%", padding: "10px 13px" }, disabled: !modeloSelecionado, onClick: aplicarModelo, children: "Usar base" }) }),
        /* @__PURE__ */ jsx(F, { label: "Salvar como base", children: /* @__PURE__ */ jsx("button", { className: "tb", style: { width: "100%", padding: "10px 13px" }, onClick: salvarModelo, children: "Salvar nova" }) }),
        /* @__PURE__ */ jsx(F, { label: "Editar base salva", children: /* @__PURE__ */ jsxs("div", { style: { display: "flex", gap: 6 }, children: [
          /* @__PURE__ */ jsx("button", { className: "tb", style: { flex: 1, padding: "10px 13px" }, disabled: !modeloSelecionado, onClick: atualizarModelo, children: "Atualizar" }),
          /* @__PURE__ */ jsx("button", { className: "tb", style: { padding: "10px 13px", color: T.red }, disabled: !modeloSelecionado, onClick: apagarModelo, children: "Apagar" })
        ] }) })
      ] }),
      confinamentos.length === 0 && /* @__PURE__ */ jsx("div", { className: "hint", style: { marginTop: 8 }, children: "Ajuste dist\xE2ncia, modalidade e custos abaixo, depois salve como base." }),
      confinamentos.length > 0 && /* @__PURE__ */ jsx("div", { className: "hint", style: { marginTop: 8 }, children: `${confinamentos.length} base(s) salva(s). Resetar inicia um novo estudo, mas mantem estas bases disponiveis.` }),
      /* @__PURE__ */ jsx("div", { className: "dvdr" })
    ] }),
    !isRev && /* @__PURE__ */ jsxs(Fragment, { children: [
      /* @__PURE__ */ jsx("div", { className: "sec-t nm", children: "Modalidade de Confinamento" }),
      /* @__PURE__ */ jsxs("div", { className: "g4", children: [
        /* @__PURE__ */ jsx(F, { label: "Modalidade", children: /* @__PURE__ */ jsx(Tg, { opts: [
          { v: "parceria", l: "Parceria" },
          { v: "arroba", l: "Arroba Prod." },
          { v: "ms", l: "Mat. Seca" },
          { v: "diaria", l: "Di\xE1ria" }
        ], val: sc.modalidade, set: u("modalidade") }) }),
        sc.modalidade === "arroba" && /* @__PURE__ */ jsx(F, { label: "Custo @ produzida (R$/@)", children: /* @__PURE__ */ jsx("input", { type: "number", value: sc.custoArrobaProd, onChange: u("custoArrobaProd") }) }),
        sc.modalidade === "ms" && /* @__PURE__ */ jsxs(Fragment, { children: [
          /* @__PURE__ */ jsx(F, { label: "Custo MS (R$/ton)", children: /* @__PURE__ */ jsx("input", { type: "number", value: sc.custoMS, onChange: u("custoMS") }) }),
          /* @__PURE__ */ jsx(F, { label: "Consumo MS (% peso vivo)", hint: "Macho: 2,3% \xB7 F\xEAmea: 2,5% \u2014 % de (chegada+abate)/2 por dia", children: /* @__PURE__ */ jsx("input", { type: "number", step: ".1", value: sc.consumoMS, onChange: u("consumoMS") }) }),
          /* @__PURE__ */ jsx(F, { label: "Custo Adm (R$/dia/cab)", children: /* @__PURE__ */ jsx("input", { type: "number", step: ".1", value: sc.custoAdm, onChange: u("custoAdm") }) }),
          /* @__PURE__ */ jsx(F, { label: "Protocolo entrada (R$/cab)", children: /* @__PURE__ */ jsx("input", { type: "number", value: sc.protocolo, onChange: u("protocolo") }) })
        ] }),
        sc.modalidade === "diaria" && /* @__PURE__ */ jsx(F, { label: "Custo di\xE1ria (R$/cab/dia)", children: /* @__PURE__ */ jsx("input", { type: "number", step: ".1", value: sc.custoDiaria, onChange: u("custoDiaria") }) })
      ] }),
      (isParceria || sc.modalidade === "arroba") && /* @__PURE__ */ jsxs("div", { className: "g3", style: { marginTop: 14 }, children: [
        /* @__PURE__ */ jsx(F, { label: "Peso de refer\xEAncia de recebimento", hint: "Base para calcular arrobas entregues", children: /* @__PURE__ */ jsx(
          Tg,
          {
            opts: [{ v: "origem", l: "Balan\xE7a origem" }, { v: "chegada", l: "Chegada conf." }],
            val: sc.refEntrada,
            set: u("refEntrada")
          }
        ) }),
        /* @__PURE__ */ jsx(F, { label: "RC de recebimento (%)", hint: "Rendimento de carca\xE7a para calcular arrobas entregues", children: /* @__PURE__ */ jsxs("div", { style: { display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }, children: [
          /* @__PURE__ */ jsx(
            Tg,
            {
              opts: [{ v: "50", l: "50%" }, { v: "52", l: "52%" }, { v: "outro", l: "Outro" }],
              val: ["50", "52"].includes(sc.rcEntrada) ? sc.rcEntrada : "outro",
              set: (v) => u("rcEntrada")(v === "outro" ? "" : v)
            }
          ),
          !["50", "52"].includes(sc.rcEntrada) && /* @__PURE__ */ jsx(
            "input",
            {
              type: "number",
              step: ".5",
              min: "40",
              max: "70",
              placeholder: "ex: 54",
              value: sc.rcEntrada,
              onChange: u("rcEntrada"),
              style: { width: 80 }
            }
          )
        ] }) })
      ] }),
      isParceria && /* @__PURE__ */ jsx("div", { className: "warn", style: { marginTop: 12 }, children: "Parceria: entrego X@ e recebo as mesmas X@ ap\xF3s o abate, ao pre\xE7o de venda do dia." }),
      /* @__PURE__ */ jsx("div", { className: "dvdr" })
    ] }),
    /* @__PURE__ */ jsx("div", { className: "sec-t nm", children: "Transporte" }),
    !freteDeles && /* @__PURE__ */ jsxs(Fragment, { children: [
      /* @__PURE__ */ jsxs("div", { className: "g3", children: [
        /* @__PURE__ */ jsx(F, { label: "Origem do frete", hint: "Fazenda, cidade ou coordenada", children: /* @__PURE__ */ jsx("input", { value: sc.origemFrete || "", onChange: u("origemFrete"), placeholder: "Ex: Fazenda Ametista, MG" }) }),
        /* @__PURE__ */ jsx(F, { label: "Destino", hint: "Confinamento ou frigor\xEDfico", children: /* @__PURE__ */ jsx("input", { value: sc.destinoFrete || "", onChange: u("destinoFrete"), placeholder: "Ex: Confinamento X, GO" }) }),
        /* @__PURE__ */ jsx(F, { label: "Google Maps", children: /* @__PURE__ */ jsxs("div", { style: { display: "flex", gap: 6 }, children: [
          /* @__PURE__ */ jsx("button", { className: "tb", style: { flex: 1, padding: "10px 13px" }, disabled: !(sc.origemFrete && sc.destinoFrete), onClick: calcularDistancia, children: "Calcular rota" }),
          /* @__PURE__ */ jsx("button", { className: "tb", style: { padding: "10px 13px" }, disabled: !(sc.origemFrete && sc.destinoFrete), onClick: () => window.open(googleMapsUrl(sc.origemFrete, sc.destinoFrete), "_blank", "noopener,noreferrer"), children: "Abrir" })
        ] }) })
      ] }),
      statusDistancia && /* @__PURE__ */ jsx("div", { className: "hint", style: { marginTop: 8 }, children: statusDistancia }),
      /* @__PURE__ */ jsx("div", { className: "dvdr" })
    ] }),
    /* @__PURE__ */ jsxs("div", { className: "g4", children: [
      /* @__PURE__ */ jsx(F, { label: "Respons\xE1vel pelo frete", children: /* @__PURE__ */ jsx(
        Tg,
        {
          opts: [{ v: "meu", l: "Meu" }, { v: "dividido", l: "50/50" }, { v: "confinamento", l: "Deles" }],
          val: sc.respFrete,
          set: u("respFrete")
        }
      ) }),
      !freteDeles && /* @__PURE__ */ jsxs(Fragment, { children: [
        /* @__PURE__ */ jsx(F, { label: "Dist\xE2ncia ida (km)", children: /* @__PURE__ */ jsx("input", { type: "number", value: sc.km, onChange: u("km") }) }),
        /* @__PURE__ */ jsx(F, { label: "R$/km da carreta (ida+volta)", hint: "Custo total da viagem. Rateado por cabe\xE7a automaticamente.", children: /* @__PURE__ */ jsx("input", { type: "number", step: ".1", value: sc.precoPorKm, onChange: u("precoPorKm") }) }),
        /* @__PURE__ */ jsx(F, { label: "Bois por carreta", hint: "Macho: 65 \xB7 F\xEAmea: 70. Total de carretas = qtd. bois \xF7 capacidade, arredondado para cima.", children: /* @__PURE__ */ jsx("input", { type: "number", min: "1", value: sc.boisPorCarreta ?? boisPorCarretaPadrao(sexo), onChange: u("boisPorCarreta") }) }),
        /* @__PURE__ */ jsx(F, { label: "Ped\xE1gios Ida+Volta (R$)", children: /* @__PURE__ */ jsx("input", { type: "number", value: sc.pedIda, onChange: u("pedIda") }) })
      ] }),
      /* @__PURE__ */ jsx(F, { label: "Perda no transporte (%)", hint: sc.perdaManual === "" ? `Auto: ${(perdaKm(parseFloat(sc.km) || 0) * 100).toFixed(1)}% p/ ${sc.km || 0} km` : "Valor manual", children: /* @__PURE__ */ jsx(
        "input",
        {
          type: "number",
          step: ".5",
          placeholder: "Auto por km",
          value: sc.perdaManual,
          onChange: u("perdaManual")
        }
      ) })
    ] }),
    /* @__PURE__ */ jsxs("div", { className: "g3", style: { marginTop: 14 }, children: [
      !freteDeles && /* @__PURE__ */ jsx(F, { label: "Frete pago no acerto?", children: /* @__PURE__ */ jsx(Ck, { checked: sc.freteNoAcerto, onChange: u("freteNoAcerto"), label: "Sim \u2014 pago s\xF3 no acerto final" }) }),
      !isRev && !isParceria && /* @__PURE__ */ jsx(F, { label: "Recupera\xE7\xE3o peso 7d (%)", hint: "% da perda de transporte recuperada", children: /* @__PURE__ */ jsx("input", { type: "number", value: sc.recuperacao, onChange: u("recuperacao") }) }),
      /* @__PURE__ */ jsx(F, { label: isRev ? "Prazo pagamento (dias)" : "Prazo pag. ap\xF3s abate (dias)", children: /* @__PURE__ */ jsx("input", { type: "number", value: sc.diasPagamento, onChange: u("diasPagamento") }) })
    ] }),
    /* @__PURE__ */ jsx("div", { className: "dvdr" }),
    /* @__PURE__ */ jsx("div", { className: "sec-t nm", children: "Simula\xE7\xE3o financeira" }),
    /* @__PURE__ */ jsx(F, { label: "Avaliar uma opera\xE7\xE3o financeira?", children: /* @__PURE__ */ jsx(Ck, { checked: sc.simularAdiantamento, onChange: u("simularAdiantamento"), label: "Sim \u2014 comparar o efeito no resultado e na rentabilidade mensal" }) }),
    sc.simularAdiantamento && /* @__PURE__ */ jsxs(Fragment, { children: [
      /* @__PURE__ */ jsx("div", { className: "g2", style: { marginTop: 14 }, children: /* @__PURE__ */ jsx(F, { label: "Tipo da opera\xE7\xE3o", hint: antecipacaoRecebimento ? "Parte do recebimento final volta antes e reduz o tempo do capital exposto" : "Dinheiro adicional colocado no neg\xF3cio; o custo reduz o resultado", children: /* @__PURE__ */ jsx(Tg, { opts: [
        { v: "capital", l: "Adiantamento de capital" },
        { v: "recebimento", l: "Antecipa\xE7\xE3o do recebimento" }
      ], val: sc.tipoAdiantamento || "capital", set: u("tipoAdiantamento") }) }) }),
      /* @__PURE__ */ jsxs("div", { className: "g3", style: { marginTop: 14 }, children: [
        /* @__PURE__ */ jsx(F, { label: antecipacaoRecebimento ? "Data da antecipa\xE7\xE3o" : "Data do adiantamento", children: /* @__PURE__ */ jsx("input", { type: "date", value: sc.dataAdiantamento || "", onChange: u("dataAdiantamento") }) }),
        /* @__PURE__ */ jsx(F, { label: antecipacaoRecebimento ? "Valor a receber antes (R$)" : "Valor adiantado (R$)", children: /* @__PURE__ */ jsx("input", { type: "number", min: "0", step: "100", value: sc.valorAdiantamento || "", onChange: u("valorAdiantamento") }) }),
        /* @__PURE__ */ jsx(F, { label: "Recebimento previsto", hint: "Calculado pela entrada, ciclo e prazo de recebimento", children: /* @__PURE__ */ jsx("input", { readOnly: true, value: fmtData(dataRecebimento) }) })
      ] }),
      /* @__PURE__ */ jsxs("div", { className: "warn", style: { marginTop: 12 }, children: [
        "Juros simples pr\xF3-rata pela taxa de ",
        custoDinheiro || "0",
        antecipacaoRecebimento ? "% a.m. O valor informado entra antes como recebimento; o principal e o custo s\xE3o descontados do saldo no acerto final. A rentabilidade mensal \xE9 recalculada pelos fluxos de caixa datados." : "% a.m., somente sobre o valor adiantado e pelos dias efetivos. Neste modo o prazo do recebimento n\xE3o muda, por isso o custo reduz a rentabilidade."
      ] }),
      resultado && /* @__PURE__ */ jsxs("div", { className: "g3", style: { marginTop: 14 }, children: [
        /* @__PURE__ */ jsx(F, { label: antecipacaoRecebimento ? "Tempo antecipado" : "Tempo do adiantamento", children: /* @__PURE__ */ jsx("input", { readOnly: true, value: `${fN(resultado.diasAdiantamento, 0)} dias` }) }),
        /* @__PURE__ */ jsx(F, { label: antecipacaoRecebimento ? "Custo da antecipa\xE7\xE3o" : "Custo do adiantamento", children: /* @__PURE__ */ jsx("input", { readOnly: true, value: fR(resultado.custoAdiantamento) }) }),
        /* @__PURE__ */ jsx(F, { label: "Impacto na rentabilidade mensal", hint: "Sem opera\xE7\xE3o \u2192 resultado mensal final", children: /* @__PURE__ */ jsx("input", { readOnly: true, value: `${fP(resultado.rMliqSemAdiantamento ?? resultado.rMliq)} \u2192 ${fP(resultado.rMliq)} a.m. (${fN(resultado.impactoAdiantamentoMensal ?? 0, 2)} p.p.)` }) })
      ] }),
      resultado && antecipacaoRecebimento && /* @__PURE__ */ jsxs("div", { className: "g3", style: { marginTop: 14 }, children: [
        /* @__PURE__ */ jsx(F, { label: "Recebido antecipadamente", children: /* @__PURE__ */ jsx("input", { readOnly: true, value: fR(resultado.valorRecebidoAntecipado) }) }),
        /* @__PURE__ */ jsx(F, { label: "Saldo no acerto final", children: /* @__PURE__ */ jsx("input", { readOnly: true, value: fR(resultado.saldoRecebimentoFinal) }) }),
        /* @__PURE__ */ jsx(F, { label: "Limite antecip\xE1vel neste cen\xE1rio", hint: resultado.valorAdiantamentoSolicitado > resultado.valorAdiantamento ? "O valor informado foi limitado para n\xE3o gerar saldo final negativo" : "Principal mais custo n\xE3o podem superar o valor final dispon\xEDvel", children: /* @__PURE__ */ jsx("input", { readOnly: true, value: fR(resultado.valorMaximoAntecipacao) }) })
      ] })
    ] }),
    isRev && /* @__PURE__ */ jsxs(Fragment, { children: [
      /* @__PURE__ */ jsx("div", { className: "warn", style: { marginTop: 12 }, children: "Revenda: receita = arrobas venda (desconto abaixo) \xD7 pre\xE7o de revenda." }),
      /* @__PURE__ */ jsxs("div", { className: "g3", style: { marginTop: 14 }, children: [
        /* @__PURE__ */ jsx(F, { label: "Pre\xE7o de Revenda (R$/@)", children: /* @__PURE__ */ jsx("input", { type: "number", value: sc.precoRevenda, onChange: u("precoRevenda") }) }),
        /* @__PURE__ */ jsx(F, { label: "Desconto de capim na venda", hint: "Sem desc. \xB7 10kg fixo \xB7 700g/@", children: /* @__PURE__ */ jsx(
          Tg,
          {
            opts: [
              { v: "sem", l: "Sem desc." },
              { v: "10kg", l: "10kg fixo" },
              { v: "700g", l: "700g/@" }
            ],
            val: sc.modoCapimVenda || "sem",
            set: u("modoCapimVenda")
          }
        ) })
      ] })
    ] }),
    !isRev && /* @__PURE__ */ jsxs(Fragment, { children: [
      /* @__PURE__ */ jsx("div", { className: "dvdr" }),
      /* @__PURE__ */ jsx("div", { className: "sec-t nm", children: "Desempenho Zoot\xE9cnico" }),
      /* @__PURE__ */ jsxs("div", { className: "g4", children: [
        /* @__PURE__ */ jsx(F, { label: "Data de entrada", children: /* @__PURE__ */ jsx("input", { type: "date", value: sc.dataEntrada || "", onChange: u("dataEntrada") }) }),
        /* @__PURE__ */ jsx(F, { label: "Ciclo (dias)", children: /* @__PURE__ */ jsx("input", { type: "number", value: sc.diasCiclo, onChange: u("diasCiclo") }) }),
        /* @__PURE__ */ jsx(F, { label: "Sa\xEDda prevista", hint: `${mesSaidaLabel(dataSaida)} \xB7 ${contratoSugerido || "sem contrato"}`, children: /* @__PURE__ */ jsx("input", { readOnly: true, value: fmtData(dataSaida) }) }),
        !isParceria && /* @__PURE__ */ jsx(F, { label: "GMD (kg/dia)", children: /* @__PURE__ */ jsx("input", { type: "number", step: ".1", value: sc.gmd, onChange: u("gmd") }) }),
        !isParceria && /* @__PURE__ */ jsx(F, { label: "RC Final (%)", children: /* @__PURE__ */ jsx("input", { type: "number", step: ".5", value: sc.rcFinal, onChange: u("rcFinal") }) }),
        !isParceria && sc.modalidade !== "arroba" && /* @__PURE__ */ jsx(F, { label: "Base do ganho de peso", children: /* @__PURE__ */ jsx(
          Tg,
          {
            opts: [{ v: "origem", l: "Origem" }, { v: "chegada", l: "Chegada" }, { v: "proc", l: "Proc. 7d" }],
            val: sc.refGanho,
            set: u("refGanho")
          }
        ) })
      ] }),
      /* @__PURE__ */ jsx("div", { className: "dvdr" }),
      /* @__PURE__ */ jsx("div", { className: "sec-t nm", children: "Pre\xE7o de Venda" }),
      /* @__PURE__ */ jsxs("div", { className: "g4", children: [
        /* @__PURE__ */ jsx(F, { label: "Modalidade de pre\xE7o", children: /* @__PURE__ */ jsx(
          Tg,
          {
            opts: [{ v: "bolsa", l: "Bolsa / Termo" }, { v: "balcao", l: "Balc\xE3o" }],
            val: sc.modoPreco,
            set: u("modoPreco")
          }
        ) }),
        sc.modoPreco === "bolsa" ? /* @__PURE__ */ jsxs(Fragment, { children: [
          /* @__PURE__ */ jsx(F, { label: isParceria ? "Contrato B3 automático" : "Contrato B3 escolhido", hint: isParceria ? `Definido pela sa\xEDda em ${mesSaidaLabel(dataSaida)}` : `Sugest\xE3o pela sa\xEDda: ${contratoSugerido || "\u2014"}`, children: isParceria ? /* @__PURE__ */ jsx("input", { readOnly: true, value: contratoSugerido || "" }) : /* @__PURE__ */ jsxs("div", { style: { display: "flex", gap: 6 }, children: [
            /* @__PURE__ */ jsx("input", { value: sc.contratoB3 || contratoSugerido, onChange: u("contratoB3"), style: { flex: 1 } }),
            /* @__PURE__ */ jsx("button", { className: "tb", style: { padding: "10px 13px" }, onClick: () => u("contratoB3")(contratoSugerido), children: "Usar sugest\xE3o" })
          ] }) }),
          /* @__PURE__ */ jsx(F, { label: "BGI Futuro compartilhado (R$/@)", hint: sc.cotacaoB3Fonte ? `${sc.cotacaoB3Fonte} \xB7 ${fmtData((sc.cotacaoB3AtualizadaEm || "").slice(0, 10))}` : "Atualize o contrato na se\xE7\xE3o Mercado BGI acima", children: /* @__PURE__ */ jsx("input", { type: "number", readOnly: true, value: sc.precoBolsa }) }),
          /* @__PURE__ */ jsx(F, { label: "Diferencial de base (%)", hint: "0 a 12,5% \u2014 desconto sobre BGI", children: /* @__PURE__ */ jsx("input", { type: "number", step: ".5", min: "0", max: "12.5", value: sc.baseDesc, onChange: u("baseDesc") }) })
        ] }) : /* @__PURE__ */ jsx(F, { label: "Pre\xE7o balc\xE3o (R$/@)", children: /* @__PURE__ */ jsx("input", { type: "number", value: sc.precoBalcao, onChange: u("precoBalcao") }) }),
        /* @__PURE__ */ jsxs("div", { className: "g2", style: { gridColumn: "1 / -1" }, children: [
          /* @__PURE__ */ jsx(F, { label: "Funrural (%)", hint: "Calculado sobre o faturamento bruto da venda", children: /* @__PURE__ */ jsx("input", { type: "number", step: ".1", min: "0", value: sc.funrural, onChange: u("funrural") }) }),
          /* @__PURE__ */ jsx(F, { label: "Finpec (%)", hint: "Normalmente 0%; informe 1% somente quando houver cobrança", children: /* @__PURE__ */ jsx("input", { type: "number", step: ".1", min: "0", value: sc.finpec, onChange: (e) => {
            upd("finpec", e.target.value);
            upd("finpecConfigurado", true);
          } }) })
        ] })
      ] })
    ] })
  ] });
}
function calcComOverride(lote, sc, overrides) {
  const loteMod = { ...lote };
  const scMod = { ...sc };
  if (overrides.precoCompra !== void 0) loteMod.precoCompra = String(overrides.precoCompra);
  if (overrides.prazoPagtoCompra !== void 0) loteMod.prazoPagtoCompra = String(overrides.prazoPagtoCompra);
  if (overrides.diasCiclo !== void 0) scMod.diasCiclo = String(overrides.diasCiclo);
  if (overrides.gmd !== void 0) scMod.gmd = String(overrides.gmd);
  if (overrides.rcFinal !== void 0) scMod.rcFinal = String(overrides.rcFinal);
  if (overrides.perdaTransporte !== void 0) scMod.perdaManual = String(overrides.perdaTransporte);
  if (overrides.precoVenda !== void 0) {
    if (scMod.modoPreco === "balcao") scMod.precoBalcao = String(overrides.precoVenda);
    else scMod.precoBolsa = String(overrides.precoVenda);
  }
  try {
    return calcCenario(loteMod, scMod);
  } catch {
    return null;
  }
}
var SLIDERS = [
  { k: "precoCompra", l: "Pre\xE7o de compra", unit: "R$/@", min: 200, max: 500, step: 5, dec: 0 },
  { k: "prazoPagtoCompra", l: "Prazo de compra", unit: "dias", min: 0, max: 60, step: 1, dec: 0 },
  { k: "diasCiclo", l: "Dias confinados", unit: "dias", min: 30, max: 240, step: 1, dec: 0 },
  { k: "gmd", l: "GMD", unit: "kg/dia", min: 0.5, max: 2.5, step: 0.1, dec: 2 },
  { k: "rcFinal", l: "RC Final", unit: "%", min: 48, max: 60, step: 0.5, dec: 1 },
  { k: "perdaTransporte", l: "Perda transporte", unit: "%", min: 0, max: 15, step: 0.5, dec: 1 },
  { k: "precoVenda", l: "Pre\xE7o de venda", unit: "R$/@", min: 250, max: 500, step: 5, dec: 0 }
];
function getBase(lote, sc, k) {
  if (k === "precoCompra") return parseFloat(lote.precoCompra) || 305;
  if (k === "prazoPagtoCompra") return parseFloat(lote.prazoPagtoCompra) || 0;
  if (k === "diasCiclo") return parseFloat(sc.diasCiclo) || 110;
  if (k === "gmd") return parseFloat(sc.gmd) || 1.3;
  if (k === "rcFinal") return parseFloat(sc.rcFinal) || 53;
  if (k === "perdaTransporte") return sc.perdaManual !== "" ? parseFloat(sc.perdaManual) || 7 : +(perdaKm(parseFloat(sc.km) || 0) * 100).toFixed(1);
  if (k === "precoVenda") return parseFloat(sc.precoBolsa) || parseFloat(sc.precoBalcao) || 350;
  return 0;
}
function SensPanel({ lote, cenarios, resultados, historico = [], setHistorico = () => {
} }) {
  const [scSel, setScSel] = useState(0);
  const [nomeTeste, setNomeTeste] = useState("Teste 1");
  const sc = cenarios[scSel] || cenarios[0];
  const color = T.sc[scSel] || T.sc[0];
  const initVals = (scIdx) => {
    const s = cenarios[scIdx] || cenarios[0];
    return Object.fromEntries(SLIDERS.map((sl) => [sl.k, getBase(lote, s, sl.k)]));
  };
  const [vals, setVals] = useState(() => initVals(0));
  const handleScSel = (i) => {
    setScSel(i);
    setVals(Object.fromEntries(SLIDERS.map((sl) => [sl.k, getBase(lote, cenarios[i], sl.k)])));
  };
  const resetSliders = () => setVals(initVals(scSel));
  const resultado = useMemo(
    () => calcComOverride(lote, sc, vals),
    [lote, sc, vals]
  );
  const salvar = () => {
    if (!resultado) return;
    const novo = {
      id: Date.now(),
      nome: nomeTeste || `Teste ${historico.length + 1}`,
      cenario: sc.nome,
      vals: { ...vals },
      // Snapshot dos inputs principais para referência
      inputs: {
        pesoMedio: lote.pesoMedio,
        precoCompra: vals.precoCompra,
        prazoPagtoCompra: vals.prazoPagtoCompra,
        diasCiclo: vals.diasCiclo,
        gmd: vals.gmd,
        rcFinal: vals.rcFinal,
        perdaTransporte: vals.perdaTransporte,
        precoVenda: vals.precoVenda
      },
      rentMensal: resultado.rentMensal,
      rentTotal: resultado.rentTotal,
      lucro: resultado.lucro,
      investInicial: resultado.investInicial,
      receita: resultado.receita,
      custos: resultado.custos
    };
    setHistorico((h) => [...h, novo]);
    setNomeTeste(`Teste ${historico.length + 2}`);
  };
  const carregarTeste = (t) => {
    setVals({ ...t.vals });
    const idx = cenarios.findIndex((c) => c.nome === t.cenario);
    if (idx >= 0) setScSel(idx);
  };
  const sliderCss = `
    .sens-slider-row { display:flex; align-items:center; gap:14px; padding:12px 0; border-bottom:1px solid ${T.border}; }
    .sens-slider-row:last-child { border-bottom:none; }
    .sens-lbl { font-size:11px; color:${T.label}; width:150px; flex-shrink:0; font-family:'Plus Jakarta Sans',sans-serif; font-weight:500; }
    .sens-slider { flex:1; -webkit-appearance:none; appearance:none; height:3px; border-radius:2px; background:${T.border}; outline:none; cursor:pointer; }
    .sens-slider::-webkit-slider-thumb { -webkit-appearance:none; width:18px; height:18px; border-radius:50%; background:var(--sc-color,${T.accent}); cursor:pointer; border:2px solid #fff; box-shadow:0 1px 4px rgba(0,0,0,.15); }
    .sens-val { font-family:'DM Mono',monospace; font-size:13px; font-weight:600; color:${T.text}; width:72px; text-align:right; flex-shrink:0; }
    .sens-unit { font-size:10px; color:${T.muted}; width:42px; flex-shrink:0; }
    .sens-result { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-top:18px; }
    .sens-kpi { background:${T.bg}; border-radius:10px; padding:14px 16px; border:1px solid ${T.border}; }
    .sens-kpi-l { font-size:10px; color:${T.muted}; text-transform:uppercase; letter-spacing:.6px; margin-bottom:6px; font-family:'Plus Jakarta Sans',sans-serif; font-weight:500; }
    .sens-kpi-v { font-family:'DM Mono',monospace; font-size:18px; font-weight:500; }
    .sens-actions { display:flex; gap:10px; margin-top:18px; align-items:center; flex-wrap:wrap; }
    .sens-save-btn { background:${T.accent}; border:none; border-radius:8px; color:#fff; cursor:pointer; font-family:'Plus Jakarta Sans',sans-serif; font-size:12px; font-weight:700; padding:9px 20px; transition:opacity .15s; }
    .sens-save-btn:hover { opacity:.85; }
    .sens-reset-btn { background:transparent; border:1.5px solid ${T.border}; border-radius:8px; color:${T.muted}; cursor:pointer; font-size:12px; padding:9px 16px; transition:all .15s; font-family:'Plus Jakarta Sans',sans-serif; }
    .sens-reset-btn:hover { border-color:${T.label}; color:${T.text}; }
    .sens-nome-input { background:${T.surface}; border:1px solid ${T.border}; border-radius:8px; color:${T.text}; font-family:'DM Mono',monospace; font-size:12px; padding:8px 12px; outline:none; flex:1; min-width:120px; max-width:200px; box-shadow:0 1px 2px rgba(0,0,0,.04); }
    .hist-table { width:100%; border-collapse:collapse; font-size:12px; margin-top:14px; }
    .hist-table th { padding:8px 12px; font-size:9px; font-weight:600; color:${T.muted}; letter-spacing:.8px; text-transform:uppercase; border-bottom:1px solid ${T.border}; text-align:right; background:${T.bg}; }
    .hist-table th:first-child { text-align:left; }
    .hist-table td { padding:9px 12px; border-bottom:1px solid ${T.border}; font-family:'DM Mono',monospace; text-align:right; color:${T.text}; }
    .hist-table td:first-child { text-align:left; font-family:'Plus Jakarta Sans',sans-serif; color:${T.label}; }
    .hist-table tr:last-child td { border-bottom:none; }
    .hist-load-btn { background:${T.bg}; border:1px solid ${T.border}; border-radius:6px; color:${T.muted}; cursor:pointer; font-size:10px; padding:4px 9px; transition:all .15s; }
    .hist-load-btn:hover { border-color:${T.accent}; color:${T.accent}; }
    .hist-del-btn { background:transparent; border:none; color:${T.red}; cursor:pointer; font-size:13px; opacity:.35; padding:0 4px; }
    .hist-del-btn:hover { opacity:1; }
    @media(max-width:600px){ .sens-result{grid-template-columns:1fr 1fr} .sens-lbl{width:110px} }
  `;
  return /* @__PURE__ */ jsxs("div", { style: { marginTop: 28 }, children: [
    /* @__PURE__ */ jsx("style", { children: sliderCss }),
    /* @__PURE__ */ jsx("div", { className: "res-ttl", children: "An\xE1lise de Sensibilidade" }),
    /* @__PURE__ */ jsx("div", { className: "res-sub", children: "Arraste os sliders \u2014 salve testes para comparar" }),
    /* @__PURE__ */ jsxs("div", { className: "sec", children: [
      /* @__PURE__ */ jsxs("div", { style: { display: "flex", gap: 12, marginBottom: 18, alignItems: "flex-end", flexWrap: "wrap" }, children: [
        /* @__PURE__ */ jsx(F, { label: "Cen\xE1rio", children: /* @__PURE__ */ jsx("select", { value: scSel, onChange: (e) => handleScSel(+e.target.value), style: { maxWidth: 220 }, children: cenarios.map((s, i) => resultados[i] ? /* @__PURE__ */ jsx("option", { value: i, children: s.nome }, s.id) : null) }) }),
        /* @__PURE__ */ jsx("button", { className: "sens-reset-btn", onClick: resetSliders, children: "\u21BA Resetar para base" })
      ] }),
      SLIDERS.map((sl) => /* @__PURE__ */ jsxs("div", { className: "sens-slider-row", children: [
        /* @__PURE__ */ jsx("span", { className: "sens-lbl", children: sl.l }),
        /* @__PURE__ */ jsx(
          "input",
          {
            type: "range",
            className: "sens-slider",
            style: { "--sc-color": color },
            min: sl.min,
            max: sl.max,
            step: sl.step,
            value: vals[sl.k],
            onChange: (e) => setVals((v) => ({ ...v, [sl.k]: +e.target.value }))
          }
        ),
        /* @__PURE__ */ jsx("span", { className: "sens-val", children: fN(vals[sl.k], sl.dec) }),
        /* @__PURE__ */ jsx("span", { className: "sens-unit", children: sl.unit })
      ] }, sl.k)),
      resultado && /* @__PURE__ */ jsxs("div", { className: "sens-result", children: [
        /* @__PURE__ */ jsxs("div", { className: "sens-kpi", children: [
          /* @__PURE__ */ jsx("div", { className: "sens-kpi-l", children: "Rent. Mensal" }),
          /* @__PURE__ */ jsx("div", { className: "sens-kpi-v", style: { color: resultado.rentMensal >= 0 ? T.green : T.red }, children: fP(resultado.rentMensal) })
        ] }),
        /* @__PURE__ */ jsxs("div", { className: "sens-kpi", children: [
          /* @__PURE__ */ jsx("div", { className: "sens-kpi-l", children: "Rent. Total" }),
          /* @__PURE__ */ jsx("div", { className: `sens-kpi-v ${resultado.rentTotal >= 0 ? "pos" : "neg"}`, children: fP(resultado.rentTotal) })
        ] }),
        /* @__PURE__ */ jsxs("div", { className: "sens-kpi", children: [
          /* @__PURE__ */ jsx("div", { className: "sens-kpi-l", children: "Lucro Total" }),
          /* @__PURE__ */ jsx("div", { className: `sens-kpi-v ${resultado.lucro >= 0 ? "pos" : "neg"}`, children: fR(resultado.lucro) })
        ] }),
        /* @__PURE__ */ jsxs("div", { className: "sens-kpi", children: [
          /* @__PURE__ */ jsx("div", { className: "sens-kpi-l", children: "Capital" }),
          /* @__PURE__ */ jsx("div", { className: "sens-kpi-v", children: fR(resultado.investInicial) })
        ] })
      ] }),
      /* @__PURE__ */ jsxs("div", { className: "sens-actions", children: [
        /* @__PURE__ */ jsx(
          "input",
          {
            className: "sens-nome-input",
            placeholder: "Nome do teste...",
            value: nomeTeste,
            onChange: (e) => setNomeTeste(e.target.value)
          }
        ),
        /* @__PURE__ */ jsx("button", { className: "sens-save-btn", onClick: salvar, children: "SALVAR TESTE" })
      ] })
    ] }),
    historico.length > 0 && /* @__PURE__ */ jsxs("div", { className: "sec", style: { marginTop: 14 }, children: [
      /* @__PURE__ */ jsx("div", { className: "sec-t", children: "Testes Salvos" }),
      /* @__PURE__ */ jsx("div", { className: "tbl-wrap", children: /* @__PURE__ */ jsxs("table", { className: "hist-table", children: [
        /* @__PURE__ */ jsx("thead", { children: /* @__PURE__ */ jsxs("tr", { children: [
          /* @__PURE__ */ jsx("th", { style: { textAlign: "left" }, children: "Nome" }),
          /* @__PURE__ */ jsx("th", { children: "Cen\xE1rio" }),
          /* @__PURE__ */ jsx("th", { children: "Compra R$/@" }),
          /* @__PURE__ */ jsx("th", { children: "Prazo compra" }),
          /* @__PURE__ */ jsx("th", { children: "Dias conf." }),
          /* @__PURE__ */ jsx("th", { children: "GMD" }),
          /* @__PURE__ */ jsx("th", { children: "RC%" }),
          /* @__PURE__ */ jsx("th", { children: "Perda%" }),
          /* @__PURE__ */ jsx("th", { children: "Venda R$/@" }),
          /* @__PURE__ */ jsx("th", { children: "Rent. Mensal" }),
          /* @__PURE__ */ jsx("th", { children: "Lucro" }),
          /* @__PURE__ */ jsx("th", { children: "Capital" }),
          /* @__PURE__ */ jsx("th", {})
        ] }) }),
        /* @__PURE__ */ jsx("tbody", { children: historico.map((t) => /* @__PURE__ */ jsxs("tr", { children: [
          /* @__PURE__ */ jsx("td", { style: { fontFamily: "Inter,sans-serif", color: T.text, fontWeight: 600 }, children: t.nome }),
          /* @__PURE__ */ jsx("td", { style: { color: T.muted, fontFamily: "Inter,sans-serif", fontSize: 11 }, children: t.cenario }),
          /* @__PURE__ */ jsx("td", { children: fN(t.inputs.precoCompra, 0) }),
          /* @__PURE__ */ jsxs("td", { children: [
            fN(t.inputs.prazoPagtoCompra, 0),
            "d"
          ] }),
          /* @__PURE__ */ jsxs("td", { children: [
            fN(t.inputs.diasCiclo, 0),
            "d"
          ] }),
          /* @__PURE__ */ jsx("td", { children: fN(t.inputs.gmd, 2) }),
          /* @__PURE__ */ jsxs("td", { children: [
            fN(t.inputs.rcFinal, 1),
            "%"
          ] }),
          /* @__PURE__ */ jsxs("td", { children: [
            fN(t.inputs.perdaTransporte, 1),
            "%"
          ] }),
          /* @__PURE__ */ jsx("td", { children: fN(t.inputs.precoVenda, 0) }),
          /* @__PURE__ */ jsx("td", { className: t.rentMensal >= 0 ? "pos" : "neg", style: { fontWeight: 700 }, children: fP(t.rentMensal) }),
          /* @__PURE__ */ jsx("td", { className: t.lucro >= 0 ? "pos" : "neg", children: fR(t.lucro) }),
          /* @__PURE__ */ jsx("td", { children: fR(t.investInicial) }),
          /* @__PURE__ */ jsxs("td", { style: { display: "flex", gap: 6, justifyContent: "flex-end" }, children: [
            /* @__PURE__ */ jsx("button", { className: "hist-load-btn", onClick: () => carregarTeste(t), title: "Carregar nos sliders", children: "\u2191 usar" }),
            /* @__PURE__ */ jsx("button", { className: "hist-del-btn", onClick: () => setHistorico((h) => h.filter((x) => x.id !== t.id)), title: "Remover", children: "\xD7" })
          ] })
        ] }, t.id)) })
      ] }) })
    ] })
  ] });
}
function EvolucaoTempo({ lote, cenarios }) {
  const ativos = cenarios.filter((sc) => sc.tipo !== "revenda");
  if (!ativos.length) return null;
  return /* @__PURE__ */ jsxs("div", { className: "sec", style: { marginTop: 18 }, children: [
    /* @__PURE__ */ jsx("div", { className: "sec-t", children: "Evolução entre 60 e 240 dias" }),
    /* @__PURE__ */ jsx("div", { className: "hint", style: { marginBottom: 16 }, children: "Cada prazo usa a cotação BGI do próprio mês de saída. A linha fica pendente quando a curva não possui aquele vencimento." }),
    ativos.map((sc) => {
      const evolucao = calcEvolucaoTempo(lote, sc);
      const diasAtual = Math.round(parseFloat(sc.diasCiclo) || 0);
      return /* @__PURE__ */ jsxs("div", { style: { marginBottom: 24 }, children: [
        /* @__PURE__ */ jsxs("div", { className: "sec-t nm", style: { marginBottom: 8 }, children: [sc.nome, " · ", sc.modalidade, " · ciclo atual ", diasAtual, " dias"] }),
        /* @__PURE__ */ jsx("div", { className: "tbl-wrap", children: /* @__PURE__ */ jsxs("table", { className: "cmp-tbl", children: [
          /* @__PURE__ */ jsx("thead", { children: /* @__PURE__ */ jsxs("tr", { children: [
            /* @__PURE__ */ jsx("th", { children: "Prazo / saída" }),
            /* @__PURE__ */ jsx("th", { children: "BGI do mês" }),
            /* @__PURE__ */ jsx("th", { children: "@ produzidas / cab" }),
            /* @__PURE__ */ jsx("th", { children: "Custo @ produzida" }),
            /* @__PURE__ */ jsx("th", { children: "Custo marginal @" }),
            /* @__PURE__ */ jsx("th", { children: "Frete / @ produzida" }),
            /* @__PURE__ */ jsx("th", { children: "Produção + frete / @" }),
            /* @__PURE__ */ jsx("th", { children: "Rent. mensal" }),
            /* @__PURE__ */ jsx("th", { children: "Resultado final" })
          ] }) }),
          /* @__PURE__ */ jsx("tbody", { children: evolucao.map((ponto) => {
            const r = ponto.resultado;
            return /* @__PURE__ */ jsxs("tr", { className: ponto.dias === diasAtual ? "tot" : "", children: [
              /* @__PURE__ */ jsxs("td", { children: [ponto.dias, " dias · ", fmtData(ponto.dataSaida)] }),
              /* @__PURE__ */ jsx("td", { children: ponto.cotacao == null ? `${ponto.contrato} · sem cotação` : `${ponto.contrato} · ${fR(ponto.cotacao)}` }),
              /* @__PURE__ */ jsx("td", { children: r ? fAt(r.arrobasProduzidasCab) : "—" }),
              /* @__PURE__ */ jsx("td", { children: r ? fR(r.custoArrobaLiquidaProduzida) : "—" }),
              /* @__PURE__ */ jsx("td", { children: r ? fR(r.custoArrobaMarginal) : "—" }),
              /* @__PURE__ */ jsx("td", { children: r ? fR(r.fretePorArrobaProduzida) : "—" }),
              /* @__PURE__ */ jsx("td", { children: r ? fR(r.custoProducaoFretePorArroba) : "—" }),
              /* @__PURE__ */ jsx("td", { className: r ? r.rMliq >= 0 ? "pos" : "neg" : "", children: r ? `${fP(r.rMliq)} a.m.` : "—" }),
              /* @__PURE__ */ jsx("td", { className: r ? r.lucroLiquido >= 0 ? "pos" : "neg" : "", children: r ? fR(r.lucroLiquido) : "—" })
            ] }, ponto.dias);
          }) })
        ] }) })
      ] }, sc.id);
    })
  ] });
}
function ItemRelatorio({ label, value }) {
  return /* @__PURE__ */ jsxs("div", { className: "report-item", children: [
    /* @__PURE__ */ jsx("div", { className: "report-label", children: label }),
    /* @__PURE__ */ jsx("div", { className: "report-value", children: value ?? "—" })
  ] });
}
function RelatorioComparativo({ lote, cenarios, resultados }) {
  const ativos = cenarios.map((sc, i) => ({ sc, r: resultados[i], i })).filter((x) => x.r);
  if (!ativos.length) return null;
  const ranked = [...ativos].sort((a, b) => b.r.rMliq - a.r.rMliq || b.r.lucroLiquido - a.r.lucroLiquido || b.r.rTliq - a.r.rTliq || a.i - b.i);
  return /* @__PURE__ */ jsxs(Fragment, { children: [
    /* @__PURE__ */ jsxs("div", { className: "sec no-print", style: { marginTop: 18 }, children: [
      /* @__PURE__ */ jsx("div", { className: "sec-t", children: "Relatório comparativo" }),
      /* @__PURE__ */ jsxs("div", { className: "g2", children: [
        /* @__PURE__ */ jsx("div", { className: "hint", children: "Inclui as premissas e os resultados de todos os confinamentos, independentemente do cenário selecionado. Na impressão, escolha Salvar como PDF." }),
        /* @__PURE__ */ jsx("button", { className: "calc-btn", style: { marginTop: 0 }, onClick: () => window.print(), children: "GERAR RELATÓRIO COMPARATIVO / PDF" })
      ] })
    ] }),
    /* @__PURE__ */ jsxs("div", { className: "report-print", children: [
      /* @__PURE__ */ jsxs("section", { className: "report-page", children: [
        /* @__PURE__ */ jsx("div", { className: "report-brand", children: "CONFINEX · RELATÓRIO COMPARATIVO" }),
        /* @__PURE__ */ jsxs("h1", { children: [lote.origemNome || "Estudo de confinamento", " · ", lote.codigoNegocio || "sem código"] }),
        /* @__PURE__ */ jsx("div", { className: "report-muted", children: `Gerado em ${(/* @__PURE__ */ new Date()).toLocaleString("pt-BR")}` }),
        /* @__PURE__ */ jsxs("div", { className: "report-grid", children: [
          /* @__PURE__ */ jsx(ItemRelatorio, { label: "Cabeças", value: lote.qtd }),
          /* @__PURE__ */ jsx(ItemRelatorio, { label: "Sexo", value: lote.sexo === "macho" ? "Macho" : "Fêmea" }),
          /* @__PURE__ */ jsx(ItemRelatorio, { label: "Peso médio", value: `${lote.pesoMedio} kg` }),
          /* @__PURE__ */ jsx(ItemRelatorio, { label: "Compra", value: `${fR(parseFloat(lote.precoCompra) || 0)}/@` }),
          /* @__PURE__ */ jsx(ItemRelatorio, { label: "Prazo da compra", value: `${lote.prazoPagtoCompra || 0} dias` }),
          /* @__PURE__ */ jsx(ItemRelatorio, { label: "Custo do dinheiro", value: `${lote.custoDinheiro || 0}% a.m.` })
        ] }),
        /* @__PURE__ */ jsx("h2", { children: "Resumo por rentabilidade mensal" }),
        /* @__PURE__ */ jsx("table", { className: "report-table", children: /* @__PURE__ */ jsxs("tbody", { children: [
          /* @__PURE__ */ jsxs("tr", { children: ["#", "Cenário", "Modalidade", "Prazo", "Contrato", "@ posta", "@ produzida", "Rent. mensal", "Resultado"].map((h) => /* @__PURE__ */ jsx("th", { children: h }, h)) }),
          ranked.map(({ sc, r }, pos) => /* @__PURE__ */ jsxs("tr", { children: [
            /* @__PURE__ */ jsx("td", { children: pos + 1 }),
            /* @__PURE__ */ jsx("td", { children: sc.nome }),
            /* @__PURE__ */ jsx("td", { children: sc.tipo === "revenda" ? "Revenda" : sc.modalidade }),
            /* @__PURE__ */ jsx("td", { children: sc.tipo === "revenda" ? "—" : `${sc.diasCiclo} dias` }),
            /* @__PURE__ */ jsx("td", { children: sc.tipo === "revenda" || sc.modoPreco !== "bolsa" ? "—" : sc.contratoB3 || contratoB3DoCenario(sc) }),
            /* @__PURE__ */ jsx("td", { children: fR(r.custoArrobaPosta) }),
            /* @__PURE__ */ jsx("td", { children: sc.tipo === "revenda" ? "—" : fR(r.custoArrobaLiquidaProduzida) }),
            /* @__PURE__ */ jsx("td", { children: `${fP(r.rMliq)} a.m.` }),
            /* @__PURE__ */ jsx("td", { children: fR(r.lucroLiquido) })
          ] }, sc.id))
        ] }) })
      ] }),
      ranked.map(({ sc, r }, pos) => {
        const evolucao = calcEvolucaoTempo(lote, sc);
        return /* @__PURE__ */ jsxs("section", { className: "report-page", children: [
          /* @__PURE__ */ jsxs("div", { className: "report-brand", children: [pos + 1, "º · ", sc.nome] }),
          /* @__PURE__ */ jsxs("h1", { children: [sc.tipo === "revenda" ? "Revenda" : sc.modalidade, " · ", `${fP(r.rMliq)} a.m.`] }),
          /* @__PURE__ */ jsxs("div", { className: "report-grid", children: [
            /* @__PURE__ */ jsx(ItemRelatorio, { label: "Entrada / saída", value: sc.tipo === "revenda" ? "—" : `${fmtData(sc.dataEntrada)} · ${fmtData(addDiasISO(sc.dataEntrada, parseFloat(sc.diasCiclo) || 0))}` }),
            /* @__PURE__ */ jsx(ItemRelatorio, { label: "Ciclo / recebimento", value: sc.tipo === "revenda" ? `${sc.diasPagamento || 0} dias` : `${sc.diasCiclo} + ${sc.diasPagamento || 0} dias` }),
            /* @__PURE__ */ jsx(ItemRelatorio, { label: "Contrato / base", value: sc.modoPreco === "bolsa" ? `${sc.contratoB3 || contratoB3DoCenario(sc)} · -${sc.baseDesc || 0}%` : `Balcão · ${fR(parseFloat(sc.precoBalcao) || 0)}` }),
            /* @__PURE__ */ jsx(ItemRelatorio, { label: "GMD / RC final", value: sc.tipo === "revenda" ? "—" : `${sc.gmd} kg/d · ${sc.rcFinal}%` }),
            /* @__PURE__ */ jsx(ItemRelatorio, { label: "Premissas da modalidade", value: sc.tipo === "revenda" ? "Revenda direta" : sc.modalidade === "ms" ? `MS ${fR(parseFloat(sc.custoMS) || 0)}/t · ${sc.consumoMS}% PV · adm ${fR(parseFloat(sc.custoAdm) || 0)}/d · protocolo ${fR(parseFloat(sc.protocolo) || 0)}` : sc.modalidade === "arroba" ? `${fR(parseFloat(sc.custoArrobaProd) || 0)}/@ produzida` : sc.modalidade === "diaria" ? `${fR(parseFloat(sc.custoDiaria) || 0)}/cab/dia` : `Parceria · RC entrada ${sc.rcEntrada}%` }),
            /* @__PURE__ */ jsx(ItemRelatorio, { label: "Peso processado / abate", value: sc.tipo === "revenda" ? `${fN(r.pesoProc, 1)} kg` : `${fN(r.pesoProc, 1)} · ${fN(r.pesoAbate, 1)} kg` }),
            /* @__PURE__ */ jsx(ItemRelatorio, { label: "Rota / responsável", value: `${sc.origemFrete || "origem não informada"} → ${sc.destinoFrete || "destino não informado"} · ${sc.respFrete || "—"}` }),
            /* @__PURE__ */ jsx(ItemRelatorio, { label: "Transporte", value: `${fR(r.freteTotal)} · ${sc.freteNoAcerto ? "acerto final" : "à vista"}` }),
            /* @__PURE__ */ jsx(ItemRelatorio, { label: "Custo confinamento", value: fR(r.custoCont) }),
            /* @__PURE__ */ jsx(ItemRelatorio, { label: "Custo @ posta", value: fR(r.custoArrobaPosta) }),
            /* @__PURE__ */ jsx(ItemRelatorio, { label: "@ líquidas produzidas", value: sc.tipo === "revenda" ? "—" : `${fAt(r.arrobasProduzidasCab)} / cab` }),
            /* @__PURE__ */ jsx(ItemRelatorio, { label: "Custo @ líquida produzida", value: sc.tipo === "revenda" ? "—" : fR(r.custoArrobaLiquidaProduzida) }),
            /* @__PURE__ */ jsx(ItemRelatorio, { label: "Custo marginal @", value: sc.tipo === "revenda" ? "—" : fR(r.custoArrobaMarginal) }),
            /* @__PURE__ */ jsx(ItemRelatorio, { label: "Produção + frete / @", value: sc.tipo === "revenda" ? "—" : fR(r.custoProducaoFretePorArroba) }),
            /* @__PURE__ */ jsx(ItemRelatorio, { label: "Capital", value: fR(r.investInicial) }),
            /* @__PURE__ */ jsx(ItemRelatorio, { label: "Lucro bruto / final", value: `${fR(r.lucro)} · ${fR(r.lucroLiquido)}` }),
            /* @__PURE__ */ jsx(ItemRelatorio, { label: "Rentabilidade", value: `${fP(r.rTliq)} total · ${fP(r.rMliq)} a.m.` }),
            /* @__PURE__ */ jsx(ItemRelatorio, { label: "Operação financeira", value: r.valorAdiantamento > 0 ? `${r.tipoAdiantamento === "recebimento" ? "Antecipação do recebimento" : "Adiantamento de capital"} · ${fR(r.valorAdiantamento)} · ${r.diasAdiantamento} dias · custo ${fR(r.custoAdiantamento)}${r.tipoAdiantamento === "recebimento" ? ` · saldo final ${fR(r.saldoRecebimentoFinal)}` : ""}` : "Não simulada" })
          ] }),
          sc.tipo !== "revenda" && /* @__PURE__ */ jsxs(Fragment, { children: [
            /* @__PURE__ */ jsx("h2", { children: "Evolução de custo e rentabilidade" }),
            /* @__PURE__ */ jsx("table", { className: "report-table report-table-compact", children: /* @__PURE__ */ jsxs("tbody", { children: [
              /* @__PURE__ */ jsxs("tr", { children: ["Dias", "Saída", "BGI", "@ prod./cab", "Custo @ prod.", "Frete/@", "Prod.+frete/@", "Rent. mês", "Resultado"].map((h) => /* @__PURE__ */ jsx("th", { children: h }, h)) }),
              evolucao.map((ponto) => {
                const e = ponto.resultado;
                return /* @__PURE__ */ jsxs("tr", { children: [
                  /* @__PURE__ */ jsx("td", { children: ponto.dias }),
                  /* @__PURE__ */ jsx("td", { children: fmtData(ponto.dataSaida) }),
                  /* @__PURE__ */ jsx("td", { children: ponto.cotacao == null ? `${ponto.contrato} · pendente` : `${ponto.contrato} · ${fR(ponto.cotacao)}` }),
                  /* @__PURE__ */ jsx("td", { children: e ? fAt(e.arrobasProduzidasCab) : "—" }),
                  /* @__PURE__ */ jsx("td", { children: e ? fR(e.custoArrobaLiquidaProduzida) : "—" }),
                  /* @__PURE__ */ jsx("td", { children: e ? fR(e.fretePorArrobaProduzida) : "—" }),
                  /* @__PURE__ */ jsx("td", { children: e ? fR(e.custoProducaoFretePorArroba) : "—" }),
                  /* @__PURE__ */ jsx("td", { children: e ? fP(e.rMliq) : "—" }),
                  /* @__PURE__ */ jsx("td", { children: e ? fR(e.lucroLiquido) : "—" })
                ] }, ponto.dias);
              })
            ] }) })
          ] })
        ] }, sc.id);
      })
    ] })
  ] });
}
function Comparativo({ resultados, cenarios, lote }) {
  const ativos = cenarios.map((sc, i) => ({ sc, r: resultados[i], i })).filter((x) => x.r);
  if (!ativos.length) return null;
  const ranked = [...ativos].sort((a, b) => b.r.rMliq - a.r.rMliq || b.r.lucroLiquido - a.r.lucroLiquido || b.r.rTliq - a.r.rTliq || a.i - b.i);
  const semCustoFrete = (r) => r.respFrete === "confinamento" || r.freteTotal === 0 && r.fretePorCab === 0;
  const rows = [
    { l: "Arrobas compra / cab", fn: (r) => fAt(r.arrobasCompra) },
    { l: "Perda no transporte", fn: (r) => fP(r.pctPerda) },
    { l: "Peso chegada (kg/cab)", fn: (r) => fN(r.pesoChegada, 1) },
    { l: "Peso abate (kg/cab)", fn: (r) => r.tipo === "revenda" ? "\u2014" : fN(r.pesoAbate, 1) },
    { l: "RC Final (%)", fn: (r) => r.tipo === "revenda" ? "\u2014" : fP(r.rcFinal) },
    { l: "@ entregues/abate / cab", fn: (r) => r.tipo === "revenda" ? fAt(r.arrobasEntrega) : fAt(r.arrobasAbate) },
    { l: "Pre\xE7o venda l\xEDq. (R$/@)", fn: (r) => fR(r.precoVendaLiq) },
    { l: "Sa\xEDda / contrato B3", fn: (r, sc) => sc?.tipo === "revenda" ? "\u2014" : `${fmtData(addDiasISO(sc?.dataEntrada, parseFloat(sc?.diasCiclo) || 0))} \xB7 ${sc?.contratoB3 || contratoB3PorData(addDiasISO(sc?.dataEntrada, parseFloat(sc?.diasCiclo) || 0)) || "\u2014"}` },
    { l: "VP da @ (R$/@)", fn: (r) => fR(r.vpArroba), hint: "Valor presente \u2014 pre\xE7o descontado pelo custo do dinheiro" },
    { l: "Pre\xE7o limite de compra (VP)", fn: (r) => fR(r.precoCompraVpMax), hint: "Maior R$/@ de compra que ainda empata o neg\xF3cio em valor presente, j\xE1 descontando frete, confinamento e custo do dinheiro.", cls: (r) => r.margemCompraVp >= 0 ? "pos" : "neg" },
    { l: "Margem vs compra atual (R$/@)", fn: (r) => fR(r.margemCompraVp), cls: (r) => r.margemCompraVp >= 0 ? "pos" : "neg" },
    { sep: true, l: "MÉTRICAS DE ARROBA" },
    { l: "Peso processado (kg/cab)", fn: (r) => fN(r.pesoProc, 1) },
    { l: "Arrobas postas a 50% RC / cab", fn: (r) => fAt(r.arrobasPostasCab) },
    { l: "Custo da @ posta (compra + frete)", fn: (r) => fR(r.custoArrobaPosta), bold: true },
    { l: "Carcaça líquida produzida (kg/cab)", fn: (r) => r.tipo === "revenda" ? "—" : fN(r.kgCarcacaProduzidaCab, 1) },
    { l: "Arrobas líquidas produzidas / cab", fn: (r) => r.tipo === "revenda" ? "—" : fAt(r.arrobasProduzidasCab) },
    { l: "Custo da @ líquida produzida", fn: (r) => r.tipo === "revenda" ? "—" : fR(r.custoArrobaLiquidaProduzida), bold: true },
    { l: "Custo marginal da @ de ganho", fn: (r) => r.tipo === "revenda" ? "—" : fR(r.custoArrobaMarginal) },
    { l: "Frete diluído / @ produzida", fn: (r) => r.tipo === "revenda" ? "—" : fR(r.fretePorArrobaProduzida) },
    { l: "Produção + frete / @ produzida", fn: (r) => r.tipo === "revenda" ? "—" : fR(r.custoProducaoFretePorArroba), bold: true },
    { sep: true, l: "CUSTOS \u2014 TOTAL DO LOTE" },
    { l: "Custo de compra", fn: (r) => fR(r.custoCompra) },
    { l: "Bois por carreta", fn: (r) => semCustoFrete(r) ? "\u2014" : r.qtdCarretas > 0 ? fN(r.boisPorCarreta, 0) : "\u2014" },
    { l: "Qtde carretas", fn: (r) => semCustoFrete(r) ? "\u2014" : r.qtdCarretas > 0 ? fN(r.qtdCarretas, 0) : "\u2014" },
    { l: "Frete por carreta", fn: (r) => semCustoFrete(r) ? fR(0) : r.qtdCarretas > 0 ? fR(r.fretePorCarreta) : "\u2014" },
    { l: "Frete total", fn: (r) => fR(r.freteTotal) },
    { l: "Frete incorporado ao capital", fn: (r) => fR(r.capitalFrete), hint: "Zero quando o frete for pago somente no acerto final" },
    { l: "Frete por cabe\xE7a", fn: (r) => fR(r.fretePorCab) },
    { l: "Consumo MS (kg/dia/cab)", fn: (r, sc) => sc?.modalidade === "ms" ? fN(r.msTotalKgCab / (parseFloat(sc?.diasCiclo) || 110), 1) : "\u2014" },
    { l: "Custo confinamento", fn: (r) => r.tipo === "revenda" ? "\u2014" : fR(r.custoCont) },
    { l: "Total custos", fn: (r) => fR(r.custos), bold: true },
    { sep: true, l: "RESULTADO \u2014 TOTAL DO LOTE" },
    { l: "Faturamento bruto da venda", fn: (r) => fR(r.faturamentoBruto ?? r.receita), cls: () => "pos" },
    { l: "Funrural", fn: (r) => r.tipo === "revenda" ? "\u2014" : fR(r.valorFunrural || 0), cls: () => "neg" },
    { l: "Finpec", fn: (r) => r.tipo === "revenda" ? "\u2014" : fR(r.valorFinpec || 0), cls: () => "neg" },
    { l: "Receita líquida", fn: (r) => fR(r.receita), cls: () => "pos" },
    { l: "Receita a valor presente", fn: (r) => fR(r.receitaVP), cls: () => "pos" },
    { l: "Lucro bruto", fn: (r) => fR(r.lucro), bold: true, cls: (r) => r.lucro >= 0 ? "pos" : "neg", best: true },
    { l: "Lucro bruto / cab", fn: (r) => fR(r.lucro / r.N), cls: (r) => r.lucro >= 0 ? "pos" : "neg" },
    { l: "Custo financeiro da compra", fn: (r) => fR(r.custoDinheiroCompra), cls: () => "neg" },
    { l: "Custo financeiro do frete", fn: (r) => fR(r.custoDinheiroFrete), cls: () => "neg" },
    { l: "Opera\xE7\xE3o financeira", fn: (r) => r.valorAdiantamento > 0 ? r.tipoAdiantamento === "recebimento" ? "Antecipa\xE7\xE3o do recebimento" : "Adiantamento de capital" : "\u2014" },
    { l: "Valor da opera\xE7\xE3o", fn: (r) => r.valorAdiantamento > 0 ? fR(r.valorAdiantamento) : "\u2014" },
    { l: "Per\xEDodo da opera\xE7\xE3o", fn: (r) => r.valorAdiantamento > 0 ? `${fmtData(r.dataAdiantamento)} a ${fmtData(r.dataRecebimentoAdiantamento)} \xB7 ${fN(r.diasAdiantamento, 0)} dias` : "\u2014" },
    { l: "Custo da opera\xE7\xE3o", fn: (r) => r.valorAdiantamento > 0 ? fR(r.custoAdiantamento) : "\u2014", cls: () => "neg" },
    { l: "Valor recebido antecipadamente", fn: (r) => r.tipoAdiantamento === "recebimento" && r.valorAdiantamento > 0 ? fR(r.valorRecebidoAntecipado) : "\u2014", cls: () => "pos" },
    { l: "Saldo previsto no acerto final", fn: (r) => r.tipoAdiantamento === "recebimento" && r.valorAdiantamento > 0 ? fR(r.saldoRecebimentoFinal) : "\u2014" },
    { l: "Custo do dinheiro total", fn: (r) => fR(r.custoDinheiroTotal), cls: () => "neg" },
    { l: "Lucro l\xEDquido sem opera\xE7\xE3o financeira", fn: (r) => fR(r.lucroLiquidoSemAdiantamento), cls: (r) => r.lucroLiquidoSemAdiantamento >= 0 ? "pos" : "neg" },
    { l: "Resultado final com opera\xE7\xE3o financeira", fn: (r) => fR(r.lucroLiquido), bold: true, cls: (r) => r.lucroLiquido >= 0 ? "pos" : "neg" },
    { l: "Lucro l\xEDquido / cab", fn: (r) => fR(r.lucroLiquido / r.N), cls: (r) => r.lucroLiquido >= 0 ? "pos" : "neg" },
    { l: "Resultado VP", fn: (r) => fR(r.resultadoVP), bold: true, cls: (r) => r.resultadoVP >= 0 ? "pos" : "neg" },
    { sep: true, l: "RENTABILIDADE" },
    { l: "Capital compra dos bois", fn: (r) => fR(r.capitalCompra), bold: true },
    { l: "Capital frete pago \xE0 vista", fn: (r) => fR(r.capitalFrete), bold: true },
    { l: "Capital total investido", fn: (r) => fR(r.investInicial), bold: true },
    { l: "Ciclo (dias)", fn: (r) => r.tipo === "revenda" ? "\u2014" : fN(r.diasTotal - r.diasPag, 0) },
    { l: "Prazo compra (dias)", fn: () => fN(parseFloat(lote.prazoPagtoCompra) || 0, 0) },
    { l: "Prazo recebimento (dias)", fn: (r) => fN(r.diasPag, 0) },
    { l: "Prazo total (dias)", fn: (r) => fN(r.diasTotal, 0), bold: true },
    { l: "Tempo capital investido (meses)", fn: (r) => fN(r.mesesCapital, 1) },
    { l: "Rent. total (sem custo $)", fn: (r) => fP(r.rentTotal), cls: (r) => r.rentTotal >= 0 ? "pos" : "neg" },
    { l: "Rent. mensal (sem custo $)", fn: (r) => fP(r.rentMensal), cls: (r) => r.rentMensal >= 0 ? "pos" : "neg" },
    { l: "Rent. total sem opera\xE7\xE3o financeira", fn: (r) => fP(r.rTliqSemAdiantamento), cls: (r) => r.rTliqSemAdiantamento >= 0 ? "pos" : "neg" },
    { l: "Rent. total final", fn: (r) => fP(r.rTliq), cls: (r) => r.rTliq >= 0 ? "pos" : "neg" },
    { l: "Rent. mensal sem opera\xE7\xE3o financeira", fn: (r) => fP(r.rMliqSemAdiantamento ?? r.rMliq), bold: true, cls: (r) => (r.rMliqSemAdiantamento ?? r.rMliq) >= 0 ? "pos" : "neg" },
    { l: "Impacto da opera\xE7\xE3o na rent. mensal", fn: (r) => `${fN(r.impactoAdiantamentoMensal ?? 0, 2)} p.p.`, cls: (r) => (r.impactoAdiantamentoMensal ?? 0) >= 0 ? "pos" : "neg" },
    { l: "Rent. mensal final", fn: (r) => `${fP(r.rMliq)} a.m.`, bold: true, cls: (r) => r.rMliq >= 0 ? "pos" : "neg", best: true }
  ];
  return /* @__PURE__ */ jsxs("div", { className: "res-wrap", children: [
    /* @__PURE__ */ jsx("div", { className: "res-ttl", children: "Resultado" }),
    /* @__PURE__ */ jsxs("div", { className: "res-sub", children: [
      lote.qtd,
      " cab \xB7 ",
      lote.sexo === "macho" ? "Macho" : "F\xEAmea",
      " \xB7 ",
      lote.pesoMedio,
      " kg \xB7 ",
      lote.origemNome || "\u2014"
    ] }),
    /* @__PURE__ */ jsx("div", { className: "rank-row", children: ranked.map(({ r, sc, i }, pos) => /* @__PURE__ */ jsx("div", { className: `rcard ${pos === 0 ? "best" : ""}`, style: { "--c": T.sc[i] }, children: /* @__PURE__ */ jsxs(Fragment, { children: [
        /* @__PURE__ */ jsx("div", { className: "rn", children: pos + 1 }),
        /* @__PURE__ */ jsx("div", { className: "rname", children: sc.nome }),
        /* @__PURE__ */ jsx("div", { className: "rtype", children: r.tipo === "revenda" ? "Revenda" : sc.modalidade }),
        /* @__PURE__ */ jsxs("div", { className: `rval ${r.rMliq >= 0 ? "pos" : "neg"}`, style: { fontSize: 22, fontWeight: 700 }, children: [
          fP(r.rMliq),
          " a.m."
        ] }),
        /* @__PURE__ */ jsxs("div", { className: "rsub", children: [
          "rent. total: ",
          fP(r.rTliq),
          " \xB7 ",
          fN(r.meses, 1),
          " meses"
        ] }),
        r.valorAdiantamento > 0 && /* @__PURE__ */ jsxs("div", { className: "rsub", style: { marginTop: 5 }, children: [
          r.tipoAdiantamento === "recebimento" ? "antecipa\xE7\xE3o: " : "adiantamento: ",
          fN(r.diasAdiantamento, 0),
          " dias \xB7 ",
          fR(r.custoAdiantamento),
          " \xB7 ",
          fP(r.rMliqSemAdiantamento ?? r.rMliq),
          " \u2192 ",
          fP(r.rMliq),
          " a.m."
        ] }),
        /* @__PURE__ */ jsxs("div", { className: "rsub", children: [
          "@ posta: ",
          fR(r.custoArrobaPosta),
          " \xB7 @ produzida: ",
          r.tipo === "revenda" ? "\u2014" : fR(r.custoArrobaLiquidaProduzida)
        ] }),
        /* @__PURE__ */ jsxs("div", { className: "rsub", children: [
          "lucro bruto: ",
          fR(r.lucro)
        ] }),
        /* @__PURE__ */ jsxs("div", { className: "rsub", children: [
          "lucro l\xEDq: ",
          fR(r.lucroLiquido)
        ] }),
        /* @__PURE__ */ jsxs("div", { className: "rsub", children: [
          "limite compra VP: ",
          fR(r.precoCompraVpMax),
          "/@"
        ] }),
        /* @__PURE__ */ jsxs("div", { className: "rsub", children: [
          "capital: ",
          fR(r.investInicial)
        ] })
      ] }) }, sc.id)) }),
    /* @__PURE__ */ jsx("div", { className: "sec", style: { padding: 0 }, children: /* @__PURE__ */ jsx("div", { className: "tbl-wrap", children: /* @__PURE__ */ jsxs("table", { className: "cmp-tbl", children: [
      /* @__PURE__ */ jsx("thead", { children: /* @__PURE__ */ jsxs("tr", { children: [
        /* @__PURE__ */ jsx("th", { style: { textAlign: "left" }, children: "Item" }),
        ranked.map(({ sc, i }, pos) => /* @__PURE__ */ jsx("th", { className: "sc-th", style: { "--c": T.sc[i] }, children: `${pos + 1}\xBA \xB7 ${sc.nome}` }, sc.id))
      ] }) }),
      /* @__PURE__ */ jsx("tbody", { children: rows.map((row, ri) => {
        if (row.sep) return /* @__PURE__ */ jsx("tr", { className: "grp", children: /* @__PURE__ */ jsx("td", { colSpan: ranked.length + 1, children: row.l }) }, ri);
        let nums = null;
        if (row.best) {
          nums = ranked.map(({ r }) => {
            const s = row.fn(r).replace(/[R$\s%@\u00a0]/g, "").replace(/\./g, "").replace(",", ".");
            return parseFloat(s);
          });
        }
        return /* @__PURE__ */ jsxs("tr", { className: row.bold ? "tot" : "", children: [
          /* @__PURE__ */ jsx("td", { children: row.l }),
          ranked.map(({ r, sc, i }, ai) => {
            const cls = row.cls ? row.cls(r, sc) : "";
            const isBest = row.best && nums && Math.max(...nums.filter((n) => !isNaN(n))) === nums[ai];
            return /* @__PURE__ */ jsx(
              "td",
              {
                className: `${cls} ${isBest ? "hi" : ""}`,
                style: isBest ? { color: T.sc[i] } : {},
                children: row.fn(r, sc)
              },
              sc.id
            );
          })
        ] }, ri);
      }) })
    ] }) }) })
  ] });
}
function Confinex() {
  const [initialState] = useState(loadSavedState);
  const [lote, setLote] = useState(initialState.lote);
  const updLote = (k, v) => setLote((p) => ({ ...p, [k]: v }));
  const [cenarios, setCenarios] = useState(initialState.cenarios);
  const [scAtivo, setScAtivo] = useState(initialState.scAtivo);
  const [resultados, setResultados] = useState(initialState.resultados);
  const [confinamentos, setConfinamentos] = useState(initialState.confinamentos || []);
  const [historico, setHistorico] = useState(initialState.historico || []);
  const [modeloSelecionado, setModeloSelecionado] = useState("");
  const [statusB3, setStatusB3] = useState("");
  const [statusDistancia, setStatusDistancia] = useState("");
  const [backendUrl, setBackendUrl] = useState(getStoredSheetsBackendUrl);
  const [statusSheets, setStatusSheets] = useState(backendUrl ? "Backend Sheets configurado." : "Sem backend Sheets: salvando apenas neste navegador.");
  const [statusSupabase, setStatusSupabase] = useState("Supabase: aguardando um negócio ser iniciado.");
  const [versoesSalvas, setVersoesSalvas] = useState(carregarVersoesNomeadas);
  const [versaoSelecionada, setVersaoSelecionada] = useState("");
  const contratosB3Estudo = [...new Set([...cenarios.map(contratoB3DoCenario).filter(Boolean), ...contratosB3DaEvolucao(cenarios)])].sort(compararContratosB3);
  useEffect(() => {
    try {
      localStorage.setItem(APP_STORAGE_KEY, JSON.stringify({
        lote,
        cenarios,
        confinamentos,
        historico,
        scAtivo,
        resultados,
        data: (/* @__PURE__ */ new Date()).toISOString()
      }));
    } catch {
    }
  }, [lote, cenarios, confinamentos, historico, scAtivo, resultados]);
  const estadoAtual = () => ({
    lote,
    cenarios,
    confinamentos,
    historico,
    scAtivo,
    resultados,
    data: (/* @__PURE__ */ new Date()).toISOString(),
    versao: "1.3-supabase"
  });
  const aplicarEstado = (state) => {
    const cenariosBase = Array.isArray(state.cenarios) && state.cenarios.length ? state.cenarios.slice(0, 5).map((sc, i) => ({ ...defaultSc(i), ...sc })) : cenarios;
    const loteBase = state.lote ? { ...defaultLote, ...state.lote, cotacoesB3: { ...state.lote.cotacoesB3 || {} } } : lote;
    const mercadoNormalizado = normalizarMercadoB3(loteBase, cenariosBase);
    if (state.lote) setLote(mercadoNormalizado.lote);
    if (Array.isArray(state.cenarios) && state.cenarios.length) {
      setCenarios(mercadoNormalizado.cenarios);
      setScAtivo(Math.min(Math.max(parseInt(state.scAtivo, 10) || 0, 0), Math.min(state.cenarios.length, 5) - 1));
    }
    if (Array.isArray(state.confinamentos)) setConfinamentos(state.confinamentos);
    if (Array.isArray(state.historico)) setHistorico(state.historico);
    if (Array.isArray(state.resultados)) setResultados(state.resultados);
    setModeloSelecionado("");
  };
  const aplicarEstadoSheets = (data) => {
    aplicarEstado(normalizeSheetsState(data));
  };
  const salvarPontoRetorno = () => {
    localStorage.setItem(RESTORE_STORAGE_KEY, JSON.stringify(estadoAtual()));
  };
  const resetarInformacoes = async () => {
    if (!window.confirm("Resetar para um novo estudo? O estado atual sera salvo como versao e as bases de confinamento ficarao disponiveis.")) return;
    const snapshotAntesReset = estadoAtual();
    const dataLabel = (/* @__PURE__ */ new Date()).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
    const versaoReset = {
      id: `${Date.now()}`,
      nome: `Antes do reset - ${lote.origemNome || "Confinex"} - ${dataLabel}`,
      criadaEm: (/* @__PURE__ */ new Date()).toISOString(),
      resumo: `${cenarios.length} cenario(s), ${historico.length} teste(s)`,
      state: snapshotAntesReset
    };
    try {
      localStorage.setItem(RESTORE_STORAGE_KEY, JSON.stringify(snapshotAntesReset));
      persistirVersoes([versaoReset, ...versoesSalvas].slice(0, 80));
      setVersaoSelecionada(versaoReset.id);
    } catch {
    }
    const limpo = { ...estadoPadraoLimpo(), confinamentos };
    aplicarEstado(limpo);
    const url = backendUrl.trim();
    if (url) {
      try {
        await sheetsPost(url, "saveVersion", versaoReset);
        setStatusSheets("Novo estudo iniciado. Versao anterior salva no Sheets e bases preservadas.");
        return;
      } catch {
      }
    }
    setStatusSheets("Novo estudo iniciado. Versao anterior salva localmente e bases preservadas.");
  };
  const retornarAntesReset = () => {
    try {
      const raw = localStorage.getItem(RESTORE_STORAGE_KEY);
      if (!raw) {
        setStatusSheets("Ainda nao existe ponto de retorno salvo.");
        return;
      }
      aplicarEstado(JSON.parse(raw));
      setStatusSheets("Estado anterior restaurado.");
    } catch {
      setStatusSheets("Nao consegui restaurar o estado anterior.");
    }
  };
  const persistirVersoes = (lista) => {
    localStorage.setItem(VERSION_STORAGE_KEY, JSON.stringify(lista));
    setVersoesSalvas(lista);
  };
  const carregarVersoesSheets = async (mostrarStatus = true) => {
    const url = backendUrl.trim();
    if (!url) {
      if (mostrarStatus) setStatusSheets("Cole a URL do Apps Script para carregar versoes.");
      return;
    }
    try {
      const data = await sheetsJsonp(url, { action: "getVersions" });
      const nuvem = Array.isArray(data?.versions) ? data.versions : [];
      // PATCH Fase 0 (R6): MERGE por id — versoes locais ainda nao enviadas
      // nao somem; as pendentes sao reenviadas em background.
      const nuvemIds = new Set(nuvem.map((v) => String(v.id)));
      const soLocais = versoesSalvas.filter((v) => !nuvemIds.has(String(v.id)));
      const lista = [...nuvem, ...soLocais].sort((a, b) => String(b.criadaEm || "").localeCompare(String(a.criadaEm || "")));
      persistirVersoes(lista.slice(0, 80));
      soLocais.forEach((v) => { sheetsPost(url, "saveVersion", v).catch(() => {}); });
      if (!versaoSelecionada && lista[0]) setVersaoSelecionada(lista[0].id);
      if (mostrarStatus) setStatusSheets(`${nuvem.length} versao(oes) na nuvem${soLocais.length ? `; ${soLocais.length} local(is) reenviada(s)` : ""}.`);
    } catch {
      if (mostrarStatus) setStatusSheets("Nao consegui carregar versoes do Sheets; mantendo lista local.");
    }
  };
  const salvarVersaoNomeada = async () => {
    const dataLabel = (/* @__PURE__ */ new Date()).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" });
    const nomePadrao = `${lote.origemNome || "Confinex"} - ${dataLabel}`;
    const nome = window.prompt("Nome da versao para salvar", nomePadrao);
    if (!nome) return;
    const versao = {
      id: `${Date.now()}`,
      nome: nome.trim() || nomePadrao,
      criadaEm: (/* @__PURE__ */ new Date()).toISOString(),
      resumo: `${cenarios.length} cenario(s), ${historico.length} teste(s)`,
      state: estadoAtual()
    };
    const lista = [versao, ...versoesSalvas].slice(0, 80);
    persistirVersoes(lista);
    setVersaoSelecionada(versao.id);
    const db = window.CFAgro?.db;
    if (db) {
      try {
        const { data: sessao } = await db.auth.getSession();
        if (sessao?.session) {
          const { error } = await db.from("confinex_testes").insert({
            nome: versao.nome,
            dispositivo: confinexDeviceId(),
            estado: versao.state
          });
          if (error) throw error;
          setStatusSupabase(`Teste salvo no Supabase: ${versao.nome}.`);
        } else {
          setStatusSupabase("Teste salvo localmente; entre no ecossistema para sincronizar com o Supabase.");
        }
      } catch (err) {
        setStatusSupabase(`Teste salvo localmente; Supabase indisponível (${err?.message || "erro"}).`);
      }
    }
    const url = backendUrl.trim();
    if (!url) {
      setStatusSheets(`Versao salva localmente: ${versao.nome}.`);
      return;
    }
    try {
      await sheetsPost(url, "saveVersion", versao);
      setStatusSheets(`Versao salva no Sheets: ${versao.nome}.`);
    } catch {
      setStatusSheets(`Versao salva localmente; nao consegui enviar ao Sheets agora.`);
    }
  };
  const restaurarVersaoNomeada = () => {
    const versao = versoesSalvas.find((v) => String(v.id) === String(versaoSelecionada));
    if (!versao) {
      setStatusSheets("Selecione uma versao salva para restaurar.");
      return;
    }
    try {
      salvarPontoRetorno();
    } catch {
    }
    aplicarEstado(versao.state || {});
    setStatusSheets(`Versao restaurada: ${versao.nome}.`);
  };
  const apagarVersaoNomeada = async () => {
    const versao = versoesSalvas.find((v) => String(v.id) === String(versaoSelecionada));
    if (!versao) {
      setStatusSheets("Selecione uma versao salva para apagar.");
      return;
    }
    if (!window.confirm(`Apagar a versao "${versao.nome}"?`)) return;
    const lista = versoesSalvas.filter((v) => String(v.id) !== String(versaoSelecionada));
    persistirVersoes(lista);
    setVersaoSelecionada(lista[0]?.id || "");
    const url = backendUrl.trim();
    if (!url) {
      setStatusSheets("Versao apagada localmente.");
      return;
    }
    try {
      await sheetsPost(url, "deleteVersion", { id: versao.id });
      setStatusSheets("Versao apagada do Sheets.");
    } catch {
      setStatusSheets("Versao apagada localmente; nao consegui apagar do Sheets agora.");
    }
  };
  const carregarSheets = async () => {
    const url = backendUrl.trim();
    if (!url) {
      setStatusSheets("Cole a URL do Apps Script para carregar do Sheets.");
      return;
    }
    setStatusSheets("Carregando do Google Sheets...");
    try {
      const data = await sheetsJsonp(url, { action: "getState" });
      skipNextAutoSaveRef.current = true;
      cloudUpdatedAtRef.current = String(data?.updated_at || "");
      cloudReadyRef.current = true;
      aplicarEstadoSheets(data);
      carregarVersoesSheets(false);
      setStatusSheets("Dados carregados do Google Sheets.");
    } catch {
      setStatusSheets("Nao consegui carregar do Sheets. Confira a URL publicada do Apps Script.");
    }
  };
  const salvarSheetsAgora = async () => {
    const url = backendUrl.trim();
    if (!url) {
      setStatusSheets("Cole a URL do Apps Script para salvar no Sheets.");
      return;
    }
    setStatusSheets("Salvando no Google Sheets...");
    try {
      // Save manual = intencional: sem carimbo, passa por cima de conflito.
      const res = await sheetsPost(url, "saveState", { state: estadoAtual(), device: confinexDeviceId() });
      cloudUpdatedAtRef.current = String(res?.updated_at || "");
      cloudReadyRef.current = true;
      setStatusSheets(`Salvo no Google Sheets as ${(/* @__PURE__ */ new Date()).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}.`);
    } catch (err) {
      setStatusSheets(`Nao consegui salvar no Sheets (${err?.message || "erro"}). Os dados continuam neste navegador.`);
    }
  };
  useEffect(() => {
    storeSheetsBackendUrl(backendUrl);
  }, [backendUrl]);
  // ==================== PATCH Fase 0 (R1/R4): carregar ANTES de salvar ====================
  // cloudReadyRef: auto-save fica bloqueado até o estado da nuvem ser carregado
  // (ou o usuário decidir explicitamente sobrescrever). Mata o bug de abrir o app
  // num navegador novo e apagar Bases/Historico na nuvem 1,2s depois.
  const cloudReadyRef = useRef(false);
  const cloudUpdatedAtRef = useRef("");
  const skipNextAutoSaveRef = useRef(true);
  useEffect(() => {
    const url = backendUrl.trim();
    if (!url) return;
    cloudReadyRef.current = false;
    let cancelado = false;
    (async () => {
      try {
        const data = await sheetsJsonp(url, { action: "getState" });
        if (cancelado) return;
        cloudUpdatedAtRef.current = String(data?.updated_at || "");
        const temNuvem = data?.state && (data.state.cenarios?.length || data.state.confinamentos?.length || data.state.historico?.length);
        const temLocal = cenarios.length > 1 || confinamentos.length || historico.length || (lote.origemNome || "");
        if (temNuvem && !temLocal) {
          skipNextAutoSaveRef.current = true;
          aplicarEstadoSheets(data);
          setStatusSheets("Estado carregado da nuvem.");
        } else if (temNuvem && temLocal) {
          setStatusSheets("Nuvem e este navegador têm dados. Use Carregar (nuvem) ou Salvar (sobrescrever).");
        }
        cloudReadyRef.current = true;
        carregarVersoesSheets(false);
      } catch {
        if (!cancelado) setStatusSheets("Sem conexao com a nuvem — alteracoes NAO estao sendo salvas no Sheets.");
      }
    })();
    return () => { cancelado = true; };
  }, [backendUrl]);
  // Auto-save: 10s de debounce (era 1,2s), só depois do load da nuvem,
  // com carimbo do estado-base para o backend detectar conflito (R4).
  useEffect(() => {
    const url = backendUrl.trim();
    if (!url) return;
    if (skipNextAutoSaveRef.current) { skipNextAutoSaveRef.current = false; return; }
    const timer = setTimeout(() => {
      if (!cloudReadyRef.current) return;
      const payload = { state: estadoAtual(), clientUpdatedAt: cloudUpdatedAtRef.current, device: confinexDeviceId() };
      sheetsPost(url, "saveState", payload).then((res) => {
        cloudUpdatedAtRef.current = String(res?.updated_at || cloudUpdatedAtRef.current);
        setStatusSheets(`Auto-salvo no Sheets as ${(/* @__PURE__ */ new Date()).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" })}.`);
      }).catch((err) => {
        if (err && err.backend && err.backend.error === "conflict") {
          setStatusSheets("CONFLITO: outro dispositivo salvou depois. Use Carregar para trazer a nuvem, ou Salvar para forcar.");
        } else {
          setStatusSheets(`Auto-save falhou (${err?.message || "erro"}). Dados seguem salvos neste navegador.`);
        }
      });
    }, 1e4);
    return () => clearTimeout(timer);
  }, [backendUrl, lote, cenarios, confinamentos, historico, scAtivo, resultados]);
  // Flush ao sair da página (fechar aba / trocar de app no iPhone)
  useEffect(() => {
    const handler = () => {
      const url = backendUrl.trim();
      if (!url || !cloudReadyRef.current) return;
      sheetsBeacon(url, "saveState", { state: estadoAtual(), clientUpdatedAt: cloudUpdatedAtRef.current, device: confinexDeviceId() });
    };
    window.addEventListener("pagehide", handler);
    document.addEventListener("visibilitychange", () => { if (document.visibilityState === "hidden") handler(); });
    return () => window.removeEventListener("pagehide", handler);
  }, [backendUrl, lote, cenarios, confinamentos, historico, scAtivo, resultados]);
  // ==================== fim do PATCH Fase 0 ====================
  const addSc = () => {
    if (cenarios.length >= 5) return;
    const idx = cenarios.length;
    const novo = defaultSc(idx);
    const contrato = contratoB3DoCenario(novo);
    const cotacao = lote.cotacoesB3?.[contrato];
    if (cotacao) {
      novo.contratoB3 = contrato;
      novo.precoBolsa = String(cotacao.preco);
      novo.cotacaoB3Fonte = cotacao.fonte || "";
      novo.cotacaoB3AtualizadaEm = cotacao.atualizadaEm || "";
    }
    setCenarios((p) => [...p, novo]);
    setScAtivo(idx);
    setResultados([]);
  };
  const delSc = (i) => {
    if (cenarios.length <= 1) return;
    const novo = cenarios.filter((_, j) => j !== i);
    setCenarios(novo);
    setScAtivo(Math.min(scAtivo, novo.length - 1));
    setResultados([]);
  };
  const updSc = (i, k, v) => {
    setCenarios((p) => p.map((s, j) => {
      if (j !== i) return s;
      const valor = k === "contratoB3" ? String(v || "").trim().toUpperCase() : v;
      const next = { ...s, [k]: valor };
      const contrato = contratoB3DoCenario(next);
      const cotacao = lote.cotacoesB3?.[contrato];
      return cotacao ? {
        ...next,
        contratoB3: contrato,
        precoBolsa: String(cotacao.preco),
        cotacaoB3Fonte: cotacao.fonte || "",
        cotacaoB3AtualizadaEm: cotacao.atualizadaEm || ""
      } : k === "contratoB3" ? {
        ...next,
        precoBolsa: "",
        cotacaoB3Fonte: "",
        cotacaoB3AtualizadaEm: ""
      } : next;
    }));
    setResultados([]);
  };
  const patchScAtivo = (patch) => {
    setCenarios((p) => p.map((s, j) => j === scAtivo ? { ...s, ...patch } : s));
    setResultados([]);
  };
  const definirCotacaoB3 = (contrato, preco) => {
    const valor = String(preco || "");
    const agora = (/* @__PURE__ */ new Date()).toISOString();
    const registro = { preco: valor, fonte: "Valor informado manualmente", atualizadaEm: agora };
    setLote((p) => ({
      ...p,
      cotacoesB3: { ...p.cotacoesB3 || {}, [contrato]: registro },
      cotacoesB3AtualizadasEm: agora
    }));
    setCenarios((p) => p.map((sc) => contratoB3DoCenario(sc) === contrato ? {
      ...sc,
      contratoB3: contrato,
      precoBolsa: valor,
      cotacaoB3Fonte: registro.fonte,
      cotacaoB3AtualizadaEm: agora
    } : sc));
    setResultados([]);
  };
  const atualizarMercadoB3 = async () => {
    if (!contratosB3Estudo.length) {
      setStatusB3("Nenhum contrato BGI usado nos cenários atuais.");
      return;
    }
    setStatusB3(`Atualizando ${contratosB3Estudo.length} contrato(s) em conjunto...`);
    const respostas = await Promise.allSettled(contratosB3Estudo.map(async (contrato) => ({ contrato, cotacao: await buscarPrecoB3PorContrato(contrato) })));
    const agora = (/* @__PURE__ */ new Date()).toISOString();
    const obtidas = respostas.filter((r) => r.status === "fulfilled").map((r) => r.value);
    const falhas = respostas.length - obtidas.length;
    if (obtidas.length) {
      const novosRegistros = {};
      obtidas.forEach(({ contrato, cotacao }) => {
        novosRegistros[contrato] = {
          preco: String(Math.round(cotacao.preco * 100) / 100),
          fonte: cotacao.fonte,
          atualizadaEm: cotacao.data || agora
        };
      });
      setLote((p) => ({
        ...p,
        cotacoesB3: { ...p.cotacoesB3 || {}, ...novosRegistros },
        cotacoesB3AtualizadasEm: agora
      }));
      setCenarios((p) => p.map((sc) => {
        const contrato = contratoB3DoCenario(sc);
        const cotacao = novosRegistros[contrato];
        return cotacao ? {
          ...sc,
          contratoB3: contrato,
          precoBolsa: cotacao.preco,
          cotacaoB3Fonte: cotacao.fonte,
          cotacaoB3AtualizadaEm: cotacao.atualizadaEm
        } : sc;
      }));
      setResultados([]);
    }
    setStatusB3(falhas ? `${obtidas.length} contrato(s) atualizado(s); ${falhas} sem cotação automática.` : `${obtidas.length} contrato(s) atualizado(s) no mesmo lote de consulta.`);
  };
  const calcularDistancia = async () => {
    const sc = cenarios[scAtivo];
    if (!(sc.origemFrete && sc.destinoFrete)) {
      setStatusDistancia("Preencha origem e destino.");
      return;
    }
    const key = window.CONFINEX_GOOGLE_MAPS_KEY;
    const endpoint = window.CONFINEX_DISTANCE_PROXY;
    if (!key && !endpoint) {
      setStatusDistancia("Abri a rota no Google Maps. Sem chave/proxy, copie a km e cole em Dist\xE2ncia ida.");
      window.open(googleMapsUrl(sc.origemFrete, sc.destinoFrete), "_blank", "noopener,noreferrer");
      return;
    }
    setStatusDistancia("Calculando rota...");
    try {
      const url = endpoint ? `${endpoint}?origin=${encodeURIComponent(sc.origemFrete)}&destination=${encodeURIComponent(sc.destinoFrete)}` : `https://maps.googleapis.com/maps/api/distancematrix/json?units=metric&origins=${encodeURIComponent(sc.origemFrete)}&destinations=${encodeURIComponent(sc.destinoFrete)}&key=${encodeURIComponent(key)}`;
      const response = await fetch(url);
      if (!response.ok) throw new Error("maps");
      const data = await response.json();
      const meters = data.distance_meters || data.rows?.[0]?.elements?.[0]?.distance?.value;
      if (!Number.isFinite(meters)) throw new Error("distancia");
      const km = Math.round(meters / 1e3);
      patchScAtivo({ km: String(km) });
      setStatusDistancia(`Dist\xE2ncia atualizada: ${km} km.`);
    } catch {
      setStatusDistancia("N\xE3o consegui calcular automaticamente. Abrindo Google Maps para confer\xEAncia.");
      window.open(googleMapsUrl(sc.origemFrete, sc.destinoFrete), "_blank", "noopener,noreferrer");
    }
  };
  const aplicarModelo = () => {
    const modelo = confinamentos.find((m) => String(m.id) === String(modeloSelecionado));
    if (!modelo) return;
    setCenarios((p) => p.map((s, j) => j === scAtivo ? scFromModelo(s, modelo) : s));
    setResultados([]);
  };
  const salvarModelo = () => {
    const nome = window.prompt("Nome da base do confinamento", cenarios[scAtivo]?.nome || "Confinamento");
    if (!nome) return;
    const novo = modeloFromSc(cenarios[scAtivo], nome.trim());
    setConfinamentos((p) => [...p, novo]);
    setModeloSelecionado(String(novo.id));
  };
  const atualizarModelo = () => {
    const modelo = confinamentos.find((m) => String(m.id) === String(modeloSelecionado));
    if (!modelo) return;
    setConfinamentos((p) => p.map(
      (m) => String(m.id) === String(modeloSelecionado) ? { ...modeloFromSc(cenarios[scAtivo], m.nome), id: m.id } : m
    ));
  };
  const apagarModelo = () => {
    const modelo = confinamentos.find((m) => String(m.id) === String(modeloSelecionado));
    if (!modelo) return;
    if (!window.confirm(`Apagar a base "${modelo.nome}"?`)) return;
    setConfinamentos((p) => p.filter((m) => String(m.id) !== String(modeloSelecionado)));
    setModeloSelecionado("");
  };
  const exportarJSON = () => {
    const dados = { lote, cenarios, confinamentos, historico, versao: "1.3-supabase", data: (/* @__PURE__ */ new Date()).toISOString() };
    const blob = new Blob([JSON.stringify(dados, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const nomePadrao = `confinex_${lote.origemNome || "lote"}_${(/* @__PURE__ */ new Date()).toLocaleDateString("pt-BR").replace(/\//g, "-")}`;
    const nomeEscolhido = window.prompt("Nome do arquivo para salvar", nomePadrao);
    if (!nomeEscolhido) {
      URL.revokeObjectURL(url);
      return;
    }
    const nomeArquivo = nomeEscolhido.trim().replace(/[\\/:*?"<>|]+/g, "-").replace(/\s+/g, "_");
    a.download = `${nomeArquivo || nomePadrao}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };
  const importarJSON = (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      try {
        const dados = JSON.parse(ev.target.result);
        aplicarEstado({ ...dados, scAtivo: 0, resultados: [] });
        setResultados([]);
      } catch {
        alert("Arquivo inv\xE1lido \u2014 use um JSON exportado pelo Confinex.");
      }
    };
    reader.readAsText(file);
    e.target.value = "";
  };
  const calcular = () => {
    const res = cenarios.map((sc) => {
      try {
        return calcCenario(lote, sc);
      } catch {
        return null;
      }
    });
    setResultados(res);
  };
  const iniciarNegocioSupabase = async () => {
    const codigo = String(lote.codigoNegocio || "").trim().toUpperCase();
    const grupoNome = String(lote.grupoOrigemNome || "").trim();
    const resultado = resultados[scAtivo];
    const cenario = cenarios[scAtivo];
    if (!/^CF-\d{2}-\d{3,}$/.test(codigo)) {
      setStatusSupabase("Informe um código no padrão CF-26-012.");
      return;
    }
    if (!grupoNome) {
      setStatusSupabase("Informe o grupo do Telegram de origem antes de iniciar o negócio.");
      return;
    }
    if (!resultado || !cenario) {
      setStatusSupabase("Calcule os cenários e selecione o cenário aprovado antes de iniciar.");
      return;
    }
    const db = window.CFAgro?.db;
    if (!db) {
      setStatusSupabase("Supabase indisponível nesta página.");
      return;
    }
    try {
      const { data: sessao } = await db.auth.getSession();
      if (!sessao?.session) {
        setStatusSupabase("Entre em um módulo do ecossistema e volte ao Confinex para registrar o negócio.");
        return;
      }
      if (!window.confirm(`Iniciar ${codigo} com a estimativa do cenário “${cenario.nome}”? A estimativa original ficará congelada.`)) return;
      setStatusSupabase(`Registrando ${codigo} no Supabase...`);
      const { data, error } = await db.rpc("iniciar_negocio_confinex", {
        p_codigo: codigo,
        p_nome: `${codigo} — ${lote.origemNome || cenario.nome}`,
        p_grupo_origem_id: null,
        p_grupo_origem_nome: grupoNome,
        p_premissas: { lote, cenario },
        p_resultado: resultado
      });
      if (error) throw error;
      setStatusSupabase(`${codigo} iniciado. Estimativa original salva e congelada no Supabase (${data}).`);
    } catch (err) {
      setStatusSupabase(`Não consegui iniciar o negócio (${err?.message || "erro"}). Nada foi alterado localmente.`);
    }
  };
  const arrobasPrev = calcArrobas({
    peso: parseFloat(lote.pesoMedio) || 0,
    sexo: lote.sexo,
    modoCapim: lote.modoCapim,
    limCapim: lote.limCapim,
    descBezerro: lote.descBezerro,
    limBezerro: lote.limBezerro
  });
  return /* @__PURE__ */ jsxs(Fragment, { children: [
    /* @__PURE__ */ jsx("style", { children: css }),
    /* @__PURE__ */ jsxs("div", { className: "app", children: [
      /* @__PURE__ */ jsxs("div", { className: "hdr", children: [
        /* @__PURE__ */ jsxs("div", { style: { display: "flex", alignItems: "center", gap: 14 }, children: [
          /* @__PURE__ */ jsx("img", { src: "./confinex-logo.jpg", alt: "Logo Confinex", style: { width: 48, height: 48, borderRadius: "50%", objectFit: "cover", flexShrink: 0 } }),
          /* @__PURE__ */ jsxs("div", { children: [
            /* @__PURE__ */ jsxs("div", { className: "logo", children: [
              "CONFINEX ",
              /* @__PURE__ */ jsx("span", { style: { fontWeight: 400, color: "#6B7280", fontSize: 18 }, children: "\u2014 PABLO FERREIRA" })
            ] }),
            /* @__PURE__ */ jsx("div", { className: "logo-sub", children: "Avalia\xE7\xE3o & Comparativo de Confinamento" })
          ] })
        ] }),
        /* @__PURE__ */ jsxs("div", { style: { marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }, children: [
          /* @__PURE__ */ jsx("a", { href: "./", className: "tb", style: { display: "inline-flex", alignItems: "center", textDecoration: "none", whiteSpace: "nowrap" }, children: "\u2302 In\xEDcio" }),
          /* @__PURE__ */ jsxs(
            "label",
            {
              style: {
                background: "transparent",
                border: `1px solid ${T.border}`,
                borderRadius: 8,
                color: T.muted,
                cursor: "pointer",
                fontFamily: "'Inter',sans-serif",
                fontSize: 12,
                fontWeight: 500,
                padding: "8px 16px",
                transition: "all .15s",
                whiteSpace: "nowrap"
              },
              onMouseOver: (e) => {
                e.currentTarget.style.borderColor = T.label;
                e.currentTarget.style.color = T.text;
              },
              onMouseOut: (e) => {
                e.currentTarget.style.borderColor = T.border;
                e.currentTarget.style.color = T.muted;
              },
              children: [
                "\u2191 Carregar",
                /* @__PURE__ */ jsx("input", { type: "file", accept: ".json", onChange: importarJSON, style: { display: "none" } })
              ]
            }
          ),
          /* @__PURE__ */ jsx(
            "button",
            {
              onClick: exportarJSON,
              style: {
                background: T.accentDim,
                border: `1px solid ${T.accent}`,
                borderRadius: 8,
                color: T.accent,
                cursor: "pointer",
                fontFamily: "'Syne',sans-serif",
                fontSize: 12,
                fontWeight: 700,
                padding: "8px 16px",
                transition: "opacity .15s",
                whiteSpace: "nowrap"
              },
              onMouseOver: (e) => e.currentTarget.style.opacity = ".8",
              onMouseOut: (e) => e.currentTarget.style.opacity = "1",
              children: "\u2193 Salvar JSON"
            }
          )
        ] })
      ] }),
      /* @__PURE__ */ jsxs("div", { className: "sec", style: { padding: "14px 18px" }, children: [
        /* @__PURE__ */ jsx("div", { className: "sec-t nm", children: "Sincroniza\xE7\xE3o tempor\xE1ria \u2014 Google Sheets" }),
        /* @__PURE__ */ jsxs("div", { className: "g4", children: [
          /* @__PURE__ */ jsx(F, { label: "URL Apps Script", span: 2, children: /* @__PURE__ */ jsx("input", { value: backendUrl, placeholder: "https://script.google.com/macros/s/.../exec", onChange: (e) => setBackendUrl(e.target.value) }) }),
          /* @__PURE__ */ jsx(F, { label: "Carregar Sheets", children: /* @__PURE__ */ jsx("button", { className: "tb", style: { width: "100%", padding: "10px 13px" }, onClick: carregarSheets, children: "Carregar" }) }),
          /* @__PURE__ */ jsx(F, { label: "Salvar agora", children: /* @__PURE__ */ jsx("button", { className: "tb on", style: { width: "100%", padding: "10px 13px" }, onClick: salvarSheetsAgora, children: "Salvar" }) })
        ] }),
        /* @__PURE__ */ jsxs("div", { className: "g4", style: { marginTop: 10 }, children: [
          /* @__PURE__ */ jsx(F, { label: "Resetar informacoes", children: /* @__PURE__ */ jsx("button", { className: "tb", style: { width: "100%", padding: "10px 13px", color: T.red }, onClick: resetarInformacoes, children: "Resetar" }) }),
          /* @__PURE__ */ jsx(F, { label: "Retornar anterior", children: /* @__PURE__ */ jsx("button", { className: "tb", style: { width: "100%", padding: "10px 13px" }, onClick: retornarAntesReset, children: "Retornar" }) }),
          /* @__PURE__ */ jsx(F, { label: "Como funciona", span: 2, children: /* @__PURE__ */ jsx("div", { className: "hint", style: { paddingTop: 8 }, children: "Resetar salva uma versao antes de limpar e mantem as bases de confinamento. Retornar recupera a copia anterior." }) })
        ] }),
        /* @__PURE__ */ jsxs("div", { className: "g4", style: { marginTop: 10 }, children: [
          /* @__PURE__ */ jsx(F, { label: "Salvar versao", children: /* @__PURE__ */ jsx("button", { className: "tb on", style: { width: "100%", padding: "10px 13px" }, onClick: salvarVersaoNomeada, children: "Salvar versao" }) }),
          /* @__PURE__ */ jsx(F, { label: "Versoes salvas", span: 2, children: /* @__PURE__ */ jsxs("select", { value: versaoSelecionada, onChange: (e) => setVersaoSelecionada(e.target.value), children: [
            /* @__PURE__ */ jsx("option", { value: "", children: versoesSalvas.length ? "Selecione uma versao" : "Nenhuma versao salva" }),
            versoesSalvas.map((v) => /* @__PURE__ */ jsxs("option", { value: v.id, children: [
              v.nome,
              " - ",
              v.resumo || "snapshot"
            ] }, v.id))
          ] }) }),
          /* @__PURE__ */ jsx(F, { label: "Restaurar/apagar", children: /* @__PURE__ */ jsxs("div", { style: { display: "flex", gap: 6 }, children: [
            /* @__PURE__ */ jsx("button", { className: "tb", style: { flex: 1, padding: "10px 13px" }, onClick: restaurarVersaoNomeada, children: "Restaurar" }),
            /* @__PURE__ */ jsx("button", { className: "tb", style: { padding: "10px 13px" }, onClick: () => carregarVersoesSheets(true), children: "Atualizar" }),
            /* @__PURE__ */ jsx("button", { className: "tb", style: { padding: "10px 13px", color: T.red }, onClick: apagarVersaoNomeada, children: "Apagar" })
          ] }) })
        ] }),
        /* @__PURE__ */ jsx("div", { className: "hint", style: { marginTop: 8 }, children: statusSheets })
      ] }),
      /* @__PURE__ */ jsxs("div", { className: "sec", children: [
        /* @__PURE__ */ jsx("div", { className: "sec-t", children: "01 \u2014 Dados do Lote (base comum a todos os cen\xE1rios)" }),
        /* @__PURE__ */ jsxs("div", { className: "g2", children: [
          /* @__PURE__ */ jsx(F, { label: "C\xF3digo do neg\xF3cio", hint: "Obrigat\xF3rio ao iniciar. Ex.: CF-26-012", children: /* @__PURE__ */ jsx("input", { value: lote.codigoNegocio || "", placeholder: "CF-26-012", onChange: (e) => updLote("codigoNegocio", e.target.value.toUpperCase()) }) }),
          /* @__PURE__ */ jsx(F, { label: "Grupo Telegram de origem", hint: "Use somente o nome do grupo; o identificador t\xE9cnico \xE9 tratado automaticamente", children: /* @__PURE__ */ jsx("input", { value: lote.grupoOrigemNome || "", placeholder: "Confinamento", onChange: (e) => updLote("grupoOrigemNome", e.target.value) }) })
        ] }),
        /* @__PURE__ */ jsx("div", { className: "dvdr" }),
        /* @__PURE__ */ jsxs("div", { className: "g4", children: [
          /* @__PURE__ */ jsx(F, { label: "Origem", children: /* @__PURE__ */ jsx("input", { value: lote.origemNome, onChange: (e) => updLote("origemNome", e.target.value) }) }),
          /* @__PURE__ */ jsx(F, { label: "Sexo", children: /* @__PURE__ */ jsx(
            Tg,
            {
              opts: [{ v: "macho", l: "Macho" }, { v: "femea", l: "F\xEAmea" }],
              val: lote.sexo,
              set: (v) => {
                const novoPadrao = boisPorCarretaPadrao(v);
                updLote("sexo", v);
                updLote("modoCapim", "10kg");
                setCenarios((prev) => prev.map((sc) => ["", "35", "65", "70"].includes(String(sc.boisPorCarreta ?? "")) ? { ...sc, boisPorCarreta: novoPadrao } : sc));
                setResultados([]);
              }
            }
          ) }),
          /* @__PURE__ */ jsx(F, { label: "Qtd Cabe\xE7as", children: /* @__PURE__ */ jsx("input", { type: "number", value: lote.qtd, onChange: (e) => updLote("qtd", e.target.value) }) }),
          /* @__PURE__ */ jsx(F, { label: "Peso M\xE9dio (kg)", children: /* @__PURE__ */ jsx("input", { type: "number", value: lote.pesoMedio, onChange: (e) => updLote("pesoMedio", e.target.value) }) })
        ] }),
        /* @__PURE__ */ jsx("div", { className: "dvdr" }),
        /* @__PURE__ */ jsxs("div", { className: "g4", children: [
          /* @__PURE__ */ jsx(F, { label: "Pre\xE7o de Compra (R$/@)", children: /* @__PURE__ */ jsx("input", { type: "number", value: lote.precoCompra, onChange: (e) => updLote("precoCompra", e.target.value) }) }),
          /* @__PURE__ */ jsx(F, { label: "Prazo pag. compra (dias)", hint: "0 = \xE0 vista. Afeta capital imobilizado e rentabilidade", children: /* @__PURE__ */ jsx("input", { type: "number", value: lote.prazoPagtoCompra, onChange: (e) => updLote("prazoPagtoCompra", e.target.value) }) }),
          /* @__PURE__ */ jsx(F, { label: "Custo do dinheiro (% a.m.)", hint: "Taxa de oportunidade mensal \u2014 padr\xE3o 2,0%", children: /* @__PURE__ */ jsx("input", { type: "number", step: ".1", value: lote.custoDinheiro, onChange: (e) => updLote("custoDinheiro", e.target.value) }) }),
          /* @__PURE__ */ jsx(F, { label: "Custo Baldeio (R$ total)", hint: "Valor total da opera\xE7\xE3o \u2014 rateado por cabe\xE7a internamente", children: /* @__PURE__ */ jsx("input", { type: "number", value: lote.baldeio, onChange: (e) => updLote("baldeio", e.target.value) }) }),
          lote.sexo === "femea" && /* @__PURE__ */ jsx(F, { label: "Desconto bezerro (f\xEAmeas)", children: /* @__PURE__ */ jsx(Ck, { checked: lote.descBezerro, onChange: (v) => updLote("descBezerro", v), label: "10 kg l\xEDquido de bezerro" }) }),
          lote.sexo === "femea" && lote.descBezerro && /* @__PURE__ */ jsx(F, { label: "Peso m\xEDn. desc. bezerro (kg)", children: /* @__PURE__ */ jsx("input", { type: "number", value: lote.limBezerro, onChange: (e) => updLote("limBezerro", e.target.value) }) })
        ] }),
        /* @__PURE__ */ jsx("div", { className: "dvdr" }),
        /* @__PURE__ */ jsx("div", { className: "sec-t nm", style: { marginBottom: 14 }, children: "Desconto de Capim na Compra" }),
        /* @__PURE__ */ jsxs("div", { className: "g4", children: [
          /* @__PURE__ */ jsx(
            F,
            {
              label: "Modalidade de desconto",
              hint: "10kg fixo \xB7 700g/@ \xB7 800g/@ \xB7 1kg/@ \xB7 Sem desc.",
              children: /* @__PURE__ */ jsx(
                Tg,
                {
                  opts: [
                    { v: "10kg", l: "10kg fixo" },
                    { v: "700g", l: "700g/@" },
                    { v: "800g", l: "800g/@" },
                    { v: "1kg", l: "1kg/@" },
                    { v: "sem", l: "Sem desc." }
                  ],
                  val: lote.modoCapim,
                  set: (v) => updLote("modoCapim", v)
                }
              )
            }
          ),
          lote.modoCapim !== "sem" && /* @__PURE__ */ jsx(
            F,
            {
              label: "Aplica desconto para pesos \u2265 (kg)",
              hint: lote.modoCapim === "10kg" ? "Abaixo deste peso: sem desconto de capim" : "Abaixo deste peso: usa peso/2/15 sem desconto",
              children: /* @__PURE__ */ jsx(
                "input",
                {
                  type: "number",
                  value: lote.limCapim,
                  onChange: (e) => updLote("limCapim", e.target.value)
                }
              )
            }
          ),
          /* @__PURE__ */ jsx(F, { label: "Arrobas calculadas / cab", hint: "Resultado do desconto de capim aplicado", children: /* @__PURE__ */ jsx("input", { readOnly: true, value: fAt(arrobasPrev) }) }),
          /* @__PURE__ */ jsx(F, { label: "Custo compra / cab", hint: "Arrobas \xD7 pre\xE7o + baldeio rateado", children: /* @__PURE__ */ jsx("input", { readOnly: true, value: fR(arrobasPrev * (parseFloat(lote.precoCompra) || 0) + (parseFloat(lote.baldeio) || 0) / (parseFloat(lote.qtd) || 1)) }) })
        ] }),
        lote.modoCapim === "10kg" && /* @__PURE__ */ jsxs("div", { className: "warn", style: { marginTop: 10 }, children: [
          "10kg fixo: para peso \u2265 ",
          lote.limCapim,
          " kg \u2192 (peso\xD750% \u2212 10kg) \xF7 15. Abaixo do limite: sem desconto."
        ] }),
        lote.modoCapim !== "sem" && lote.modoCapim !== "10kg" && /* @__PURE__ */ jsxs("div", { className: "warn", style: { marginTop: 10 }, children: [
          lote.modoCapim,
          ": para peso \u2265 ",
          lote.limCapim,
          " kg \u2192 peso \xF7 ",
          (15 / (15 - (lote.modoCapim === "700g" ? 0.7 : lote.modoCapim === "800g" ? 0.8 : 1)) * 30).toFixed(2),
          " (divisor). Abaixo do limite: peso/2/15."
        ] })
      ] }),
      /* @__PURE__ */ jsxs("div", { className: "sec", children: [
        /* @__PURE__ */ jsx("div", { className: "sec-t", children: "Mercado BGI \u2014 cota\xE7\xF5es por contrato" }),
        /* @__PURE__ */ jsxs("div", { className: "g4", children: [
          contratosB3Estudo.map((contrato) => {
            const registro = lote.cotacoesB3?.[contrato];
            const cenarioReferencia = cenarios.find((sc) => contratoB3DoCenario(sc) === contrato);
            return /* @__PURE__ */ jsx(F, { label: contrato, hint: registro?.fonte || "Valor inicial do estudo", children: /* @__PURE__ */ jsx("input", { type: "number", value: registro?.preco ?? cenarioReferencia?.precoBolsa ?? "", onChange: (e) => definirCotacaoB3(contrato, e.target.value) }) }, contrato);
          }),
          /* @__PURE__ */ jsx(F, { label: "Atualizar a curva usada", hint: "Consulta juntos os vencimentos da janela de 60 a 240 dias", children: /* @__PURE__ */ jsx("button", { className: "tb on", style: { width: "100%", padding: "10px 13px" }, onClick: atualizarMercadoB3, children: "Atualizar cota\xE7\xF5es BGI" }) })
        ] }),
        /* @__PURE__ */ jsx("div", { className: "hint", style: { marginTop: 10 }, children: statusB3 || "Cada contrato tem uma única cotação compartilhada. A curva inclui os meses necessários para comparar permanências entre 60 e 240 dias." }),
        lote.cotacoesB3AtualizadasEm && /* @__PURE__ */ jsx("div", { className: "hint", style: { marginTop: 4 }, children: `Última alteração conjunta: ${(/* @__PURE__ */ new Date(lote.cotacoesB3AtualizadasEm)).toLocaleString("pt-BR")}` })
      ] }),
      /* @__PURE__ */ jsxs("div", { className: "sec", style: { padding: 0 }, children: [
        /* @__PURE__ */ jsx("div", { style: { padding: "18px 22px 0" }, children: /* @__PURE__ */ jsx("div", { className: "sec-t", children: "02 \u2014 Cen\xE1rios (at\xE9 5)" }) }),
        /* @__PURE__ */ jsxs("div", { className: "sc-bar", children: [
          cenarios.map((sc, i) => /* @__PURE__ */ jsxs(
            "button",
            {
              className: `sc-tab ${scAtivo === i ? "on" : ""}`,
              style: { "--c": T.sc[i] },
              onClick: () => setScAtivo(i),
              children: [
                /* @__PURE__ */ jsx("span", { style: { maxWidth: 110, overflow: "hidden", textOverflow: "ellipsis" }, children: sc.nome }),
                cenarios.length > 1 && /* @__PURE__ */ jsx("button", { className: "sc-del", onClick: (e) => {
                  e.stopPropagation();
                  delSc(i);
                }, children: "\xD7" })
              ]
            },
            sc.id
          )),
          cenarios.length < 5 && /* @__PURE__ */ jsx("button", { className: "sc-add", onClick: addSc, title: "Adicionar cen\xE1rio", children: "+" })
        ] }),
        /* @__PURE__ */ jsxs("div", { className: "sc-body", children: [
          /* @__PURE__ */ jsxs("div", { className: "g3", style: { marginBottom: 20 }, children: [
            /* @__PURE__ */ jsx(F, { label: "Nome do cen\xE1rio", children: /* @__PURE__ */ jsx(
              "input",
              {
                value: cenarios[scAtivo].nome,
                onChange: (e) => updSc(scAtivo, "nome", e.target.value)
              }
            ) }),
            /* @__PURE__ */ jsx(F, { label: "Tipo", children: /* @__PURE__ */ jsx(
              Tg,
              {
                opts: [{ v: "confinamento", l: "Confinamento" }, { v: "revenda", l: "Revenda" }],
                val: cenarios[scAtivo].tipo,
                set: (v) => updSc(scAtivo, "tipo", v)
              }
            ) })
          ] }),
          /* @__PURE__ */ jsx(
            ScPanel,
            {
              sc: cenarios[scAtivo],
              upd: (k, v) => updSc(scAtivo, k, v),
              sexo: lote.sexo,
              custoDinheiro: lote.custoDinheiro,
              resultado: resultados[scAtivo],
              confinamentos,
              modeloSelecionado,
              setModeloSelecionado,
              aplicarModelo,
              salvarModelo,
              atualizarModelo,
              apagarModelo,
              calcularDistancia,
              statusDistancia
            },
            cenarios[scAtivo].id
          )
        ] })
      ] }),
      /* @__PURE__ */ jsxs("button", { className: "calc-btn", onClick: calcular, children: [
        "CALCULAR E COMPARAR ",
        cenarios.length,
        " ",
        cenarios.length === 1 ? "CEN\xC1RIO" : "CEN\xC1RIOS"
      ] }),
      resultados.length > 0 && /* @__PURE__ */ jsx(Comparativo, { resultados, cenarios, lote }),
      resultados.length > 0 && /* @__PURE__ */ jsx(EvolucaoTempo, { lote, cenarios }),
      resultados.length > 0 && /* @__PURE__ */ jsx(RelatorioComparativo, { lote, cenarios, resultados }),
      resultados.length > 0 && /* @__PURE__ */ jsxs("div", { className: "sec", style: { marginTop: 18 }, children: [
        /* @__PURE__ */ jsx("div", { className: "sec-t", children: "Iniciar neg\xF3cio \u2014 Supabase" }),
        /* @__PURE__ */ jsxs("div", { className: "g3", children: [
          /* @__PURE__ */ jsx(F, { label: "Cen\xE1rio selecionado", children: /* @__PURE__ */ jsx("input", { readOnly: true, value: cenarios[scAtivo]?.nome || "\u2014" }) }),
          /* @__PURE__ */ jsx(F, { label: "Estimativa original", hint: "Ser\xE1 congelada e n\xE3o poder\xE1 ser sobrescrita", children: /* @__PURE__ */ jsx("input", { readOnly: true, value: resultados[scAtivo] ? `${fP(resultados[scAtivo].rMliq)} a.m. \xB7 ${fP(resultados[scAtivo].rTliq)} total \xB7 ${fR(resultados[scAtivo].lucroLiquido)}` : "\u2014" }) }),
          /* @__PURE__ */ jsx(F, { label: "Confirmar abertura", children: /* @__PURE__ */ jsx("button", { className: "tb on", style: { width: "100%", padding: "10px 13px" }, onClick: iniciarNegocioSupabase, children: "Iniciar neg\xF3cio" }) })
        ] }),
        /* @__PURE__ */ jsx("div", { className: "hint", style: { marginTop: 10 }, children: statusSupabase })
      ] }),
      resultados.length > 0 && /* @__PURE__ */ jsx(SensPanel, { lote, cenarios, resultados, historico, setHistorico })
    ] })
  ] });
}

// src/confinex-entry.jsx
import { jsx as jsx2 } from "react/jsx-runtime";
createRoot(document.getElementById("root")).render(/* @__PURE__ */ jsx2(Confinex, {}));
