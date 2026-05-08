# Microsoft'un resmi Playwright Python image'i: Chromium + tum sistem
# bagimliliklari (libnss, libatk, fonts, vb.) onceden kurulu olarak gelir.
# Tag = playwright python lib surumu (requirements.txt ile uyumlu olmali).
FROM mcr.microsoft.com/playwright/python:v1.59.0-jammy

# Sistem zaman dilimini Europe/Istanbul'a sabitle. tzdata bazen image'da eksik
# olabiliyor; DEBIAN_FRONTEND=noninteractive ile interaktif promtu engelleyip
# install ediyoruz. /etc/localtime symlink + /etc/timezone POSIX standardi.
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && ln -sf /usr/share/zoneinfo/Europe/Istanbul /etc/localtime \
    && echo "Europe/Istanbul" > /etc/timezone \
    && dpkg-reconfigure --frontend noninteractive tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pip cache'ini katmana yazmadan paketleri kur. Playwright paketi zaten
# image'da var ama tekrar kurulumu zarar vermez (idempotent).
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# patchright kendi patched Chromium'unu kullanir; standart playwright
# binary'si patches'i ic-runtime ile bekledigi icin DNS/network sorunlari
# cikarabiliyor. Patchright'in kendi browser'ini indirip kuruyoruz.
RUN python -m patchright install --with-deps chromium

# Uygulama kaynaklari + tarihsel data dosyasi.
COPY *.py ./
COPY historical_odds.json ./

# Railway tarafindan inject edilecek env'lar:
#   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID  -> Telegram credential'lari
#   HEADLESS=true                         -> tarayiciyi gizli modda calistir
#   TZ=Europe/Istanbul                    -> schedule lib local time uyumu
#   DB_PATH=/data/aiscore_bot.db          -> volume mount uzerinde persist
ENV PYTHONUNBUFFERED=1 \
    HEADLESS=true \
    TZ=Europe/Istanbul

CMD ["python", "-u", "main.py"]
