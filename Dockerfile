FROM python:3.15-alpine AS build

WORKDIR /app
RUN python -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.15-alpine
ARG VERSION=dev
COPY --from=build /app/venv /app/venv
COPY leafspy2mqtt.py /app/leafspy2mqtt.py
ENV PATH="/app/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    LEAFSPY2MQTT_VERSION=$VERSION
USER 65534:65534
EXPOSE 8888
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8888/healthz || exit 1

ENTRYPOINT ["/app/venv/bin/python", "/app/leafspy2mqtt.py"]
