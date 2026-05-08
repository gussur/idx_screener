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

# Jam bursa IDX yang SKIP (win rate rendah berdasarkan backtest)
# 13:00 WIB = win rate 23.5% — paling buruk
SKIP_HOURS_WIB    = {13}

# Threshold sinyal
PRE_MAX_BELOW     = 0.98
PRE_MIN_BELOW     = 0.92
PRE_VOL_MIN       = 1.2
PRE_RSI_MIN       = 43

BRK_ABOVE         = 1.003
BRK_PREV          = 1.00
BRK_VOL_MIN       = 1.5

# ─────────────────────────────────────────────
# PRIORITY WATCHLIST
# Saham dengan win rate tertinggi dari backtest (min 5 sinyal)
# WIIM 72.7% | GTSI 63.6% | MSIN 62.5% | MDKA 66.7%
# EXCL 60%   | ANTM 58.8% | MAPA 58.3% | BREN 57.1%
# ─────────────────────────────────────────────
PRIORITY_STOCKS = {
    "WIIM", "MDKA", "GTSI", "MSIN",
    "EXCL", "ANTM", "MAPA", "BREN",
}

# ─────────────────────────────────────────────
# FETCH SEMUA SAHAM IDX — multi-source fallback
# ─────────────────────────────────────────────
def get_all_idx_stocks():
    # Source 1: IDX API resmi
    try:
        url  = "https://www.idx.co.id/primary/StockData/GetSecuritiesStock"
        resp = requests.get(url,
            params={"start": 0, "length": 9999, "s": "Kode", "d": "asc"},
            headers={"X-Requested-With": "XMLHttpRequest", "User-Agent": "Mozilla/5.0"},
            timeout=20)
        data   = resp.json()
        stocks = [item["Kode"] + ".JK" for item in data["data"] if item.get("Kode")]
        if stocks:
            print(f"[IDX API] {len(stocks)} saham.")
            return stocks
    except Exception as e:
        print(f"[IDX API] Gagal: {e}")

    # Source 2: GitHub public list
    try:
        url    = "https://raw.githubusercontent.com/dsernst/idx-stocks/main/stocks.txt"
        resp   = requests.get(url, timeout=15)
        stocks = [l.strip() + ".JK" for l in resp.text.splitlines() if l.strip()]
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

# ─────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────
WIB = pytz.timezone("Asia/Jakarta")

def calc_rsi(series, window=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(window).mean()
    loss  = (-delta.clip(upper=0)).rolling(window).mean()
    rs    = gain / loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))

def current_hour_wib():
    return datetime.now(WIB).hour

# ─────────────────────────────────────────────
# CEK JAM — skip jika di luar jam produktif
# ─────────────────────────────────────────────
now_hour = current_hour_wib()

if now_hour in SKIP_HOURS_WIB:
    print(f"[{datetime.now(WIB).strftime('%H:%M WIB')}] Skip jam {now_hour}:00 (win rate rendah).")
    exit(0)

# ─────────────────────────────────────────────
# SCREENING LOOP
# ─────────────────────────────────────────────
stocks = get_all_idx_stocks()
print(f"Total akan discreen: {len(stocks)} saham")

# --- PERUBAHAN: BATCH DOWNLOAD ---
print(f"Mendownload data secara batch (multi-threading)...")
tickers_str = " ".join(stocks)
# Fitur group_by="ticker" akan membuat kolom MultiIndex, memudahkan ekstraksi per saham
batch_data = yf.download(tickers=tickers_str, interval=INTERVAL, period=PERIOD,
                         group_by="ticker", threads=True, progress=False)

priority_candidates = []
regular_candidates  = []

