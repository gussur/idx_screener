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

PRIORITY_STOCKS = {
    "WIIM", "MDKA", "GTSI", "MSIN",
    "EXCL", "ANTM", "MAPA", "BREN",
}

# ─────────────────────────────────────────────
# FALLBACK LIST — 150 saham IDX liquid
# Dipakai jika IDX API gagal
# ─────────────────────────────────────────────
FALLBACK_STOCKS = [
    # LQ45
    "BBCA.JK","BBRI.JK","BMRI.JK","BBNI.JK","TLKM.JK",
    "ASII.JK","ICBP.JK","INDF.JK","UNVR.JK","PGAS.JK",
    "ADRO.JK","PTBA.JK","ANTM.JK","MDKA.JK","TINS.JK",
    "EXCL.JK","ISAT.JK","KLBF.JK","CPIN.JK","JPFA.JK",
    "SMGR.JK","INTP.JK","BSDE.JK","CTRA.JK","PWON.JK",
    "AKRA.JK","TPIA.JK","BRPT.JK","MIKA.JK","HEAL.JK",
    "AMRT.JK","ACES.JK","MYOR.JK","AALI.JK","LSIP.JK",
    "INKP.JK","TKIM.JK","TBIG.JK","TOWR.JK","ESSA.JK",
    "BRIS.JK","BTPS.JK","SIDO.JK","EMTK.JK","FILM.JK",
    # Mid cap aktif
    "BBKP.JK","NISP.JK","BNGA.JK","BJTM.JK","BJBR.JK",
    "PNBN.JK","AGRO.JK","PTPP.JK","ADHI.JK","NRCA.JK",
    "TOTL.JK","JKON.JK","WTON.JK","JSMR.JK","BIRD.JK",
    "GIAA.JK","RALS.JK","LPPF.JK","MAPI.JK","MIDI.JK",
    "DMAS.JK","SSIA.JK","KIJA.JK","BEST.JK","DILD.JK",
    "APLN.JK","SMRA.JK","LPKR.JK","MTEL.JK","PGEO.JK",
    "WTON.JK","KPIG.JK","FAST.JK","MSIN.JK","MPPA.JK",
    "BBTN.JK","SDRA.JK","BJTM.JK","BJBR.JK","MKPI.JK",
    # Small cap likuid
    "WIFI.JK","PACK.JK","BBYB.JK","ARTO.JK","BREN.JK",
    "CUAN.JK","AMMN.JK","MBMA.JK","NCKL.JK","MPMX.JK",
    "MAPA.JK","HRUM.JK","ITMG.JK","BYAN.JK","DEWA.JK",
    "MBSS.JK","GGRM.JK","HMSP.JK","WIIM.JK","PYFA.JK",
    "TSPC.JK","SCMA.JK","MNCN.JK","BMTR.JK","SRTG.JK",
    "MLPL.JK","MEDC.JK","INCO.JK","TOBA.JK","ABMM.JK",
    "GTSI.JK","MARK.JK","KBLV.JK","MLIA.JK","KAEF.JK",
    # Sektoral tambahan
    "DLTA.JK","MLBI.JK","SKBM.JK","ULTJ.JK","CLEO.JK",
    "BBTN.JK","BNLI.JK","NISP.JK","PNBN.JK","BNGA.JK",
]

# ─────────────────────────────────────────────
# FETCH SAHAM IDX
# ─────────────────────────────────────────────
def get_all_idx_stocks():
    # Source 1: IDX API resmi
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

    # Source 2: stocks.csv lokal
    try:
        stocks = pd.read_csv("stocks.csv", header=None)[0].tolist()
        if len(stocks) > 10:
            print(f"[stocks.csv] {len(stocks)} saham.")
            return stocks
    except Exception as e:
        print(f"[stocks.csv] Gagal: {e}")

    # Source 3: hardcoded fallback
    print(f"[Fallback] Menggunakan {len(FALLBACK_STOCKS)} saham hardcoded.")
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
            if stock.replace(".JK", "") in PRIORITY_STOCKS:
                priority_candidates.append(entry)
            else:
                regular_candidates.append(entry)

    except Exception as e:
        print(f"Error {stock}: {e}")

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

message  = f"📡 MA20 Alert ({now_str})\n"
message += f"Sinyal: {len(all_candidates)} saham"
message += "  🔥 Jam terbaik\n\n" if now_hour == 14 else "\n\n"

if priority_candidates:
    message += "⭐ PRIORITY\n"
    message += "─" * 24 + "\n"
    for c in priority_candidates:
        icon = SIGNAL_ICON.get(c["signal"], "•")
        message += (
            f"{icon} {c['stock']} — {c['signal']}\n"
            f"   Rp{c['price']:,}  |  MA20: Rp{c['ma20']:,}  |  {c['gap']:+.2f}%\n"
            f"   RSI: {c['rsi']}  |  Vol: x{c['vol_ratio']}\n\n"
        )

if regular_candidates:
    if priority_candidates:
        message += "─" * 24 + "\n"
    for c in regular_candidates[:MAX_CANDIDATES]:
        icon = SIGNAL_ICON.get(c["signal"], "•")
        message += (
            f"{icon} {c['stock']} — {c['signal']}\n"
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
