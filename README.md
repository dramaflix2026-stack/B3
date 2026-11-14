# B3 Scalper AI

Simulador educacional de scalping para WIN e WDO. **Não envia ordens reais.**

## Rodar
```bash
python -m unittest discover -s tests -v
python -m http.server 8000
```
Abra `http://localhost:8000` para o painel demonstrativo. O painel é independente do motor Python nesta primeira versão.

## Premissas
WIN: tick 5 pontos, R$1/tick/contrato. WDO: tick 0,5 ponto, R$5/tick/contrato. Custos e slippage são configuráveis. Stop é simulado e pode ultrapassar o limite em gaps. Sem dados de mercado ou corretora conectados.
