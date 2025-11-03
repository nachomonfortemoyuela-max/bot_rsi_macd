import time
import json
import requests
import pandas as pd
import ta
from datetime import datetime

# ===========================
# CONFIGURACIÓN
# ===========================
# PARES a vigilar
PAIRS = ["BTCUSDT", "ETHUSDT"]

# Timeframes Binance
TF_EXEC = "4h"   # operativo
TF_CONFIRM = "1d"  # confirmación diaria

# Límite de velas
LIMIT = 300

# Telegram (pon tus datos)
TELEGRAM_TOKEN = "PON_AQUI_TU_TOKEN"   # ej: "123456789:AA...."
CHAT_ID = 123456789                    # ej: 5012345678 (número entero)

# Frecuencia de comprobación (segundos)
SLEEP_SECONDS = 300   # 5 minutos (mientras pruebas). Luego puedes poner 3600 (1h)

# Umbrales estrategia
RSI_OVERSOLD = 30
RSI_OVERBOUGHT = 70
VOL_MULT = 1.2  # volumen 4H > 1.2x media 20
# ===========================


# ---------------------------
# Utilidades
# ---------------------------
def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not CHAT_ID:
        print("ℹ️ Telegram no configurado.")
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    params = {"chat_id": CHAT_ID, "text": msg}
    try:
        requests.get(url, params=params, timeout=10)
    except Exception as e:
        print(f"⚠️ Error enviando a Telegram: {e}")


