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
