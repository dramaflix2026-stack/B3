# Integração de dados reais — decisão técnica

## Provedor candidato: Cedro Technologies

Fontes:
- https://cedrotech.com/produtos/market-data-cedro/
- https://cedrotech.com/market-apis/api-websocket/
- https://www.b3.com.br/en_us/market-data-and-indices/data-services/market-data/distributors/faq/

**Status: não contratado, sem credenciais, não conectado.** Não alegar cotação em tempo real enquanto não houver feed autenticado e monitorado.

## Antes de ativar

Solicitar confirmação comercial e técnica de:
1. WIN e WDO com **código de vencimento**, regras de rolagem e grade de tick.
2. Streaming de negócios e/ou OHLC de 1 minuto, carimbo de tempo com timezone e identificação do pregão.
3. Custo total, limites de chamadas, reconexão, retenção histórica e ambiente de teste.
4. Licenças para uso interno, armazenamento, processamento e eventual exibição em site público.
5. Documentação de autenticação e exemplos oficiais. Segredos somente em variáveis Railway; nunca no GitHub.

## Arquitetura-alvo

Provider WebSocket autenticado → normalizador de eventos → validador de símbolo/preço/horário → agregador de candles → engine de risco e paper trading → armazenamento Supabase → dashboard Railway.

Separar três estados:
- **Sintético**: gerado artificialmente, somente testes.
- **Histórico importado**: replay de CSV licenciado, sem acesso antecipado a candles futuros.
- **Ao vivo**: só quando houver autenticação, heartbeat e timestamp recentes.

Falha de conexão, timestamp atrasado, dados inconsistentes ou contrato vencido: congelar novas entradas, exibir estado de feed indisponível, registrar evento. Nenhuma ordem real.

## Critérios de aceite

- Identificar claramente símbolo e vencimento.
- Não usar ticker contínuo como contrato negociável.
- Validar timezone, tick size, duplicação e sequência.
- Testar desconexão, reconexão, atraso, troca de sessão e gaps.
- Comparar P&L líquido com custos e slippage.
- Separar resultados de backtest e forward paper trading.
- Executar testes unitários e integração antes de liberar modo ao vivo.
