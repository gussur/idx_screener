import yfinance as yf
import pandas as pd
import requests
import os
from datetime import datetime
import pytz

TOKEN   = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# ─────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────
MA_WINDOW         = 20
MIN_PRICE         = 50
MIN_AVG_VALUE_IDR = 1_000_000_000   # Rp 1 miliar/hari
MAX_CANDIDATES    = 10
INTERVAL          = "15m"
PERIOD            = "5d"

# Sinyal 1 — PRE-BREAKOUT (early warning)
# Harga masih di bawah MA20, tapi sudah squeeze dan volume mulai masuk
PRE_MAX_BELOW_MA  = 0.97   # Harga minimal 97% dari MA20 (dalam 3% di bawah)
PRE_MIN_BELOW_MA  = 0.90   # Tidak lebih dari 10% di bawah MA20 (bukan downtrend)
PRE_VOL_RATIO_MIN = 1.3    # Volume mulai naik (lebih rendah dari breakout)
PRE_RSI_MIN       = 45     # RSI sudah mulai naik

# Sinyal 2 — BREAKOUT MA20 (konfirmasi)
# Baru saja cross MA20 ke atas dengan volume
BRK_PRICE_ABOVE   = 1.005  # Cukup 0.5% di atas MA20
BRK_PRICE_PREV    = 1.00   # Candle sebelumnya di bawah MA20
BRK_VOL_RATIO_MIN = 1.5

# ─────────────────────────────────────────────
# FETCH SEMUA SAHAM IDX — multi-source fallback
# ─────────────────────────────────────────────
def get_all_idx_stocks():
    # Source 1: IDX API
    try:
        url = "https://www.idx.co.id/primary/StockData/GetSecuritiesStock"
        resp = requests.get(url,
            params={"start": 0, "length": 9999, "s": "Kode", "d": "asc"},
            headers={"X-Requested-With": "XMLHttpRequest",
                     "User-Agent": "Mozilla/5.0"},
            timeout=20)
        data = resp.json()
        stocks = [item["Kode"] + ".JK" for item in data["data"] if item.get("Kode")]
        if stocks:
            print(f"[IDX API] {len(stocks)} saham.")
            return stocks
    except Exception as e:
        print(f"[IDX API] Gagal: {e}")

    # Source 2: GitHub raw — daftar saham IDX publik
    try:
        url = "https://raw.githubusercontent.com/dsernst/idx-stocks/main/stocks.txt"
        resp = requests.get(url, timeout=15)
        stocks = [line.strip() + ".JK" for line in resp.text.splitlines() if line.strip()]
        if stocks:
            print(f"[GitHub list] {len(stocks)} saham.")
            return stocks
    except Exception as e:
        print(f"[GitHub list] Gagal: {e}")

    # Source 3: stocks.csv lokal
    try:
        stocks = pd.read_csv("stocks.csv", header=None)[0].tolist()
        print(f"[stocks.csv] {len(stocks)} saham.")
        return stocks
    except Exception as e:
        print(f"[stocks.csv] Gagal: {e}")
        return []

stocks = get_all_idx_stocks()
print(f"Total akan discreen: {len(stocks)} saham\n")

# ─────────────────────────────────────────────
# RSI helper (tanpa library ta)
# ─────────────────────────────────────────────
def calc_rsi(series, window=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(window).mean()
    loss  = (-delta.clip(upper=0)).rolling(window).mean()
    rs    = gain / loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))

# ─────────────────────────────────────────────
# SCREENING LOOP
# ─────────────────────────────────────────────
pre_breakout = []
breakout     = []

