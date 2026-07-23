# Bateria de testes do ecossistema

Atualizado em 2026-07-23. A entrada única é:

```bash
python3 tools/test_ecossistema.py
```

O comando padrão é local, determinístico e não acessa nem altera o Supabase.
Ele roda todos os testes Python de `tools/`, a simulação da fila em
`tools/test_revisoes_frontend.js`, a verificação sintática de `revisoes.js`,
confirma que `revisoes.html` não voltou a ter script inline e executa
`git diff --check`. O mesmo comando roda em cada push e pull request pelo GitHub
Actions.

O workflow também executa a bateria local toda segunda-feira às 10:17 UTC
(07:17 em Brasília). A execução agendada não recebe credenciais e não acessa a
VPS ou o Supabase; serve para detectar regressões do código e mudanças no
ambiente dos runners. Há limite de dez minutos e execuções da mesma referência
não se sobrepõem.

## O que a bateria protege

- **Telegram e anexos:** `MediaPath`, `MediaPaths`, `media://inbound/...`,
  `media:/inbound/...` e `inbound/...` devem chegar primeiro a
  `arquivo_grupo_router.py`. As URIs resolvem somente dentro de
  `/root/.openclaw/media/inbound`, sem traversal. Foto e PDF são aceitos;
  `pdf`, `image`, `file_fetch` e OCR interno não podem ser usados antes nem
  depois do roteador. Compra usa OCR OpenClaw/OpenAI, calcula somente com base
  suficiente e informa peso, data ou pagamento ausentes sem inventar.
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
python3 tools/test_ecossistema.py --completa
```

`--completa` exige as seis variáveis de contexto acima, ativa a simulação do
agente e roda, em uma única chamada: bateria local, handlers da VPS, OpenClaw,
serviços, PDF, foto, trajetória do Juan e assinatura do Supabase antes/depois.
A chave SSH pode ser informada por `CONFINEX_VPS_IDENTITY`; sem ela, o SSH usa
o agente e a configuração padrão do ambiente.

O orquestrador envia o conteúdo versionado de `tools/test_juan_vps.py`
diretamente ao Python remoto, sem deixar script instalado. Para cada arquivo
real, ele exige `ocr_origem=openclaw_openai`, executa o roteador em `--dry-run`
pelos caminhos absoluto, `media://`, `media:/` e `inbound/`, e valida pendências
ou cálculo. Com `--testar-agente`, simula na mesma sessão duas mensagens usando
a URI entregue pelo runtime e uma terceira com `MediaPath`/`MediaPaths`. As três
tentativas precisam chamar o roteador em `--dry-run`; `pdf`, `image`,
`file_fetch`, `pdftotext`, `pdftoppm` e OCR interno são proibidos em qualquer
ponto da trajetória.

Antes e depois, o teste compara a assinatura das nove tabelas. Ao terminar,
mesmo em caso de falha, remove somente sessões marcadas pelo próprio teste,
remove somente entradas de cache OCR criadas durante a execução e usa um
diretório temporário isolado para `py_compile`. O conteúdo do cache anterior é
preservado. Para a sessão, remove os arquivos com o marcador único, confirma
em prévia que existe exatamente uma referência ausente e pede ao OpenClaw para
remover somente essa referência; retenção global nunca é forçada. Nenhuma
promoção ou criação de rascunho faz parte desta bateria.

A execução completa não foi colocada no GitHub Actions: a VPS está em rede
privada, os anexos reais ficam no servidor e não há clone canônico do Confinex
na VPS. Também não foi criada cópia permanente do verificador em cron, evitando
que um script solto fique diferente do repositório. Rode `--completa` a partir
do clone canônico após mudanças em Juan, roteamento, OCR ou promoção.

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
