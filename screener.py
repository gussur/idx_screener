import yfinance as yf
import pandas as pd
import ta
import requests
import os

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

stocks = ["BBCA.JK", "BMRI.JK", "TLKM.JK", "ASII.JK"]

candidates = []

for stock in stocks:
    try:
        data = yf.download(stock, interval="15m", period="5d", progress=False)

        if len(data) < 50:
            continue

        data["rsi"] = ta.momentum.RSIIndicator(data["Close"], window=14).rsi()
        data["ma20"] = data["Close"].rolling(20).mean()

        latest = data.iloc[-1]

        if latest["Close"] > latest["ma20"] and latest["rsi"] > 60:
            candidates.append(stock)

    except Exception as e:
        print(f"Error processing {stock}: {e}")

if candidates:
    message = "📈 Kandidat:\n" + "\n".join(candidates)
else:
    message = "Tidak ada kandidat saat ini."

requests.get(
    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
    params={"chat_id": CHAT_ID, "text": message},
)
