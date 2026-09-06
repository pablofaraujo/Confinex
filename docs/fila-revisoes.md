# Fila de revisões e promoção operacional

Atualizado em 2026-07-23. Este documento é o roteiro operacional da versão atual da fila de revisões/Juan.

## Fluxo final

1. Juan recebe uma mensagem no Telegram e mantém o nome do grupo como contexto humano.
2. O conteúdo vira um rascunho em `operation_drafts`. Auditorias antigas de compras passam antes por `reconciliar_compras_telegram.py`, que agrupa ocorrências, deduplica candidatos e deixa inferências sem confirmação como pendentes.
3. `revisoes.html` apresenta o rascunho por grupo, sem mostrar ID de grupo ou JSON. A pessoa revisora pode salvar ajustes, devolver para confirmação ou rejeitar com motivo.
4. A correção guiada destaca cada campo obrigatório ausente. **Salvar ajustes** permanece permitido; somente **Preparar promoção operacional** fica bloqueado enquanto houver falta.
5. A aprovação registra a decisão, mas ainda não grava dado operacional.
6. A preparação cria uma `pending_actions` do tipo `promover_revisao_operacional`, no estado `aguardando_confirmacao`, e um evento `promocao_operacional_preparada`.
7. A promoção só continua com uma nova mensagem `PROMOVER <id>` no mesmo contexto da origem. `promocao_confirmacao_router.py` valida a mensagem e entrega a execução a `promocao_operacional.py`.
8. O executor assume a pendência como `em_execucao` antes de inserir, grava exatamente um destino permitido e encerra como `executado`. O rascunho ligado passa a `realizado`.
9. `eventos` registra a decisão ou o resultado. A fila apresenta o histórico em linguagem operacional, com data, responsável e ID do registro quando houver.

Nenhuma compra é promovida automaticamente. Preparar, aprovar, simular e reconciliar auditorias não equivalem a gravar um lançamento.

## Complementos em outra sessão do Telegram

Comissão, frete, pagamento ou correção posterior devem recuperar primeiro as
evidências já recebidas no mesmo grupo/tópico, inclusive em sessões anteriores.
Sem correspondência inequívoca, mostrar os candidatos e perguntar somente qual
negócio, não pedir novamente todos os documentos. Extrato calculado no chat não
comprova rascunho salvo nem lançamento; o registro atual deve ser consultado.
A camada ativada em 05/09/2026 para essa recuperação é somente leitura e não
cria nem altera a fila. Juan continua consultando registros atuais pela ponte;
complementar rascunho e corrigir operação definitiva são decisões separadas,
nunca consequências automáticas de encontrar histórico. Fluxograma e testes:
[`continuidade-juan.md`](continuidade-juan.md).

A confirmação auditada de comissão é uma **proposta ainda inativa**. O módulo
puro apresenta negócio/grupo humanos, fornecedor, data, cabeças, beneficiário,
base preservada, percentual e valor. Se houver dois candidatos, exige escolher
um; encontrar nomes parecidos não autoriza decidir. O SQL proposto impede
sobrescrita por aba antiga, mas ainda não pode ser aplicado nem habilitado sem
os gates de [`complemento-comissao-juan.md`](complemento-comissao-juan.md).
**Salvar ajustes** do fluxo atual não foi modificado nesta etapa. A futura
comissão confirmada exige edição mediada; por segurança, seu novo contrato
fica congelado contra edição/rejeição/cancelamento pelo editor legado até essa
edição ser homologada. Não habilitar este recurso como se estivesse completo.

## Investigação antes de apresentar a dúvida

Uma pendência pode passar por uma investigação proativa antes de aparecer para
decisão. A camada consulta somente fontes autorizadas, reutiliza o staging
existente e apresenta alternativas, evidências favoráveis e contrárias,
cobertura e campos ainda ausentes. Ela não completa o formulário por
inferência, não reabre revisão encerrada e não transforma ausência de resultado
em ausência do fato quando a cobertura da fonte estiver incompleta.

A definição do rascunho, da pendência e do evento continua exclusiva de
`materializar_revisoes_staging.py`. No modo protegido, a tripla só aparece
depois que a investigação terminou: uma RPC atômica confere o grupo completo
de candidatos, cria as três linhas e anexa alternativas, prós e contras na
mesma transação. Falha ou snapshot alterado não deixa revisão parcial.
**Usar esta informação**, salvar ajustes, aprovar, preparar e confirmar promoção
continuam decisões separadas. O contrato e o plano de implantação estão em
[`docs/investigacoes-proativas.md`](investigacoes-proativas.md).

