# BingX Trading Bot

O backend em Python (FastAPI + APScheduler + SQLite) roda a estratégia. O painel em Next.js mostra o que o bot está fazendo e permite controlá-lo.

## Como rodar

1. Configure `backend/.env` (use `backend/.env.example` como modelo). O padrão é a conta **demo** (`BINGX_TESTNET=true`).
2. Dê dois cliques em `iniciar_backend.bat`. A API sobe em http://127.0.0.1:8010.
3. Dê dois cliques em `iniciar_painel.bat` e abra http://localhost:3000.
4. No painel, ligue a **Automação** na aba "Visão geral". Ela começa **desligada**.

Antes de cada trade, a IA da NVIDIA (`NVIDIA_API_KEY`, modelo Nemotron 3 Super) avalia três fontes:

- **Manchetes cripto:** Cointelegraph, CoinDesk e Decrypt, das últimas 2h.
- **Eventos da moeda:** CoinMarketCal (`COINMARKETCAL_API_KEY`), de 2h atrás até 48h à frente.
- **Macro dos EUA:** CPI, desemprego e payroll (BLS, `BLS_API_KEY`) e PIB real (BEA, `BEA_API_KEY`).

Se a IA considerar o cenário negativo para a operação, o trade é cancelado. `NEWS_ENABLED=false` desliga a checagem.

## Ciclo

| Quando | O quê |
|---|---|
| A cada 5 min, 5s após o fechamento do candle | Scanner, seguido imediatamente pela auto-execução |
| A cada 30s | Sync: atualiza PnL, move o stop para breakeven após o TP1 e detecta fechamentos |
| A cada 15 min | Manutenção: confirma o PnL real na BingX e calcula o resultado "sombra" dos sinais cancelados por notícia |

## Estrutura

| Arquivo | Papel (nome na especificação) |
|---|---|
| `backend/app/strategy.py` | Lógica pura da estratégia (indicadores, score, filtros, SL/TP), usada pelo scanner e pelo backtest |
| `backend/app/scanner.py` | advancedMarketScanner: filtros de mercado, chamada da estratégia, gravação dos sinais |
| `backend/app/auto_execute.py` | autoExecuteSignals: risco, duplicatas, cooldown, exposição, notícias, execução |
| `backend/app/news.py` | checkNewsBeforeTrade: RSS + CoinMarketCal + BLS/BEA analisados pela NVIDIA NIM |
| `backend/app/executor.py` | executeManualSignal: as 6 etapas (CROSS, alavancagem, MARKET, recálculo, SL, TP1, TP2/TP3 em background) |
| `backend/app/sync.py` | syncPositions: PnL, breakeven, motivo de saída e PnL real |
| `backend/app/risk.py` | Limite de perda diária, perdas seguidas e cooldown por par |
| `backend/app/performance.py` | Relatório de desempenho (aba Desempenho) |
| `backend/app/backtest.py` | Backtest com candles históricos reais (aba Backtest) |
| `backend/app/maintenance.py` | Apuração periódica (PnL real e sombras) |
| `frontend/` | Painel: visão geral, sinais, posições, trades, desempenho, backtest, configurações e logs |

## Regras da estratégia

Os pesos do score são os da especificação. Os critérios de cada item foram definidos na implementação:

| Item | Pontos | Critério |
|---|---|---|
| EMA + slope | 35 | 20 pelo alinhamento EMA9 > EMA21 > EMA50 (ou o inverso), mais até 15 pela força da inclinação da EMA50 |
| Pullback | 20 | Tocou a EMA21 = 20, tocou só a EMA9 = 14, apenas próximo = 8. É obrigatório para gerar sinal. |
| RSI | 25 | 40–60 vale 25 pts. 35–40 ou 60–65 vale 15 pts. A zona seguinte vale 5 pts. |
| Volume | 20 | Volume do último candle em relação à média de 20: ≥1.3x = 20, ≥1.0x = 14, ≥0.8x = 8 |
| Volatilidade | 10 | ATR entre 0.3% e 3% do preço = 10 pts. Entre 0.2% e 5% = 5 pts. |
| Bollinger | 10 | Preço perto da banda do meio (%B entre 0.3 e 0.65 a favor) = 10. Faixa vizinha = 5. |
| MACD | 10 | Histograma virando a favor = 5, linha vs sinal a favor = 3, linha do lado certo do zero = 2 |
| Momentum | 15 | 5 pts para cada: candle a favor, fechamento além do anterior, ROC(3) a favor |

