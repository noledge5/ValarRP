# ValarRP im Browser (Schritt 3, Entscheidungen 2, 175 und 188). Daten liegen außerhalb des Abbilds in /daten.
# Die CI baut das Abbild nach jedem Merge und legt es nach ghcr.io/noledge5/valarrp; die Synology holt es dort.
FROM python:3.13-slim
LABEL org.opencontainers.image.source=https://github.com/noledge5/ValarRP

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Version für die Einstellungen (Entscheidung 211): Die CI setzt Commit und Bauzeit; ohne sie steht „Entwicklung“ da.
ARG VALAR_VERSION=""
ARG VALAR_BUILT=""
ENV VALAR_VERSION=$VALAR_VERSION VALAR_BUILT=$VALAR_BUILT
RUN pip install --no-cache-dir "pydantic>=2.7,<3" "pyyaml>=6,<7"

WORKDIR /app
COPY valar/ valar/
COPY welt/ welt/
RUN useradd --system --uid 1000 --no-create-home valar && mkdir /daten && chown valar /daten
USER valar

VOLUME /daten
EXPOSE 8000
HEALTHCHECK --interval=1m --timeout=5s CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/stil.css', timeout=3)"]
CMD ["python", "-m", "valar.web", "--daten", "/daten", "--adresse", "0.0.0.0", "--port", "8000"]
