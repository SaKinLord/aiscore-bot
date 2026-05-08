import os
import time

from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup

# Cloudflare/headless detection'i atlatmak icin playwright-stealth
# (navigator.webdriver, canvas, plugin patches). Local'de yoksa sessizce skip.
try:
    from playwright_stealth import stealth_sync
except ImportError:  # pragma: no cover
    stealth_sync = None

BASE_URL = "https://www.aiscore.com"
MAX_MATCHES_PER_SCAN = 20  # cap per scan; combined with Sort-by-time toggle
ODDS_PAGE_TIMEOUT = 25000   # navigation (page.goto) timeout
ODDS_WAIT_TIMEOUT = 6000    # how long to wait for the 1X2 box before giving up
                            # — oransiz maclarda bos yere 25sn beklemeyi onler.
# Default tarayici modu: env var ile override edilebilir (Railway icin HEADLESS=true).
DEFAULT_HEADLESS = os.environ.get("HEADLESS", "false").lower() in ("1", "true", "yes")

# JS snippet that drives the vue-recycle-scroller from top to bottom and
# harvests every match-container that ever passes through the DOM. The recycler
# destroys nodes as they leave the viewport, so we must collect on each step.
# Accepts a targetCount: stops as soon as we have that many scheduled matches,
# so when the caller only needs the top 20 we don't waste time scrolling further.
_HARVEST_SCRIPT = r"""
async (targetCount) => {
  const seen = new Map();
  const target = (typeof targetCount === 'number' && targetCount > 0) ? targetCount : 0;

  const snapshot = () => {
    document.querySelectorAll('a.match-container').forEach(a => {
      const id = a.getAttribute('data-id');
      if (!id || seen.has(id)) return;
      const home = a.querySelector('[itemprop="homeTeam"]');
      const away = a.querySelector('[itemprop="awayTeam"]');
      if (!home || !away) return;
      const href = a.getAttribute('href') || '';
      const timeEl = a.querySelector('.time');
      const startTime = timeEl ? (timeEl.innerText || '').trim() : '';
      // Skip live / finished matches: their status text is a minute number
      // ("42"), "HT", "FT", etc. Only truly-scheduled matches show "-" or empty.
      const statusEl = a.querySelector('.status');
      const status = statusEl ? (statusEl.innerText || '').trim() : '';
      if (status && status !== '-') return;
      let country = '', league = '';
      const comp = a.closest('.comp-container');
      if (comp) {
        const titleEl = comp.querySelector('.title');
        if (titleEl) {
          const c = titleEl.querySelector('.country-name');
          const l = titleEl.querySelector('.compe-name');
          if (c) country = (c.innerText || '').trim().replace(/:$/, '').trim();
          if (l) league = (l.innerText || '').trim();
        }
      }
      seen.set(id, {
        id,
        href,
        homeTeam: (home.innerText || '').trim(),
        awayTeam: (away.innerText || '').trim(),
        country,
        league,
        startTime,
      });
    });
  };

  // The scheduled list lives inside a vue-recycle-scroller in page-mode,
  // so the page itself is what scrolls.
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  // First pass at the top.
  window.scrollTo(0, 0);
  await sleep(400);
  snapshot();

  let stable = 0;
  let lastY = -1;
  for (let i = 0; i < 400; i++) {
    if (target && seen.size >= target) break;
    const before = window.scrollY;
    window.scrollBy(0, Math.max(400, window.innerHeight * 0.85));
    await sleep(350);
    snapshot();
    const after = window.scrollY;
    const atBottom =
      after + window.innerHeight >=
      (document.documentElement.scrollHeight - 4);
    if (Math.abs(after - before) < 4 || after === lastY) {
      stable++;
    } else {
      stable = 0;
    }
    lastY = after;
    if (atBottom && stable >= 2) break;
    if (stable >= 6) break;
  }

  // Final sweep in case the last item rendered after the loop exit.
  await sleep(500);
  snapshot();
  return Array.from(seen.values());
}
"""


def _parse_float(text):
    if text is None:
        return None
    t = text.strip()
    if not t or t == "-":
        return None
    try:
        return float(t)
    except ValueError:
        return None