for stock in stocks:
    try:
        data = yf.download(stock, interval=INTERVAL, period=PERIOD,
                           progress=False, auto_adjust=True)

        if len(data) < MA_WINDOW + 5:
            continue

        close    = data["Close"].squeeze()
        vol      = data["Volume"].squeeze()
        ma20     = close.rolling(MA_WINDOW).mean()
        vol_ma20 = vol.rolling(MA_WINDOW).mean()
        rsi      = calc_rsi(close)

        price_now  = float(close.iloc[-1])
        price_prev = float(close.iloc[-2])
        ma_now     = float(ma20.iloc[-1])
        ma_prev    = float(ma20.iloc[-2])
        vol_now    = float(vol.iloc[-1])
        vol_ma_now = float(vol_ma20.iloc[-1])
        rsi_now    = float(rsi.iloc[-1])
        rsi_prev   = float(rsi.iloc[-2])

        # Filter likuiditas
        if price_now < MIN_PRICE:
            continue
        if vol_ma_now * price_now * 26 < MIN_AVG_VALUE_IDR:
            continue
        if vol_ma_now == 0:
            continue

        vol_ratio  = round(vol_now / vol_ma_now, 2)
        dist_ma    = (price_now - ma_now) / ma_now  # positif = di atas MA20

        # ── Sinyal PRE-BREAKOUT ──
        # Harga mendekati MA20 dari bawah, volume & RSI mulai naik
        if (
            PRE_MIN_BELOW_MA <= (price_now / ma_now) <= PRE_MAX_BELOW_MA and
            vol_ratio >= PRE_VOL_RATIO_MIN and
            rsi_now >= PRE_RSI_MIN and
            rsi_now > rsi_prev      # RSI sedang naik
        ):
            pre_breakout.append({
                "stock":     stock.replace(".JK", ""),
                "price":     round(price_now),
                "ma20":      round(ma_now),
                "gap":       round(dist_ma * 100, 2),  # negatif = di bawah MA20
                "rsi":       round(rsi_now, 1),
                "vol_ratio": vol_ratio,
            })

        # ── Sinyal BREAKOUT MA20 ──
        elif (
            price_prev < ma_prev * BRK_PRICE_PREV and
            price_now  > ma_now  * BRK_PRICE_ABOVE and
            vol_ratio  >= BRK_VOL_RATIO_MIN
        ):
            breakout.append({
                "stock":     stock.replace(".JK", ""),
                "price":     round(price_now),
                "ma20":      round(ma_now),
                "gap":       round(dist_ma * 100, 2),
                "rsi":       round(rsi_now, 1),
                "vol_ratio": vol_ratio,
            })

    except Exception as e:
        print(f"Error {stock}: {e}")

# ─────────────────────────────────────────────
# SUSUN PESAN & KIRIM TELEGRAM
# ─────────────────────────────────────────────
wib = pytz.timezone("Asia/Jakarta")
now = datetime.now(wib).strftime("%d/%m %H:%M WIB")

pre_breakout = sorted(pre_breakout, key=lambda x: x["vol_ratio"], reverse=True)
breakout     = sorted(breakout,     key=lambda x: x["vol_ratio"], reverse=True)

if not pre_breakout and not breakout:
    print(f"[{now}] Tidak ada sinyal.")
    exit(0)

message = f"📡 MA20 Alert ({now})\n\n"

if pre_breakout:
    message += f"⚡ PRE-BREAKOUT — {len(pre_breakout)} saham\n"
    message += "(Mendekati MA20, belum break)\n"
    message += "─" * 24 + "\n"
    for i, c in enumerate(pre_breakout[:MAX_CANDIDATES], 1):
        message += (
            f"{i}. {c['stock']}  Rp{c['price']:,}\n"
            f"   MA20: Rp{c['ma20']:,}  |  {c['gap']}%\n"
            f"   RSI: {c['rsi']}  |  Vol: x{c['vol_ratio']}\n\n"
        )

if breakout:
    message += f"\n✅ BREAKOUT — {len(breakout)} saham\n"
    message += "(Baru cross MA20 ke atas)\n"
    message += "─" * 24 + "\n"
    for i, c in enumerate(breakout[:MAX_CANDIDATES], 1):
        message += (
            f"{i}. {c['stock']}  Rp{c['price']:,}\n"
            f"   MA20: Rp{c['ma20']:,}  |  +{c['gap']}%\n"
            f"   RSI: {c['rsi']}  |  Vol: x{c['vol_ratio']}\n\n"
        )

print(message)

requests.get(
    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
    params={"chat_id": CHAT_ID, "text": message},
    timeout=10,
)
