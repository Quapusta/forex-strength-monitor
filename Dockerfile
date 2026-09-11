# syntax=docker/dockerfile:1

FROM python:3.12-slim

# Системні залежності, потрібні для збірки деяких Python-пакетів
# (наприклад, cryptography/twisted) на Linux.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        libssl-dev \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Спочатку копіюємо тільки requirements.txt, щоб Docker кешував шар
# з залежностями окремо від коду — прискорює повторні збірки.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Тепер копіюємо сам код застосунку.
COPY forex_strength ./forex_strength

# Cloud Run Job запускає контейнер один раз до завершення процесу,
# не тримає HTTP-сервер — тому просто виконуємо worker напряму.
RUN useradd --create-home --uid 1000 worker
USER worker

CMD ["python", "-m", "forex_strength.run"]
