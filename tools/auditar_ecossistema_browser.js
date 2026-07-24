#!/usr/bin/env node
/* Auditoria real de navegação. Requer playwright e não escreve no produto. */
'use strict';

const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

function argumento(nome) {
  const indice = process.argv.indexOf(nome);
  if (indice < 0 || !process.argv[indice + 1]) throw new Error(`faltou ${nome}`);
  return process.argv[indice + 1];
}

const baseUrl = new URL(argumento('--base-url'));
const config = JSON.parse(fs.readFileSync(argumento('--config'), 'utf8'));
const saida = argumento('--saida');
const artefatos = path.resolve(config.artefatos);
fs.mkdirSync(artefatos, { recursive: true });

const viewports = [
  { nome: 'desktop', largura: 1440, altura: 1000 },
  { nome: 'celular', largura: 390, altura: 844 },
];

function item(id, requisito, cenario, esperado, ok, evidencia) {
  return {
    id,
    requisito,
    cenario,
    esperado,
    status: ok ? 'aprovado' : 'falhou',
    evidencia,
    camada: 'navegador',
  };
}

function slug(texto) {
  return texto.normalize('NFD').replace(/[\u0300-\u036f]/g, '')
    .replace(/[^a-zA-Z0-9]+/g, '-').replace(/^-|-$/g, '').toLowerCase();
}