A interface bloqueia a revisão se a view de investigações falhar e nunca mostra
UUID, fingerprint ou timestamp técnico. A mesma regra existe no banco: cliente
antigo ou tela desatualizada não prepara promoção com investigação ativa ou
concluída sem anexo. Se a promoção ganhou a corrida antes da nova evidência, a
nova rodada fica complementar e não interfere no executor já autorizado.
Quando a promoção termina, a ativação proposta grava apenas um pedido
idempotente de sucessão. Se houve gravação operacional, ou se uma corretiva
ficou stale, a nova rodada exige replanejamento explícito das fontes e CAS;
ela não reaproveita silenciosamente o plano anterior. Uma revisão corretiva já
visível é preservada, cancelada e ligada à sucessora por evento auditável, sem
alterar o registro operacional.

A preparação atômica da promoção investigada é exclusiva de um mediador de
servidor com `service_role`; o navegador autenticado não pode chamar a RPC nem
receber essa credencial. Até o mediador validar autoria, vínculo, snapshots e o
pedido sanitizado, a feature flag permanece desligada. Se for ligada antes
disso, a tela falha fechada antes de qualquer escrita, sem voltar ao fluxo
legado. **Salvar ajustes** continua funcionando normalmente.

Enquanto o tipo do futuro lançamento não estiver definido, a tela apresenta
“Escolha o tipo de lançamento”, mantém **Salvar ajustes** disponível e bloqueia
**Preparar promoção operacional**. Assim, uma investigação manual nunca cria
uma pendência de promoção com destino inválido.

Rascunhos vindos do monitor fiscal preservam no conteúdo revisável a data de
emissão, número da NF-e, GTA identificada, quantidade, valor, contraparte e os
sete campos do contexto de origem. A tela também aceita `data_emissao` como
fonte da data humana. Dados já presentes no vínculo superior do rascunho são
herdados pela revisão, sem obrigar a pessoa a redigitar canal, mensagem, agente
ou situação da confirmação.

A revisão fiscal começa por uma ficha de pistas: emitente, destinatário,
natureza da operação, descrição dos animais, origem emitida/recebida, número,
data, valor, cabeças, GTA e relação sugerida. Os mesmos dados permanecem
editáveis logo abaixo. A ficha serve para localizar e confirmar o negócio; ela
não transforma a NF em compra, venda ou movimentação da Fazenda.

Quando a consolidação preserva uma ou mais leituras em `versoes_revisao`, a
tela apresenta cada alternativa separadamente. Ela mostra os valores humanos,
destaca os campos realmente divergentes, resume as evidências e explica por que
cada versão é plausível (por exemplo, correção explícita ou repetição em mais de
uma fonte). A pessoa pode usar uma versão como ponto de partida ou corrigir os
campos manualmente. A escolha só preenche o formulário no navegador: nenhuma
decisão é persistida até **Salvar ajustes**, e nenhuma promoção é preparada ou
executada por essa seleção. Identificadores de mensagem, UUIDs e conteúdo JSON
continuam fora da interface.

Na VPS, o sandbox do Juan não acessa o Supabase diretamente. O cliente envia
leituras e escritas de `operation_drafts`, `pending_actions`, `eventos` e demais
tabelas não operacionais permitidas à fila privada de `confinex_db_bridge.py`.
O worker do host executa uma tentativa por escrita e até cinco tentativas por
leitura. A restrição de escrita em tabelas operacionais pertence ao adaptador
de `ConfinexClient`, não à ponte inteira: ela mantém ações legadas mutantes.
O leitor de continuidade só disponibiliza `get_read`. Testes somente leitura
devem bloquear as demais capacidades antes da execução, não confiar apenas
em uma instrução textual para não salvar.

## Foto ou PDF de compra no Juan

Ao receber um documento de compra de gado, Juan segue esta ordem:

1. ao encontrar `MediaPath`, `MediaPaths`, `media://inbound/...`,
   `media:/inbound/...` ou `inbound/...`, chama `arquivo_grupo_router.py` antes
   de qualquer ferramenta interna de PDF, imagem ou pesagem; as URIs são
   normalizadas somente para `/root/.openclaw/media/inbound/NOME`, com bloqueio
   de traversal;
