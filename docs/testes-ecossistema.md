# Bateria de testes do ecossistema

Atualizado em 2026-07-23. A entrada única é:

```bash
python3 tools/test_ecossistema.py
```

O comando padrão é local, determinístico e não acessa nem altera o Supabase.
Ele roda todos os testes Python de `tools/`, a simulação da fila em
`tools/test_revisoes_frontend.js`, a verificação sintática do JavaScript
embutido em `revisoes.html` e `git diff --check`. O mesmo comando roda em cada
push e pull request pelo GitHub Actions.

## O que a bateria protege

- **Telegram e anexos:** `MediaPath` e `MediaPaths` devem chegar primeiro a
  `arquivo_grupo_router.py`; foto e PDF são aceitos; ferramenta interna de
  imagem/PDF não pode anteceder o roteador; compra usa OCR OpenClaw/OpenAI,
  calcula somente com base suficiente e informa peso, data ou pagamento
  ausentes sem inventar.
- **Fila de Revisões:** rascunhos permanecem separados pelo nome do contexto;
  IDs de grupo e JSON não aparecem na apresentação; cada campo obrigatório
  ausente recebe aviso e destaque; salvar ajustes não promove; preparação fica
  bloqueada enquanto houver falta; rejeição exige motivo; devolução preserva
  os dados e registra o histórico.
- **Supabase:** os estados de eventos pertencem ao contrato vigente; a
  reconciliação cria somente `operation_drafts`; nenhuma compra operacional
  nasce sem o executor e a confirmação exata.
- **Memória e contexto:** `memorias_agentes` guarda apenas regras, decisões ou
  preferências reutilizáveis; `contexto_handoff` serve apenas à continuidade
  temporária. Nenhuma das duas substitui rascunhos, pendências ou eventos.
- **VPS/Juan:** todos os handlers passam em `py_compile` e nos testes Python; a
  configuração OpenClaw é válida; gateway e trabalhador OCR estão ativos; foto
  e PDF reais da caixa de entrada passam pelo extrator e pelo roteador em
  `--dry-run`.

## Auditoria somente leitura do Supabase

Com as variáveis já protegidas no ambiente:

```bash
python3 tools/test_ecossistema.py --supabase
```

A auditoria lê `operation_drafts`, `pending_actions`, `eventos`, `compras`,
`vendas`, `pesagens_caderno`, `abates`, `memorias_agentes` e
`contexto_handoff`. Para cada tabela, compara quantidade e SHA-256 da lista de
IDs antes e depois. Qualquer diferença reprova a bateria.

O teste corrente não precisa de escrita temporária. Se um caso futuro exigir
escrita, ele deve usar um marcador inequívoco e IDs exatos, limitar-se a um
registro, apagar dependências em bloco `finally` e repetir a assinatura após a
limpeza. É proibido limpar por data, texto parcial ou contexto amplo.

## Validação da VPS e do agente

Os caminhos, o endereço da VPS e o identificador técnico do grupo pertencem ao
ambiente privado e não devem ser commitados. Exemplo com variáveis protegidas:

```bash
export CONFINEX_VPS_HOST='<host-privado>'
export CONFINEX_VPS_IDENTITY='<chave-ssh>'
export CONFINEX_TESTE_PDF='<pdf-real-em-media-inbound>'
export CONFINEX_TESTE_FOTO='<foto-real-em-media-inbound>'
export CONFINEX_TESTE_GRUPO_ID='<grupo-de-homologacao>'
export CONFINEX_TESTE_LEGENDA_PDF='<contexto-do-pdf>'
export CONFINEX_TESTE_LEGENDA_FOTO='<contexto-da-foto>'
python3 tools/test_ecossistema.py --vps-host "$CONFINEX_VPS_HOST" --testar-agente
```

O orquestrador envia o conteúdo versionado de `tools/test_juan_vps.py`
diretamente ao Python remoto, sem deixar script instalado. Para cada arquivo
real, ele exige `ocr_origem=openclaw_openai`, executa o roteador em `--dry-run`
e valida pendências ou cálculo. Com `--testar-agente`, simula uma mensagem com
`MediaPath` e `MediaPaths` e lê a trajetória: a primeira ferramenta que toca o
anexo precisa ser o roteador, também em `--dry-run`.

Antes e depois, o teste compara a assinatura das nove tabelas. Ao terminar,
mesmo em caso de falha, remove somente sessões marcadas pelo próprio teste,
limpa cache de OCR e `__pycache__` e executa a manutenção de sessões do Juan.
Nenhuma promoção ou criação de rascunho faz parte desta bateria.

## Leitura do resultado

O resultado local termina em `VALIDAÇÃO DO ECOSSISTEMA: OK`. Na VPS, o resumo
também informa:

- compilação e testes dos handlers;
- configuração OpenClaw;
- estado do gateway e do trabalhador OCR;
- estados de evento encontrados;
- formato, classe e origem do OCR de cada arquivo real;
- `supabase_inalterado: true`.

Uma falha é bloqueante: corrija a causa e rode novamente a bateria completa.
Não contorne falha de contrato removendo a asserção sem confirmar a regra
operacional correspondente.
