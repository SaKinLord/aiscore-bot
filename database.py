import sqlite3
import os

# Railway/Docker'da volume mount path'ini env ile gecirebilelim. Local'de eski yol.
DB_PATH = os.environ.get('DB_PATH', 'aiscore_bot.db')

def init_db():
    """Initializes the database and creates the necessary tables if they don't exist."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Maç bildirimlerini takip edeceğimiz tablo
    # match_id: AiScore'un verdiği eşsiz maç id'si
    # alert_type: 'side_change', 'odds_repeat_opening', 'odds_repeat_current', 'odds_repeat_vertical', 'historical_opening', 'historical_closing'
    # last_alert_time: Son bildirim zamanı
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS alerts (
            match_id TEXT,
            alert_type TEXT,
            last_alert_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (match_id, alert_type)
        )
    ''')
    
    conn.commit()
    conn.close()

def has_alert_been_sent(match_id, alert_type):
    """Checks if an alert of this type has already been sent for this match."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 1 FROM alerts WHERE match_id = ? AND alert_type = ?
    ''', (match_id, alert_type))
    result = cursor.fetchone()
    conn.close()
    return result is not None

def record_alert(match_id, alert_type):
    """Records that an alert has been sent for this match."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT OR REPLACE INTO alerts (match_id, alert_type, last_alert_time)
        VALUES (?, ?, CURRENT_TIMESTAMP)
    ''', (match_id, alert_type))
    conn.commit()
    conn.close()