2. identifica a intenção de compra antes de encaminhar o arquivo ao OCR de pesagem;
3. tenta extrair os dados e montar um extrato sem perguntar antes se deve fazer a leitura;
4. apresenta os campos reconhecidos para confirmação;
5. calcula os valores derivados quando houver peso suficiente;
6. se faltarem peso, data ou condição/data de pagamento, informa que o cálculo não fecha e lista cada pendência de forma objetiva;
7. declara que nada foi salvo automaticamente; se o roteador falhar, encerra a
   leitura com erro técnico rastreável, sem tentar `pdf`, `image`, `file_fetch`
   ou OCR interno e sem pedir reenvio genérico;
8. somente no final oferece criar um rascunho na fila de Revisões.

Reconhecer, extrair, confirmar ou calcular nunca autoriza escrita em `compras`, `operation_drafts` ou qualquer outra tabela. O rascunho depende de aceite explícito posterior e continua sujeito à revisão visual e à promoção controlada.

Planilhas `.xlsx` entram primeiro no mesmo roteador. A leitura usa modo somente
leitura, valores calculados já armazenados, links externos desativados e limites
para tamanho compactado, conteúdo expandido e quantidade de entradas. A prévia
mostra abas, dimensões e cabeçalhos para classificação; fórmulas não são
executadas e nenhum dado é importado sem confirmação posterior.

PDF de extrato bancário é uma classe separada de compra. O roteador usa texto
determinístico do próprio PDF para reconhecê-lo antes do OCR visual; o item de
revisão registra que nenhuma transação foi importada ou conciliada e nunca pode
ser promovido como compra. Ao criar qualquer par vindo de arquivo,
`operation_drafts.pending_action_id` e `pending_actions.resultado.operation_draft_id`
devem apontar um para o outro. Reprocessar a mesma mensagem devolve o mesmo par;
uma classificação diferente para a mesma origem é bloqueada para revisão.

Desde 23/07/2026, `compra_documento_ocr.py` usa primeiro o canal OpenClaw/OpenAI já autenticado por OAuth (`openclaw infer image describe`) para ler foto/PDF de compra. Como o sandbox do Juan não possui rede externa, o pedido passa por uma fila privada na pasta de trabalho e é atendido por um trabalhador local supervisionado. PDFs têm até oito páginas processadas em paralelo; resultados são armazenados por assinatura do conteúdo para evitar releitura do mesmo anexo. O fallback local com Tesseract permanece para indisponibilidade do trabalhador ou do canal visual. O fluxo extrai compra, vendedor, cabeças, preço por arroba, peso total ou médio, desconto de barriga, data e pagamento quando legíveis; calcula peso total, arrobas e valor quando houver dados suficientes; e marca claramente quando precisou do fallback.

O runtime do Juan mantém o sandbox em `workspace-write`, mas não interrompe esse comando local com pedido de autorização. Alterar essa política sem repetir os testes do agente pode fazer Juan voltar a perguntar antes de ler o documento.

No contexto Boi Balança, fêmeas usam a regra operacional permanente: dividir o
peso total de balança por dois, descontar 20 kg por cabeça, dividir o peso
líquido por 15 para obter arrobas e multiplicar pelo preço informado. O extrato
de conferência mostra todas essas etapas e não pergunta novamente pelo desconto,
salvo quando o sexo não puder ser confirmado ou houver uma exceção expressa na
mensagem.

### Teste operacional validado na VPS

Arquivos reais recebidos pelo Telegram, um em foto e outro em PDF de duas páginas, confirmaram o seguinte comportamento, com os dados comerciais sensíveis omitidos deste repositório público:

- fornecedor, quantidade, preço por arroba e condição de desconto foram reconhecidos;
- foto e PDF foram lidos pelo canal OpenClaw/OpenAI;
- a primeira leitura do PDF terminou dentro do limite da ferramenta após o processamento paralelo;
- a trajetória do agente registrou `arquivo_grupo_router.py` como ferramenta de mídia, sem chamada à ferramenta interna de PDF ou imagem;
- peso médio foi convertido em peso total quando necessário;
- Juan calculou arrobas e valor quando havia peso suficiente;
- quando peso, data ou pagamento estavam ausentes, as pendências foram apresentadas objetivamente;
- Juan informou que nenhum dado havia sido salvo;
- a criação de rascunho foi oferecida somente ao final.