function urlLocal(href) {
  if (/^https?:\/\//i.test(href)) return href;
  return new URL(href.replace(/^\.\//, ''), baseUrl).href;
}

async function auditarPagina(browser, pagina, viewport, resultados) {
  const context = await browser.newContext({
    viewport: { width: viewport.largura, height: viewport.altura },
  });
  const page = await context.newPage();
  const erros = [];
  page.on('console', msg => {
    if (['error', 'warning'].includes(msg.type())) erros.push(`console ${msg.type()}: ${msg.text()}`);
  });
  page.on('pageerror', erro => erros.push(`javascript: ${erro.message}`));
  page.on('requestfailed', req => erros.push(`rede: ${req.url()} — ${req.failure()?.errorText || 'falhou'}`));
  page.on('response', res => {
    if (res.status() >= 400) erros.push(`http ${res.status()}: ${res.url()}`);
  });

  const destino = new URL(pagina.arquivo, baseUrl).href;
  let resposta;
  try {
    resposta = await page.goto(destino, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.waitForLoadState('load', { timeout: 30000 });
  } catch (erro) {
    erros.push(`carregamento: ${erro.message}`);
  }
  const urlFinal = page.url();
  const redirectEsperado = pagina.redirect
    ? new URL(pagina.redirect.replace(/^\.\//, ''), baseUrl).href
    : null;
  const urlEsperada = redirectEsperado || destino;
  resultados.push(item(
    `browser:${viewport.nome}:${pagina.arquivo}:direto`,
    'Carregamento direto',
    `${pagina.arquivo} em ${viewport.nome}`,
    redirectEsperado ? `redireciona apenas para o legado declarado ${redirectEsperado}` : 'permanece na rota solicitada',
    urlFinal === urlEsperada,
    `solicitado=${destino} final=${urlFinal} status=${resposta?.status() ?? 'sem resposta'}`,
  ));

  try {
    await page.reload({ waitUntil: 'load', timeout: 30000 });
    resultados.push(item(
      `browser:${viewport.nome}:${pagina.arquivo}:reload`,
      'Recarregamento',
      `${pagina.arquivo} em ${viewport.nome}`,
      'a página recarrega sem mudar de destino',
      page.url() === urlEsperada,
      `final=${page.url()}`,
    ));
  } catch (erro) {
    resultados.push(item(
      `browser:${viewport.nome}:${pagina.arquivo}:reload`,
      'Recarregamento',
      `${pagina.arquivo} em ${viewport.nome}`,
      'a página recarrega sem erro',
      false,
      erro.message,
    ));
  }

  const estado = await page.evaluate(() => ({
    shell: document.body.classList.contains('has-shell'),
    ativas: Array.from(document.querySelectorAll('.shell-link.ativa')).map(a => a.textContent.trim()),
    larguraDocumento: document.documentElement.scrollWidth,
  }));
  resultados.push(item(
    `browser:${viewport.nome}:${pagina.arquivo}:shell`,
    'Shell compartilhado',
    `${pagina.arquivo} em ${viewport.nome}`,
    'o shell do Confinex está presente',
    estado.shell,
    `shell=${estado.shell}`,
  ));
  resultados.push(item(
    `browser:${viewport.nome}:${pagina.arquivo}:responsivo`,
    'Layout sem estouro horizontal da página',
    `${pagina.arquivo} em ${viewport.nome}`,
    `largura do documento não excede ${viewport.largura}px`,
    estado.larguraDocumento <= viewport.largura,
    `documento=${estado.larguraDocumento}px viewport=${viewport.largura}px`,
  ));
  resultados.push(item(
    `browser:${viewport.nome}:${pagina.arquivo}:console-rede`,
    'Console e rede sem erros',
    `${pagina.arquivo} em ${viewport.nome}`,
    'nenhum erro JavaScript, console, HTTP ou requisição',
    erros.length === 0,
    erros.length ? erros.join(' | ') : 'nenhum erro capturado',
  ));

  const captura = path.join(artefatos, `${viewport.nome}-${slug(pagina.arquivo)}.png`);
  await page.screenshot({ path: captura, fullPage: true });
  resultados.push(item(
    `browser:${viewport.nome}:${pagina.arquivo}:evidencia-visual`,
    'Evidência visual',
    `${pagina.arquivo} em ${viewport.nome}`,
    'captura de tela integral é gerada',
    fs.existsSync(captura) && fs.statSync(captura).size > 0,
    captura,
  ));
  await context.close();
}

async function auditarMenu(browser, viewport, resultados) {
  const context = await browser.newContext({
    viewport: { width: viewport.largura, height: viewport.altura },
  });
  const page = await context.newPage();
  for (const menu of config.menu) {
    if (/^https?:\/\//i.test(menu.href)) continue;
    await page.goto(baseUrl.href, { waitUntil: 'load', timeout: 30000 });
    const seletor = `.shell-link[href="${urlLocal(menu.href)}"]`;
    const links = page.locator(seletor);
    const quantidade = await links.count();
    if (quantidade !== 1) {
      resultados.push(item(
        `browser:${viewport.nome}:menu:${menu.rotulo}:clique`,
        'Clique pelo menu',
        `${menu.rotulo} em ${viewport.nome}`,
        'existe um único link clicável',
        false,
        `quantidade=${quantidade} seletor=${seletor}`,
      ));
      continue;
    }
    const antes = page.url();
    await links.click();
    await page.waitForLoadState('domcontentloaded', { timeout: 30000 }).catch(() => {});
    const depois = page.url();
    const destino = urlLocal(menu.href);
    const parsed = new URL(destino);
    const ancoraExiste = !parsed.hash || await page.locator(parsed.hash).count() === 1;
    resultados.push(item(
      `browser:${viewport.nome}:menu:${menu.rotulo}:clique`,
      'Clique pelo menu',
      `${menu.rotulo} em ${viewport.nome}`,
      'o clique navega para um destino real na mesma aba',
      depois === destino && ancoraExiste,
      `antes=${antes} depois=${depois} âncora=${parsed.hash || 'não usada'} existe=${ancoraExiste}`,
    ));
    const ativas = await page.locator('.shell-link.ativa').allTextContents();
    resultados.push(item(
      `browser:${viewport.nome}:menu:${menu.rotulo}:ativo`,
      'Item ativo correto',
      `${menu.rotulo} em ${viewport.nome}`,
      'somente o item selecionado fica ativo',
      ativas.length === 1 && ativas[0].includes(menu.rotulo),
      `ativos=${JSON.stringify(ativas)}`,
    ));
    await page.goBack({ waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    resultados.push(item(
      `browser:${viewport.nome}:menu:${menu.rotulo}:voltar`,
      'Botão voltar',
      `${menu.rotulo} em ${viewport.nome}`,
      'retorna à Visão Geral',
      page.url() === baseUrl.href,
      `final=${page.url()}`,
    ));
  }
  await context.close();
}

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || undefined,
  });
  const resultados = [];
  try {
    for (const viewport of viewports) {
      for (const pagina of config.paginas) {
        await auditarPagina(browser, pagina, viewport, resultados);
      }
      await auditarMenu(browser, viewport, resultados);
    }
  } finally {
    await browser.close();
  }
  fs.writeFileSync(saida, JSON.stringify({ resultados }, null, 2) + '\n');
})().catch(erro => {
  console.error(erro);
  process.exitCode = 1;
});
