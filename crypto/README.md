# Laboratório cripto (isolado do B3 em produção)

## Objetivo
BTCUSDT e ETHUSDT **spot** em candles M1 públicos da Binance Vision. Simulação, sem corretora, API key ou ordens reais. Os dados não são de WIN/WDO.

## Execução
```bash
python crypto/download_history.py --start 2021-11 --end 2026-10 --symbol BTCUSDT --out data/crypto
python crypto/backtest.py --data 'data/crypto/BTCUSDT-1m-*.csv' --qty 0.001 --capital 1000 --fee 0.1 --slippage 1 --stop 5
```

A coleta mensal é retomável, verifica o SHA-256 publicado pela Binance e registra lacunas no manifesto. **Não foi executada integralmente**: é necessário um ambiente com acesso externo ao domínio data.binance.vision. A existência de todos os 60 arquivos ainda precisa ser comprovada. A base pode exigir centenas de MB ou mais. Não publicar os CSVs em GitHub nem redistribuir sem conferir os termos de uso.

## Hipóteses e limitações
- Separação cronológica de 70% para desenvolvimento e 30% para validação; não existe aprendizado de máquina treinado nesta fase.
- Entradas na abertura do candle seguinte ao sinal; stop estimado pelas máximas/mínimas; em gap, preço de abertura desfavorável. Não há book, spread dinâmico, profundidade ou latência real.
- Taxa `--fee` é um **valor fixo em USDT por lado**, não percentual; `--slippage` é variação absoluta do preço em USDT por lado.
- Limite de stop é valor alvo, não perda máxima garantida; gaps podem piorar a execução.
- Estratégias são protótipos. Resultados não são evidência de lucro futuro.
- Dados da Binance usam UTC. Spot BTCUSDT negocia continuamente, sem vencimento/rolagem.
- Teste CI `crypto-smoke.yml` é manual e baixa somente um mês; não representa validação de cinco anos.
- Não integrar ao serviço Railway B3 original até a validação técnica e aprovação do usuário.

## Próximas entregas
1. Executar download e verificar cobertura mensal e lacunas.
2. Implementar backtest em streaming e modelo percentual de taxas.
3. Testar resultados fora da amostra, inclusive em outros regimes de mercado.
4. Criar dashboard cripto independente.
