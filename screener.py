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

# ===== MODE A FULL AUTO =====

# Morning high (UTC 02:00–02:30 = 09:00–09:30 WIB)
morning_data = data.between_time("02:00", "02:30")
if morning_data.empty:
    continue

morning_high = morning_data["High"].max()

# Volume spike
last_vol = data["Volume"].iloc[-1]
avg_vol_10 = data["Volume"].rolling(10).mean().iloc[-1]
vol_ratio = last_vol / avg_vol_10
vol_spike = vol_ratio > 1.5

# Breakout sesi pagi
breakout = latest["Close"] > morning_high

# Resistance 5 hari terakhir
resistance = data["High"].rolling(100).max().iloc[-1]
distance = (resistance - latest["Close"]) / latest["Close"]
room_ok = distance > 0.02  # minimal 2% ruang

# Final Decision Engine
if (
    breakout and
    vol_spike and
    latest["rsi"] > 55 and
    latest["Close"] > latest["ma20"] and
    room_ok
):
    candidates.append({
        "stock": stock,
        "rsi": round(latest["rsi"], 1),
        "vol_ratio": round(vol_ratio, 2),
        "room": round(distance * 100, 2)
    })

    except Exception as e:
        print(f"Error processing {stock}: {e}")

# Urutkan berdasarkan volume ratio terbesar
from datetime import datetime
import pytz

# waktu WIB
wib = pytz.timezone("Asia/Jakarta")
now = datetime.now(wib).strftime("%H:%M WIB")

# Urutkan berdasarkan volume ratio terbesar
candidates = sorted(candidates, key=lambda x: x["vol_ratio"], reverse=True)

if candidates:
    message = f"📈 IDX Screener ({now})\n\n"
    message += f"Total kandidat: {len(candidates)}\n\n"
    
    for i, c in enumerate(candidates[:5], start=1):
        message += f"{i}. {c['stock']} | RSI {c['rsi']} | Vol x{c['vol_ratio']} | Room {c['room']}%\n"
else:
    message = f"⏳ IDX Screener ({now})\nTidak ada kandidat saat ini."

requests.get(
    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
    params={"chat_id": CHAT_ID, "text": message},
)