Na mesma validação, a compilação Python, 15 testes automatizados do Juan e a validação da configuração OpenClaw foram aprovados. As assinaturas dos IDs de `operation_drafts`, `pending_actions`, `eventos`, `compras`, `vendas`, `pesagens_caderno` e `abates` foram comparadas antes e depois e permaneceram idênticas: não houve escrita no Supabase. O gateway e o trabalhador de OCR foram reiniciados e permaneceram ativos.

## Tabelas e responsabilidades

| Tabela | Responsabilidade no fluxo |
|---|---|
| `operation_drafts` | Rascunho separado por contexto, dados extraídos, campos pendentes, inferências e vínculo com o registro realizado. É a área de correção; não é dado operacional. |
| `pending_actions` | Ordem controlada, confirmação, estado da execução, erro e resultado da promoção. A ação operacional desta fila é `promover_revisao_operacional`. |
| `eventos` | Trilha de auditoria das decisões, preparação e execução. Guarda tipo, estado, responsável, observação e vínculos; a tela apresenta uma versão humana desses dados. |
| `compras` | Destino operacional de compras aprovadas. Exige negócio, data, cabeças e valor total. |
| `vendas` | Destino operacional de venda/abate. Exige data do abate, cabeças, peso de carcaça, valor bruto e previsão de recebimento. |
| `pesagens_caderno` | Destino operacional de pesagens. Exige contexto, data da folha e peso. |
| `abates` | Destino operacional de abate detalhado. Exige data, lote, cabeças e peso líquido. |
| `memorias_agentes` | Memória auxiliar para continuidade do atendimento de Juan. Não autoriza nem comprova uma promoção. |
| `contexto_handoff` | Passagem de contexto entre agentes/processos. Não substitui rascunho, pendência ou evento e não é fonte de dado financeiro. |

Os vínculos que precisam ser preservados são: `source_draft_id`, `source_pending_action_id`, ID da promoção, ID do evento e `target_record_id`. O nome do grupo é a referência de tela; o identificador técnico do Telegram fica restrito à integração.

## Estados operacionais

| Nome na operação | Estado gravado | Significado e ação segura |
|---|---|---|
| Em revisão | `rascunho` ou `em_revisao` | Item pode ser corrigido e salvo. Ainda não existe lançamento operacional. |
| Aguardando confirmação | `aguardando_confirmacao` | Promoção preparada. Aguardar nova mensagem `PROMOVER <id>` no mesmo contexto. |
| Em execução | `em_execucao` | Um executor assumiu a pendência. Não iniciar outro executor. |
| Executado | `executado` | Lançamento e auditoria finalizados; conferir `target_record_id`. O rascunho de origem deve estar `realizado`. |
| Erro antes da gravação | `erro` | O destino operacional não foi confirmado como criado. Corrigir a causa e devolver ao fluxo de revisão; não há repetição automática. |
| Erro pós-gravação / precisa conferir | `erro_pos_gravacao` | O lançamento já foi criado, mas a finalização da auditoria falhou. Conferir o ID preservado e reconciliar; nunca repetir a promoção. |
| Rejeitado/cancelado | `rejeitado` em ação ou `cancelado` em rascunho | Encerrado sem novo lançamento. O motivo e o evento permanecem para auditoria. |

`confirmado_telegram`, `aprovado_confinex` e `realizado` são marcos intermediários ou de origem. Não substituem o estado final da `pending_actions` de promoção.

## Ferramentas

### `tools/reconciliar_compras_telegram.py`

Recebe um relatório JSON de auditoria, agrupa candidatos por conversa/contexto, descarta repetições e gera IDs determinísticos. O padrão é somente simulação:

```bash
python3 tools/reconciliar_compras_telegram.py caminho/da/auditoria.json
python3 tools/reconciliar_compras_telegram.py caminho/da/auditoria.json --consultar-banco
```

Para criar um lote pequeno e revisável, use limite explícito:

```bash
python3 tools/reconciliar_compras_telegram.py caminho/da/auditoria.json --executar --limite 2
```

