import yfinance as yf
import pandas as pd
import requests
import os
from datetime import datetime
import pytz

TOKEN   = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ─────────────────────────────────────────────
# SETTINGS — sesuaikan jika perlu
# ─────────────────────────────────────────────
MA_WINDOW         = 20
PRICE_ABOVE_MA    = 1.01   # Harga sekarang minimal 1% di atas MA20
PRICE_BELOW_MA    = 0.99   # Harga sebelumnya maksimal 1% di bawah MA20
VOL_RATIO_MIN     = 1.5    # Volume candle terakhir vs MA20 volume
MIN_PRICE         = 50     # Filter saham gorengan
MIN_AVG_VALUE_IDR = 1_000_000_000  # Rp 1 miliar/hari (likuiditas minimum)
MAX_CANDIDATES    = 10
INTERVAL          = "15m"
PERIOD            = "5d"

# ─────────────────────────────────────────────
# FETCH SEMUA SAHAM IDX
# ─────────────────────────────────────────────
def get_all_idx_stocks():
    try:
        url = "https://www.idx.co.id/primary/StockData/GetSecuritiesStock"
        params = {"start": 0, "length": 9999, "s": "Kode", "d": "asc"}
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "User-Agent": "Mozilla/5.0"
        }
        resp   = requests.get(url, params=params, headers=headers, timeout=20)
        data   = resp.json()
        stocks = [item["Kode"] + ".JK" for item in data["data"] if item.get("Kode")]
        return stocks
    except Exception as e:
        print(f"[IDX API] Gagal: {e} — fallback ke stocks.csv")
        try:
            return pd.read_csv("stocks.csv", header=None)[0].tolist()
        except Exception:
            return []

stocks = get_all_idx_stocks()

# ─────────────────────────────────────────────
# SCREENING LOOP
# ─────────────────────────────────────────────
candidates = []

for stock in stocks:
    try:
        data = yf.download(stock, interval=INTERVAL, period=PERIOD,
                           progress=False, auto_adjust=True)

        if len(data) < MA_WINDOW + 5:
            continue

        close = data["Close"].squeeze()
        vol   = data["Volume"].squeeze()

        ma20     = close.rolling(MA_WINDOW).mean()
        vol_ma20 = vol.rolling(MA_WINDOW).mean()

        price_now  = float(close.iloc[-1])
        price_prev = float(close.iloc[-2])
        ma_now     = float(ma20.iloc[-1])
        ma_prev    = float(ma20.iloc[-2])
        vol_now    = float(vol.iloc[-1])
        vol_ma_now = float(vol_ma20.iloc[-1])

        # ── Filter likuiditas ──
        if price_now < MIN_PRICE:
            continue
        avg_daily_value = vol_ma_now * price_now * 26
        if avg_daily_value < MIN_AVG_VALUE_IDR:
            continue

        # ── Sinyal Break MA20 ──
        cross_up  = (price_prev < ma_prev * PRICE_BELOW_MA) and (price_now > ma_now * PRICE_ABOVE_MA)
        vol_spike = (vol_now / vol_ma_now) >= VOL_RATIO_MIN if vol_ma_now > 0 else False

        if cross_up and vol_spike:
            vol_ratio = round(vol_now / vol_ma_now, 2)
            gap_pct   = round(((price_now - ma_now) / ma_now) * 100, 2)
            candidates.append({
                "stock":     stock.replace(".JK", ""),
                "price":     round(price_now),
                "ma20":      round(ma_now),
                "gap":       gap_pct,
                "vol_ratio": vol_ratio,
            })

    except Exception as e:
        print(f"Error {stock}: {e}")

# ─────────────────────────────────────────────
# KIRIM KE TELEGRAM
# ─────────────────────────────────────────────
wib = pytz.timezone("Asia/Jakarta")
now = datetime.now(wib).strftime("%d/%m %H:%M WIB")

# Urutkan: volume ratio terbesar dulu
candidates = sorted(candidates, key=lambda x: x["vol_ratio"], reverse=True)

if candidates:
    message  = f"📊 Break MA20 Alert ({now})\n"
    message += f"Sinyal baru: {len(candidates)} saham\n"
    message += "─" * 26 + "\n\n"

    for i, c in enumerate(candidates[:MAX_CANDIDATES], start=1):
        message += (
            f"{i}. {c['stock']}\n"
            f"   Harga: Rp{c['price']:,}  |  MA20: Rp{c['ma20']:,}\n"
            f"   Gap: +{c['gap']}%  |  Vol: x{c['vol_ratio']}\n\n"
        )

    if len(candidates) > MAX_CANDIDATES:
        message += f"...dan {len(candidates) - MAX_CANDIDATES} saham lainnya.\n"

    requests.get(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        params={"chat_id": CHAT_ID, "text": message},
        timeout=10,
    )
else:
    # Tidak kirim pesan kalau tidak ada sinyal — tidak ada notif "Tidak ada kandidat"
    print(f"[{now}] Tidak ada sinyal Break MA20.")