for stock in stocks:
    try:
        # --- PERUBAHAN: EKSTRAKSI DATA DARI BATCH ---
        if len(stocks) > 1:
            # Skip jika yfinance gagal mendownload ticker ini
            if stock not in batch_data.columns.levels[0]:
                continue
            # Ambil data spesifik untuk saham ini dan buang baris kosong (NaN)
            data = batch_data[stock].dropna()
        else:
            data = batch_data.dropna()

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

        if pd.isna(ma_now) or pd.isna(rsi_now) or vol_ma_now == 0:
            continue
        if price_now < MIN_PRICE:
            continue
            
        # --- PERUBAHAN: ASUMSI 22 CANDLE 15-MENIT PER HARI ---
        if vol_ma_now * price_now * 22 < MIN_AVG_VALUE_IDR:
            continue

        vol_ratio = vol_now / vol_ma_now
        ratio_now = price_now / ma_now
        signal    = None

        # ── PRE-BREAKOUT ──
        if (
            PRE_MIN_BELOW <= ratio_now <= PRE_MAX_BELOW and
            vol_ratio >= PRE_VOL_MIN and
            rsi_now >= PRE_RSI_MIN and
            rsi_now > rsi_prev
        ):
            signal = "Pre-Breakout"

        # ── BREAKOUT ──
        elif (
            price_prev < ma_prev * BRK_PREV and
            price_now  > ma_now  * BRK_ABOVE and
            vol_ratio  >= BRK_VOL_MIN
        ):
            signal = "Breakout"

        if signal:
            dist_ma = (price_now - ma_now) / ma_now
            entry   = {
                "stock":     stock.replace(".JK", ""),
                "signal":    signal,
                "price":     round(price_now),
                "ma20":      round(ma_now),
                "gap":       round(dist_ma * 100, 2),
                "rsi":       round(rsi_now, 1),
                "vol_ratio": round(vol_ratio, 2),
            }
            ticker = stock.replace(".JK", "")
            if ticker in PRIORITY_STOCKS:
                priority_candidates.append(entry)
            else:
                regular_candidates.append(entry)

    except Exception as e:
        # Menangkap error individu agar loop tidak berhenti total
        pass

# ─────────────────────────────────────────────
# SUSUN PESAN & KIRIM TELEGRAM
# ─────────────────────────────────────────────
now_str = datetime.now(WIB).strftime("%d/%m %H:%M WIB")

# Urutkan: vol_ratio terbesar dulu
priority_candidates = sorted(priority_candidates, key=lambda x: x["vol_ratio"], reverse=True)
regular_candidates  = sorted(regular_candidates,  key=lambda x: x["vol_ratio"], reverse=True)

all_candidates = priority_candidates + regular_candidates

if not all_candidates:
    print(f"[{now_str}] Tidak ada sinyal.")
    exit(0)

SIGNAL_ICON = {"Pre-Breakout": "⚡", "Breakout": "✅"}

message  = f"📡 MA20 Alert ({now_str})\n"
message += f"Sinyal: {len(all_candidates)} saham"

# Tampilkan label jam jika jam produktif terbaik
if now_hour == 14:
    message += "  🔥 Jam terbaik\n\n"
else:
    message += "\n\n"

# Priority watchlist dulu
if priority_candidates:
    message += "⭐ PRIORITY\n"
    message += "─" * 24 + "\n"
    for c in priority_candidates:
        icon = SIGNAL_ICON.get(c["signal"], "•")
        gap_str = f"{c['gap']:+.2f}%"
        message += (
            f"{icon} {c['stock']} — {c['signal']}\n"
            f"   Rp{c['price']:,}  |  MA20: Rp{c['ma20']:,}  |  {gap_str}\n"
            f"   RSI: {c['rsi']}  |  Vol: x{c['vol_ratio']}\n\n"
        )

# Regular candidates
if regular_candidates:
    if priority_candidates:
        message += "─" * 24 + "\n"
    for c in regular_candidates[:MAX_CANDIDATES]:
        icon = SIGNAL_ICON.get(c["signal"], "•")
        gap_str = f"{c['gap']:+.2f}%"
        message += (
            f"{icon} {c['stock']} — {c['signal']}\n"
            f"   Rp{c['price']:,}  |  MA20: Rp{c['ma20']:,}  |  {gap_str}\n"
            f"   RSI: {c['rsi']}  |  Vol: x{c['vol_ratio']}\n\n"
        )

    if len(regular_candidates) > MAX_CANDIDATES:
        message += f"...dan {len(regular_candidates) - MAX_CANDIDATES} saham lainnya.\n"

print(message)

# Kirim ke Telegram (Aman diletakkan di try-except agar tidak crash kalau gagal jaringan)
try:
    requests.get(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        params={"chat_id": CHAT_ID, "text": message},
        timeout=10,
    )
except Exception as e:
    print(f"Gagal kirim pesan ke Telegram: {e}")
