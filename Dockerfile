# syntax=docker/dockerfile:1

# The UI is a real React app, so it is compiled in its own stage. Nothing from
# node ships in the runtime image.
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build


FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py ./
COPY musiclib.py ./
COPY --from=ui /ui/dist ./static

EXPOSE 8000

# Single worker: the background collector thread must not run once per worker.
CMD ["gunicorn", "--workers", "1", "--threads", "8", "--timeout", "120", \
     "--bind", "0.0.0.0:8000", "app:app"]
