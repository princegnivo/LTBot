# Image officielle Playwright : Chromium et ses dépendances système sont déjà installés.
# La version doit rester alignée sur « playwright== » de requirements.txt.
FROM mcr.microsoft.com/playwright/python:v1.56.0-noble
WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 DATA_DIR=/data TZ=UTC
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /data
VOLUME /data
# Le bot écrit un fichier « heartbeat » toutes les 30 s : s'il est périmé, le conteneur est déclaré en panne.
HEALTHCHECK --interval=60s --timeout=10s --start-period=90s --retries=3 \
  CMD python -c "import sys,time; sys.exit(0 if time.time()-int(open('/data/heartbeat').read())<120 else 1)"
CMD ["python", "main.py"]
