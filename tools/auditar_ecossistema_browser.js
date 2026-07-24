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
  let navegacaoIntencional = true;
  page.on('console', msg => {
    if (['error', 'warning'].includes(msg.type())) erros.push(`console ${msg.type()}: ${msg.text()}`);
  });
  page.on('pageerror', erro => erros.push(`javascript: ${erro.message}`));
  page.on('requestfailed', req => {
    const motivo = req.failure()?.errorText || 'falhou';
    if (navegacaoIntencional && motivo === 'net::ERR_ABORTED') return;
    erros.push(`rede: ${req.url()} — ${motivo}`);
  });
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
  } finally {
    navegacaoIntencional = false;
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
    navegacaoIntencional = true;
    await page.reload({ waitUntil: 'load', timeout: 30000 });
    navegacaoIntencional = false;
    resultados.push(item(
      `browser:${viewport.nome}:${pagina.arquivo}:reload`,
      'Recarregamento',
      `${pagina.arquivo} em ${viewport.nome}`,
      'a página recarrega sem mudar de destino',
      page.url() === urlEsperada,
      `final=${page.url()}`,
    ));
  } catch (erro) {
    navegacaoIntencional = false;
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
  navegacaoIntencional = true;
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
    if (destino !== antes) {
      await page.goBack({ waitUntil: 'domcontentloaded', timeout: 30000 }).catch(() => {});
    }
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

function numeroPtBr(texto) {
  const limpo = String(texto || '').replace(/[^\d,.-]/g, '')
    .replace(/\./g, '').replace(',', '.');
  return Number(limpo);
}

async function auditarPagamentoConfinamento(browser, viewport, resultados) {
  const context = await browser.newContext({
    viewport: { width: viewport.largura, height: viewport.altura },
  });
  await context.addInitScript(() => {
    localStorage.clear();
    Object.defineProperty(window, 'CONFINEX_SHEETS_API_URL', {
      configurable: false,
      get: () => '',
      set: () => {},
    });
  });
  const page = await context.newPage();
  const erros = [];
  page.on('console', msg => {
    if (msg.type() === 'error') erros.push(`console: ${msg.text()}`);
  });
  page.on('pageerror', erro => erros.push(`javascript: ${erro.message}`));

  try {
    await page.goto(new URL('confinex.html', baseUrl).href, {
      waitUntil: 'load',
      timeout: 30000,
    });
    await page.locator('#root .app').waitFor({ state: 'visible', timeout: 30000 });

    const preencher = async (rotulo, valor) => {
      const campo = page.locator('.fld').filter({ hasText: rotulo })
        .locator('input:not([readonly])').first();
      await campo.fill(String(valor));
    };
    await preencher('Qtd Cabeças', 1);
    await preencher('Custo do dinheiro (% a.m.)', 2);
    await page.getByRole('button', { name: 'Diária', exact: true }).click();
    await preencher('Custo diária (R$/cab/dia)', 100);
    await preencher('Ciclo (dias)', 90);
    await preencher('Prazo pag. após abate (dias)', 0);

    const observados = {};
    for (const [modo, botao] of [
      ['adiantado', 'Adiantado'],
      ['mensal', 'Mensal'],
      ['final', 'No final'],
    ]) {
      await page.getByRole('button', { name: botao, exact: true }).click();
      await page.getByRole('button', { name: /CALCULAR E COMPARAR/ }).click();
      const campoCusto = page.locator('.fld')
        .filter({ hasText: 'Custo do dinheiro do confinamento' })
        .locator('input[readonly]').first();
      await campoCusto.waitFor({ state: 'visible' });
      observados[modo] = numeroPtBr(await campoCusto.inputValue());
    }

    const custoTotal = 1 * 100 * 90;
    const esperados = {
      adiantado: custoTotal * (1.02 ** 3 - 1),
      mensal: (custoTotal / 3) * ((1.02 ** 2 - 1) + (1.02 - 1)),
      final: 0,
    };
    // fR exibe estes cartões em reais inteiros; aceite apenas o arredondamento
    // visual, enquanto a regressão unitária continua comparando casas decimais.
    const margemMoeda = 0.51;
    const calculosOk = Object.keys(esperados).every(
      modo => Math.abs(observados[modo] - esperados[modo]) <= margemMoeda,
    );
    resultados.push(item(
      `browser:${viewport.nome}:confinex:pagamento-fluxos`,
      'Pagamento datado do confinamento',
      `adiantado, mensal e final em ${viewport.nome}`,
      'os custos visíveis coincidem com cálculos manuais a 2% a.m.',
      calculosOk,
      `observado=${JSON.stringify(observados)} esperado=${JSON.stringify(esperados)}`,
    ));

    const lerContrato = () => page.evaluate(() => {
      const rotulos = [
        'Lucro bruto',
        'Custo financeiro total',
        'Lucro líquido',
        'Resultado a valor presente',
      ];
      const linhas = Array.from(document.querySelectorAll('.cmp-tbl tbody tr'));
      return Object.fromEntries(rotulos.map(rotulo => {
        const linha = linhas.find(tr =>
          tr.cells?.[0]?.textContent?.trim() === rotulo);
        return [rotulo, linha?.cells?.[1]?.textContent?.trim() || ''];
      }));
    });
    const contratoComCustoTexto = await lerContrato();
    const contratoComCusto = Object.fromEntries(
      Object.entries(contratoComCustoTexto).map(
        ([chave, valor]) => [chave, numeroPtBr(valor)]),
    );
    const diferencaCalculada =
      contratoComCusto['Lucro bruto'] - contratoComCusto['Lucro líquido'];
    const contratoComCustoOk =
      contratoComCusto['Custo financeiro total'] > 0 &&
      Math.abs(
        diferencaCalculada - contratoComCusto['Custo financeiro total'],
      ) <= 1.5;
    resultados.push(item(
      `browser:${viewport.nome}:confinex:lucro-com-financeiro`,
      'Contrato lucro bruto e líquido',
      `taxa positiva em ${viewport.nome}`,
      'lucro bruto − lucro líquido coincide com o custo financeiro total',
      contratoComCustoOk,
      JSON.stringify(contratoComCustoTexto),
    ));

    await preencher('Custo do dinheiro (% a.m.)', 0);
    await page.getByRole('button', { name: /CALCULAR E COMPARAR/ }).click();
    const contratoSemCustoTexto = await lerContrato();
    const contratoSemCusto = Object.fromEntries(
      Object.entries(contratoSemCustoTexto).map(
        ([chave, valor]) => [chave, numeroPtBr(valor)]),
    );
    const contratoSemCustoOk =
      contratoSemCusto['Custo financeiro total'] === 0 &&
      contratoSemCusto['Lucro bruto'] === contratoSemCusto['Lucro líquido'];
    resultados.push(item(
      `browser:${viewport.nome}:confinex:lucro-sem-financeiro`,
      'Igualdade explicável entre bruto e líquido',
      `taxa zero em ${viewport.nome}`,
      'bruto e líquido são iguais somente com custo financeiro zero',
      contratoSemCustoOk,
      JSON.stringify(contratoSemCustoTexto),
    ));
    await preencher('Custo do dinheiro (% a.m.)', 2);
    await page.getByRole('button', { name: /CALCULAR E COMPARAR/ }).click();

    const textoRelatorio = await page.locator('.report-print').textContent();
    resultados.push(item(
      `browser:${viewport.nome}:confinex:contrato-pdf`,
      'Contrato financeiro no PDF',
      `relatório comparativo em ${viewport.nome}`,
      'o relatório nomeia bruto, líquido, custo financeiro e fluxo de parcelas',
      ['Lucro bruto / líquido', 'Custo financeiro total', 'Fluxo do confinamento']
        .every(texto => textoRelatorio.includes(texto)),
      'relatório de impressão usa o mesmo resultado calculado',
    ));

    const estadoSalvo = await page.evaluate(() =>
      localStorage.getItem('confinex:last-state:v3') || '');
    resultados.push(item(
      `browser:${viewport.nome}:confinex:pagamento-estado`,
      'Estado salvo do pagamento',
      `forma final em ${viewport.nome}`,
      'o cenário persiste a forma de pagamento sem gravar em backend externo',
      estadoSalvo.includes('"pagamentoConfinamento":"final"'),
      `campo persistido=${estadoSalvo.includes('"pagamentoConfinamento":"final"')} Sheets desabilitado no contexto de teste`,
    ));

    const larguraDocumento = await page.evaluate(
      () => document.documentElement.scrollWidth);
    resultados.push(item(
      `browser:${viewport.nome}:confinex:pagamento-responsivo`,
      'Pagamento responsivo',
      `resultado completo em ${viewport.nome}`,
      `a interação não cria estouro acima de ${viewport.largura}px`,
      larguraDocumento <= viewport.largura,
      `documento=${larguraDocumento}px viewport=${viewport.largura}px`,
    ));
    resultados.push(item(
      `browser:${viewport.nome}:confinex:pagamento-console`,
      'Pagamento sem erro JavaScript',
      `cálculo completo em ${viewport.nome}`,
      'nenhum erro de console ou execução',
      erros.length === 0,
      erros.length ? erros.join(' | ') : 'nenhum erro capturado',
    ));

    const captura = path.join(
      artefatos,
      `${viewport.nome}-confinex-pagamento-confinamento.png`,
    );
    await page.screenshot({ path: captura, fullPage: true });
    resultados.push(item(
      `browser:${viewport.nome}:confinex:pagamento-evidencia`,
      'Evidência visual do pagamento',
      `resultado completo em ${viewport.nome}`,
      'captura integral é gerada',
      fs.existsSync(captura) && fs.statSync(captura).size > 0,
      captura,
    ));
  } catch (erro) {
    resultados.push(item(
      `browser:${viewport.nome}:confinex:pagamento-execucao`,
      'Pagamento datado do confinamento',
      `execução em ${viewport.nome}`,
      'o cenário automatizado termina',
      false,
      erro.stack || erro.message,
    ));
  } finally {
    await context.close();
  }
}

function clienteFinanceiroSimulado() {
  function isoComDias(dias) {
    const data = new Date();
    data.setHours(12, 0, 0, 0);
    data.setDate(data.getDate() + dias);
    return data.toISOString().slice(0, 10);
  }
  function dadosPositivos() {
    return {
      fluxo_caixa: [
        {
          tipo: 'saida',
          descricao: 'Diária do confinamento',
          categoria: 'Confinamento',
          origem_referencia: 'CF-27',
          valor: 1000,
          valor_pago: 250,
          vencimento: isoComDias(-4),
          realizado: false,
        },
        {
          tipo: 'entrada',
          descricao: 'Recebimento da venda',
          categoria: 'Venda de gado',
          origem_referencia: 'Lote comercial',
          valor: 4610965.43,
          vencimento: isoComDias(5),
          realizado: false,
        },
        {
          tipo: 'entrada',
          descricao: 'Crédito confirmado',
          categoria: 'Venda',
          origem_referencia: '123e4567-e89b-12d3-a456-426614174000',
          valor: 500,
          vencimento: isoComDias(-1),
          realizado: true,
        },
      ],
      emprestimos: [
        {
          numero_contrato: 'Capital de giro',
          credor: 'Instituição financeira',
          valor_principal: 10000,
          saldo_devedor: 7000,
          proximo_vencimento: isoComDias(10),
          parcelas_pagas: 3,
          numero_parcelas: 12,
          taxa_juros_aa: 12,
          status: 'renegociado',
          renegociado_em: isoComDias(-20),
        },
      ],
      promissorias: [
        {
          numero: 'Documento comercial',
          credor: 'Fornecedor',
          valor: 3000,
          valor_pago: 1000,
          vencimento: isoComDias(20),
          status: 'parcial',
        },
      ],
      transacoes_banco: [
        {
          data: isoComDias(-1),
          descricao: 'Crédito identificado',
          categoria: 'Recebimento',
          lote_ref: 'Lote comercial',
          valor: 500,
        },
      ],
    };
  }
  function resposta(nome) {
    const modo = new URLSearchParams(location.search).get('fixture') || 'positivo';
    if (modo === 'falha-principal' && nome !== 'transacoes_banco') {
      return { data: null, error: { message: 'detalhe técnico que não deve aparecer' } };
    }
    if (modo === 'falha-banco' && nome === 'transacoes_banco') {
      return { data: null, error: { message: 'indisponível' } };
    }
    const dados = modo === 'vazio' ? {} : dadosPositivos();
    return { data: dados[nome] || [], error: null };
  }
  const cliente = {
    auth: {
      getSession: async () => ({ data: { session: { user: { id: 'auditoria' } } } }),
      signInWithPassword: async () => ({ data: {}, error: null }),
      signOut: async () => ({ error: null }),
    },
    from(nome) {
      const consulta = {
        select() {
          return {
            limit: async () => resposta(nome),
          };
        },
      };
      ['insert', 'update', 'upsert', 'delete'].forEach(operacao => {
        consulta[operacao] = () => {
          window.__mutacoesFinanceiro = (window.__mutacoesFinanceiro || 0) + 1;
          throw new Error(`mutação inesperada: ${operacao}`);
        };
      });
      return consulta;
    },
    rpc() {
      window.__mutacoesFinanceiro = (window.__mutacoesFinanceiro || 0) + 1;
      throw new Error('mutação inesperada: rpc');
    },
  };
  window.__mutacoesFinanceiro = 0;
  window.supabase = { createClient: () => cliente };
}

async function abrirFinanceiroSimulado(browser, viewport, modo) {
  const context = await browser.newContext({
    viewport: { width: viewport.largura, height: viewport.altura },
  });
  await context.route('**/supabase.min.js', async route => {
    await route.fulfill({
      status: 200,
      contentType: 'application/javascript',
      body: `(${clienteFinanceiroSimulado.toString()})();`,
    });
  });
  const page = await context.newPage();
  const erros = [];
  page.on('console', msg => {
    if (['error', 'warning'].includes(msg.type())) {
      erros.push(`console ${msg.type()}: ${msg.text()}`);
    }
  });
  page.on('pageerror', erro => erros.push(`javascript: ${erro.message}`));
  page.on('requestfailed', req => {
    erros.push(`rede: ${req.url()} — ${req.failure()?.errorText || 'falhou'}`);
  });
  page.on('response', res => {
    if (res.status() >= 400) erros.push(`http ${res.status()}: ${res.url()}`);
  });
  const destino = new URL(`financeiro.html?fixture=${modo}`, baseUrl).href;
  await page.goto(destino, { waitUntil: 'load', timeout: 30000 });
  await page.locator('#app').waitFor({ state: 'visible', timeout: 10000 });
  await page.waitForFunction(
    () => !document.getElementById('subtitle').textContent.includes('Carregando'),
    null,
    { timeout: 10000 },
  );
  return { context, page, erros };
}

async function auditarFinanceiro(browser, viewport, resultados) {
  let execucao;
  try {
    execucao = await abrirFinanceiroSimulado(browser, viewport, 'positivo');
    const { page, erros } = execucao;
    const estado = await page.evaluate(() => ({
      kpis: document.querySelectorAll('#kpis .kpi').length,
      obrigacoes: document.querySelectorAll('#obrigacoes tr').length,
      dividas: document.querySelectorAll('#dividas tr').length,
      lembretes: document.querySelectorAll('#lembretes tr').length,
      transacoes: document.querySelectorAll('#transacoes tr').length,
      texto: document.getElementById('app').innerText,
      largura: document.documentElement.scrollWidth,
      mutacoes: window.__mutacoesFinanceiro,
      kpisSemEstouro: Array.from(document.querySelectorAll('#kpis .kpi .v'))
        .every(valor => valor.scrollWidth <= valor.clientWidth),
    }));
    resultados.push(item(
      `browser:${viewport.nome}:financeiro:positivo`,
      'Financeiro previsto e realizado',
      `dados positivos em ${viewport.nome}`,
      'KPIs, obrigações, dívidas, lembretes e conciliação são apresentados',
      estado.kpis === 6 && estado.obrigacoes === 3 && estado.dividas === 2 &&
        estado.lembretes >= 3 && estado.transacoes === 1,
      `kpis=${estado.kpis} obrigações=${estado.obrigacoes} dívidas=${estado.dividas} lembretes=${estado.lembretes} transações=${estado.transacoes}`,
    ));
    resultados.push(item(
      `browser:${viewport.nome}:financeiro:parcial-renegociacao`,
      'Pagamentos parciais e renegociação',
      `compromissos em ${viewport.nome}`,
      'saldo pago, saldo aberto, parcela e renegociação ficam legíveis',
      estado.texto.includes('Parcial') && estado.texto.includes('Renegociada') &&
        estado.texto.includes('3/12'),
      'marcadores legíveis verificados no conteúdo renderizado',
    ));
    resultados.push(item(
      `browser:${viewport.nome}:financeiro:origem-humana`,
      'Origem financeira em linguagem humana',
      `vínculos em ${viewport.nome}`,
      'a origem é navegável e UUID técnico não aparece',
      estado.texto.includes('Confinamento') &&
        !/[0-9a-f]{8}-[0-9a-f-]{27,}/i.test(estado.texto),
      'origem Confinamento presente; nenhum UUID no conteúdo',
    ));
    await page.locator('#filtroSituacao').selectOption('atrasado');
    const filtradas = await page.locator('#obrigacoes tr').count();
    resultados.push(item(
      `browser:${viewport.nome}:financeiro:filtro`,
      'Filtros financeiros',
      `apenas atrasadas em ${viewport.nome}`,
      'o filtro reduz a agenda sem recarregar ou escrever dados',
      filtradas === 1,
      `linhas filtradas=${filtradas}`,
    ));
    resultados.push(item(
      `browser:${viewport.nome}:financeiro:responsivo`,
      'Financeiro responsivo',
      `resultado completo em ${viewport.nome}`,
      `a página não excede ${viewport.largura}px`,
      estado.largura <= viewport.largura,
      `documento=${estado.largura}px viewport=${viewport.largura}px`,
    ));
    resultados.push(item(
      `browser:${viewport.nome}:financeiro:kpi-sem-estouro`,
      'Valores monetários dentro dos KPIs',
      `valor multimilionário em ${viewport.nome}`,
      'cada valor cabe na largura útil do próprio cartão',
      estado.kpisSemEstouro,
      `valores sem estouro=${estado.kpisSemEstouro}`,
    ));
    resultados.push(item(
      `browser:${viewport.nome}:financeiro:sem-escrita`,
      'Financeiro somente leitura',
      `carga e filtro em ${viewport.nome}`,
      'nenhuma mutação é chamada',
      estado.mutacoes === 0,
      `mutações=${estado.mutacoes}`,
    ));
    resultados.push(item(
      `browser:${viewport.nome}:financeiro:console-rede`,
      'Financeiro sem erros',
      `dados positivos em ${viewport.nome}`,
      'console, JavaScript e rede não registram erros',
      erros.length === 0,
      erros.length ? erros.join(' | ') : 'nenhum erro capturado',
    ));
    const captura = path.join(
      artefatos,
      `${viewport.nome}-financeiro-cenario-positivo.png`,
    );
    await page.screenshot({ path: captura, fullPage: true });
    resultados.push(item(
      `browser:${viewport.nome}:financeiro:evidencia`,
      'Evidência visual do Financeiro',
      `dados positivos em ${viewport.nome}`,
      'captura integral é gerada',
      fs.existsSync(captura) && fs.statSync(captura).size > 0,
      captura,
    ));
  } catch (erro) {
    resultados.push(item(
      `browser:${viewport.nome}:financeiro:execucao`,
      'Financeiro no navegador',
      `dados positivos em ${viewport.nome}`,
      'o cenário automatizado termina',
      false,
      erro.stack || erro.message,
    ));
  } finally {
    if (execucao) await execucao.context.close();
  }

  for (const modo of ['vazio', 'falha-banco', 'falha-principal']) {
    let cenario;
    try {
      cenario = await abrirFinanceiroSimulado(browser, viewport, modo);
      const estado = await cenario.page.evaluate(() => ({
        texto: document.getElementById('app').innerText,
        subtitulo: document.getElementById('subtitle').textContent,
        erroBanco: document.getElementById('erroBanco').textContent,
        mutacoes: window.__mutacoesFinanceiro,
      }));
      const esperado = modo === 'vazio'
        ? estado.texto.includes('Nenhuma obrigação') &&
          estado.texto.includes('Nenhuma dívida') &&
          estado.texto.includes('Nenhuma transação')
        : modo === 'falha-banco'
          ? estado.erroBanco.includes('não pôde ser carregada') &&
            estado.texto.includes('Diária do confinamento')
          : estado.subtitulo.includes('Não foi possível carregar os dados') &&
            !estado.texto.includes('detalhe técnico');
      resultados.push(item(
        `browser:${viewport.nome}:financeiro:${modo}`,
        modo === 'vazio' ? 'Estado vazio do Financeiro' : 'Falha controlada do Financeiro',
        `${modo} em ${viewport.nome}`,
        modo === 'vazio'
          ? 'todas as seções mostram estados vazios claros'
          : modo === 'falha-banco'
            ? 'falha bancária não derruba as demais áreas'
            : 'falha principal mostra mensagem humana sem detalhe técnico',
        esperado && estado.mutacoes === 0 && cenario.erros.length === 0,
        `subtítulo=${estado.subtitulo} erro bancário=${estado.erroBanco || 'nenhum'} mutações=${estado.mutacoes} erros=${cenario.erros.length}`,
      ));
    } catch (erro) {
      resultados.push(item(
        `browser:${viewport.nome}:financeiro:${modo}`,
        'Estado alternativo do Financeiro',
        `${modo} em ${viewport.nome}`,
        'o cenário automatizado termina',
        false,
        erro.stack || erro.message,
      ));
    } finally {
      if (cenario) await cenario.context.close();
    }
  }
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
      await auditarPagamentoConfinamento(browser, viewport, resultados);
      await auditarFinanceiro(browser, viewport, resultados);
    }
  } finally {
    await browser.close();
  }
  fs.writeFileSync(saida, JSON.stringify({ resultados }, null, 2) + '\n');
})().catch(erro => {
  console.error(erro);
  process.exitCode = 1;
});
