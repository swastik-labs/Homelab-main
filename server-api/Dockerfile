FROM python:3.13-alpine
LABEL org.opencontainers.image.title="homelab-dashboard"       org.opencontainers.image.version="1.0.0"       org.opencontainers.image.description="Lightweight homelab dashboard with incidents and phone alerts"
RUN apk add --no-cache tzdata
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=3000
WORKDIR /app
COPY app.py panels.py links.py incidents.py ./
COPY web ./web
EXPOSE 3000
HEALTHCHECK --interval=60s --timeout=5s --start-period=20s --retries=3 CMD wget -qO- http://127.0.0.1:3000/healthz || exit 1
CMD ["python", "-u", "app.py"]
