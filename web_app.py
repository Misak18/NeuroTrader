import sys
import webview
import random
import time
import json
from trading_bot import TradingBot

class MarketDataStream:
    def __init__(self, window):
        self.window = window
        self.running = False

    def start_stream(self, symbol="BTCUSDT", tf="5m"):
        self.running = True
        tf_seconds = {'1s': 1, '1m': 60, '5m': 300, '15m': 900, '1h': 3600}.get(tf, 300)
        
        current_price = 64000.0
        
        while self.running:
            now = int(time.time())
            candle_time = (now // tf_seconds) * tf_seconds
            change = random.uniform(-5, 5)
            current_price += change
            
            tick_data = {
                "time": candle_time,
                "close": round(current_price, 2),
            }
            
            # Отправляем тик напрямую в фронтенд без опроса (polling)
            js_code = f"onNewTick({json.dumps(tick_data)});"
            try:
                self.window.evaluate_js(js_code)
            except Exception:
                pass
            
            time.sleep(0.2)

class Bridge:
    def __init__(self, bot):
        self.bot = bot

    def start_bot(self, settings):
        print(f"⚙️ Приняты настройки: {settings}")
        if hasattr(self.bot, 'start'):
            self.bot.start()
        elif hasattr(self.bot, 'start_bot'):
            self.bot.start_bot()
        return {"status": "started"}

    def stop_bot(self):
        """Остановка бота."""
        if hasattr(self.bot, 'stop'):
            self.bot.stop()
        elif hasattr(self.bot, 'stop_bot'):
            self.bot.stop_bot()
        return {"status": "stopped"}

    def get_dashboard_data(self, symbol="BTCUSDT", tf="1m"):
        bybit_tf_map = {
            '1s': '1', '1m': '1', '5m': '5', '15m': '15', 
            '1h': '60', '4h': '240', '1D': 'D'
        }
        interval = bybit_tf_map.get(tf, '1')

        # 1. Запрашиваем данные
        df = self.bot.get_historical_data(symbol, interval=interval, limit=200)
        
        candles = []
        if df is not None and not df.empty:
            df_sorted = df.iloc[::-1].copy()
            
            for timestamp, row in df_sorted.iterrows():
                try:
                    if hasattr(timestamp, 'timestamp'):
                        t = int(timestamp.timestamp())
                    else:
                        t = int(float(timestamp) / 1000)

                    candles.append({
                        "time": t,
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"])
                    })
                except Exception as e:
                    print(f"⚠️ Ошибка обработки свечи: {e}")

        # 2. Метрики бота
        profit = self.bot.get_total_profit()
        success = self.bot.calculate_success_rate()
        running = self.bot.running

        # 3. Открытые позиции
        active_orders = []
        for sym, pos in self.bot.positions.items():
            active_orders.append({
                "id": sym,
                "symbol": sym,
                "type": pos.get("side", "BUY").upper(),
                "entry": float(pos.get("price", 0)),
                "amount": float(pos.get("order_amount", 0)),
                "pnl": 0.0
            })

        # 4. История сделок
        history_orders = []
        for trade in self.bot.paper_trading_data[-20:][::-1]:
            history_orders.append({
                "id": str(trade.get("timestamp_unix", "")),
                "symbol": trade.get("symbol", "N/A"),
                "type": trade.get("action", "BUY").upper(),
                "entry": float(trade.get("price", 0)),
                "exit": float(trade.get("price", 0)),
                "pnl": float(trade.get("profit", 0)),
                "time": str(trade.get("timestamp", ""))
            })

        print(f"📊 [Dashboard] Успешно передано {len(candles)} свечей в JS")

        return {
            "symbol": symbol,
            "tf": tf,
            "candles": candles,
            "profit": round(profit, 2),
            "success_rate": round(success, 2),
            "running": running,
            "orders": active_orders,
            "history": history_orders
        }


HTML_LAYOUT = """
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>NeuroTrader</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <!-- TradingView Lightweight Charts CDN -->
    <script src="https://unpkg.com/lightweight-charts@4.1.1/dist/lightweight-charts.standalone.production.js"></script>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', sans-serif; user-select: none; }
        body, html { background-color: #0d0e12; color: #f3f4f6; height: 100%; width: 100%; overflow: hidden; display: flex; flex-direction: column; }

        /* Top Header */
        .topbar {
            height: 48px; background-color: #121418; border-bottom: 1px solid #1f232d;
            display: flex; align-items: center; justify-content: space-between; padding: 0 16px; flex-shrink: 0;
        }
        .logo { font-size: 15px; font-weight: 700; color: #f7a600; display: flex; align-items: center; gap: 8px; }
        .pair-selector { display: flex; gap: 6px; }
        .pair-btn {
            background: #1a1d24; border: 1px solid #2a2e39; color: #9ca3af;
            padding: 4px 10px; border-radius: 4px; cursor: pointer; font-size: 12px; font-weight: 600;
        }
        .pair-btn.active, .pair-btn:hover { background: #2a2e3d; color: #f7a600; border-color: #f7a600; }

        /* Main Workspace Grid */
        .main-container { flex: 1; display: grid; grid-template-columns: 260px 1fr 300px; height: calc(100vh - 48px); overflow: hidden; }

        /* Left Panel - Settings */
        .left-panel {
            background: #121418; border-right: 1px solid #1f232d; padding: 16px;
            display: flex; flex-direction: column; gap: 14px; overflow-y: auto;
        }
        .section-title { font-size: 12px; font-weight: 600; color: #848e9c; text-transform: uppercase; letter-spacing: 0.5px; }
        .input-group { display: flex; flex-direction: column; gap: 4px; }
        .input-group label { font-size: 11px; color: #848e9c; }
        .input-group input {
            background: #1a1d24; border: 1px solid #2a2e39; color: #fff;
            padding: 8px 10px; border-radius: 4px; font-size: 12px; outline: none;
        }
        .input-group input:focus { border-color: #f7a600; }

        .btn-action {
            padding: 10px; border: none; border-radius: 4px; font-weight: 600;
            font-size: 13px; cursor: pointer; transition: 0.2s; margin-top: 4px;
        }
        .btn-start { background: #0ecb81; color: #000; }
        .btn-start:hover { background: #0bb371; }
        .btn-stop { background: #f6465d; color: #fff; }
        .btn-stop:hover { background: #d93d52; }

        /* Middle - Chart Area */
        .chart-panel { background: #0d0e12; display: flex; flex-direction: column; width: 100%; height: 100%; position: relative; }
        
        .tf-bar {
            height: 36px; background: #121418; border-bottom: 1px solid #1f232d;
            display: flex; align-items: center; padding: 0 12px; gap: 4px; flex-shrink: 0;
        }
        .tf-btn {
            background: transparent; border: none; color: #848e9c;
            padding: 4px 8px; border-radius: 3px; cursor: pointer; font-size: 12px; font-weight: 600;
        }
        .tf-btn.active, .tf-btn:hover { background: #1a1d24; color: #f7a600; }

        #chart { width: 100%; flex: 1; position: relative; }

        /* Right Panel - Orders & History */
        .right-panel {
            background: #121418; border-left: 1px solid #1f232d;
            display: flex; flex-direction: column; height: 100%; overflow: hidden;
        }
        .tabs-header { display: flex; border-bottom: 1px solid #1f232d; background: #0d0e12; }
        .tab-btn {
            flex: 1; padding: 12px; background: transparent; border: none;
            color: #848e9c; font-size: 12px; font-weight: 600; cursor: pointer;
            border-bottom: 2px solid transparent; text-align: center;
        }
        .tab-btn.active { color: #f7a600; border-bottom-color: #f7a600; background: #121418; }

        .tab-content { flex: 1; padding: 12px; overflow-y: auto; display: flex; flex-direction: column; gap: 8px; }

        .order-card {
            background: #1a1d24; border-radius: 6px; padding: 10px; border-left: 3px solid #848e9c;
            display: flex; flex-direction: column; gap: 4px; font-size: 11px;
        }
        .order-card.LONG { border-left-color: #0ecb81; }
        .order-card.SHORT { border-left-color: #f6465d; }
        .order-header { display: flex; justify-content: space-between; font-weight: 700; }
        .order-header .LONG { color: #0ecb81; }
        .order-header .SHORT { color: #f6465d; }
        .order-row { display: flex; justify-content: space-between; color: #848e9c; }
        .pnl-positive { color: #0ecb81; font-weight: 700; }
        .pnl-negative { color: #f6465d; font-weight: 700; }

        .status-tag { font-size: 10px; padding: 2px 6px; border-radius: 3px; background: #2a2e39; color: #848e9c; }
        .status-tag.active { background: rgba(14, 203, 129, 0.2); color: #0ecb81; }
    </style>
</head>
<body>

    <div class="topbar">
        <div class="logo">TERMINAL <span id="statusTag" class="status-tag">ОСТАНОВЛЕН</span></div>
        <div class="pair-selector">

            <button class="pair-btn active" onclick="switchPair('BTCUSDT', this)">BTC/USDT</button>
            <button class="pair-btn" onclick="switchPair('ETHUSDT', this)">ETH/USDT</button>
            <button class="pair-btn" onclick="switchPair('SOLUSDT', this)">SOL/USDT</button>

            <button class="pair-btn" onclick="switchPair('BNBUSDT', this)">BNB/USDT</button>
            <button class="pair-btn" onclick="switchPair('XRPUSDT', this)">XRP/USDT</button>
            <button class="pair-btn" onclick="switchPair('ADAUSDT', this)">ADA/USDT</button>
            <button class="pair-btn" onclick="switchPair('AVAXUSDT', this)">AVA/USDT</button>
            <button class="pair-btn" onclick="switchPair('NEARUSDT', this)">NEAR/USDT</button>
            <button class="pair-btn" onclick="switchPair('APTUSDT', this)">APT/USDT</button>
            <button class="pair-btn" onclick="switchPair('SUIUSDT', this)">SUI/USDT</button>
            <button class="pair-btn" onclick="switchPair('LINKUSDT', this)">LINK/USDT</button>
            <button class="pair-btn" onclick="switchPair('DOTUSDT', this)">DOT/USDT</button>
            <button class="pair-btn" onclick="switchPair('LTCUSDT', this)">LTC/USDT</button>
            <button class="pair-btn" onclick="switchPair('DOGEUSDT', this)">DOGE/USDT</button>
            <button class="pair-btn" onclick="switchPair('OPUSDT', this)">OPU/USDT</button>
            <button class="pair-btn" onclick="switchPair('ARBUSDT', this)">ARB/USDT</button>
            <button class="pair-btn" onclick="switchPair('INJUSDT', this)">INJ/USDT</button>
        </div>
    </div>

    <div class="main-container">
        <!-- СЛЕВА: Настройки бота -->
        <div class="left-panel">
            <div class="section-title">Параметры ордера</div>
            
            <div class="input-group">
                <label>Сумма на ордер (USDT)</label>
                <input type="number" id="orderAmount" value="50">
            </div>

            <div class="input-group">
                <label>Минимальный профит (%)</label>
                <input type="number" step="0.1" id="minProfit" value="1.5">
            </div>

            <div class="input-group">
                <label>Stop Loss (%)</label>
                <input type="number" step="0.1" id="stopLoss" value="0.8">
            </div>

            <div class="input-group">
                <label>Плечо (Leverage)</label>
                <input type="number" id="leverage" value="10">
            </div>

            <hr style="border: 0; border-top: 1px solid #1f232d; margin: 4px 0;">

            <button class="btn-action btn-start" onclick="startBot()">Запустить бота</button>
            <button class="btn-action btn-stop" onclick="stopBot()">Остановить бота</button>

            <div style="margin-top: auto; background: #1a1d24; padding: 10px; border-radius: 6px;">
                <div class="section-title" style="margin-bottom: 6px;">Общая статистика</div>
                <div class="order-row">Прибыль: <span id="totalProfit" class="pnl-positive">0.00 USDT</span></div>
                <div class="order-row">Винрейт: <span id="successRate" style="color: #fff;">0%</span></div>
            </div>
        </div>

        <!-- В ЦЕНТРЕ: Панель Таймфреймов и График -->
        <div class="chart-panel">
            <div class="tf-bar">
                <span style="font-size: 11px; color: #848e9c; margin-right: 8px;">Таймфрейм:</span>
                <button class="tf-btn" onclick="switchTF('1s', this)">1s</button>
                <button class="tf-btn active" onclick="switchTF('1m', this)">1m</button>
                <button class="tf-btn" onclick="switchTF('5m', this)">5m</button>
                <button class="tf-btn" onclick="switchTF('15m', this)">15m</button>
                <button class="tf-btn" onclick="switchTF('1h', this)">1h</button>
                <button class="tf-btn" onclick="switchTF('4h', this)">4h</button>
                <button class="tf-btn" onclick="switchTF('1D', this)">1D</button>
            </div>
            <div id="chart"></div>
        </div>

        <!-- СПРАВА: Открытые ордеры / История -->
        <div class="right-panel">
            <div class="tabs-header">
                <button class="tab-btn active" id="tabActiveBtn" onclick="switchTab('active')">Открытые</button>
                <button class="tab-btn" id="tabHistoryBtn" onclick="switchTab('history')">История</button>
            </div>

            <div id="activeTabContent" class="tab-content">
                <!-- Открытые ордеры -->
            </div>

            <div id="historyTabContent" class="tab-content" style="display: none;">
                <!-- История ордеров -->
            </div>
        </div>
    </div>

<script>
    let currentPair = 'BTCUSDT';
    let currentTF = '1m';
    let currentTab = 'active';
    let chart, candlestickSeries;
    let priceLines = [];

    function initChart() {
        const chartContainer = document.getElementById('chart');
        
        chart = LightweightCharts.createChart(chartContainer, {
            layout: { backgroundColor: '#0d0e12', textColor: '#848e9c' },
            grid: { vertLines: { color: '#1f232d' }, horzLines: { color: '#1f232d' } },
            crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
            
            // Включаем корректное автомасштабирование правой шкалы цен
            rightPriceScale: {
                autoScale: true,
                borderColor: '#1f232d',
                scaleMargins: {
                    top: 0.1,    // Отступ сверху (10%), чтобы свечи не упирались в край
                    bottom: 0.1, // Отступ снизу (10%)
                },
            },
            timeScale: { 
                borderColor: '#1f232d', 
                timeVisible: true,
                secondsVisible: true
            },
            handleScroll: { mouseWheel: true, pressedMove: true, horzTouchDrag: true, vertTouchDrag: true },
            handleScale: { axisPressedMouseMove: true, mouseWheel: true, pinch: true }
        });

        candlestickSeries = chart.addCandlestickSeries({
            upColor: '#0ecb81', downColor: '#f6465d',
            borderUpColor: '#0ecb81', borderDownColor: '#f6465d',
            wickUpColor: '#0ecb81', wickDownColor: '#f6465d'
        });

        // Автоматическое растягивание графика при изменении размера окна
        const resizeChart = () => {
            chart.applyOptions({ 
                width: chartContainer.clientWidth, 
                height: chartContainer.clientHeight 
            });
        };

        new ResizeObserver(resizeChart).observe(chartContainer);
        setTimeout(resizeChart, 100);
    }

    function switchPair(symbol, btn) {
        currentPair = symbol;
        document.querySelectorAll('.pair-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        updateData(true);
    }

    function switchTF(tf, btn) {
        currentTF = tf;
        document.querySelectorAll('.tf-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        updateData(true);
    }

    function switchTab(tab) {
        currentTab = tab;
        if(tab === 'active') {
            document.getElementById('tabActiveBtn').classList.add('active');
            document.getElementById('tabHistoryBtn').classList.remove('active');
            document.getElementById('activeTabContent').style.display = 'flex';
            document.getElementById('historyTabContent').style.display = 'none';
        } else {
            document.getElementById('tabHistoryBtn').classList.add('active');
            document.getElementById('tabActiveBtn').classList.remove('active');
            document.getElementById('historyTabContent').style.display = 'flex';
            document.getElementById('activeTabContent').style.display = 'none';
        }
    }

    function updateData(resetZoom = false) {
        if (window.pywebview && window.pywebview.api) {
            window.pywebview.api.get_dashboard_data(currentPair, currentTF).then(data => {
                if (!data.candles || data.candles.length === 0) return;

                // 1. Обновляем данные свечей
                if (resetZoom) {
                    // При смене пары/ТФ полностью обновляем массив
                    candlestickSeries.setData(data.candles);
                    chart.priceScale('right').applyOptions({ autoScale: true });
                    chart.timeScale().fitContent();
                } else {
                    // Обновляем только последнюю свечу
                    const lastCandle = data.candles[data.candles.length - 1];
                    candlestickSeries.update(lastCandle);
                }

                // 2. Статистика
                document.getElementById('totalProfit').innerText = data.profit + ' USDT';
                document.getElementById('successRate').innerText = data.success_rate + '%';
                const tag = document.getElementById('statusTag');
                if(data.running) {
                    tag.innerText = "В РАБОТЕ"; tag.classList.add('active');
                } else {
                    tag.innerText = "ОСТАНОВЛЕН"; tag.classList.remove('active');
                }

                // 3. Очищаем прошлые линии ордеров с графика
                priceLines.forEach(line => candlestickSeries.removePriceLine(line));
                priceLines = [];

                // 4. Отрисовка активных ордеров
                const activeContainer = document.getElementById('activeTabContent');
                activeContainer.innerHTML = '';

                data.orders.forEach(order => {
                    const pnlClass = order.pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
                    const pnlSign = order.pnl >= 0 ? '+' : '';
                    
                    activeContainer.innerHTML += `
                        <div class="order-card ${order.type}">
                            <div class="order-header">
                                <span class="${order.type}">${order.type} ${order.symbol}</span>
                                <span class="${pnlClass}">${pnlSign}${order.pnl} USDT</span>
                            </div>
                            <div class="order-row">
                                <span>Вход: ${order.entry}</span>
                                <span>Объем: $${order.amount}</span>
                            </div>
                        </div>
                    `;

                    if (order.symbol === currentPair) {
                        const line = candlestickSeries.createPriceLine({
                            price: order.entry,
                            color: order.type === 'LONG' ? '#0ecb81' : '#f6465d',
                            lineWidth: 2,
                            lineStyle: LightweightCharts.LineStyle.Dashed,
                            axisLabelVisible: true,
                            title: `${order.type} OPEN`,
                        });
                        priceLines.push(line);
                    }
                });

                // 5. Отрисовка истории ордеров
                const historyContainer = document.getElementById('historyTabContent');
                historyContainer.innerHTML = '';

                data.history.forEach(item => {
                    const pnlClass = item.pnl >= 0 ? 'pnl-positive' : 'pnl-negative';
                    const pnlSign = item.pnl >= 0 ? '+' : '';
                    historyContainer.innerHTML += `
                        <div class="order-card ${item.type}">
                            <div class="order-header">
                                <span class="${item.type}">${item.type} ${item.symbol}</span>
                                <span class="${pnlClass}">${pnlSign}${item.pnl} USDT</span>
                            </div>
                            <div class="order-row">
                                <span>Вход: ${item.entry} | Выход: ${item.exit}</span>
                                <span>${item.time}</span>
                            </div>
                        </div>
                    `;
                });
            });
        }
    }

    function startBot() {
        const settings = {
            amount: document.getElementById('orderAmount').value,
            min_profit: document.getElementById('minProfit').value,
            stop_loss: document.getElementById('stopLoss').value,
            leverage: document.getElementById('leverage').value
        };
        window.pywebview.api.start_bot(settings);
    }

    function stopBot() {
        window.pywebview.api.stop_bot();
    }

    window.onload = () => {
        initChart();
        setTimeout(() => updateData(true), 300);
        setInterval(() => updateData(false), 1000);
    };
</script>
</body>
</html>
"""


def main():
    bot = TradingBot()
    bridge = Bridge(bot)

    window = webview.create_window(
        title='Bybit Pro Terminal',
        html=HTML_LAYOUT,
        js_api=bridge,
        width=1380,
        height=780,
        resizable=True
    )

    window.events.closed += lambda: bot.stop() if hasattr(bot, 'stop') else None
    webview.start(gui='qt')


if __name__ == '__main__':
    main()