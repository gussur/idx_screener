import yfinance as yf
import pandas as pd
import ta
import requests
import os

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

stocks = pd.read_csv("stocks.csv", header=None)[0].tolist()

candidates = []

for stock in stocks:
    try:
        data = yf.download(stock, interval="15m", period="5d", progress=False)

        if len(data) < 50:
            continue

        data["rsi"] = ta.momentum.RSIIndicator(data["Close"], window=14).rsi()
        data["ma20"] = data["Close"].rolling(20).mean()

        avg_volume = data["Volume"].rolling(20).mean()
        volume_ratio = data["Volume"].iloc[-1] / avg_volume.iloc[-1]

        latest = data.iloc[-1]

        if (
            latest["Close"] > latest["ma20"] and
            latest["rsi"] > 55 and
            volume_ratio > 1.3
        ):
            candidates.append({
                "stock": stock,
                "rsi": round(latest["rsi"], 1),
                "vol_ratio": round(volume_ratio, 2)
            })

    except Exception as e:
        print(f"Error processing {stock}: {e}")

# Urutkan berdasarkan volume ratio terbesar
candidates = sorted(candidates, key=lambda x: x["vol_ratio"], reverse=True)

if candidates:
    message = "📈 Kandidat:\n\n"
    for i, c in enumerate(candidates[:5], start=1):
        message += f"{i}. {c['stock']} | RSI: {c['rsi']} | Vol x{c['vol_ratio']}\n"
else:
    message = "Tidak ada kandidat saat ini."

requests.get(
    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
    params={"chat_id": CHAT_ID, "text": message},
)
