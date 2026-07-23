import os
os.environ["QT_QPA_PLATFORM"] = "xcb"
import sys
import time
import json
import ctypes
import atexit
import threading
import traceback
import numpy as np
import pandas as pd
import tkinter as tk
from tkinter import ttk, messagebox

import talib
from talib import RSI, MACD, SMA, CCI
from talib import CDLDOJI, CDLENGULFING, CDLSHOOTINGSTAR

from sklearn.model_selection import train_test_split
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import accuracy_score

from pybit.unified_trading import WebSocket, HTTP
dll_path_1 = r"C:\Users\ulan\trading_bot_project\new_venv\Scripts\xgboost.dll"
dll_path_2 = os.path.join(os.path.dirname(__file__), "xgboost.dll")

if os.path.exists(dll_path_1):
    ctypes.CDLL(dll_path_1)
elif os.path.exists(dll_path_2):
    ctypes.CDLL(dll_path_2)
else:
    print(f"xgboost.dll не найден, могут быть ошибки!")

from xgboost import XGBClassifier
from catboost import CatBoostRegressor

df = pd.DataFrame()


def log_exception(exc_type, exc_value, exc_traceback):
    error_message = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    print(error_message)
    with open("error_log.txt", "w", encoding="utf-8") as f:
        f.write(error_message)

sys.excepthook = log_exception


class MiniAI:
    def __init__(self, trading_bot):
        self.errors = []
        self.trading_bot = trading_bot  # Для доступа к истории сделок
        self.analysis_log = []

    def remember_error(self, error):
        """Запоминает ошибку для будущего анализа."""
        self.errors.append(error)
        print(f"🔄 Ошибка запомнена: {error}")

    def analyze(self, ml_decision, symbol):
        """
        Анализирует решение ML с учетом текущего состояния.
        Только проверяет возможность выполнения сделки, не переопределяя решение ML.
        """
        analysis = {
            "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
            "symbol": symbol,
            "ml_decision": ml_decision,
            "final_decision": ml_decision,
            "reason": "Разрешено"
        }

        # Проверка на возможность продажи (если нет открытой позиции)
        if ml_decision == "Продажа" and symbol not in self.trading_bot.positions:
            analysis.update({
                "final_decision": None,
                "reason": "Нет открытой позиции для продажи"
            })
            print("Невозможно продать - позиция не открыта")
            return None

        # Проверка на лимит открытых сделок
        if (ml_decision == "Покупка" and 
            self.trading_bot.open_trades_count >= self.trading_bot.max_open_trades):
            analysis.update({
                "final_decision": None,
                "reason": "Достигнут лимит открытых сделок"
            })
            print("Лимит открытых сделок достигнут")
            return None

        self.analysis_log.append(analysis)
        print(f"Сделка разрешена ({ml_decision})")
        return ml_decision
    def select_best_timeframe(self, df_15m):
        """
        ИИ выбирает 15m или 1h в зависимости от волатильности рынка.
        """
        if df_15m.empty or len(df_15m) < 14:
            return "15" # По умолчанию 15m

        # Считаем ATR (средний истинный диапазон) за 14 свечей
        high_low = df_15m['high'] - df_15m['low']
        atr = high_low.rolling(14).mean().iloc[-1]
        current_price = df_15m['close'].iloc[-1]
        
        # Относительная волатильность в процентах
        volatility_pct = (atr / current_price) * 100

        # Если волатильность высокая (> 1.2%), переключаемся на 1h, чтобы не ловить шум
        if volatility_pct > 1.2:
            print(f"Высокая волатильность ({volatility_pct:.2f}%). Выбран ТФ: 1h (60m)")
            return "60"
        else:
            print(f"Спокойный рынок ({volatility_pct:.2f}%). Выбран ТФ: 15m")
            return "15"