Mesmo com `--executar`, o único destino é `operation_drafts`; a ferramenta nunca escreve em `compras`.

### `tools/promocao_operacional.py`

Sem `--executar`, valida e mostra a prévia da pendência:

```bash
python3 tools/promocao_operacional.py --pending-action-id <id>
```

A execução exige frase exata, conversa de origem e uma mensagem nova. No uso real esses valores vêm do roteador de Juan, não de uma digitação improvisada no servidor:

```bash
python3 tools/promocao_operacional.py \
  --pending-action-id <id> \
  --usuario <responsavel> \
  --executar \
  --confirmacao "PROMOVER <id>" \
  --origem-conversa-id <contexto> \
  --origem-mensagem-id <nova-mensagem>
```

Destinos permitidos: `compras`, `vendas`, `pesagens_caderno` e `abates`. Campos fora da lista permitida são descartados antes da inserção.

A proteção persistente de compras contra repetição foi aplicada pelo arquivo
`202607250001_compras_idempotencia.sql`, com dados antigos e RLS preservados.
O cliente e o executor implantados na VPS fazem cada promoção de compra usar uma
chave derivada da pendência. Uma repetição com os mesmos dados retorna
`duplicate`; a mesma chave com dados diferentes é recusada. Timeout é
reconciliado por leitura e nunca dispara um segundo envio automático. A
implantação foi validada com testes simulados e prévia real sem escrita.
Consulte
[`docs/idempotencia-compras.md`](idempotencia-compras.md).

### `tools/promocao_confirmacao_router.py`

Reconhece apenas a mensagem exata `PROMOVER <id>`. Para validar o caminho sem gravar:

```bash
python3 tools/promocao_confirmacao_router.py \
  --texto "PROMOVER <id>" \
  --grupo-id <contexto> \
  --mensagem-id <nova-mensagem> \
  --usuario <responsavel> \
  --preview
```

Sem `--preview`, a mensagem válida é encaminhada ao executor. Mensagem sem ID, reutilizada ou recebida em outro contexto é recusada.

## Roteiro de testes

### 1. Frontend local

```bash
node tools/test_revisoes_frontend.js
sed -n '/<script>/,/<\/script>/p' revisoes.html | sed '1d;$d' | node --check -
```

As simulações devem confirmar:

- contadores, filtros e separação pelo nome do contexto;
- compra incompleta/completa;
- venda sem/com previsão de recebimento;
- pesagem incompleta/completa;
- abate detalhado incompleto/completo;
- destaque, aviso e foco de cada campo ausente;
- **Salvar ajustes** habilitado e **Preparar promoção operacional** bloqueado somente enquanto faltar campo;
- rejeição sem motivo bloqueada, rejeição com motivo auditada, devolução e ajustes auditados;
- histórico sem JSON bruto para aguardando confirmação, em execução, executado, erro pós-gravação e rejeitado.
- comparação de versões com diferenças, evidências e justificativas humanas;
- seleção de uma versão preenchendo o formulário sem salvar, aprovar ou promover;
- nome humano do grupo na interface, com o ID técnico preservado somente no vínculo de origem.

O contrato e o procedimento de normalização dos contextos estão em
[`docs/contextos-por-grupo.md`](contextos-por-grupo.md). A normalização deve
ser simulada antes de qualquer escrita e não autoriza promoção operacional.

### 2. Executor local

```bash
python3 -m unittest discover -s tools -p 'test_*.py'
git diff --check
```

Os testes usam clientes simulados e devem cobrir confirmação exata, mensagem nova, contexto correto, trava de concorrência, destinos permitidos, erro antes da inserção, erro pós-gravação, deduplicação da auditoria e proibição de compra direta.

Com credenciais protegidas disponíveis, a prévia `promocao_operacional.py --pending-action-id <id>` pode validar uma pendência real sem gravar.

### 3. VPS/Juan

