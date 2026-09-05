# Rastreabilidade bancária e componentes — aprofundamento P1

Status: diagnóstico offline, anterior à normalização. Não muda o importador,
o Financeiro, o Juan, políticas, tabelas ou registros. Não contém migração nem
comando de execução operacional.

## Identidade antes de conciliação

| Camada | Evidência | O que não comprova sozinha |
|---|---|---|
| Arquivo | SHA-256 dos bytes preservados | Autenticidade bancária ou aprovação |
| Demonstrativo | BANKID, BRANCHID, ACCTID, ACCTTYPE, CURDEF | Equivalência com um nome livre de conta |
| Lançamento | Identidade completa + FITID, data, valor com sinal, tipo e conteúdo | Negócio ao qual o pagamento pertence |
| Registro de conferência | ID da fonte + hash + dados conferidos no arquivo | Autorização para eliminar outra ocorrência |
| Alias proposto | Rótulo existente associado a identidade e fonte verificadas | Regra global válida para todos os períodos |

Identificadores continuam texto, preservando caixa, zeros e pontuação. Não
comparar FITID globalmente. Não usar valor absoluto para igualar crédito e
débito. Identidade incompleta, fonte ausente, colisão ou divergência ficam
pendentes. Uma importação repetida não deve apagar a evidência original.

`tools/perfilar_identidade_ofx.py` lê OFX XML/SGML autorizado, por demonstrativo.
Preserva todas as ocorrências e classifica o conjunto de arquivos. Não retorna
MEMO, descrições ou nomes; o hash do conteúdo permite detectar diferenças sem
publicar o texto. O perfil continua privado, pois tem contas/FITID/valores.

Datas OFX compactas e a variante ISO explícita preservam data original,
representação, fração e fuso. Comparar dia civil não equivale a comparar instante.
Formato inválido bloqueia, não vira data inferida. XML malformado ou agregados
truncados não devem ser tratados como extração bem-sucedida.

O hash de transação XML usa a serialização do ElementTree; no SGML usa o trecho
textual do bloco decodificado em UTF-8. Não é o hash dos bytes originais da
transação nem um identificador canônico entre formatos. Diferença de hash exige
conferência: pode ser correção real, formato ou horário. O hash do arquivo
inteiro continua sendo dos bytes originais.

## Como executar sem gravação na base

```sh
python3 -B tools/perfilar_identidade_ofx.py \
  --ofx /caminho/privado/extrato-a.ofx \
  --ofx /caminho/privado/extrato-b.ofx \
  --saida docs/privado/perfil-ofx-novo
```

A saída precisa ser nova e privada (diretório 700, arquivos 600). O terminal
mostra só contagens/assinaturas. Todas as fontes são relidas antes de salvar.
Não existe `--executar`, cliente Supabase ou rede nesse perfilador.

A consulta `supabase/audits/proveniencia_ofx_somente_leitura.sql` deve ser usada
separadamente, somente por acesso autorizado: duas fotografias, papel SQL,
horários e hash da consulta. Proteções `READ ONLY`, `REPEATABLE READ`, limites
de tempo e `ROLLBACK`. Exporta fontes e transações em conferência, sem payloads,
com contagens completas e números decimais textuais. Igualdade antes/depois
prova estabilidade dessas projeções, não de toda a base ou de suas permissões.

Para conferir uma fonte: encontrar o arquivo pelo hash; conferir identidade por
demonstrativo; comparar cada linha pela origem, FITID, dia civil e valor exato;
preservar divergências de conteúdo. Nome do arquivo ou coincidência de valores
não substituem esse caminho. Plano de alias sempre restrito aos registros e
fontes comprovados, nunca aplicado automaticamente.

## Componentes: fornecedor não é subgrupo de animais

O contrato existente em [interunidades](interunidades.md) dá a
`compras_componentes` papel informativo por fornecedor/corretor. O total
econômico vem de `compras`. Sexo, categoria e destino descrevem subdivisões
animais; não são obrigatórios em todo componente por mera ausência no JSON.

Comparar pai/filhos e linhas da aba Compras exige conferir cobertura e corte
temporal. Código da operação pode abranger várias compras. Quantidade diferente
não prova que o total está errado; origem parcial não deve gerar componente
inventado. Valor de animais, comissão, frete e total composto são campos
distintos. Célula vazia ou fórmula sem cache não vira zero.

Chave histórica com sufixo de candidato é uma pista, não vínculo comprovado.
Confirmar o produtor da chave e sua fonte antes de associar ou copiar campos.

## Riscos ainda abertos no importador existente

- `importar_ofx_staging.py` usa primeiro BANKID/ACCTID do arquivo e descarta
  repetição local sem conferir todo o conteúdo; não usar o novo diagnóstico
  como declaração de que esse caminho publicado já foi corrigido.
- Consumidores `propor_conciliacoes_staging.py` e `analisar_extrato_ofx.py`
  precisam de gate próprio para comparação de FITID/escopo; heurística por
  valor não resolve identidade.
- Antes de alterar esses consumidores: regressões de contas distintas,
  múltiplos demonstrativos, aliases comprovados, conteúdo divergente e crédito
  versus débito; plano idempotente, reversível e aprovado para qualquer escrita.

Não há dados a reverter nesta fase. Relatórios privados são derivados; fontes
permanecem intactas. Não excluir lançamentos reais para fazer os totais baterem.

```sh
python3 -B -m unittest tools.test_perfilar_identidade_ofx tools.test_sql_proveniencia_ofx tools.test_diagnosticar_vinculos_negocios
python3 -B tools/test_ecossistema.py
git diff --check
```
