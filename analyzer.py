import math

def is_close(a, b, tolerance=0.02):
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tolerance


def _score_outcome(score):
    """Map a 'h-a' score string to '1' (home), '2' (away), or 'X' (draw)."""
    if not score or "-" not in score:
        return "?"
    try:
        h_str, a_str = score.split("-", 1)
        h, a = int(h_str.strip()), int(a_str.strip())
    except ValueError:
        return "?"
    if h > a:
        return "1"
    if h < a:
        return "2"
    return "X"


def htft_label(score_ht, score_ft):
    """Return İY/MS label like '1/2', '2/1', 'X/1' from half-time and full-time scores."""
    ht = _score_outcome(score_ht)
    ft = _score_outcome(score_ft)
    return f"{ht}/{ft}"

def check_side_change(opening, current):
    """
    Taraf değişimi kontrolü (Sadece 1 ve 2).
    Açılışta favori olan takım (oranı düşük olan), güncelde favori olmaktan çıkıp
    karşı takımın oranı daha düşük hale gelirse True döner.
    """
    if not opening or not current:
        return False
        
    op_1 = opening.get('home')
    op_2 = opening.get('away')
    cur_1 = current.get('home')
    cur_2 = current.get('away')

    if op_1 is None or op_2 is None or cur_1 is None or cur_2 is None:
        return False

    # Ev sahibi açılışta favori
    if op_1 < op_2:
        # Güncelde deplasman favori olduysa
        if cur_2 < cur_1:
            return True
            
    # Deplasman açılışta favori
    elif op_2 < op_1:
        # Güncelde ev sahibi favori olduysa
        if cur_1 < cur_2:
            return True

    return False

def check_odds_repeat(opening, current):
    """
    Oran tekrarı kontrolü (Yatay ve Dikey).
    Bulunan tekrarların listesini string olarak döner (boşsa None).
    """
    if not opening or not current:
        return None
        
    op_1 = opening.get('home')
    op_x = opening.get('draw')
    op_2 = opening.get('away')
    
    cur_1 = current.get('home')
    cur_x = current.get('draw')
    cur_2 = current.get('away')

    repeats = []

    # 1. Yatay Tekrar (Açılış)
    if op_1 is not None and op_x is not None and is_close(op_1, op_x, 0):
        repeats.append("Açılış Yatay Tekrar (1=X)")
    if op_1 is not None and op_2 is not None and is_close(op_1, op_2, 0):
        repeats.append("Açılış Yatay Tekrar (1=2)")
    if op_x is not None and op_2 is not None and is_close(op_x, op_2, 0):
        repeats.append("Açılış Yatay Tekrar (X=2)")

    # 2. Yatay Tekrar (Güncel)
    if cur_1 is not None and cur_x is not None and is_close(cur_1, cur_x, 0):
        repeats.append("Güncel Yatay Tekrar (1=X)")
    if cur_1 is not None and cur_2 is not None and is_close(cur_1, cur_2, 0):
        repeats.append("Güncel Yatay Tekrar (1=2)")
    if cur_x is not None and cur_2 is not None and is_close(cur_x, cur_2, 0):
        repeats.append("Güncel Yatay Tekrar (X=2)")

    # 3. Dikey Tekrar (Açılış ile Güncel aynı)
    if op_1 is not None and cur_1 is not None and is_close(op_1, cur_1, 0):
        repeats.append("Dikey Tekrar (Açılış 1 == Güncel 1)")
    if op_x is not None and cur_x is not None and is_close(op_x, cur_x, 0):
        repeats.append("Dikey Tekrar (Açılış X == Güncel X)")
    if op_2 is not None and cur_2 is not None and is_close(op_2, cur_2, 0):
        repeats.append("Dikey Tekrar (Açılış 2 == Güncel 2)")

    if repeats:
        return ", ".join(repeats)
    return None

def check_historical_match(current_match_opening, current_match_closing, historical_data):
    """
    Sürpriz maçlar JSON'u ile +/- 0.02 toleranslı eşleşme kontrolü.
    Eşleşen tarihsel maçların bilgisini döner.
    """
    matches_found = []
    
    if not historical_data:
        return matches_found

    cur_op_1 = current_match_opening.get('home')
    cur_op_x = current_match_opening.get('draw')
    cur_op_2 = current_match_opening.get('away')
    
    cur_cl_1 = current_match_closing.get('home')
    cur_cl_x = current_match_closing.get('draw')
    cur_cl_2 = current_match_closing.get('away')

    for hist_match in historical_data:
        hist_op = hist_match.get('oddsOpening') or {}
        hist_cl = hist_match.get('oddsClosing') or {}
        
        hist_op_1 = hist_op.get('home')
        hist_op_x = hist_op.get('draw')
        hist_op_2 = hist_op.get('away')
        
        hist_cl_1 = hist_cl.get('home')
        hist_cl_x = hist_cl.get('draw')
        hist_cl_2 = hist_cl.get('away')
        
        # 1. Açılış Oranları Eşleşmesi
        opening_match = False
        if (cur_op_1 is not None and hist_op_1 is not None and 
            cur_op_x is not None and hist_op_x is not None and 
            cur_op_2 is not None and hist_op_2 is not None):
            
            if (is_close(cur_op_1, hist_op_1, 0.02) and 
                is_close(cur_op_x, hist_op_x, 0.02) and 
                is_close(cur_op_2, hist_op_2, 0.02)):
                opening_match = True
                
        # 2. Güncel/Kapanış Oranları Eşleşmesi
        closing_match = False
        if (cur_cl_1 is not None and hist_cl_1 is not None and 
            cur_cl_x is not None and hist_cl_x is not None and 
            cur_cl_2 is not None and hist_cl_2 is not None):
            
            if (is_close(cur_cl_1, hist_cl_1, 0.02) and 
                is_close(cur_cl_x, hist_cl_x, 0.02) and 
                is_close(cur_cl_2, hist_cl_2, 0.02)):
                closing_match = True

        if opening_match or closing_match:
            ht_score = hist_match.get('scoreHalfTime') or '?'
            ft_score = hist_match.get('scoreFullTime') or '?'
            htft = htft_label(ht_score, ft_score)
            match_info = (
                f"{hist_match.get('homeTeam')} vs {hist_match.get('awayTeam')} "
                f"(İY {ht_score} - MS {ft_score} | İY/MS: {htft})"
            )
            if opening_match and closing_match:
                matches_found.append(f"Veritabanındaki '{match_info}' maçı ile HEM açılış HEM kapanış oranı uyuşuyor.")
            elif opening_match:
                matches_found.append(f"Veritabanındaki '{match_info}' maçının açılış oranı ile uyuşuyor.")
            elif closing_match:
                matches_found.append(f"Veritabanındaki '{match_info}' maçının kapanış oranı ile uyuşuyor.")

    return matches_found
