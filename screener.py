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
MIN_AVG_VALUE_IDR = 1_000_000_000
MAX_CANDIDATES    = 10
INTERVAL          = "15m"
PERIOD            = "5d"
SKIP_HOURS_WIB    = {13}

PRE_MAX_BELOW     = 0.98
PRE_MIN_BELOW     = 0.92
PRE_VOL_MIN       = 1.2
PRE_RSI_MIN       = 43

BRK_ABOVE         = 1.003
BRK_PREV          = 1.00
BRK_VOL_MIN       = 1.5

# ── Filter Tren Harian ──────────────────────
# Sinyal 15m hanya lolos jika tren harian tidak bearish
# Kondisi GUGUR jika salah satu benar:
#   - Harga harian < MA20 harian (tren turun)
#   - MA20 harian < MA50 harian (death cross)
#   - MACD histogram harian < threshold (momentum turun tajam)
DAILY_MACD_MIN    = -3.0   # MACD histogram harian boleh negatif tapi tidak terlalu dalam
                            # Sesuaikan per saham — untuk harga ratusan cukup -3.0

PRIORITY_STOCKS = {
    "WIIM", "MDKA", "GTSI", "MSIN",
    "EXCL", "ANTM", "MAPA", "BREN",
}

FALLBACK_STOCKS = [
    "BBCA.JK","BBRI.JK","BMRI.JK","BBNI.JK","TLKM.JK",
    "ASII.JK","ICBP.JK","INDF.JK","UNVR.JK","PGAS.JK",
    "ADRO.JK","PTBA.JK","ANTM.JK","MDKA.JK","TINS.JK",
    "EXCL.JK","ISAT.JK","KLBF.JK","CPIN.JK","JPFA.JK",
    "SMGR.JK","INTP.JK","BSDE.JK","CTRA.JK","PWON.JK",
    "AKRA.JK","TPIA.JK","BRPT.JK","MIKA.JK","HEAL.JK",
    "AMRT.JK","ACES.JK","MYOR.JK","AALI.JK","LSIP.JK",
    "INKP.JK","TKIM.JK","TBIG.JK","TOWR.JK","ESSA.JK",
    "BRIS.JK","BTPS.JK","SIDO.JK","EMTK.JK","FILM.JK",
    "BBKP.JK","NISP.JK","BNGA.JK","BJTM.JK","BJBR.JK",
    "PNBN.JK","AGRO.JK","PTPP.JK","ADHI.JK","NRCA.JK",
    "TOTL.JK","JKON.JK","WTON.JK","JSMR.JK","BIRD.JK",
    "GIAA.JK","RALS.JK","LPPF.JK","MAPI.JK","MIDI.JK",
    "DMAS.JK","SSIA.JK","KIJA.JK","BEST.JK","DILD.JK",
    "APLN.JK","SMRA.JK","LPKR.JK","MTEL.JK","PGEO.JK",
    "KPIG.JK","FAST.JK","MSIN.JK","MPPA.JK","BBTN.JK",
    "WIFI.JK","PACK.JK","BBYB.JK","ARTO.JK","BREN.JK",
    "CUAN.JK","AMMN.JK","MBMA.JK","NCKL.JK","MPMX.JK",
    "MAPA.JK","HRUM.JK","ITMG.JK","BYAN.JK","DEWA.JK",
    "MBSS.JK","GGRM.JK","HMSP.JK","WIIM.JK","PYFA.JK",
    "TSPC.JK","SCMA.JK","MNCN.JK","BMTR.JK","SRTG.JK",
    "MLPL.JK","MEDC.JK","INCO.JK","TOBA.JK","ABMM.JK",
    "GTSI.JK","MARK.JK","KBLV.JK","KAEF.JK","DLTA.JK",
    "MLBI.JK","SKBM.JK","ULTJ.JK","CLEO.JK","BBTN.JK",
]