1. Conferir o commit implantado e os hashes das três ferramentas locais e da VPS.
2. Rodar os testes Python no ambiente da Ponte antes de reiniciar qualquer processo.
3. Para anexos, confirmar que o trabalhador de OCR está ativo e que a configuração OpenClaw é válida.
4. Executar `compra_documento_ocr.py` e `arquivo_grupo_router.py --dry-run` com foto e PDF reais da pasta de entrada.
5. Simular mensagens do agente com `MediaPath` e `MediaPaths`; conferir na trajetória que a primeira ferramenta de mídia é `arquivo_grupo_router.py` e que não houve chamada interna de PDF/imagem.
6. Comparar os IDs das sete tabelas antes e depois para provar que o teste de leitura não escreveu no Supabase.
7. Para promoção, preparar um caso controlado no Confinex e anotar os IDs do rascunho, da pendência e do evento.
8. Executar primeiro o roteador com `--preview`, usando uma nova mensagem do mesmo grupo.
9. Confirmar que nenhum registro operacional apareceu na prévia.
10. Somente então testar a mensagem real `PROMOVER <id>` pelo caminho normal de Juan.
11. Confirmar no Supabase: uma pendência `executado`, um rascunho `realizado`, um evento de execução e exatamente um registro operacional com o mesmo `target_record_id`.

Não é necessário alterar a VPS em uma mudança apenas de frontend ou documentação.

### 4. Teste real controlado e limpeza

Use um rascunho criado especificamente para homologação, com marcador inequívoco em `origem_registro` ou observação, valores não confundíveis com operação real e todos os IDs anotados antes da promoção. Limite o teste a um registro.

Depois da validação:

1. confira novamente os vínculos entre rascunho, pendência, evento e destino;
2. exporte ou registre a evidência necessária da homologação;
3. remova somente os registros com os IDs anotados e o marcador de teste, em transação e respeitando as dependências;
4. nunca faça limpeza por data, contexto, texto parcial ou filtro amplo;
5. consulte novamente todos os IDs e confirme que nenhum registro de teste ficou na fila, em `eventos` ou nas quatro tabelas operacionais;
6. limpe arquivos temporários e `__pycache__` criados pelo teste.

Se o teste representar uma operação real válida, não a apague: retire apenas o marcador de homologação conforme a regra operacional e preserve toda a auditoria.

## Reversão

### Identificar a versão

```bash
git status --short --branch
git log -5 --oneline
git show --stat <commit>
```

Registre o commit atual, o commit anterior e os hashes dos arquivos implantados. Não use uma pasta espelho como fonte.

### Reverter frontend ou documentação

Crie uma reversão auditável, sem apagar o histórico:

```bash
git revert <commit>
git push origin main
```

O push em `main` aciona o GitHub Pages. Depois, abra `revisoes.html`, recarregue sem cache e refaça a simulação frontend. Se o commit misturar mudanças independentes, restaure somente os arquivos afetados a partir do commit anterior, revise o diff e crie um novo commit explicando a reversão.

### Restaurar ferramenta na VPS

1. pare somente o processo consumidor afetado;
2. localize o caminho de backup registrado na implantação e confirme o hash e a data — não escolha um backup apenas pelo nome;
3. preserve uma cópia da versão atual antes de substituí-la;
4. restaure somente o arquivo afetado em `/root/ponte/tools`, mantendo proprietário e permissões;
5. compare o hash com o commit desejado, rode os testes Python na VPS e só então retome o processo;
6. registre qual arquivo, backup, commit, horário e responsável participaram da reversão.

Se a mudança envolver leitura de anexos, reverta em conjunto o roteador, o extrator, o trabalhador de OCR, a instrução efetiva do workspace e a política de aprovação do runtime. Recarregue as unidades supervisionadas, valide a configuração OpenClaw e repita um PDF e uma foto em `--dry-run`; restaurar somente um desses componentes pode reintroduzir pergunta preliminar, OCR interno ou estouro de tempo.

Se não houver backup validado, copie a versão do commit conhecido no clone canônico. Não improvise uma versão a partir de histórico de terminal.

### Conferir Supabase e eventos

Reverter código não desfaz lançamentos. Para cada promoção atingida, consulte pelo ID exato:

- `pending_actions`: estado, erro, `resultado.target_record_id` e confirmação;
- `operation_drafts`: estado e `entidade_final_id`;
- `eventos`: preparação, decisões e execução;
- tabela operacional indicada por `target_table`: existência e conteúdo do `target_record_id`.

Em `erro_pos_gravacao`, considere o lançamento existente até prova em contrário e nunca repita a promoção. Qualquer correção de dados deve ser uma reconciliação explícita e auditada; exclusão é reservada a registros inequivocamente marcados como teste e identificados antes da execução.
