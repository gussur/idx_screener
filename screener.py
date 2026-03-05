import yfinance as yf
import pandas as pd
import ta
import requests
import os
from datetime import datetime
import pytz

TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ─────────────────────────────────────────────
# FETCH ALL IDX STOCKS (~900 saham)
# ─────────────────────────────────────────────
def get_all_idx_stocks():
    """
    Ambil semua saham IDX dari API resmi idx.co.id.
    Fallback ke stocks.csv jika gagal.
    """
    try:
        url = "https://www.idx.co.id/primary/StockData/GetSecuritiesStock"
        params = {"start": 0, "length": 9999, "s": "Kode", "d": "asc"}
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        resp = requests.get(url, params=params, headers=headers, timeout=20)
        data = resp.json()
        stocks = [item["Kode"] + ".JK" for item in data["data"] if item.get("Kode")]
        print(f"[IDX API] Berhasil fetch {len(stocks)} saham.")
        return stocks
    except Exception as e:
        print(f"[IDX API] Gagal: {e} — Fallback ke stocks.csv")
        try:
            return pd.read_csv("stocks.csv", header=None)[0].tolist()
        except Exception:
            return []

stocks = get_all_idx_stocks()
print(f"Total saham yang akan discreen: {len(stocks)}")

# ─────────────────────────────────────────────
# INTRADAY FILTER SETTINGS
# ─────────────────────────────────────────────
# Tuning parameter — ubah sesuai kondisi pasar
MIN_PRICE          = 100       # Hindari saham < Rp100 (fraksi lebar, spread besar)
MIN_AVG_VALUE_IDR  = 2_000_000_000  # Minimum nilai transaksi rata-rata: Rp 2 miliar/hari
                                    # (proxy likuiditas intraday)
VOL_SPIKE_RATIO    = 1.5       # Volume candle terakhir vs rata-rata 10 candle
RSI_MIN_MOMENTUM   = 52        # Mode breakout/momentum
RSI_MAX_REVERSAL   = 38        # Mode reversal (oversold bounce)
RSI_REVERSAL_NOW   = 42        # RSI sudah naik ke atas level ini saat reversal
ROOM_TO_RESISTANCE = 0.015     # Minimal 1.5% ruang ke resistance
MA_WINDOW          = 20
RSI_WINDOW         = 14
MAX_CANDIDATES     = 8         # Maksimal kandidat yang dikirim ke Telegram

# ─────────────────────────────────────────────
# SCREENING LOOP
# ─────────────────────────────────────────────
candidates = []

