FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p static/qrcodes && \
    useradd --system --no-create-home appuser && \
    chown -R appuser /app/static/qrcodes
USER appuser

ENV APP_ENV=production \
    GUNICORN_BIND=0.0.0.0:5000
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=4).status == 200 else 1)"

# Same server and settings as the EC2 deployment (gunicorn, one worker).
CMD ["gunicorn", "--config", "gunicorn.conf.py", "wsgi:app"]