def _parse_odds_box(box):
    """
    Parse an openingBg1 / preMatchBg1 box into {'home','draw','away'} for the 1X2 market.
    Returns None if the box is not a 1X2 row (e.g. it contains a handicap line)
    or if any value is missing.
    """
    if box is None:
        return None
    items = box.select(".oddItems")
    if len(items) < 3:
        return None
    vals = []
    for it in items[:3]:
        # Handicap / Goals / Corners columns carry a `.handicap` span — skip those rows.
        if it.select_one(".handicap"):
            return None
        v = _parse_float(it.get_text(" ", strip=True))
        if v is None:
            return None
        vals.append(v)
    return {"home": vals[0], "draw": vals[1], "away": vals[2]}


def _extract_opening_and_closing(html):
    """
    From a match's /odds page, return (opening, closing) dicts for the 1X2 market.
    Strategy: take the first bookmaker row whose opening + pre-match boxes both
    parse as 1X2 odds (3 floats). The first column of the bookmaker row is 1X2;
    later columns are Handicap / Goals / Corners and are filtered out by
    _parse_odds_box.
    """
    soup = BeautifulSoup(html, "html.parser")
    opening_boxes = soup.select(".openingBg1")
    pre_match_boxes = soup.select(".preMatchBg1")

    for ob, pb in zip(opening_boxes, pre_match_boxes):
        opening = _parse_odds_box(ob)
        closing = _parse_odds_box(pb)
        if opening and closing:
            return opening, closing
    return None, None


def _build_odds_url(href):
    """
    Some match links are bare (`/match-slug/id`) and some are deep-linked
    (`/match-slug/id/h2h`). Strip any trailing subpage so we always end up at
    `/match-slug/id/odds`.
    """
    if not href:
        return None
    path = href.split("?")[0].split("#")[0].strip("/")
    parts = path.split("/")
    if len(parts) < 2 or not parts[0].startswith("match-"):
        return None
    return f"{BASE_URL}/{parts[0]}/{parts[1]}/odds"


def _normalize_harvested(raw_matches):
    """Turn the raw JS harvest output into the shape main.py expects."""
    out = []
    for m in raw_matches or []:
        match_id = (m.get("id") or "").strip()
        odds_url = _build_odds_url(m.get("href") or "")
        if not match_id or not odds_url:
            continue
        country = (m.get("country") or "").strip()
        league = (m.get("league") or "").strip()
        league_label = f"{country}: {league}".strip(": ").strip()
        out.append(
            {
                "id": match_id,
                "homeTeam": (m.get("homeTeam") or "").strip(),
                "awayTeam": (m.get("awayTeam") or "").strip(),
                "league": league_label or "Unknown League",
                "startTime": (m.get("startTime") or "").strip(),
                "oddsUrl": odds_url,
            }
        )
    return out


def _start_time_sort_key(m):
    """Sort by HH:MM string; matches without a parseable time go to the end."""
    raw = (m.get("startTime") or "").strip()
    parts = raw.split(":")
    if len(parts) >= 2:
        try:
            return (int(parts[0]), int(parts[1]))
        except ValueError:
            pass
    return (99, 99)


def _parse_home_page(html):
    """
    Static fallback parser used by tests against captured HTML dumps.
    Live scraping uses _harvest_matches_via_scroll instead, since the home
    page virtualizes its match list and only renders a small batch at a time.
    """
    soup = BeautifulSoup(html, "html.parser")
    matches = []

    for comp in soup.select(".comp-container"):
        title = comp.select_one(".title")
        country = ""
        league = ""
        if title:
            country_el = title.select_one(".country-name")
            league_el = title.select_one(".compe-name")
            if country_el:
                country = country_el.get_text(strip=True).rstrip(":")
            if league_el:
                league = league_el.get_text(strip=True)

        for row in comp.select("a.match-container"):
            home_el = row.find(attrs={"itemprop": "homeTeam"})
            away_el = row.find(attrs={"itemprop": "awayTeam"})
            if not home_el or not away_el:
                continue

            home_team = home_el.get_text(strip=True)
            away_team = away_el.get_text(strip=True)
            href = row.get("href", "").strip()
            match_id = row.get("data-id", "").strip()

            odds_url = _build_odds_url(href)
            if not match_id or not odds_url:
                continue

            league_label = f"{country}: {league}".strip(": ").strip()
            matches.append(
                {
                    "id": match_id,
                    "homeTeam": home_team,
                    "awayTeam": away_team,
                    "league": league_label or "Unknown League",
                    "oddsUrl": odds_url,
                }
            )

    return matches


