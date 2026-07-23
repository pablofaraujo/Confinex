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
interface. `memorias_agentes` continua restrita a regras, decisões e
preferências reutilizáveis. `contexto_handoff` continua sendo passagem
temporária e não é fonte durável.

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

## Aplicação segura

Primeiro aplique e revise a migração de estrutura. Depois rode novamente o
dry-run e anote o `plano_id` emitido. A escrita só é liberada por uma frase
vinculada exatamente àquele plano:

```bash
python3 tools/normalizar_contextos.py \
  --mapa docs/privado/contextos-canais.json \
  --executar \
  --confirmacao "NORMALIZAR CONTEXTOS <plano_id>"
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
  --confirmacao "CRIAR RASCUNHO 7051f8c5f62a"
```

Essa ação cria somente um `operation_draft` e seu evento. Não cria venda,
abate ou compra operacional. A deduplicação por conversa e mensagem impede
repetição do mesmo caso.

## Reversão

Antes da escrita, exporte os sete campos anteriores dos IDs listados no plano.
Para reverter, restaure somente esses IDs e campos, nunca por data, nome ou
filtro amplo. A migração de colunas é aditiva e pode permanecer instalada
mesmo se a normalização de dados for revertida.