**Configuração validada por backtest** (100 pares, 180 dias de mercado real):

- **Timeframe:** 1h, com 4h como timeframe maior. No 5m e no 15m a estratégia perde dinheiro, porque os custos comem os movimentos pequenos.
- **ADX mínimo de 40:** foi o filtro que mais separou trade bom de ruim.
- **Amplitude mínima de 2:** só executa quando pelo menos 2 pares deram sinal no mesmo candle. Sinal isolado dá −0,03R por trade, com 8 ou mais pares o resultado chega a +0,40R.
- **Carteira:** até 10 posições simultâneas, sem limite por direção. As ondas boas são vários pares indo na mesma direção. Numa onda, todos os sinais são executados no mesmo ciclo.
- **Dados de mercado da conta real:** ranking, candles, spread e funding vêm da conta real mesmo na demo. As ordens vão para a conta configurada e só pares que existem nela são operados.

**Filtros adicionais** (todos ligáveis/desligáveis em Configurações):

- **Inclinação mínima:** a EMA50 precisa ter inclinação mínima.
- **Candle de confirmação:** exige um candle a favor depois do pullback.
- **Timeframe maior:** a tendência no 1h precisa confirmar a direção.
- **BTC:** não opera LONG em alt com o BTC em queda, nem SHORT com o BTC em alta.
- **Custo:** a distância do stop precisa ser pelo menos 4× o custo (spread mais taxas de ida e volta).

**Gestão do trade:**

- **Stop:** fica a 1,5 ATR ou além do fundo/topo do pullback, o que for mais largo, com limite de 3 ATR.
- **Alvos:** são múltiplos do risco (TP1 = 1R, TP2 = 1,67R, TP3 = 2,67R). Com stop de 1,5 ATR, equivalem exatamente aos 1,5 / 2,5 / 4 ATR da especificação.
- **Breakeven:** quando o TP1 é atingido, o stop do restante vai para a entrada mais as taxas.
- **Tamanho da posição:** é calculado pelo risco. Por padrão, a perda no stop é de 0,5% do saldo. O "% do saldo" vira o teto de margem por posição.

**Controle de risco:**

- **Limite diário:** pausa novas entradas até 00:00 UTC quando a perda do dia passa de 5% do saldo ou há 4 perdas seguidas.
- **Cooldown:** pausa de 90 min no par depois de um stop.
- **Exposição:** no máximo 3 posições na mesma direção.

## Conta real

Além de `BINGX_TESTNET=false`, ajuste em Configurações:

- **Volume 24h mínimo:** 20 a 50 milhões de USDT.
- **Spread máximo:** 0,1%.
- **Backtest antes:** rode um na aba Backtest com essa configuração.

## Avisos

- **Proteções:** se o Stop Loss falhar, a posição fica **sem proteção**, exatamente como a especificação manda. O painel mostra um alerta vermelho "SEM SL" nesse caso.
- **Acesso local:** a API não tem senha. Ela escuta só em `127.0.0.1`, então não a exponha na internet.

## Painel na Vercel (acesso pelo celular)

O repositório tem duas partes:

- `frontend/` — painel Next.js (PWA). **É o que a Vercel publica.** Em *Project Settings → General → Root Directory*, use `frontend`.
- `backend/` — o bot (FastAPI + motor quant). **Roda no PC**, não na Vercel.

O painel na Vercel precisa alcançar o backend do PC por um endereço público (túnel). Variáveis de ambiente na Vercel:

| Variável | Valor |
|---|---|
| `BACKEND_URL` | endereço público do backend (túnel para `http://127.0.0.1:8010`) |
| `BOT_API_TOKEN` | o mesmo valor de `BOT_API_TOKEN` do `backend/.env` |
| `DASHBOARD_PASSWORD` | senha para entrar no painel |

Segurança: o backend recusa qualquer chamada sem `X-Bot-Token`; o token só é usado no servidor do Next (nunca vai ao navegador); o painel exige login quando `DASHBOARD_PASSWORD` está definida. Execução real continua bloqueada (somente DEMO).
