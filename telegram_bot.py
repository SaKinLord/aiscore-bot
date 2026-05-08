import os
import requests
import time

# Sadece env'den okur. Local'de main.py'in load_dotenv() ile yukledigi .env
# kullanilir; production'da Railway Variables panelinden gelir.
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# Telegram chat başına ~1 msg/sn limiti var. Üzerine çıkılınca 429 dönüp
# kuyruğa atıyor ve toplu gönderiyor; bu yüzden gönderimler arasında
# küçük bir bekleme uyguluyoruz.
_MIN_SEND_INTERVAL = 1.2
_last_send_ts = 0.0


def send_message(text):
    """Telegram üzerinden mesaj gönderir (throttle + 429 retry)."""
    global _last_send_ts

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"Telegram env'leri (TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID) bos. Mesaj atilmadi:\n{text}")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }

    elapsed = time.monotonic() - _last_send_ts
    if elapsed < _MIN_SEND_INTERVAL:
        time.sleep(_MIN_SEND_INTERVAL - elapsed)

    for attempt in range(4):
        try:
            response = requests.post(url, json=payload, timeout=15)
        except Exception as e:
            print(f"Telegram bağlantı hatası: {e}")
            time.sleep(1.5)
            continue

        if response.status_code == 200:
            _last_send_ts = time.monotonic()
            print("Telegram bildirimi başarıyla gönderildi.")
            return True

        if response.status_code == 429:
            try:
                retry_after = float(
                    response.json().get("parameters", {}).get("retry_after", 2)
                )
            except Exception:
                retry_after = 2.0
            print(f"Telegram rate-limit. {retry_after:.1f}s bekleniyor.")
            time.sleep(retry_after + 0.2)
            continue

        print(f"Telegram bildirim hatası: {response.text}")
        _last_send_ts = time.monotonic()
        return False

    print("Telegram bildirimi 4 denemede gönderilemedi.")
    _last_send_ts = time.monotonic()
    return False

def format_odds(odds_dict):
    if not odds_dict:
        return "Bilinmiyor"
    return f"1: {odds_dict.get('home', '-')} | X: {odds_dict.get('draw', '-')} | 2: {odds_dict.get('away', '-')}"


def send_document(path, caption=""):
    """Telegram'a sendDocument ile bir dosya yollar (HTML, PNG, log vb.).
    Tani amacli kullanim: render-fail dump'larini Railway'den disari cikarmak."""
    global _last_send_ts
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"Telegram env'leri bos. Dosya gonderilmedi: {path}")
        return False
    if not os.path.isfile(path):
        print(f"send_document: dosya bulunamadi: {path}")
        return False

    elapsed = time.monotonic() - _last_send_ts
    if elapsed < _MIN_SEND_INTERVAL:
        time.sleep(_MIN_SEND_INTERVAL - elapsed)

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    try:
        with open(path, "rb") as fh:
            files = {"document": (os.path.basename(path), fh)}
            data = {"chat_id": TELEGRAM_CHAT_ID}
            if caption:
                data["caption"] = caption[:1024]  # Telegram caption limiti
            response = requests.post(url, data=data, files=files, timeout=60)
    except Exception as e:
        print(f"send_document baglanti hatasi: {e}")
        return False

    _last_send_ts = time.monotonic()
    if response.status_code == 200:
        print(f"Telegram dosya yollandi: {path}")
        return True
    print(f"send_document hatasi {response.status_code}: {response.text[:200]}")
    return False


def _start_line(match_info):
    st = (match_info.get('startTime') or '').strip()
    return f"🕒 <b>Başlama:</b> {st}\n" if st else ""


def notify_side_change(match_info, opening, current):
    text = (
        f"🔄 <b>TARAF DEĞİŞİMİ BİLDİRİMİ</b>\n\n"
        f"⚽ <b>Maç:</b> {match_info['homeTeam']} vs {match_info['awayTeam']}\n"
        f"🏆 <b>Lig:</b> {match_info.get('league', 'Bilinmiyor')}\n"
        f"{_start_line(match_info)}"
        f"\n📉 <b>Açılış Oranları:</b>\n{format_odds(opening)}\n"
        f"📈 <b>Güncel Oranlar:</b>\n{format_odds(current)}\n\n"
        f"<i>Açılışta favori olan takım güncelde favori olmaktan çıktı!</i>"
    )
    send_message(text)

