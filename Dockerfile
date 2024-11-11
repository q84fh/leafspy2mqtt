FROM python:3.13-alpine AS build

WORKDIR /app
RUN python -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"
COPY requirements.txt .
RUN pip install -r requirements.txt

FROM python:3.13-alpine
WORKDIR /app/venv
COPY --from=build /app/venv /app/venv
COPY leafspy2mqtt.py /app/leafspy2mqtt.py
ENV PATH="/app/venv/bin:$PATH"
EXPOSE 8888

ENTRYPOINT ["/app/venv/bin/python", "/app/leafspy2mqtt.py"]