def get_klines_binance(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    """OHLCV desde la API pública de Binance (gratis, sin API key)."""
    url = "https://api.binance.com/api/v3/klines"
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    cols = ["open_time","open","high","low","close","volume",
            "close_time","quote_volume","trades","taker_base","taker_quote","ignore"]
    df = pd.DataFrame(data, columns=cols)
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    df["time"] = pd.to_datetime(df["open_time"], unit="ms")
    return df[["time","open","high","low","close","volume"]]


def macd_columns(close_series: pd.Series):
    macd_obj = ta.trend.MACD(close_series, window_slow=26, window_fast=12, window_sign=9)
    return macd_obj.macd(), macd_obj.macd_signal(), macd_obj.macd_diff()


def last_safe(series: pd.Series, n: int = 1):
    """Regresa últimos valores sin NaN (por seguridad)."""
    s = series.dropna()
    if len(s) < n:
        return None
    return s.iloc[-n:]


def load_state(path="last_signals.json"):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_state(state, path="last_signals.json"):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ No se pudo guardar estado: {e}")


# ---------------------------
# Estrategia (4H + confirmación 1D)
# ---------------------------
def analyze_pair(symbol: str) -> dict:
    # Datos 4H y 1D
    h4 = get_klines_binance(symbol, TF_EXEC, LIMIT)
    d1 = get_klines_binance(symbol, TF_CONFIRM, LIMIT)

    # Verifica datos
    if h4.empty or d1.empty:
        return {"pair": symbol, "signal": "SIN_DATOS"}

    # Indicadores 4H
    h4["RSI"] = ta.momentum.RSIIndicator(h4["close"], window=14).rsi()
    macd, macd_sig, macd_hist = macd_columns(h4["close"])
    h4["MACD"] = macd
    h4["MACD_SIG"] = macd_sig
    h4["MACD_HIST"] = macd_hist
    h4["VolMA20"] = h4["volume"].rolling(20).mean()

    # Indicadores 1D (confirmación)
    d1["RSI"] = ta.momentum.RSIIndicator(d1["close"], window=14).rsi()
    d_macd, d_sig, d_hist = macd_columns(d1["close"])
    d1["MACD"] = d_macd
    d1["MACD_SIG"] = d_sig
    d1["MACD_HIST"] = d_hist
    d1["EMA20"] = ta.trend.EMAIndicator(d1["close"], window=20).ema_indicator()

    # Últimas velas válidas
    if any(len(x.dropna()) == 0 for x in [h4["RSI"], h4["MACD"], h4["MACD_SIG"], h4["VolMA20"], d1["EMA20"]]):
        return {"pair": symbol, "signal": "SIN_DATOS"}

    # 4H actuales
    rsi4 = h4["RSI"].iloc[-1]
    macd4 = h4["MACD"].iloc[-1]
    macds4 = h4["MACD_SIG"].iloc[-1]
    hist4 = h4["MACD_HIST"].iloc[-1]
    vol4 = h4["volume"].iloc[-1]
    volma4 = h4["VolMA20"].iloc[-1]
    price4 = h4["close"].iloc[-1]

    # 1D confirmación
    rsi1d = d1["RSI"].iloc[-1]
    macd1d = d1["MACD"].iloc[-1]
    macds1d = d1["MACD_SIG"].iloc[-1]
    ema1d = d1["EMA20"].iloc[-1]
    price1d = d1["close"].iloc[-1]

    # Reglas LONG (4H) + confirmación 1D
    cond_long_4h = (rsi4 < RSI_OVERSOLD) and (macd4 > macds4) and (vol4 > VOL_MULT * volma4)
    cond_long_1d = (price1d > ema1d) and (macd1d >= macds1d) and (rsi1d < 50)

    # Reglas SHORT (4H) + confirmación 1D
    cond_short_4h = (rsi4 > RSI_OVERBOUGHT) and (macd4 < macds4) and (vol4 > VOL_MULT * volma4)
    cond_short_1d = (price1d < ema1d) and (macd1d <= macds1d) and (rsi1d > 50)

    if cond_long_4h and cond_long_1d:
        signal = "LONG"
    elif cond_short_4h and cond_short_1d:
        signal = "SHORT"
    else:
        signal = "SIN_SEÑAL"

    return {
        "pair": symbol,
        "signal": signal,
        "price_4h": price4,
        "RSI_4h": round(rsi4, 2),
        "MACD_4h": round(macd4, 5),
        "MACD_SIG_4h": round(macds4, 5),
        "HIST_4h": round(hist4, 5),
        "Vol_x_MA20_4h": round(vol4 / max(volma4, 1e-9), 2),
        "price_1d": price1d,
        "EMA20_1d": round(ema1d, 2),
        "RSI_1d": round(rsi1d, 2),
        "MACD_1d": round(macd1d, 5),
        "MACD_SIG_1d": round(macds1d, 5),
        "time": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    }


def format_messages(res: dict):
    """Dos formatos: completo y simple."""
    pair = res["pair"]
    if res["signal"] == "LONG":
        simple = f"📈 Señal LONG en {pair}"
    elif res["signal"] == "SHORT":
        simple = f"📉 Señal SHORT en {pair}"
    elif res["signal"] == "SIN_DATOS":
        simple = f"⚠️ {pair}: sin datos"
    else:
        simple = f"⚪ {pair}: sin señal"

    if res["signal"] in ["LONG", "SHORT"]:
        full = (
            f"⚡ {simple}\n"
            f"⏱ {res['time']}\n"
            f"4H → Precio: {res['price_4h']:.2f} | RSI: {res['RSI_4h']} | MACD: {res['MACD_4h']} / Sig: {res['MACD_SIG_4h']} | "
            f"Hist: {res['HIST_4h']} | Vol/MA20: {res['Vol_x_MA20_4h']}\n"
            f"1D → Precio: {res['price_1d']:.2f} | EMA20: {res['EMA20_1d']} | RSI: {res['RSI_1d']} | "
            f"MACD: {res['MACD_1d']} / Sig: {res['MACD_SIG_1d']}\n"
        )
    else:
        full = f"{simple} — {res.get('time','')}"
    return simple, full


def main():
    print("\n=== Bot de señales (Binance) | 4H con confirmación 1D | RSI + MACD + Vol + EMA20 ===")
    state = load_state()  # para no repetir alertas
    while True:
        try:
            for pair in PAIRS:
                res = analyze_pair(pair)

                # Mostrar en consola
                if res.get("signal") in ["LONG","SHORT"]:
                    print(f"[{res['time']}] {pair} → {res['signal']} | RSI4h:{res['RSI_4h']} MACD4h:{res['MACD_4h']} VolX:{res['Vol_x_MA20_4h']} | EMA1d:{res['EMA20_1d']} RSI1d:{res['RSI_1d']}")
                else:
                    print(f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}] {pair} → {res['signal']}")

            # Envío Telegram (solo nuevas señales distintas a la última registrada)
            if res.get("signal") in ["LONG", "SHORT"]:
                mensaje = f"""
🚀 *SEÑAL DETECTADA*
Par: {pair}
Tipo: {res['signal']}
RSI (4H): {res['RSI_4h']}
MACD (4H): {res['MACD_4h']}
EMA20 (1D): {res['EMA20_1d']}
Volumen/MA20: {res['Vol_x_MA20_4h']}
Hora: {res['time']}
"""
                print(mensaje)
                send_telegram_message(mensaje)

        except Exception as e:
            print(f"⚠️ Error en loop: {e}")

        print(f"⏳ Esperando {SLEEP_SECONDS//60} min...\n")
        time.sleep(SLEEP_SECONDS)


if __name__ == "__main__":
    main()

# === FUNCIÓN PARA ENVIAR MENSAJES A TELEGRAM ===
import requests

TELEGRAM_TOKEN = "7927043830:AAFgbgm5aSD-DhMh_OlREwdHmRcQR6wi0u4"
TELEGRAM_CHAT_ID = "6811984655"

def send_telegram_message(text):
    """Envía un mensaje al bot de Telegram"""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, data=payload)
    except Exception as e:
        print(f"Error enviando mensaje a Telegram: {e}")