def notify_odds_repeat(match_info, opening, current, repeat_types):
    text = (
        f"🔁 <b>ORAN TEKRARI BİLDİRİMİ</b>\n\n"
        f"⚽ <b>Maç:</b> {match_info['homeTeam']} vs {match_info['awayTeam']}\n"
        f"🏆 <b>Lig:</b> {match_info.get('league', 'Bilinmiyor')}\n"
        f"{_start_line(match_info)}"
        f"\n📉 <b>Açılış Oranları:</b>\n{format_odds(opening)}\n"
        f"📈 <b>Güncel Oranlar:</b>\n{format_odds(current)}\n\n"
        f"⚠️ <b>Tespit Edilen Tekrarlar:</b>\n{repeat_types}"
    )
    send_message(text)

def notify_historical_match(match_info, opening, current, match_alerts):
    alerts_text = "\n".join([f"- {alert}" for alert in match_alerts])
    text = (
        f"🚨 <b>SÜRPRİZ MAÇ EŞLEŞMESİ!</b>\n\n"
        f"⚽ <b>Maç:</b> {match_info['homeTeam']} vs {match_info['awayTeam']}\n"
        f"🏆 <b>Lig:</b> {match_info.get('league', 'Bilinmiyor')}\n"
        f"{_start_line(match_info)}"
        f"\n📉 <b>Açılış Oranları:</b>\n{format_odds(opening)}\n"
        f"📈 <b>Güncel Oranlar:</b>\n{format_odds(current)}\n\n"
        f"🔍 <b>Eşleşmeler (+/- 0.02 tolerans):</b>\n{alerts_text}"
    )
    send_message(text)


def notify_scan_complete(stats):
    """stats: dict with keys timestamp, total_matches, side_change, odds_repeat, historical."""
    text = (
        f"✅ <b>TARAMA TAMAMLANDI</b>\n\n"
        f"🕒 <b>Saat:</b> {stats.get('timestamp', '-')}\n"
        f"⚽ <b>Taranan maç:</b> {stats.get('total_matches', 0)}\n\n"
        f"🔄 <b>Taraf değişimi:</b> {stats.get('side_change', 0)}\n"
        f"🔁 <b>Oran tekrarı:</b> {stats.get('odds_repeat', 0)}\n"
        f"🚨 <b>Sürpriz maç eşleşmesi:</b> {stats.get('historical', 0)}\n\n"
        f"📨 <b>Toplam yeni uyarı:</b> {stats.get('total_alerts', 0)}"
    )
    send_message(text)


def _format_match_row(m):
    """Tek maç için tablo satırı: '14:30 — Home vs Away (Lig)'."""
    start = (m.get('startTime') or '').strip() or '--:--'
    home = m.get('homeTeam') or 'Ev'
    away = m.get('awayTeam') or 'Deplasman'
    league = m.get('league') or ''
    line = f"• {start} — {home} vs {away}"
    if league:
        line += f" ({league})"
    return line


def notify_scan_table(repeat_list, historical_list):
    """Bu taramada tetiklenen oran-tekrar ve sürpriz-eşleşme maçlarını tek mesajda yollar.
    Her iki liste de boşsa mesaj atılmaz."""
    if not repeat_list and not historical_list:
        return

    sections = ["📋 <b>BU TARAMADA TETİKLENENLER</b>"]
    if repeat_list:
        sections.append(f"\n🔁 <b>Oran Tekrarı</b> ({len(repeat_list)}):")
        sections.extend(_format_match_row(m) for m in repeat_list)
    if historical_list:
        sections.append(f"\n🚨 <b>Sürpriz Eşleşme</b> ({len(historical_list)}):")
        sections.extend(_format_match_row(m) for m in historical_list)

    send_message("\n".join(sections))
