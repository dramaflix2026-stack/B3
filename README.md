# B3 Scalper AI — Laboratório de Simulação

## Estado atual
Protótipo de navegador: replay progressivo de **preços sintéticos**, operações manuais e robô de referência (cruzamento de médias móveis 5/15), com carteiras separadas, slippage e taxas configuráveis, limite diário de perda, stop e histórico.

**Sem integração B3, sem cotação real, sem IA treinada e sem envio de ordens reais.** Resultados não são indicativos de rentabilidade. A execução é simplificada e não modela livro de ofertas, margem real, gaps intrabar ou latência de corretora.

## Executar o dashboard
Abra `index.html` localmente no navegador, ou execute `python -m http.server 8000` e visite `http://localhost:8000`.

## Testes do motor Python (independente do painel)
```bash
python -m unittest discover -s tests -v
```
O dashboard usa um simulador JavaScript próprio e **ainda não se conecta** a `simulator.py`.

## Próximos passos
1. Unificar o motor de execução e risco via API.
2. Corrigir o processamento intrabar e implementar ordens com margem, fila, custo, liquidez e stop mais realistas.
3. Importar dados históricos licenciados da B3 e garantir replay sem vazamento do futuro.
4. Persistir sessões e comparar estratégias fora da amostra.
5. Implementar testes de integração e deploy Railway.
6. Somente depois avaliar paper trading ao vivo e eventual conexão com corretora.

## Especificações
WIN: tick 5 pontos, R$ 1 por tick por contrato. WDO: tick 0,5 ponto, R$ 5 por tick por contrato. Conferir regras oficiais, horários, vencimentos e margens antes de operar.
