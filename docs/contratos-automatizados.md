# Contratos e aditivos — fluxo seguro

O fluxo preparado para o Wey é:

WhatsApp → anexo privado → hash → vínculo com negócio → extração →
comparação com dados e termos aprovados → triagem de risco → aprovação
específica → plataforma de assinatura → arquivo final.

Somente a etapa de pré-análise está implementada. A ferramenta foi validada com
fixtures fictícias e instalada como skill do Wey depois de backup. Ela não move
arquivos, não grava no Supabase, não envia mensagens, não cria envelope, não
assina e não cria garantia.

## Pré-análise

`tools/contratos_workflow.py` recebe um documento e arquivos JSON com dados já
extraídos, negócio, termos aprovados e hashes históricos. O resultado:

- calcula SHA-256 e impede documento repetido;
- propõe um destino privado no Drive, sem mover o arquivo;
- compara quantidade, peso, valor, datas e pagamento;
- aponta cláusulas ausentes ou alteradas;
- registra página e confiança quando fornecidas pela extração;
- exige aprovação específica e revisão jurídica;
- mantém envio, assinatura e garantia bloqueados.

Os JSONs são interfaces internas; dados reais devem permanecer em armazenamento
privado e nunca virar fixture ou documentação pública.

## Finpec

Quando Finpec for identificado, a análise confere quantidade e unicidade dos
brincos. Sem identificação individual confirmada, o estado é
`BRINCOS_PENDENTES`. Mesmo com todos os brincos e cláusula compatível, o estado
é `REVISAO_JURIDICA`: nenhuma garantia é criada automaticamente.

## Drive e versões

A estrutura proposta é
`ClaudeCoWork/Contratos/<negócio>/<hash>-<arquivo>`. Antes de qualquer
organização real deve existir uma prévia com origem, destino, hash e operação
reversível. Compartilhamento permanece proibido por padrão.

O diretório `ClaudeCoWork/Confinex` é um espelho antigo e não é fonte de código.
O GitHub continua sendo a fonte canônica do aplicativo.

## Testes

```bash
python3 -m unittest tools/test_contratos_workflow.py
```

Os testes usam arquivos e partes fictícios. Não acessam Drive, WhatsApp,
Supabase ou plataforma de assinatura.

## Prévia offline por confinamento

`tools/planejar_acerto_contrato_confinamento.py` acrescenta um contrato puro
para organizar um snapshot já fornecido. Ele não consulta o Supabase, o Wey ou
o Drive e ainda não é chamado pelo gerador de dúvidas, pelo orquestrador ou
pelo timer diário.

```text
snapshot offline
  → vínculo exato operação → confinamento → contato
  → autorização de leitura separada por escopo
  → cobertura e pendências por abate
  → comparação do documento com negócio e termos aprovados
  → prévia para revisão, sem ação externa
```

A função pública é
`planejar_acerto_contrato(entrada_normalizada, *, autorizacoes_leitura)`. As
autorizações ficam em argumento separado do corpus e são apenas declarações do
chamador — não são credencial nem comprovação. Uma integração futura deverá
construí-las em camada confiável separada de OCR, documento e mensagem. A
entrada usa referências
opacas e o schema `entrada-acerto-contrato-confinamento-v1`; ela não descreve
novas colunas do banco. Os únicos papéis aceitos para vínculos são os já
existentes em `confinamento_contatos`: `confinamento`, `administrativo`,
`intermediario`, `finpec` e `outro`.

O vínculo exige igualdade das referências de operação, confinamento, contato,
abate e acerto. Nome semelhante ou homônimo nunca participa. `principal`
apenas permanece um atributo do vínculo: não concede autorização e não resolve
dois contatos autorizados. A autorização de leitura é uma lista separada por
escopo (`acerto`, `contrato` ou `aditivo`); zero ou mais de um candidato mantém
a seleção pendente.

Cada abate exige quantidade positiva e conserva sua própria cobertura de período, animais, romaneio,
custos, descontos e recebimento. Um acerto textual `pago`, `finalizado` ou
equivalente é apenas evidência e não confirma recebimento bancário. Extrato ou
comprovante só conta quando referencia explicitamente o par abate/acerto. Um
acerto de um abate nunca fecha os demais abates da mesma operação.

Documento e aditivo são dados não confiáveis. O SHA-256 declarado permite
detectar repetição dentro do snapshot, mas não autentica o conteúdo. Negócio ou
termos sem nenhum campo útil continuam pendentes; divergência nunca é apagada
por ausência de referência. Os estados `recebido`, `conferido`, `aprovado`,
`recebimento_bancario`, `assinatura` e `envio` são independentes e só preservam
`pendente`, `confirmado` ou `desconhecido` explicitamente informados.

A saída `plano-acerto-contrato-confinamento-v1` declara cobertura e somas apenas
do snapshot recebido, sem atestar completude. Com acertos concorrentes ou item
financeiro repetido entre versões, a soma consolidável fica indisponível; os
totais brutos permanecem nomeados apenas como candidatos do snapshot. Ela mantém
`autoriza_escrita=false`, `escritas=0` e todas as ações externas falsas. Não há
CLI, download, assinatura, envio, DML, fila, timer ou integração operacional.

Limitação conhecida do fluxo ativo: `gerar_pendencias_acertos` atualmente deixa
de emitir todos os abates de uma operação quando encontra qualquer acerto
considerado final nessa operação. O planejador novo evita essa inferência em
seu snapshot offline, mas não corrige nem substitui o gerador em produção.

Teste permanente:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tools/test_planejar_acerto_contrato_confinamento.py
```

O padrão `test_*.py` da bateria canônica já inclui esse arquivo; não existe uma
segunda execução especial.