# ─────────────────────────────────────────────
# FETCH SAHAM IDX
# ─────────────────────────────────────────────
def get_all_idx_stocks():
    try:
        url  = "https://www.idx.co.id/primary/StockData/GetSecuritiesStock"
        resp = requests.get(url,
            params={"start": 0, "length": 9999, "s": "Kode", "d": "asc"},
            headers={"X-Requested-With": "XMLHttpRequest", "User-Agent": "Mozilla/5.0"},
            timeout=20)
        resp.raise_for_status()
        data   = resp.json()
        stocks = [item["Kode"] + ".JK" for item in data["data"] if item.get("Kode")]
        if len(stocks) > 100:
            print(f"[IDX API] {len(stocks)} saham.")
            return stocks
    except Exception as e:
        print(f"[IDX API] Gagal: {e}")

    try:
        stocks = pd.read_csv("stocks.csv", header=None)[0].tolist()
        if len(stocks) > 10:
            print(f"[stocks.csv] {len(stocks)} saham.")
            return stocks
    except Exception as e:
        print(f"[stocks.csv] Gagal: {e}")

    print(f"[Fallback] {len(FALLBACK_STOCKS)} saham hardcoded.")
    return FALLBACK_STOCKS

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

def calc_macd(series, fast=12, slow=26, signal=9):
    ema_fast   = series.ewm(span=fast, adjust=False).mean()
    ema_slow   = series.ewm(span=slow, adjust=False).mean()
    macd_line  = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    histogram  = macd_line - signal_line
    return macd_line, signal_line, histogram

def get_daily_trend(stock):
    """
    Ambil data harian dan evaluasi tren.
    Return: (trend_ok, trend_label, detail)
      trend_ok    = True jika tren harian layak untuk intraday entry
      trend_label = 'Bullish' / 'Sideways' / 'Bearish'
      detail      = string ringkasan untuk Telegram
    """
    try:
        daily = yf.download(stock, interval="1d", period="3mo",
                            progress=False, auto_adjust=True)
        if len(daily) < 50:
            return True, "Unknown", ""   # data kurang, loloskan saja

        close_d  = daily["Close"].squeeze()
        ma20_d   = close_d.rolling(20).mean()
        ma50_d   = close_d.rolling(50).mean()
        _, _, hist = calc_macd(close_d)

        price_d  = float(close_d.iloc[-1])
        ma20_val = float(ma20_d.iloc[-1])
        ma50_val = float(ma50_d.iloc[-1])
        macd_h   = float(hist.iloc[-1])
        macd_h_prev = float(hist.iloc[-2])

        # Scoring tren harian
        # +1 tiap kondisi bullish, -1 tiap kondisi bearish
        score = 0
        score += 1 if price_d > ma20_val else -1
        score += 1 if price_d > ma50_val else -1
        score += 1 if ma20_val > ma50_val else -1   # golden cross / death cross
        score += 1 if macd_h > macd_h_prev else -1  # MACD histogram naik

        # Tren harian
        if score >= 2:
            trend_label = "Bullish"
            trend_ok    = True
        elif score == 0 or score == 1:
            trend_label = "Sideways"
            trend_ok    = True    # sideways tetap boleh entry
        else:
            trend_label = "Bearish"
            trend_ok    = False   # bearish → skip sinyal ini

        detail = f"Tren D: {trend_label} (skor {score:+d})"
        return trend_ok, trend_label, detail

    except Exception:
        return True, "Unknown", ""   # error → loloskan, jangan block sinyal

# ─────────────────────────────────────────────
# CEK JAM
# ─────────────────────────────────────────────
now_wib  = datetime.now(WIB)
now_hour = now_wib.hour

if now_hour in SKIP_HOURS_WIB:
    print(f"[{now_wib.strftime('%H:%M WIB')}] Skip jam {now_hour}:00.")
    exit(0)

# ─────────────────────────────────────────────
# SCREENING
# ─────────────────────────────────────────────
stocks = get_all_idx_stocks()
print(f"Total akan discreen: {len(stocks)} saham")