for stock in stocks:
    try:
        data = yf.download(stock, interval="15m", period="5d", progress=False, auto_adjust=True)

        if len(data) < 50:
            continue

        # ── Indikator ──
        close = data["Close"].squeeze()
        high  = data["High"].squeeze()
        low   = data["Low"].squeeze()
        vol   = data["Volume"].squeeze()

        data["rsi"]  = ta.momentum.RSIIndicator(close, window=RSI_WINDOW).rsi()
        data["ma20"] = close.rolling(MA_WINDOW).mean()

        latest = data.iloc[-1]
        prev   = data.iloc[-2]

        # ── Filter Likuiditas ──
        price = float(latest["Close"])
        if price < MIN_PRICE:
            continue

        # Estimasi nilai transaksi harian (volume * harga * 4 sesi per hari ≈ 26 candle 15m)
        avg_daily_value = float(vol.rolling(20).mean().iloc[-1]) * price * 26
        if avg_daily_value < MIN_AVG_VALUE_IDR:
            continue

        # ── Data Derivatif ──
        rsi_now  = float(latest["rsi"])
        rsi_prev = float(prev["rsi"])
        ma20     = float(latest["ma20"])
        last_close = float(latest["Close"])

        vol_avg10  = float(vol.rolling(10).mean().iloc[-1])
        vol_ratio  = float(vol.iloc[-1]) / vol_avg10 if vol_avg10 > 0 else 0
        vol_spike  = vol_ratio > VOL_SPIKE_RATIO

        resistance = float(high.rolling(100).max().iloc[-1])
        distance   = (resistance - last_close) / last_close
        room_ok    = distance > ROOM_TO_RESISTANCE

        # Morning high: UTC 02:00–02:30 = 09:00–09:30 WIB
        morning_data = data.between_time("02:00", "02:30")
        morning_high = float(morning_data["High"].max()) if not morning_data.empty else None

        signal = None
        score  = 0

        # ══ MODE 1: BREAKOUT ══════════════════════════════════════════════
        # Harga breakout di atas high sesi pagi + volume spike + RSI kuat
        if (
            morning_high and
            last_close > morning_high and
            vol_spike and
            rsi_now > RSI_MIN_MOMENTUM and
            last_close > ma20 and
            room_ok
        ):
            signal = "Breakout"
            score  = vol_ratio * (rsi_now / 100)

        # ══ MODE 2: MOMENTUM ══════════════════════════════════════════════
        # Trend naik bersih: harga di atas MA20, RSI > 52, volume di atas rata-rata
        elif (
            rsi_now > RSI_MIN_MOMENTUM and
            rsi_now > rsi_prev and         # RSI sedang naik
            last_close > ma20 and
            vol_ratio > 1.2 and
            room_ok
        ):
            signal = "Momentum"
            score  = vol_ratio * (rsi_now / 100)

        # ══ MODE 3: REVERSAL (Oversold Bounce) ════════════════════════════
        # RSI sebelumnya sangat rendah, kini mulai berbalik naik + volume masuk
        elif (
            rsi_prev < RSI_MAX_REVERSAL and
            rsi_now > RSI_REVERSAL_NOW and
            vol_spike and
            last_close > float(data["Close"].iloc[-3])  # candle naik 2 dari 3 terakhir
        ):
            signal = "Reversal"
            score  = vol_ratio * ((60 - rsi_prev) / 60)  # makin oversold, makin tinggi score

        if signal:
            candidates.append({
                "stock":     stock.replace(".JK", ""),
                "signal":    signal,
                "price":     round(price),
                "rsi":       round(rsi_now, 1),
                "vol_ratio": round(vol_ratio, 2),
                "room":      round(distance * 100, 2),
                "score":     round(score, 3),
            })

    except Exception as e:
        print(f"Error {stock}: {e}")

# ─────────────────────────────────────────────
# SUSUN DAN KIRIM KE TELEGRAM
# ─────────────────────────────────────────────
wib = pytz.timezone("Asia/Jakarta")
now = datetime.now(wib).strftime("%d/%m %H:%M WIB")

candidates = sorted(candidates, key=lambda x: x["score"], reverse=True)

SIGNAL_ICON = {
    "Breakout":  "🚀",
    "Momentum":  "📈",
    "Reversal":  "🔄",
}

if candidates:
    message  = f"🔍 IDX Screener ({now})\n"
    message += f"Kandidat intraday: {len(candidates)} saham\n"
    message += "─" * 28 + "\n\n"

    for i, c in enumerate(candidates[:MAX_CANDIDATES], start=1):
        icon = SIGNAL_ICON.get(c["signal"], "•")
        message += (
            f"{i}. {icon} {c['stock']} — {c['signal']}\n"
            f"   Harga: Rp{c['price']:,}  |  RSI: {c['rsi']}\n"
            f"   Vol: x{c['vol_ratio']}  |  Room: {c['room']}%\n\n"
        )

    if len(candidates) > MAX_CANDIDATES:
        message += f"...dan {len(candidates) - MAX_CANDIDATES} kandidat lainnya.\n"
else:
    message = (
        f"⏳ IDX Screener ({now})\n"
        f"Belum ada kandidat yang memenuhi kriteria intraday."
    )

print(message)

requests.get(
    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
    params={"chat_id": CHAT_ID, "text": message},
    timeout=10,
)
