# Сборка: docker build -t ai-video-studio .
# Запуск (пример): docker run --rm -v "%cd%/storage:/app/storage" -v "%cd%/.env:/app/.env:ro" ai-video-studio python main.py --topic "Тема" --format short
#
# В контейнере нет интерактивного Chrome для ручной выгрузки ИИ-видео; для шага «гибридного» движка
# установлен Chromium (ищется через PATH). Для OAuth YouTube смонтируйте client_secrets.json и пробросьте порт при необходимости.

FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-dejavu-core \
    fonts-liberation \
    fontconfig \
    chromium \
    && fc-cache -f \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

# Тонкие модели Whisper при первом запуске качаются в кэш пользователя
ENV WHISPER_CACHE_DIR=/app/storage/whisper_cache

EXPOSE 8000

# По умолчанию справка CLI; в docker-compose переопределите command на uvicorn (дашборд).
CMD ["python", "main.py", "--help"]
