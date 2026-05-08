import schedule
import time
import json
import os
from dotenv import load_dotenv

# Local'de .env'i yukle; Railway'de zaten env'ler injected geliyor (load_dotenv no-op).
load_dotenv()

from database import init_db, has_alert_been_sent, record_alert
from scraper import get_formatted_matches_with_odds
from analyzer import check_side_change, check_odds_repeat, check_historical_match
from telegram_bot import (
    notify_side_change,
    notify_odds_repeat,
    notify_historical_match,
    notify_scan_complete,
    notify_scan_table,
)

def load_historical_data():
    file_path = "historical_odds.json"
    if os.path.exists(file_path):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"JSON okuma hatası: {e}")
            return []
    print(f"{file_path} dosyası bulunamadı, sürpriz maç eşleşmesi yapılmayacak.")
    return []

def job():
    started_at = time.strftime('%Y-%m-%d %H:%M:%S')
    print(f"\n--- [ {started_at} ] Yeni Tarama Başlatılıyor ---")

    historical_data = load_historical_data()
    matches = get_formatted_matches_with_odds()

    stats = {
        'timestamp': started_at,
        'total_matches': 0,
        'side_change': 0,
        'odds_repeat': 0,
        'historical': 0,
        'total_alerts': 0,
    }

    if not matches:
        print("İncelenecek maç bulunamadı veya veri çekilemedi.")
        notify_scan_complete(stats)
        return

    # Bu taramada hangi maclar oran-tekrar / surpriz-eslesme tetikledi?
    # Sonda tek mesaj halinde Telegram tablosu olarak yollanir.
    triggered_repeat = []
    triggered_historical = []

    for match in matches:
        match_id = match.get('id')
        opening = match.get('oddsOpening')
        current = match.get('oddsClosing') # Güncel oranlar

        if not opening or not current:
            continue

        stats['total_matches'] += 1

        match_info = {
            'homeTeam': match.get('homeTeam', 'Ev Sahibi'),
            'awayTeam': match.get('awayTeam', 'Deplasman'),
            'league': match.get('league', 'Bilinmeyen Lig'),
            'startTime': match.get('startTime', ''),
        }

        # 1. Taraf Değişimi Kontrolü
        if check_side_change(opening, current):
            if not has_alert_been_sent(match_id, 'side_change'):
                notify_side_change(match_info, opening, current)
                record_alert(match_id, 'side_change')
                stats['side_change'] += 1

        # 2. Oran Tekrarı Kontrolü
        repeat_types = check_odds_repeat(opening, current)
        if repeat_types:
            if not has_alert_been_sent(match_id, 'odds_repeat'):
                notify_odds_repeat(match_info, opening, current, repeat_types)
                record_alert(match_id, 'odds_repeat')
                stats['odds_repeat'] += 1
                triggered_repeat.append(match_info)

        # 3. Tarihsel JSON Sürpriz Maç Kontrolü
        historical_alerts = check_historical_match(opening, current, historical_data)
        if historical_alerts:
            # Geçmiş maçların string halini hash'leyip veya uzunluğunu kontrol edip
            # aynı uyarıyı tekrar atmayı engelleyebiliriz
            alert_id = f"historical_{len(historical_alerts)}"
            if not has_alert_been_sent(match_id, alert_id):
                notify_historical_match(match_info, opening, current, historical_alerts)
                record_alert(match_id, alert_id)
                stats['historical'] += 1
                triggered_historical.append(match_info)

    stats['total_alerts'] = (
        stats['side_change'] + stats['odds_repeat'] + stats['historical']
    )
    print(
        f"--- Tarama Tamamlandı | maclar: {stats['total_matches']} | "
        f"taraf: {stats['side_change']} | tekrar: {stats['odds_repeat']} | "
        f"sürpriz: {stats['historical']} ---"
    )
    notify_scan_table(triggered_repeat, triggered_historical)
    notify_scan_complete(stats)

def main():
    print("AiScore Bot Başlatılıyor...")
    init_db()

    # İlk çalıştırmada hemen bir kez tara (state ısınması için)
    job()

    # Pre-match oranları en taze haliyle yakalamak için her 15-dk diliminin
    # son 3 dakikasında tarama: :12, :27, :42, :57.
    # Maçlar genellikle :00/:15/:30/:45'te başladığı için bu pencere ideal.
    target_minutes = [12, 27, 42, 57]
    for minute in target_minutes:
        schedule.every().hour.at(f":{minute:02d}").do(job)

    print(
        f"Sistem zamanlayıcısı aktif. Tarama saatleri: her saatin "
        f"{', '.join(f':{m:02d}' for m in target_minutes)} dakikalarinda."
    )

    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == "__main__":
    main()