priority_candidates = []
regular_candidates  = []
filtered_by_trend   = 0

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

        if pd.isna(ma_now) or pd.isna(rsi_now) or vol_ma_now == 0:
            continue
        if price_now < MIN_PRICE:
            continue
        if vol_ma_now * price_now * 26 < MIN_AVG_VALUE_IDR:
            continue

        vol_ratio = vol_now / vol_ma_now
        ratio_now = price_now / ma_now
        signal    = None

        if (
            PRE_MIN_BELOW <= ratio_now <= PRE_MAX_BELOW and
            vol_ratio >= PRE_VOL_MIN and
            rsi_now >= PRE_RSI_MIN and
            rsi_now > rsi_prev
        ):
            signal = "Pre-Breakout"
        elif (
            price_prev < ma_prev * BRK_PREV and
            price_now  > ma_now  * BRK_ABOVE and
            vol_ratio  >= BRK_VOL_MIN
        ):
            signal = "Breakout"

        if signal:
            # ── Filter tren harian ──
            trend_ok, trend_label, trend_detail = get_daily_trend(stock)

            if not trend_ok:
                filtered_by_trend += 1
                print(f"  [SKIP] {stock} — sinyal {signal} tapi tren harian {trend_label}")
                continue

            dist_ma = (price_now - ma_now) / ma_now
            entry   = {
                "stock":        stock.replace(".JK", ""),
                "signal":       signal,
                "price":        round(price_now),
                "ma20":         round(ma_now),
                "gap":          round(dist_ma * 100, 2),
                "rsi":          round(rsi_now, 1),
                "vol_ratio":    round(vol_ratio, 2),
                "trend":        trend_label,
            }
            if stock.replace(".JK", "") in PRIORITY_STOCKS:
                priority_candidates.append(entry)
            else:
                regular_candidates.append(entry)

    except Exception as e:
        print(f"Error {stock}: {e}")

print(f"Difilter tren bearish: {filtered_by_trend} saham")

# ─────────────────────────────────────────────
# KIRIM TELEGRAM
# ─────────────────────────────────────────────
now_str = now_wib.strftime("%d/%m %H:%M WIB")

priority_candidates = sorted(priority_candidates, key=lambda x: x["vol_ratio"], reverse=True)
regular_candidates  = sorted(regular_candidates,  key=lambda x: x["vol_ratio"], reverse=True)
all_candidates      = priority_candidates + regular_candidates

if not all_candidates:
    print(f"[{now_str}] Tidak ada sinyal.")
    exit(0)

SIGNAL_ICON = {"Pre-Breakout": "⚡", "Breakout": "✅"}
TREND_ICON  = {"Bullish": "🟢", "Sideways": "🟡", "Unknown": "⚪"}

message  = f"📡 MA20 Alert ({now_str})\n"
message += f"Sinyal: {len(all_candidates)} saham"
message += "  🔥 Jam terbaik\n\n" if now_hour == 14 else "\n\n"

if priority_candidates:
    message += "⭐ PRIORITY\n"
    message += "─" * 24 + "\n"
    for c in priority_candidates:
        t_icon = TREND_ICON.get(c["trend"], "⚪")
        message += (
            f"{SIGNAL_ICON.get(c['signal'],'•')} {c['stock']} — {c['signal']}  {t_icon}{c['trend']}\n"
            f"   Rp{c['price']:,}  |  MA20: Rp{c['ma20']:,}  |  {c['gap']:+.2f}%\n"
            f"   RSI: {c['rsi']}  |  Vol: x{c['vol_ratio']}\n\n"
        )

if regular_candidates:
    if priority_candidates:
        message += "─" * 24 + "\n"
    for c in regular_candidates[:MAX_CANDIDATES]:
        t_icon = TREND_ICON.get(c["trend"], "⚪")
        message += (
            f"{SIGNAL_ICON.get(c['signal'],'•')} {c['stock']} — {c['signal']}  {t_icon}{c['trend']}\n"
            f"   Rp{c['price']:,}  |  MA20: Rp{c['ma20']:,}  |  {c['gap']:+.2f}%\n"
            f"   RSI: {c['rsi']}  |  Vol: x{c['vol_ratio']}\n\n"
        )
    if len(regular_candidates) > MAX_CANDIDATES:
        message += f"...dan {len(regular_candidates) - MAX_CANDIDATES} saham lainnya.\n"

print(message)

requests.get(
    f"https://api.telegram.org/bot{TOKEN}/sendMessage",
    params={"chat_id": CHAT_ID, "text": message},
    timeout=10,
)
