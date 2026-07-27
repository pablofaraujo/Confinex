# Consolidação das fontes operacionais

`tools/consolidar_fontes_operacionais.py` cruza snapshots privados e somente
leitura de Supabase, mensagens do Juan, planilha de NFs/GTAs e extrato bancário.
O resultado é um plano de conferência; ele não possui caminho de execução e não
faz chamadas de rede.

O cruzamento cobre:

- contagens e datas de corte das fontes;
- correspondência da planilha bancária com `transacoes_banco` por identificador;
- candidatos de conciliação por data e valor, sempre marcados como não confirmados;
- ambiguidades preservadas sem vínculo;
- NFs e GTAs entre planilha, staging fiscal, entradas de confinamento e tabela `gtas`;
- menções operacionais deduplicadas das sessões do Juan;
- estados de rascunhos, ações e eventos;
- datas futuras atípicas no fluxo de caixa.

Exemplo, usando somente arquivos privados temporários:

```bash
python3 tools/consolidar_fontes_operacionais.py \
  --supabase /caminho/privado/snapshot.json \
  --juan /caminho/privado/mensagens.json \
  --gta-planilha /caminho/privado/gta.json \
  --banco-planilha /caminho/privado/banco.json \
  --data-referencia AAAA-MM-DD \
  --saida-json /caminho/privado/plano.json \
  --saida-md /caminho/privado/relatorio.md
```

O relatório não autoriza conciliar banco, criar GTA, vincular documento,
alterar rascunho ou lançar operação. Uma data de corte anterior à referência é
um bloqueio de atualização, não um dado a ser preenchido por inferência.

## Atualização por OFX

`tools/analisar_extrato_ofx.py` compara um OFX recém-baixado com um snapshot
somente leitura de `transacoes_banco`. Ele não guarda dados de conta, nomes,
descrições ou valores no relatório: registra apenas hash do arquivo, período,
contagens, duplicidades e quantidade de identificadores já existentes ou novos.

```bash
python3 tools/analisar_extrato_ofx.py \
  --ofx /caminho/privado/extrato.ofx \
  --snapshot /caminho/privado/transacoes-banco.json \
  --data-referencia AAAA-MM-DD \
  --saida-json /caminho/privado/plano-extrato.json \
  --saida-md /caminho/privado/relatorio-extrato.md
```

O comando não tem opção de execução. Lançamentos ausentes são apenas apontados;
uma importação posterior precisa de autorização própria, chave idempotente por
`FITID` e comparação de contagens antes/depois.
