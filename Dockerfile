# Dockerfile — Sistem Absensi Face Recognition V2 (FastAPI + Uvicorn)
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Dependensi sistem untuk OpenCV
RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 \
        libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Pasang dependensi Python (facenet-pytorch dipasang dengan --no-deps)
COPY requirements.txt ./
RUN pip install --upgrade pip && \
    pip install -r requirements.txt && \
    pip install --no-deps facenet-pytorch==2.6.0

# Salin kode aplikasi + artefak non-sensitif (threshold.txt)
COPY . .

# Pastikan direktori runtime tersedia
RUN mkdir -p data/face_uploads exports logs

# Variabel penting di runtime/platform:
#   SECRET_KEY    = string acak panjang (wajib di produksi)
#   PORT          = port listen (default 8010)
#   COOKIE_SECURE = "true" bila dilayani lewat HTTPS
EXPOSE 8010

CMD ["sh", "-c", "python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8010}"]
