import ccxt.async_support as ccxt
import asyncio
import json
import logging
import talib
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import ParseMode
from aiogram.utils import executor

# Logging konfigurieren
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger("TradingBot")

# Config laden
def load_config():
    with open("config.json") as f:
        return json.load(f)

CONFIG = load_config()

# Telegram-Bot konfigurieren
TELEGRAM_BOT_TOKEN = CONFIG['telegram']['bot_token']
TELEGRAM_CHAT_ID = CONFIG['telegram']['chat_id']
telegram_bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher(telegram_bot)
dp.middleware.setup(LoggingMiddleware())

# Handelsstrategie-Parameter
MIN_PRICE_CHANGE = CONFIG['strategy']['min_price_change']
MIN_VOLUME = CONFIG['strategy']['min_volume']
TAKE_PROFIT = CONFIG['strategy']['take_profit']
STOP_LOSS = CONFIG['strategy']['stop_loss']
TRAILING_STOP = CONFIG['strategy'].get('trailing_stop', 0.015)
TRADE_SIZE = CONFIG['strategy']['trade_size']
MAX_PAIRS = CONFIG['strategy']['max_pairs']
MIN_TRADE_AMOUNT = CONFIG['strategy']['min_trade_amount']
CHECK_INTERVAL = CONFIG['general']['check_interval']

# API-Schlüssel und Exchanges
API_KEY = CONFIG['api']['key']
API_SECRET = CONFIG['api']['secret']
SUPPORTED_EXCHANGES = ["binance", "kraken"]

async def initialize_exchanges():
    exchanges = {}
    for exchange_name in SUPPORTED_EXCHANGES:
        exchange_class = getattr(ccxt, exchange_name)
        exchanges[exchange_name] = exchange_class({
            "apiKey": API_KEY,
            "secret": API_SECRET,
            "enableRateLimit": True
        })
    return exchanges

# JSON-Speicherung
STATE_FILE = "trades_state.json"

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=4)

state = load_state()

async def send_telegram_message(message):
    try:
        await telegram_bot.send_message(TELEGRAM_CHAT_ID, message, parse_mode=ParseMode.HTML)
    except Exception as e:
        logger.error(f"Fehler beim Senden der Telegram-Nachricht: {e}")

async def fetch_market_data(exchange):
    try:
        markets = await exchange.load_markets()
        tradable_pairs = []
        for symbol, data in markets.items():
            if data["active"] and "/" in symbol:
                tradable_pairs.append(symbol)
        return tradable_pairs
    except Exception as e:
        logger.error(f"Fehler beim Abrufen von Märkten: {e}")
        return []

async def analyze_market(exchange, symbol):
    try:
        ohlcv = await exchange.fetch_ohlcv(symbol, timeframe="1m", limit=50)
        closes = [x[4] for x in ohlcv]
        volumes = [x[5] for x in ohlcv]

        if len(closes) < 2:
            return None

        price_change = ((closes[-1] - closes[-2]) / closes[-2]) * 100
        avg_volume = sum(volumes) / len(volumes)

        if price_change >= MIN_PRICE_CHANGE and avg_volume >= MIN_VOLUME:
            macd, signal, hist = talib.MACD(np.array(closes), fastperiod=12, slowperiod=26, signalperiod=9)
            if macd[-1] > signal[-1]:  # Kaufsignal
                return {
                    "symbol": symbol,
                    "price_change": price_change,
                    "volume": avg_volume
                }
        return None
    except Exception as e:
        logger.error(f"Fehler beim Analysieren von {symbol}: {e}")
        return None

async def trade(exchange, trade_info):
    symbol = trade_info["symbol"]
    try:
        balance = await exchange.fetch_balance()
        quote_currency = symbol.split("/")[1]
        available_funds = balance["free"].get(quote_currency, 0)

        if available_funds < MIN_TRADE_AMOUNT:
            logger.info(f"Nicht genug Kapital für {symbol}.")
            return

        amount = TRADE_SIZE / trade_info["price"]
        order = await exchange.create_market_buy_order(symbol, amount)

        state[symbol] = {
            "amount": amount,
            "entry_price": trade_info["price"],
            "take_profit": trade_info["price"] * (1 + TAKE_PROFIT),
            "stop_loss": trade_info["price"] * (1 - STOP_LOSS)
        }
        save_state(state)

        await send_telegram_message(f"<b>Kauf ausgeführt</b>\nSymbol: {symbol}\nPreis: {trade_info['price']}\nMenge: {amount}")
    except Exception as e:
        logger.error(f"Fehler beim Handeln von {symbol}: {e}")

async def main():
    exchanges = await initialize_exchanges()
    while True:
        for exchange_name, exchange in exchanges.items():
            tradable_pairs = await fetch_market_data(exchange)
            tasks = [analyze_market(exchange, symbol) for symbol in tradable_pairs[:MAX_PAIRS]]
            results = await asyncio.gather(*tasks)
            for trade_info in filter(None, results):
                await trade(exchange, trade_info)

        await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Bot gestoppt.")
    except Exception as e:
        logger.error(f"Unbekannter Fehler: {e}")