class TradingBot:
    def __init__(self):
        self.api_key = "ВАШ_API_КЛЮЧ"
        self.api_secret = "ВАШ_API_SECRET"
        self.trade_amount = 10
        self.timeframe = 300
        self.total_profit = 0
        self.use_paper_trading = True
        self.running = True
        self.ws_lock = threading.Lock()
        self.save_lock = threading.Lock()
        self.ws = None
        self.ws_connected = False
        self.paper_trading_data = []
        self.history = []
        self.trade_data = []
        self.historical_data = {}
        self.positions = {}
        self.last_trade_time = {}
        self.error_memory = []
        self.data_file = "trade_data.json"
        self.symbols = [ "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "NEARUSDT", "APTUSDT", "SUIUSDT", "LINKUSDT", "DOTUSDT", "LTCUSDT", "DOGEUSDT", "OPUSDT", "ARBUSDT", "INJUSDT", "FETUSDT" ]
        self.load_data()
        self.model_accuracy = 0.0  # Точность модели

        # Инициализация MiniAI
        self.mini_ai = MiniAI(self)

        try:
            self.client = HTTP(api_key=self.api_key, api_secret=self.api_secret, testnet=False)
            print("✅ HTTP клиент Bybit инициализирован.")
        except Exception as e:
            print(f"❌ Ошибка инициализации API: {e}")
            self.client = None

        self.max_open_trades = 10  # Максимальное количество открытых сделок
        self.open_trades_count = 0  # Текущее количество открытых сделок

        print("✅ Инициализация торгового бота...")
        self.model = XGBClassifier(n_estimators=100, max_depth=3, learning_rate=0.1)
        self.scaler = StandardScaler()
        self.train_model()

        # Регистрируем функцию завершения
        atexit.register(self.on_exit)

        threading.Thread(target=self.paper_trading_loop, daemon=True).start()
        threading.Thread(target=self.connect_bybit, daemon=True).start()
        threading.Thread(target=self.keep_alive, daemon=True).start()

    def on_exit(self):
        """Функция, вызываемая при завершении программы."""
        print("🛑 Завершение работы бота...")
        self.running = False  # Останавливаем все циклы
        self.save_trades()    # Сохраняем данные о сделках
        if self.ws:
            try:
                self.ws.exit()  # Закрываем WebSocket соединение
            except Exception:
                pass
        print("✅ Данные сохранены, соединения закрыты.")

    def start(self):
        self.running = True
        self.positions.clear()
        self.open_trades_count = 0
        print("✅ Массивы очищены, бот запущен.")

    def save_trades(self):
        """Сохраняет сделки в файл paper_trading_data.json"""
        with self.save_lock:  # Блокируем доступ к файлу
            try:
                print("🔄 Попытка сохранения сделок в paper_trading_data.json...")
                with open("paper_trading_data.json", "w", encoding="utf-8") as f:
                    json.dump(self.paper_trading_data, f, indent=4, ensure_ascii=False)
                print("✅ Данные о сделках успешно сохранены!")
            except Exception as e:
                print(f"❌ Ошибка сохранения сделок: {e}")
            finally:
                print("Завершение операции сохранения.")

    def toggle_paper_trading(self):
        """Переключает режим торговли между бумажной и реальной."""
        self.use_paper_trading = not self.use_paper_trading
        mode = "Бумажная торговля" if self.use_paper_trading else "Реальная торговля"
        print(f"✅ Режим торговли переключен на: {mode}")

    def load_data(self):
        """Загружает историю сделок из файлов."""
        print("📂 Загрузка данных...")
        try:
            if os.path.exists("paper_trading_data.json"):
                with open("paper_trading_data.json", "r", encoding="utf-8") as f:
                    self.paper_trading_data = json.load(f)
            else:
                self.paper_trading_data = []

            print(f"✅ Загружено {len(self.paper_trading_data)} сделок.")
        except json.JSONDecodeError:
            print("❌ Ошибка: файлы повреждены! История сброшена.")
            self.paper_trading_data = []

    def get_latest_price(self, symbol):
        """Получает последнюю цену для заданной валютной пары."""
        try:
            response = self.client.get_tickers(category="spot", symbol=symbol)
            if response and "result" in response and "list" in response["result"]:
                price = float(response["result"]["list"][0]["lastPrice"])
                return price
            print(f"⚠️ API Bybit вернул пустой ответ для {symbol}.")
            return None
        except Exception as e:
            print(f"❌ Ошибка получения цены для {symbol}: {e}")
            return None

    def get_model_accuracy(self):
        """Возвращает точность модели."""
        return self.model_accuracy


    def calculate_success_rate(self):
        """Рассчитывает процент успешных сделок."""
        closed_trades = [trade for trade in self.paper_trading_data if trade.get("status") in ["Закрыто с прибылью", "Закрыто с убытком"]]
        if not closed_trades:
            return 0

        successful_trades = [trade for trade in closed_trades if trade.get("profit", 0) > 0]
        success_rate = (len(successful_trades) / len(closed_trades)) * 100
        return success_rate

    def get_total_profit(self):
        """Возвращает общую прибыль по завершённым сделкам."""
        closed_trades = [trade for trade in self.paper_trading_data if trade.get("status") in ["Закрыто с прибылью", "Закрыто с убытком"]]
        total_profit = sum(trade.get("profit", 0) for trade in closed_trades)
        return total_profit

    def save_historical_data(self):
        """Сохраняет исторические данные в файл."""
        try:
            with open("historical_data.json", "w", encoding="utf-8") as file:
                json.dump(self.historical_data, file, indent=4, ensure_ascii=False)
            print("✅ Исторические данные успешно сохранены!")
        except Exception as e:
            print(f"❌ Ошибка при сохранении исторических данных: {e}")

    def log_error(self, message):
        """Логирует ошибку в файл."""
        with open("error_log.txt", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - {message}\n")

    def keep_alive(self):
        """Поддерживает соединение с WebSocket, отправляя пинг."""
        while self.running:
            try:
                with self.ws_lock:
                    if self.is_ws_connected():
                        try:
                            self.ws._send_custom_ping()
                            print("📡 WebSocket: ping отправлен")
                        except Exception as e:
                            print(f"⚠️ Ошибка при отправке ping: {e}")
                            self.ws_connected = False
                    else:
                        print("⚠️ WebSocket соединение закрыто, переподключаемся...")
                        self.connect_bybit()
            except Exception as e:
                print(f"❌ Критическая ошибка в keep_alive: {e}")
                self.ws_connected = False
            time.sleep(15)

    def is_ws_connected(self):
        """Проверяет, активно ли WebSocket соединение."""
        return (
            self.ws
            and hasattr(self.ws, "ws")
            and self.ws.ws
            and hasattr(self.ws.ws, "sock")
            and self.ws.ws.sock
            and self.ws.ws.sock.connected
        )

    def load_historical_data(self):
        """Загружает историю сделок из файла trade_data.json."""
        try:
            with open("trade_data.json", "r", encoding="utf-8") as file:
                self.paper_trading_data = json.load(file)

            print(f"📂 DEBUG: Загружено {len(self.paper_trading_data)} сделок.")
        except FileNotFoundError:
            print("⚠️ Файл trade_data.json не найден, создаём пустую историю.")
            self.paper_trading_data = []

    def ensure_websocket(self):
        """Проверяет и при необходимости подключает WebSocket."""
        with self.ws_lock:
            if self.is_ws_connected():
                print("📡 WebSocket уже подключен, ничего не делаем.")

    def connect_bybit(self):
        """Подключается к Bybit WebSocket."""
        with self.ws_lock:
            try:
                if not hasattr(self, "symbols"):
                    print("❌ Ошибка: атрибут symbols не найден!")
                    return

                if self.is_ws_connected():
                    print("✅ WebSocket уже работает, повторное подключение не требуется.")
                    return

                print("🔌 Подключаемся к Bybit WebSocket...")
                self.ws = WebSocket(
                    testnet=False,
                    channel_type="spot",
                    ping_interval=30,
                    ping_timeout=10
                )

                for symbol in self.symbols:
                    print(f"📡 Подписка на {symbol}...")
                    self.ws.ticker_stream(symbol=symbol, callback=self.on_ticker)

                print("✅ WebSocket успешно подключен.")
                self.ws_connected = True
            except Exception as e:
                print(f"❌ Ошибка WebSocket: {e}")
                self.ws = None
                self.ws_connected = False

    def on_ticker(self, message):
        """Обработка данных от WebSocket."""
        try:
            if isinstance(message, dict):
                data = message
            else:
                data = json.loads(message)

            if "data" in data:
                symbol = data["data"].get("symbol")
                last_price = data["data"].get("lastPrice")
                if symbol and last_price:
                    print(f"📡 Получены данные для {symbol}: последняя цена = {last_price}")
                else:
                    print(f"⚠️ Неожиданный формат данных: {data}")
            else:
                print(f"⚠️ Неожиданный формат данных: {data}")
        except Exception as e:
            print(f"❌ Ошибка при обработке сообщения: {e}")
            print("❌ WebSocket соединение закрыто, пытаемся переподключиться...")
            self.connect_bybit()

    def stop(self):
        """Останавливает бота и закрывает WebSocket."""
        print("🛑 Остановка бота...")
        self.running = False

        if self.ws:
            try:
                self.ws.exit()
            except Exception:
                pass
            self.ws_connected = False

        time.sleep(1)

    def add_history_features(self, df, symbol):
        """Добавляет признаки из истории сделок в DataFrame."""
        history = [t for t in self.paper_trading_data if t.get("symbol") == symbol]
    
        df["last_trade_profit"] = 0.0
        df["success_rate_7d"] = 0.5
        df["avg_profit_5trades"] = 0.0
    
        if not history:
            return df

        for trade in history:
            try:
                if isinstance(trade.get("timestamp"), str):
                    trade["_unix_time"] = time.mktime(time.strptime(
                        trade["timestamp"], '%Y-%m-%d %H:%M:%S'))
                else:
                    trade["_unix_time"] = trade.get("timestamp", time.time())
            except Exception as e:
                print(f"⚠️ Ошибка конвертации времени: {e}")
                trade["_unix_time"] = time.time()

        seven_days_ago = time.time() - 604800

        df["last_trade_profit"] = history[-1].get("profit", 0.0)

        last_7_days = [t for t in history[-7:] if t.get("_unix_time", 0) >= seven_days_ago]    
        if last_7_days:
            success_count = sum(1 for t in last_7_days if t.get("profit", 0) > 0)
            df["success_rate_7d"] = success_count / len(last_7_days)

        last_5_trades = history[-5:]
        if last_5_trades:
            df["avg_profit_5trades"] = float(np.mean([t.get("profit", 0.0) for t in last_5_trades]))

        return df

    def add_indicators(self, df):
        """Добавляет индикаторы в DataFrame."""
        if df.empty or "close" not in df.columns:
            print("❌ Отсутствуют необходимые данные для индикаторов.")
            return df

        df['rsi'] = talib.RSI(df['close'], timeperiod=14)
        df['macd'], df['macd_signal'], df['macd_hist'] = talib.MACD(df['close'], fastperiod=12, slowperiod=26, signalperiod=9)
        df['sma_50'] = talib.SMA(df['close'], timeperiod=50)
        df['sma_200'] = talib.SMA(df['close'], timeperiod=200)

        return df

    def train_model(self):
        try:
            print("🔄 Обучение модели с историческими признаками...")
            all_data = []
        
            for symbol in self.symbols:
                df_hist = self.get_historical_data(symbol)
                if df_hist.empty:
                    continue
                
                df_hist = self.add_indicators(df_hist)
                df_hist = self.add_history_features(df_hist, symbol)
                all_data.append(df_hist)

            if not all_data:
                print("❌ Нет данных для обучения")
                return

            combined_df = pd.concat(all_data)
            FUTURE_STEPS = 5
            PROFIT_THRESHOLD = 1.003 # +0.3%

            combined_df["target"] = (
                combined_df["close"].shift(-FUTURE_STEPS) > (combined_df["close"] * PROFIT_THRESHOLD)
            ).astype(int)
            combined_df.dropna(inplace=True)

            features = [
                "open", "high", "low", "close", "volume", "turnover",
                "rsi", "macd", "macd_signal", "macd_hist", "sma_50", "sma_200",
                "last_trade_profit", "success_rate_7d", "avg_profit_5trades"
            ]

            X = combined_df[features]
            y = combined_df["target"]

            X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.2, random_state=42)
        
            self.scaler.fit(X_train)
            X_train = self.scaler.transform(X_train)
            X_test = self.scaler.transform(X_test)

            self.model.fit(X_train, y_train)
            predictions = self.model.predict(X_test)
            accuracy = accuracy_score(y_test, predictions)
            self.model_accuracy = accuracy

            print("\n📊 Важность признаков:")
            for name, importance in zip(features, self.model.feature_importances_):
                print(f"  {name}: {importance:.4f}")

            print(f"✅ Модель обучена. Точность: {accuracy:.2f}")

        except Exception as e:
            print(f"❌ Ошибка обучения модели: {e}")
            traceback.print_exc()

    def predict_trade(self, df):
        """Предсказывает рост или падение цены."""
        if not hasattr(self.scaler, "mean_"):
            print("❌ StandardScaler не был обучен. Сначала вызовите train_model.")
            return None

        features = ["open", "high", "low", "close", "volume", "turnover"]
        if not all(col in df.columns for col in features):
            print(f"❌ DataFrame не содержит всех необходимых колонок: {features}")
            return None

        X = df[features]
        X = pd.DataFrame(X, columns=features)
        X_scaled = self.scaler.transform(X)

        return self.model.predict(X_scaled)[-1]

    def calculate_candle_patterns(self, df):
        """Рассчитывает все 50 свечных паттернов и добавляет их в DataFrame."""
        if df.empty:
            print("❌ DataFrame пустой, невозможно рассчитать паттерны.")
            return df

        patterns = {
            "CDL2CROWS": talib.CDL2CROWS,
            "CDL3BLACKCROWS": talib.CDL3BLACKCROWS,
            "CDL3INSIDE": talib.CDL3INSIDE,
            "CDL3LINESTRIKE": talib.CDL3LINESTRIKE,
            "CDL3OUTSIDE": talib.CDL3OUTSIDE,
            "CDL3STARSINSOUTH": talib.CDL3STARSINSOUTH,
            "CDL3WHITESOLDIERS": talib.CDL3WHITESOLDIERS,
            "CDLABANDONEDBABY": talib.CDLABANDONEDBABY,
            "CDLADVANCEBLOCK": talib.CDLADVANCEBLOCK,
            "CDLBELTHOLD": talib.CDLBELTHOLD,
            "CDLBREAKAWAY": talib.CDLBREAKAWAY,
            "CDLCLOSINGMARUBOZU": talib.CDLCLOSINGMARUBOZU,
            "CDLCONCEALBABYSWALL": talib.CDLCONCEALBABYSWALL,
            "CDLCOUNTERATTACK": talib.CDLCOUNTERATTACK,
            "CDLDARKCLOUDCOVER": talib.CDLDARKCLOUDCOVER,
            "CDLDOJI": talib.CDLDOJI,
            "CDLDOJISTAR": talib.CDLDOJISTAR,
            "CDLDRAGONFLYDOJI": talib.CDLDRAGONFLYDOJI,
            "CDLENGULFING": talib.CDLENGULFING,
            "CDLEVENINGDOJISTAR": talib.CDLEVENINGDOJISTAR,
            "CDLEVENINGSTAR": talib.CDLEVENINGSTAR,
            "CDLGAPSIDESIDEWHITE": talib.CDLGAPSIDESIDEWHITE,
            "CDLGRAVESTONEDOJI": talib.CDLGRAVESTONEDOJI,
            "CDLHAMMER": talib.CDLHAMMER,
            "CDLHANGINGMAN": talib.CDLHANGINGMAN,
            "CDLHARAMI": talib.CDLHARAMI,
            "CDLHARAMICROSS": talib.CDLHARAMICROSS,
            "CDLHIGHWAVE": talib.CDLHIGHWAVE,
            "CDLHIKKAKE": talib.CDLHIKKAKE,
            "CDLHIKKAKEMOD": talib.CDLHIKKAKEMOD,
            "CDLHOMINGPIGEON": talib.CDLHOMINGPIGEON,
            "CDLIDENTICAL3CROWS": talib.CDLIDENTICAL3CROWS,
            "CDLINNECK": talib.CDLINNECK,
            "CDLINVERTEDHAMMER": talib.CDLINVERTEDHAMMER,
            "CDLKICKING": talib.CDLKICKING,
            "CDLKICKINGBYLENGTH": talib.CDLKICKINGBYLENGTH,
            "CDLLADDERBOTTOM": talib.CDLLADDERBOTTOM,
            "CDLLONGLEGGEDDOJI": talib.CDLLONGLEGGEDDOJI,
            "CDLLONGLINE": talib.CDLLONGLINE,
            "CDLMARUBOZU": talib.CDLMARUBOZU,
            "CDLMATCHINGLOW": talib.CDLMATCHINGLOW,
            "CDLMATHOLD": talib.CDLMATHOLD,
            "CDLMORNINGDOJISTAR": talib.CDLMORNINGDOJISTAR,
            "CDLMORNINGSTAR": talib.CDLMORNINGSTAR,
            "CDLONNECK": talib.CDLONNECK,
            "CDLPIERCING": talib.CDLPIERCING,
            "CDLRICKSHAWMAN": talib.CDLRICKSHAWMAN,
            "CDLRISEFALL3METHODS": talib.CDLRISEFALL3METHODS,
            "CDLSEPARATINGLINES": talib.CDLSEPARATINGLINES,
            "CDLSHOOTINGSTAR": talib.CDLSHOOTINGSTAR,
            "CDLSHORTLINE": talib.CDLSHORTLINE,
            "CDLSPINNINGTOP": talib.CDLSPINNINGTOP,
            "CDLSTALLEDPATTERN": talib.CDLSTALLEDPATTERN,
            "CDLSTICKSANDWICH": talib.CDLSTICKSANDWICH,
            "CDLTAKURI": talib.CDLTAKURI,
            "CDLTASUKIGAP": talib.CDLTASUKIGAP,
            "CDLTHRUSTING": talib.CDLTHRUSTING,
            "CDLTRISTAR": talib.CDLTRISTAR,
            "CDLUNIQUE3RIVER": talib.CDLUNIQUE3RIVER,
            "CDLUPSIDEGAP2CROWS": talib.CDLUPSIDEGAP2CROWS,
            "CDLXSIDEGAP3METHODS": talib.CDLXSIDEGAP3METHODS,
        }

        for name, func in patterns.items():
            df[name] = func(df["open"], df["high"], df["low"], df["close"])

        print("✅ Все свечные паттерны рассчитаны!")
        return df

    def execute_trade(self, action, price, symbol, tp_pct=0.015, sl_pct=0.008):
        """
        tp_pct = 0.015 (+1.5% Take Profit)
        sl_pct = 0.008 (-0.8% Stop Loss)
        """
        print(f"🚀 Выполнение сделки: {action} {symbol} по цене {price}")
        now = time.time()
    
        if action == "Покупка":
            if len(self.positions) >= self.max_open_trades:
                print(f"⚠️ Лимит сделок ({self.max_open_trades}) достигнут")
                return

            if symbol in self.positions:
                print(f"⚠️ Позиция по {symbol} уже открыта")
                return

            entry_price = float(price)
            # Высчитываем конкретные уровни TP и SL
            take_profit_price = entry_price * (1 + tp_pct)
            stop_loss_price = entry_price * (1 - sl_pct)

            self.positions[symbol] = {
                "side": "buy",
                "price": entry_price,
                "take_profit": take_profit_price,
                "stop_loss": stop_loss_price,
                "order_amount": float(self.trade_amount),
                "open_time": now,
                "open_time_str": time.strftime('%Y-%m-%d %H:%M:%S')
            }
            self.open_trades_count = len(self.positions)
            print(f"🎯 Установлены ордера: TP = {take_profit_price:.2f} | SL = {stop_loss_price:.2f}")

    def check_and_close_positions(self, symbol, current_price):
        """Проверяет достижение TP или SL для открытой позиции."""
        if symbol not in self.positions:
            return

        pos = self.positions[symbol]
        entry_price = pos["price"]
        tp = pos["take_profit"]
        sl = pos["stop_loss"]

        reason = None
        if current_price >= tp:
            reason = "Take Profit (+1.5%)"
        elif current_price <= sl:
            reason = "Stop Loss (-0.8%)"

        if reason:
            quantity = float(pos["order_amount"]) / entry_price
            profit = (current_price - entry_price) * quantity
            now = time.time()

            trade_data = {
                "symbol": str(symbol),
                "action": "Продажа",
                "price": float(current_price),
                "quantity": float(quantity),
                "profit": float(profit),
                "status": f"Закрыто по {reason}",
                "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
                "timestamp_unix": float(now)
            }

            self.paper_trading_data.append(trade_data)
            del self.positions[symbol]
            self.open_trades_count = len(self.positions)
            self.save_trades()

            print(f"🥊 ПОЗИЦИЯ ЗАКРЫТА: {symbol} | Причина: {reason} | Профит: {profit:.2f} USDT")

    def get_open_trades_status(self):
        """Возвращает строку с информацией об открытых сделках"""
        return f"Открытые сделки: {self.open_trades_count}/{self.max_open_trades}"

    def should_trade(self, df, symbol):
        try:
            print(f"\n🔍 Анализ торгового сигнала для {symbol}...")
    
            if df.empty:
                print("❌ Нет данных для анализа")
                return None

            df = self.add_indicators(df)
            df = self.calculate_candle_patterns(df)
            df = self.add_history_features(df, symbol)

            last_row = df.iloc[-1]
    
            features = [
                "open", "high", "low", "close", "volume", "turnover",
                "rsi", "macd", "macd_signal", "macd_hist", "sma_50", "sma_200",
                "last_trade_profit", "success_rate_7d", "avg_profit_5trades"
            ]

            missing_features = [f for f in features if f not in df.columns]
            if missing_features:
                print(f"❌ Отсутствуют признаки: {missing_features}")
                return None

            latest_data = last_row[features].values.reshape(1, -1)
            latest_data = pd.DataFrame(latest_data, columns=features)
            latest_data = self.scaler.transform(latest_data)

            prediction = self.model.predict(latest_data)[0]
            proba = self.model.predict_proba(latest_data)[0][prediction]
    
            ml_decision = "Покупка" if prediction == 1 else "Продажа"
            print(f"🤖 ML решение: {ml_decision} (вероятность: {proba:.2%})")

            if proba < 0.55:
                print(f"⚠️ Вероятность {proba:.2%} < 55%, сделка отменена")
                return None

            return self.mini_ai.analyze(ml_decision, symbol)

        except Exception as e:
            print(f"❌ Ошибка в should_trade: {e}")
            traceback.print_exc()
            return None

    def get_historical_data(self, symbol, interval="1", limit=500):
        """Получает исторические данные для символа от биржи."""
        try:
            print(f"📡 Загрузка исторических данных для {symbol}...")
            response = self.client.get_kline(
                category="spot",
                symbol=symbol,
                interval=interval,
                limit=limit
            )
        
            if not response or "result" not in response or not response["result"]["list"]:
                print(f"⚠️ Нет данных для {symbol}")
                return pd.DataFrame()

            df = pd.DataFrame(
                response["result"]["list"],
                columns=["timestamp", "open", "high", "low", "close", "volume", "turnover"]
            )
        
            df = df.apply(lambda x: pd.to_numeric(x, errors='coerce'))
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
            df.set_index("timestamp", inplace=True)
        
            print(f"✅ Загружено {len(df)} свечей для {symbol}")
            return df

        except Exception as e:
            print(f"❌ Ошибка загрузки данных для {symbol}: {e}")
            return pd.DataFrame()

    def paper_trading_loop(self, interval=10):
        """Основной торговый цикл."""
        while self.running:
            try:
                print("\n" + "="*50)
                print(f"🔁 Проверка рынка. {self.get_open_trades_status()}")
                print("="*50)

                for symbol in self.symbols:
                    latest_price = self.get_latest_price(symbol)
                    if latest_price is None:
                        continue

                    # 1. Первым делом проверяем существующую позицию на TP/SL
                    if symbol in self.positions:
                        self.check_and_close_positions(symbol, latest_price)
                        continue # Если позиция уже открыта, новую по этому тикеру не ищем

                    # 2. Загружаем базовые 15m свечи для оценки рынка
                    df_15m = self.get_historical_data(symbol, interval="15", limit=200)
                    if df_15m.empty:
                        continue

                    # 3. MiniAI выбирает наилучший таймфрейм (15m или 60m)
                    selected_tf = self.mini_ai.select_best_timeframe(df_15m)

                    # 4. Берем датафрейм для выбранного ИИ таймфрейма
                    if selected_tf == "60":
                        df_analysis = self.get_historical_data(symbol, interval="60", limit=200)
                    else:
                        df_analysis = df_15m

                    # 5. Анализируем сигнал XGBoost
                    action = self.should_trade(df_analysis, symbol)

                    if action == "Покупка":
                        self.execute_trade("Покупка", latest_price, symbol)

                time.sleep(interval)
            except Exception as e:
                print(f"❌ Ошибка в paper_trading_loop: {e}")
                time.sleep(5)


class TradingBotApp:
    def __init__(self, root, trading_bot):
        self.root = root
        self.trading_bot = trading_bot
        self.trading_bot.app = self  # Теперь бот может обновлять UI
        self.root.title('Торговый Бот')
        self.root.geometry('800x600')

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.account = "Гость"
        self.error_label = tk.Label(root, text="", fg="red", font=("Arial", 10))
        self.error_label.pack()
        self.trade_data = trading_bot.trade_data

        self.init_ui()

    def on_close(self):
        """Обработчик события закрытия окна."""
        print("🛑 Закрытие приложения...")
        self.trading_bot.on_exit()
        self.root.destroy()

    def init_ui(self):
        """Создание UI."""
        self.main_frame = tk.Frame(self.root)
        self.main_frame.pack(fill=tk.BOTH, expand=True)

        self.theme_button = tk.Button(self.main_frame, text='Сменить тему', command=self.switch_theme)
        self.theme_button.pack(pady=10)

        self.toggle_button = tk.Button(self.main_frame, text="Переключить счёт", command=self.trading_bot.toggle_paper_trading)
        self.toggle_button.pack(pady=10)

        self.account_label = tk.Label(self.main_frame, text=f'Аккаунт: {self.account}')
        self.account_label.pack(pady=5)

        self.trade_amount_label = tk.Label(self.main_frame, text='Сумма сделки (USD):')
        self.trade_amount_label.pack(pady=5)
        self.trade_amount_entry = tk.Entry(self.main_frame)
        self.trade_amount_entry.insert(0, str(self.trading_bot.trade_amount))
        self.trade_amount_entry.pack(pady=5)

        self.set_trade_amount_button = tk.Button(self.main_frame, text='Установить сумму сделки', command=self.set_trade_amount)
        self.set_trade_amount_button.pack(pady=5)

        self.timeframe_label = tk.Label(self.main_frame, text='Таймфрейм (сек):')
        self.timeframe_label.pack(pady=5)
        self.timeframe_entry = tk.Entry(self.main_frame)
        self.timeframe_entry.insert(0, str(self.trading_bot.timeframe))
        self.timeframe_entry.pack(pady=5)

        self.set_timeframe_button = tk.Button(self.main_frame, text='Установить таймфрейм', command=self.set_timeframe)
        self.set_timeframe_button.pack(pady=5)

        self.trade_history_label = tk.Label(self.main_frame, text="История сделок:")
        self.trade_history_label.pack(pady=5)

        self.history_button = tk.Button(self.main_frame, text='Показать историю сделок', command=self.show_trade_history)
        self.history_button.pack(pady=10)

        self.order_button = tk.Button(self.main_frame, text='Запустить автоторговлю', command=self.start_trading)
        self.order_button.pack(pady=10)

        self.success_rate_label = tk.Label(self.main_frame, text='Процент успешных сделок: 0%')
        self.success_rate_label.pack(pady=5)

        self.accuracy_label = tk.Label(self.main_frame, text=f"Точность модели: {self.trading_bot.get_model_accuracy():.2f}")
        self.accuracy_label.pack(pady=5)

        self.total_profit_label = tk.Label(self.main_frame, text='Общая прибыль: 0.00 USDT')
        self.total_profit_label.pack(pady=5)

        self.update_ui()

    def update_statistics(self):
        """Обновляет статистику в интерфейсе."""
        if hasattr(self, 'total_profit_label') and hasattr(self, 'success_rate_label'):
            total_profit = self.trading_bot.get_total_profit()
            success_rate = self.trading_bot.calculate_success_rate()

            self.total_profit_label.config(text=f"Общая прибыль: {total_profit:.2f} USDT")
            self.success_rate_label.config(text=f"Процент успешных сделок: {success_rate:.2f}%")
        else:
            print("⚠️ Метки для отображения статистики не найдены! Убедитесь, что они созданы в init_ui.")

    def update_ui(self):
        """Обновляет интерфейс."""
        if hasattr(self, 'total_profit_label') and hasattr(self, 'success_rate_label'):
            total_profit = self.trading_bot.get_total_profit()
            success_rate = self.trading_bot.calculate_success_rate()
            model_accuracy = self.trading_bot.get_model_accuracy()

            self.total_profit_label.config(text=f"Общая прибыль: {total_profit:.2f} USDT")
            self.success_rate_label.config(text=f"Процент успешных сделок: {success_rate:.2f}%")
            self.accuracy_label.config(text=f"Точность модели: {model_accuracy:.2f}")
        else:
            print("⚠️ Метки для отображения статистики не найдены! Убедитесь, что они созданы в init_ui.")

    def switch_theme(self):
        """Переключает тему интерфейса."""
        current_bg = self.root.cget("bg")
        if current_bg == "white" or current_bg == "#ffffff":
            new_bg, new_fg = "black", "white"
        else:
            new_bg, new_fg = "white", "black"

        self.root.config(bg=new_bg)
        self.main_frame.config(bg=new_bg)
        for widget in self.main_frame.winfo_children():
            try:
                widget.config(bg=new_bg, fg=new_fg)
            except Exception:
                pass

    def set_trade_amount(self):
        """Устанавливает сумму сделки."""
        try:
            trade_amount = float(self.trade_amount_entry.get())
            self.trading_bot.trade_amount = trade_amount
            print(f"✅ Сумма сделки установлена: {trade_amount} USD")
        except ValueError:
            print("❌ Ошибка: введите корректное число для суммы сделки.")

    def set_timeframe(self):
        """Устанавливает таймфрейм."""
        try:
            timeframe = int(self.timeframe_entry.get())
            self.trading_bot.timeframe = timeframe
            print(f"✅ Таймфрейм установлен: {timeframe} сек")
        except ValueError:
            print("❌ Ошибка: введите корректное число для таймфрейма.")

    def start_trading(self):
        """Запускает торгового бота."""
        self.trading_bot.start()

    def show_trade_history(self):
        """Отображает историю сделок."""
        if not self.trading_bot.paper_trading_data:
            print("⚠️ История сделок пуста.")
            return

        history_window = tk.Toplevel(self.root)
        history_window.title("История сделок")
        history_window.geometry("600x400")

        scrollbar = tk.Scrollbar(history_window)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        history_text = tk.Text(history_window, wrap=tk.NONE, yscrollcommand=scrollbar.set)
        history_text.pack(fill=tk.BOTH, expand=True)

        if self.trading_bot.paper_trading_data:
            for trade in self.trading_bot.paper_trading_data:
                history_text.insert(tk.END, f"{trade}\n")
        else:
            history_text.insert(tk.END, "История сделок пуста.")

        scrollbar.config(command=history_text.yview)


if __name__ == "__main__":
    bot = TradingBot()
    root = tk.Tk()
    app = TradingBotApp(root, bot)
    root.mainloop()