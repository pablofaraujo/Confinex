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

O modo `--modo-descoberta` existe somente para o Ciclo 1. Ele aprova a própria
auditoria quando, e somente quando, detecta os quatro defeitos já relatados:
Portfolio B3 em outra janela e os destinos inexistentes de Financeiro,
Pendências e Eventos. Qualquer falha adicional ou a ausência de uma dessas
detecções reprova a execução, salvo defeito adicional registrado explicitamente
pela primeira passagem exploratória. A auditoria inicial também detectou estouro
horizontal de 49 px no Painel Boi Gordo em desktop; ele permanece visível na
linha de base até a correção. O modo padrão é estrito e será o gate permanente
depois das correções.

## Comandos

```bash
python3 tools/auditar_ecossistema.py --modo-descoberta
npm ci
npx playwright install chromium
python3 tools/auditar_ecossistema.py --navegador --modo-descoberta \
  --saida-json artifacts/auditoria-ecossistema/relatorio.json \
  --saida-md artifacts/auditoria-ecossistema/relatorio.md
```