def fetch_live_and_upcoming_matches(max_matches=MAX_MATCHES_PER_SCAN, headless=None):
    """
    Scrape AiScore for scheduled (upcoming) matches and pull their real
    opening + current (pre-match) 1X2 odds from each match's /odds page.
    Returns a list of dicts shaped for analyzer/main.py.
    """
    if headless is None:
        headless = DEFAULT_HEADLESS
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            screen={"width": 1920, "height": 1080},
            device_scale_factor=1,
            is_mobile=False,
            has_touch=False,
            # AiScore decides "today" using the browser's locale/timezone — without
            # this it falls back to UTC and the Scheduled list often comes back empty.
            locale="tr-TR",
            timezone_id="Europe/Istanbul",
        )
        # Belt-and-suspenders: some sites sniff navigator/touch events even when
        # the UA looks desktop. Spoof the desktop-shaped values before any page JS runs.
        context.add_init_script(
            """
            Object.defineProperty(navigator, 'maxTouchPoints', { get: () => 0 });
            Object.defineProperty(navigator, 'userAgentData', { get: () => undefined });
            Object.defineProperty(navigator, 'platform', { get: () => 'Win32' });
            """
        )
        page = context.new_page()

        # Cloudflare/headless detection'a karsi stealth patch'i (varsa).
        if stealth_sync is not None:
            try:
                stealth_sync(page)
                print("Scraper: stealth_sync uygulandi.")
            except Exception as e:
                print(f"Scraper Uyarisi: stealth_sync uygulanamadi: {e}")
        else:
            print("Scraper Uyarisi: playwright_stealth import edilemedi; stealth pas geciliyor.")

        def _prepare_scheduled_list():
            """Scheduled tab'ina geç, Sort-by-time'i aç, ilk batch'i hasat et.
            Reload retry'da çağrılabilsin diye fonksiyon olarak ayrıldı."""
            print("Scraper: 'Scheduled' sekmesine geciliyor...")
            try:
                page.wait_for_selector(".changeItem", state="attached", timeout=30000)
            except Exception as e:
                print(f"Scraper Uyarisi: Tab serisi yuklenmedi: {e}")

            clicked = False
            try:
                page.locator(".changeItem", has_text="Scheduled").first.click(timeout=10000)
                clicked = True
            except Exception as e:
                print(f"Scraper Uyarisi: locator click basarisiz, JS fallback: {e}")
            if not clicked:
                try:
                    clicked = page.evaluate(
                        """() => {
                            const els = Array.from(document.querySelectorAll('.changeItem'));
                            const t = els.find(e => /^scheduled\\b/i.test((e.textContent || '').trim()));
                            if (t) { t.click(); return true; }
                            return false;
                        }"""
                    )
                except Exception as e:
                    print(f"Scraper Uyarisi: JS click hatasi: {e}")

            if clicked:
                try:
                    page.wait_for_function(
                        """() => {
                            const els = Array.from(document.querySelectorAll('.changeItem'));
                            const a = els.find(e => e.className.includes('activ')
                                && /^scheduled\\b/i.test((e.textContent || '').trim()));
                            return !!a;
                        }""",
                        timeout=10000,
                    )
                    print("Scraper: Scheduled tab aktif.")
                except Exception:
                    print("Scraper Uyarisi: Scheduled tab aktiflesmedi (devam ediliyor).")
            else:
                print("Scraper Hatasi: Scheduled tab tiklanamadi.")

            try:
                page.wait_for_function(
                    "() => document.querySelectorAll('a.match-container').length > 0",
                    timeout=20000,
                )
            except Exception:
                print("Scraper Uyarisi: Scheduled listesi 20s icinde DOM'a inmedi (gerckten bos olabilir).")
            page.wait_for_timeout(2000)

            if "Just a moment" in page.content():
                print("Scraper Hatasi: Cloudflare asilamadi.")
                return None

            print("Scraper: 'Sort by time' kutusu isaretleniyor...")
            sort_state = page.evaluate(
                """() => {
                    const box = document.querySelector('.sortByBox');
                    if (!box) return 'no-box';
                    const cb = box.querySelector('.el-checkbox__input');
                    if (cb && cb.className.includes('is-checked')) return 'already-on';
                    const target = box.querySelector('.el-checkbox__inner')
                        || box.querySelector('.sortByText')
                        || box;
                    target.click();
                    return 'clicked';
                }"""
            )
            print(f"Scraper: sort-by-time sonucu = {sort_state}")
            if sort_state == 'clicked':
                try:
                    page.wait_for_function(
                        """() => {
                            const cb = document.querySelector('.sortByBox .el-checkbox__input');
                            return cb && cb.className.includes('is-checked');
                        }""",
                        timeout=5000,
                    )
                except Exception:
                    print("Scraper Uyarisi: sort-by-time checkbox aktif konuma gectigi dogrulanamadi.")
                page.wait_for_timeout(1500)

            target_count = max_matches if (max_matches and max_matches > 0) else 0
            if target_count:
                print(f"Scraper: Tepedeki ilk {target_count} mac toplaniyor...")
            else:
                print("Scraper: Tum maclari toplamak icin liste kaydiriliyor...")
            try:
                return page.evaluate(_HARVEST_SCRIPT, target_count)
            except Exception as e:
                print(f"Scraper Hatasi: Mac listesi toplanamadi: {e}")
                return None

        def _is_desktop_render():
            """Sayfa masaüstü mü mobil mi yüklenmiş onu söyler.
            URL'e ve masaüstüne özgü iki konteynerin varlığına bakar."""
            try:
                url = page.url or ""
            except Exception:
                url = ""
            on_mobile_host = "m.aiscore.com" in url.lower()
            try:
                desktop_markers = page.evaluate(
                    """() => ({
                        changTabBox: !!document.querySelector('.changTabBox'),
                        sortByBox: !!document.querySelector('.sortByBox'),
                    })"""
                )
            except Exception:
                desktop_markers = {"changTabBox": False, "sortByBox": False}
            return (
                not on_mobile_host
                and desktop_markers.get("changTabBox", False)
            ), {
                "url": url,
                "on_mobile_host": on_mobile_host,
                **desktop_markers,
            }

        # Cloudflare Managed Challenge yok olana + aiscore DOM gelene kadar
        # bekleyen akilli bir wait. Cloudflare JS challenge'i 5-25sn surebilir.
        _CF_WAIT_SCRIPT = """() => {
            const txt = (document.body && document.body.innerText) || '';
            const cfActive = !!document.querySelector('[name="cf-turnstile-response"]')
                || /Just a moment|Dogrulaniyor|Doğrulanıyor|Verifying you are human|cf-spinner/i.test(txt);
            if (cfActive) return false;          // hala challenge ekraninda
            return !!document.querySelector('.changTabBox');  // gercek site geldi
        }"""

        def _wait_past_cloudflare(timeout_ms):
            """Cloudflare challenge'inin kendiliginden cozulmesini bekler.
            True: cozuldu ve aiscore DOM mevcut. False: timeout."""
            try:
                page.wait_for_function(_CF_WAIT_SCRIPT, timeout=timeout_ms)
                return True
            except Exception:
                return False

        try:
            print("Scraper: AiScore ana sayfasi aciliyor...")
            page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)

            # Cloudflare challenge'in kendi kendine cozulmesi icin yeterli sure ver.
            print("Scraper: Cloudflare/Vue render bekleniyor (45sn'e kadar)...")
            cf_ok = _wait_past_cloudflare(45000)
            if cf_ok:
                print("Scraper: site DOM'a indi (Cloudflare gecildi veya hic yoktu).")
            is_desktop, render_info = _is_desktop_render()
            if not is_desktop:
                print(f"Scraper Uyarisi: masaüstü render dogrulanamadi {render_info}; tekrar yukleniyor...")
                try:
                    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
                    print("Scraper: 2. denemede Cloudflare/Vue bekleniyor (45sn)...")
                    _wait_past_cloudflare(45000)
                    is_desktop, render_info = _is_desktop_render()
                except Exception as e:
                    print(f"Scraper Uyarisi: ikinci goto basarisiz: {e}")
                if is_desktop:
                    print("Scraper: ikinci denemede masaüstü render geldi.")
                else:
                    print(f"Scraper Uyarisi: hala masaüstü degil {render_info}; yine de devam ediliyor.")
                    # Tani amacli: page HTML + screenshot'i volume'e dump et,
                    # ardindan Telegram'a yolla (Railway'e shell ile baglanmadan
                    # Cloudflare'in ne gosterdigini gorebilelim).
                    diag_dir = "/data" if os.path.isdir("/data") else os.getcwd()
                    try:
                        ts = time.strftime("%Y%m%d_%H%M%S")
                        html_path = os.path.join(diag_dir, f"render_fail_{ts}.html")
                        png_path = os.path.join(diag_dir, f"render_fail_{ts}.png")
                        with open(html_path, "w", encoding="utf-8") as f:
                            f.write(page.content())
                        page.screenshot(path=png_path, full_page=True)
                        print(f"Scraper Tani: dump yazildi -> {html_path} ; {png_path}")
                        try:
                            from telegram_bot import send_document
                            caption = f"render_fail @ {ts}\n{render_info}"
                            send_document(png_path, caption=caption)
                            send_document(html_path, caption=f"render_fail HTML @ {ts}")
                        except Exception as e:
                            print(f"Scraper Uyarisi: tani dosyalari Telegram'a yollanamadi: {e}")
                    except Exception as e:
                        print(f"Scraper Uyarisi: tani dump basarisiz: {e}")

            raw_matches = _prepare_scheduled_list()

            # Boş hasat genelde mobil/eksik render veya geç hidrasyon kaynaklı.
            # Sayfayı 1 kere reload edip yeniden dene; intermitting durumlar bu şekilde kırılır.
            if not raw_matches:
                print("Scraper: ilk denemede 0 mac. Sayfa yenilenip tekrar denenecek...")
                try:
                    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=60000)
                    raw_matches = _prepare_scheduled_list()
                except Exception as e:
                    print(f"Scraper Uyarisi: reload basarisiz: {e}")

            if not raw_matches:
                browser.close()
                return results

            match_list = _normalize_harvested(raw_matches)
            match_list.sort(key=_start_time_sort_key)
            print(f"Scraper: Ana sayfada {len(match_list)} mac toplandi (basla saatine gore siralandi).")

            if max_matches and max_matches > 0:
                match_list = match_list[:max_matches]
                print(f"Scraper: ilk {len(match_list)} mac taranacak.")

            for idx, m in enumerate(match_list, start=1):
                odds_url = m["oddsUrl"]
                try:
                    print(
                        f"  [{idx}/{len(match_list)}] {m['homeTeam']} vs "
                        f"{m['awayTeam']} -> oranlar cekiliyor"
                    )
                    page.goto(odds_url, wait_until="domcontentloaded", timeout=ODDS_PAGE_TIMEOUT)
                    # The odds DOM nodes are rendered hidden until the user scrolls/expands,
                    # so wait for them to be *attached* (not necessarily visible). Bu
                    # bekleme oranlar acilana kadar surer; oran yoksa varsayilan 25sn
                    # tamamen bos gecerdi, bu yuzden ayri/kisa bir timeout kullaniyoruz.
                    try:
                        page.wait_for_selector(
                            ".openingBg1", state="attached", timeout=ODDS_WAIT_TIMEOUT
                        )
                    except Exception:
                        print("    ! oran tablosu yuklenmedi, atlandi")
                        continue
                    page.wait_for_timeout(800)
                    odds_html = page.content()
                except Exception as e:
                    print(f"    ! oran sayfasi yuklenemedi: {e}")
                    continue

                opening, closing = _extract_opening_and_closing(odds_html)
                if not opening or not closing:
                    print("    ! 1X2 oran satiri bulunamadi, atlandi")
                    continue

                results.append(
                    {
                        "id": m["id"],
                        "homeTeam": m["homeTeam"],
                        "awayTeam": m["awayTeam"],
                        "league": m["league"],
                        "startTime": m.get("startTime", ""),
                        "oddsOpening": opening,
                        "oddsClosing": closing,
                    }
                )
        except Exception as e:
            print(f"Scraper Playwright Hatasi: {e}")
        finally:
            browser.close()

    print(f"Scraper: {len(results)} mac icin acilis+guncel oranlar elde edildi.")
    return results


def get_formatted_matches_with_odds():
    return fetch_live_and_upcoming_matches()


if __name__ == "__main__":
    print("Scraper test ediliyor...")
    sonuclar = fetch_live_and_upcoming_matches(max_matches=5)
    print(f"Bulunan mac sayisi: {len(sonuclar)}")
    for s in sonuclar:
        print(s)
