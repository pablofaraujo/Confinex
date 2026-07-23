# Contexto por grupo

Atualizado em 2026-07-23. Este documento define o vínculo entre uma conversa
de origem e os registros auditáveis do Confinex.

## Contrato único

| Campo | Uso |
|---|---|
| `contexto_canonico` | Chave estável, no formato `canal:escopo:identificador` |
| `contexto_nome` | Nome humano mostrado na fila e nos relatórios |
| `origem_canal` | Canal de entrada, por exemplo Telegram |
| `origem_conversa_id` | Identificador técnico original, nunca mostrado no frontend |
| `origem_mensagem_id` | Mensagem que sustenta o fato ou a decisão |
| `agente` | Agente responsável pela captura ou decisão |
| `escopo` | `grupo`, `direto` ou `sistema` |

O nome não substitui o identificador técnico. Um alias histórico pode ser
aceito como entrada da normalização, mas toda nova persistência deve conservar
os sete campos. Sem prova de conversa ou mensagem, o registro fica pendente e
não recebe contexto por aproximação.

`operation_drafts`, `pending_actions`, `eventos` e `memorias_agentes` recebem
os campos canônicos pela migração
`supabase/migrations/202607230001_contextos_canonicos.sql`. A tabela
`contextos_canais` centraliza nomes e aliases; somente `contexto_nome` chega à
interface. Em `memorias_agentes`, o campo preexistente `escopo` continua
representando o alcance funcional da memória; `contexto_escopo` guarda se a
conversa de origem é grupo, direta ou sistêmica. `memorias_agentes` continua
restrita a regras, decisões e preferências reutilizáveis. `contexto_handoff`
continua sendo passagem temporária e não é fonte durável.

## Ferramentas e dry-run

`tools/contexto_canonico.py` implementa o contrato compartilhado.
`tools/reconciliar_compras_telegram.py` passou a preservar separadamente nome,
chave canônica e ID técnico. `revisoes.html` não converte mais um nome humano
em ID de conversa ao salvar.

`tools/normalizar_contextos.py` é somente leitura por padrão. O mapa com nomes,
IDs e aliases deve ficar em `docs/privado/contextos-canais.json`:

```bash
python3 tools/normalizar_contextos.py \
  --mapa docs/privado/contextos-canais.json
```

Em 2026-07-23, o dry-run real encontrou 20 registros com vínculo comprovável:

| Tabela | Registros no plano |
|---|---:|
| `operation_drafts` | 8 |
| `pending_actions` | 8 |
| `eventos` | 2 |
| `memorias_agentes` | 2 |

O plano atual é `d1e8f0a7d4b4`: 16 vínculos de Boi Balança e 4 de
Confinamento. As 25 referências ambíguas permanecem fora do plano.

Outros 25 registros com alguma referência ficaram fora: 2 rascunhos, 4 ações,
7 eventos e 12 memórias. Eles não devem ser corrigidos sem evidência adicional.
Os eventos sem conversa também permanecem intactos; ausência não autoriza
inferência. A comparação das assinaturas antes e depois do dry-run confirmou
que nenhuma tabela foi alterada.

O dry-run também lista passagens abertas sem expor seu conteúdo. A passagem
atual contém fatos operacionais e informação durável misturados. Ela não foi
copiada automaticamente para memória: primeiro é necessário vincular os fatos
às fontes operacionais corretas, guardar apenas regras reutilizáveis em
`memorias_agentes` e então encerrar o handoff. Isso permanece pendente de
revisão e autorização de escrita.

`tools/planejar_handoff.py` aprofunda essa triagem em modo somente leitura. Ele
conta fragmentos com sinais de dado operacional, evento, memória reutilizável,
continuidade temporária ou revisão humana, mas publica somente contagens e a
assinatura da fonte. O conteúdo não aparece no relatório, e o encerramento
permanece bloqueado.

## Aplicação segura

Primeiro aplique e revise a migração de estrutura. Depois rode novamente o
dry-run e anote o `plano_id` emitido. A escrita só é liberada por uma frase
vinculada exatamente àquele plano:

```bash
psql "$SUPABASE_DB_URL" \
  --set ON_ERROR_STOP=1 \
  --file supabase/migrations/202607230001_contextos_canonicos.sql
```

O arquivo abre e conclui sua própria transação. Ele cria colunas, índices,
política de leitura, tabela de contextos e triggers; não contém `UPDATE`,
`DELETE`, `TRUNCATE` ou remoção de tabela. Antes de executá-lo, a URL de banco
deve vir do cofre e nunca ser gravada no repositório.

```bash
python3 tools/normalizar_contextos.py \
  --mapa docs/privado/contextos-canais.json \
  --executar \
  --confirmacao "NORMALIZAR CONTEXTOS d1e8f0a7d4b4"
```

Se qualquer registro mudar entre a simulação e a execução, gere um novo plano
e revise-o. Compare contagem e assinatura dos IDs antes e depois. A
normalização altera metadados de contexto; não insere em tabelas operacionais.

## Lacuna de venda/abate

A auditoria encontrou uma venda/abate real sem rascunho nem evento. A revisão
final das sessões recuperou o nome do grupo e a mensagem exata; o arquivo
privado `docs/privado/candidato-venda-abate.json` guarda a proposta pendente.
O peso continua marcado para conferência porque houve correções individuais
depois da primeira soma. Nenhuma escrita foi autorizada neste ciclo.

Depois de recuperar esse vínculo e revisar os campos, obtenha o
`rascunho_plano_id` no dry-run e use:

```bash
python3 tools/normalizar_contextos.py \
  --mapa docs/privado/contextos-canais.json \
  --candidato-rascunho docs/privado/candidato-venda-abate.json \
  --criar-rascunho \
  --confirmacao "CRIAR RASCUNHO da1cc9a243cf"
```

Essa ação cria somente um `operation_draft`, a `pending_action` de revisão e
seu evento, todos ligados pela mesma origem. Não cria venda, abate ou compra
operacional. A deduplicação por conversa e mensagem impede repetição do mesmo
caso. A frase anterior foi invalidada ao retirar o peso ainda não conferido dos
dados promovíveis; ele permanece apenas como inferência histórica. Os IDs do
rascunho, da pendência e do evento são determinísticos, permitindo retomar com
segurança se uma chamada for interrompida entre as gravações.

## Reversão

Antes da escrita, exporte os sete campos anteriores dos IDs listados no plano.
Para reverter, restaure somente esses IDs e campos, nunca por data, nome ou
filtro amplo. A migração de colunas é aditiva e pode permanecer instalada
mesmo se a normalização de dados for revertida.
