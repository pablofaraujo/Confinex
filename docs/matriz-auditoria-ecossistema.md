# Matriz permanente de auditoria do ecossistema

Atualizado em 2026-07-23. A fonte executável é
`tools/auditar_ecossistema.py`; o navegador é dirigido por
`tools/auditar_ecossistema_browser.js`.

| Requisito | Cenário | Resultado esperado | Evidência |
|---|---|---|---|
| Inventário | repositório normal, vazio e referência inválida | páginas, scripts, testes, workflows, rotas, menu e dependências são listados; vazio e arquivo ausente falham | JSON e Markdown da auditoria |
| Menu | cada item interno | arquivo e âncora existem; não há redirecionamento inesperado | resultado estático por item |
| Janelas | Portfolio B3 | navega na mesma janela | `target`/política do manifesto |
| Janelas | Datamars, AgroNota e IMA/SIDAGRO | nova janela é aceita por serem ferramentas externas | política explícita por item |
| Navegação | clique, URL direta, recarga e voltar | destino permanece correto e volta à Visão Geral | execução Chromium |
| Navegação | Financeiro, Pendências e Eventos | cada módulo tem arquivo real; nenhuma âncora da Home é usada como substituta | regressão Python + Chromium |
| Conteúdo protegido | dados positivos, inválidos, vazios e falha da API | projeções legíveis, valores inválidos não contaminam totais, vazio é explícito e erro não vaza detalhe interno | `tools/test_gestao_frontend.js` |
| Privacidade | item sem contexto humano | a interface mostra “Contexto não informado”, nunca UUID ou ID de grupo | regressão JavaScript |
| Estado | página acessada | somente o item correspondente fica ativo | DOM após navegação |
| Runtime | página carregada | nenhum erro JavaScript, console, HTTP ou requisição | eventos do Chromium |
| Desktop | 1440 × 1000 | shell presente, sem estouro da página | PNG integral + medição |
| Celular | 390 × 844 | shell presente, sem estouro da página | PNG integral + medição |
| CI | push, PR, agenda e execução manual | auditoria estática e Chromium geram artefato | GitHub Actions |

## Estados

- `aprovado`: o resultado observado corresponde ao esperado.
- `falhou`: houve divergência reproduzível.
- `não testado`: a camada não foi executada; no modo estrito bloqueia a
  conclusão.

## Linha de base anterior às correções

O modo `--modo-descoberta` preserva somente a prova histórica do Ciclo 1. Ele aprovava a própria
auditoria quando, e somente quando, detecta os quatro defeitos já relatados:
Portfolio B3 em outra janela e os destinos inexistentes de Financeiro,
Pendências e Eventos. Qualquer falha adicional ou a ausência de uma dessas
detecções reprovava a execução, salvo defeito adicional registrado explicitamente
pela primeira passagem exploratória. A auditoria inicial também detectou estouro
horizontal de 49 px no Painel Boi Gordo em desktop. O Ciclo 2 corrigiu os cinco
defeitos e promoveu o modo estrito a gate permanente; a configuração histórica
continua versionada para demonstrar o que a auditoria detectava antes da correção.

## Comandos

```bash
python3 tools/auditar_ecossistema.py --somente-estatico
npm ci
npx playwright install chromium
python3 tools/auditar_ecossistema.py --navegador \
  --saida-json artifacts/auditoria-ecossistema/relatorio.json \
  --saida-md artifacts/auditoria-ecossistema/relatorio.md
```

`--somente-estatico` mantém a camada de navegador registrada como “não
testado”, mas permite que a bateria parcial valide arquivos e contratos. Ele
não é usado no gate Chromium: a execução com `--navegador` continua reprovando
qualquer falha ou requisito sem teste.
