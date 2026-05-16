# syntax=docker/dockerfile:1.7

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libxcb1 libx11-6 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --default-timeout=1000 --retries=10 \
      --index-url https://download.pytorch.org/whl/cpu \
      torch==2.7.1 torchvision==0.22.1 && \
    pip install --default-timeout=1000 --retries=10 -r requirements.txt

COPY . .
