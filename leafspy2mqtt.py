#!/usr/bin/env python
"""Bridge between LeafSpy Pro "server" upload and an MQTT broker.

LeafSpy sends one HTTP GET per interval with all values in the query string.
We authenticate it, normalise the values, drop the credentials and publish:

* the whole sample as JSON to ``MQTT_TOPIC`` (retained, backwards compatible),
* Home Assistant MQTT discovery configs (one device per VIN),
* an availability topic (LWT) so HA marks entities unavailable when we die.

Optionally (``INFLUXDB_URL``) every sample is also written to InfluxDB 1.x, 2.x or 3.x
as line protocol; MQTT can be switched off with ``MQTT_ENABLED=false``.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import math
import os
import signal
import ssl
import time
from collections import OrderedDict
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlencode

import paho.mqtt.client as mqtt
import tornado.httpclient
import tornado.web
from dotenv import load_dotenv

log = logging.getLogger("leafspy2mqtt")

# Set at image build time from the git tag (see Dockerfile / CI); "dev" when run from source.
VERSION = os.getenv("LEAFSPY2MQTT_VERSION") or "dev"

# Query parameters that must never leave this process.
SECRET_FIELDS = {"user", "pass"}

# key -> discovery fragment. Values are published in the JSON state message;
# every entry becomes a Home Assistant entity reading ``value_json.<key>``.
SENSORS: dict[str, dict[str, Any]] = {
    "SOC": {"name": "State of charge", "unit_of_measurement": "%", "device_class": "battery",
            "state_class": "measurement", "suggested_display_precision": 1},
    "Gids": {"name": "GIDs", "icon": "mdi:battery-high", "state_class": "measurement"},
    "AHr": {"name": "Battery capacity", "unit_of_measurement": "Ah", "icon": "mdi:battery-heart-variant",
            "state_class": "measurement", "suggested_display_precision": 2},
    "SOH": {"name": "State of health", "unit_of_measurement": "%", "icon": "mdi:battery-heart",
            "state_class": "measurement", "suggested_display_precision": 2},
    "Hx": {"name": "Hx", "unit_of_measurement": "%", "icon": "mdi:battery-sync",
           "state_class": "measurement", "suggested_display_precision": 2},
    "BatTemp": {"name": "Battery temperature", "unit_of_measurement": "°C", "device_class": "temperature",
                "state_class": "measurement", "suggested_display_precision": 1},
    "Amb": {"name": "Ambient temperature", "unit_of_measurement": "°C", "device_class": "temperature",
            "state_class": "measurement", "suggested_display_precision": 1},
    "BatVolts": {"name": "Battery voltage", "unit_of_measurement": "V", "device_class": "voltage",
                 "state_class": "measurement", "suggested_display_precision": 1},
    "BatAmps": {"name": "Battery current", "unit_of_measurement": "A", "device_class": "current",
                "state_class": "measurement", "suggested_display_precision": 2},
    "BatPower": {"name": "Battery power", "unit_of_measurement": "W", "device_class": "power",
                 "state_class": "measurement", "suggested_display_precision": 0},
    "ChrgPwr": {"name": "Charging power", "unit_of_measurement": "W", "device_class": "power",
                "state_class": "measurement"},
    # LeafSpy reports m/s; the display unit (km/h) can be changed per entity in HA.
    "Speed": {"name": "Speed", "unit_of_measurement": "m/s", "device_class": "speed",
              "state_class": "measurement", "suggested_display_precision": 0},
    "RPM": {"name": "Motor RPM", "unit_of_measurement": "rpm", "icon": "mdi:engine",
            "state_class": "measurement"},
    "Odo": {"name": "Odometer", "unit_of_measurement": "km", "device_class": "distance",
            "state_class": "total_increasing"},
    "Trip": {"name": "Trip", "unit_of_measurement": "km", "device_class": "distance",
             "state_class": "measurement"},
    "Elv": {"name": "Elevation", "unit_of_measurement": "m", "device_class": "distance",
            "icon": "mdi:image-filter-hdr", "entity_category": "diagnostic"},
    "DevBat": {"name": "Phone battery", "unit_of_measurement": "%", "device_class": "battery",
               "state_class": "measurement", "entity_category": "diagnostic"},
    "PlugState": {"name": "Plug state", "icon": "mdi:ev-plug-type1", "entity_category": "diagnostic"},
    "ChrgMode": {"name": "Charge mode", "icon": "mdi:ev-station", "entity_category": "diagnostic"},
    "Seq": {"name": "Sequence", "icon": "mdi:counter", "entity_category": "diagnostic",
            "enabled_by_default": False},
    "received_at": {"name": "Last update", "device_class": "timestamp", "entity_category": "diagnostic"},
}

# PlugState: 0 = not plugged, 1 = partially plugged, 2 = plugged.
# ChrgMode: 0 = not charging, 1 = L1, 2 = L2, 3 = L3 (CHAdeMO). (LeafSpy Pro manual)
BINARY_SENSORS: dict[str, dict[str, Any]] = {
    "plugged": {"name": "Plugged in", "device_class": "plug",
                "value_template": "{{ 'ON' if value_json.PlugState | int(0) == 2 else 'OFF' }}"},
    "charging": {"name": "Charging", "device_class": "battery_charging",
                 "value_template": "{{ 'ON' if value_json.ChrgMode | int(0) > 0 else 'OFF' }}"},
    "power_on": {"name": "Power switch", "device_class": "power",
                 "value_template": "{{ 'ON' if value_json.PwrSw | int(0) == 1 else 'OFF' }}"},
}


def env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    def __init__(self) -> None:
        self.mqtt_enabled = env_bool("MQTT_ENABLED", True)
        self.mqtt_host = os.getenv("MQTT_HOST", "localhost")
        self.mqtt_username = os.getenv("MQTT_USERNAME") or None
        self.mqtt_password = os.getenv("MQTT_PASSWORD") or None
        self.mqtt_tls = env_bool("MQTT_TLS_ENABLED", True)
        self.mqtt_tls_ca_certs = os.getenv("MQTT_TLS_CA_CERTS") or None    # default: system CA bundle
        self.mqtt_tls_cert_file = os.getenv("MQTT_TLS_CERT_FILE") or None  # optional client certificate
        self.mqtt_tls_key_file = os.getenv("MQTT_TLS_KEY_FILE") or None    # optional client key
        self.mqtt_tls_insecure = env_bool("MQTT_TLS_INSECURE", False)      # skip certificate verification
        self.mqtt_port = int(os.getenv("MQTT_PORT") or (8883 if self.mqtt_tls else 1883))
        self.mqtt_client_id = os.getenv("MQTT_CLIENT_ID", "leafspy2mqtt")
        self.topic = (os.getenv("MQTT_TOPIC") or "leafspy").rstrip("/")
        self.username = os.getenv("USERNAME")
        self.password_hash = (os.getenv("PASSWORD_HASH") or "").lower()
        self.listen_port = int(os.getenv("LISTEN_PORT") or 8888)
        self.discovery = env_bool("HA_DISCOVERY", True)
        self.discovery_prefix = (os.getenv("HA_DISCOVERY_PREFIX") or "homeassistant").rstrip("/")
        self.device_name = os.getenv("HA_DEVICE_NAME", "Nissan Leaf")
        self.entity_prefix = os.getenv("HA_ENTITY_PREFIX", "leafspy")

        # InfluxDB (optional, enabled by INFLUXDB_URL).
        self.influx_url = (os.getenv("INFLUXDB_URL") or "").rstrip("/") or None
        self.influx_version = (os.getenv("INFLUXDB_VERSION") or "2").strip()
        self.influx_token = os.getenv("INFLUXDB_TOKEN") or None
        self.influx_org = os.getenv("INFLUXDB_ORG") or None                 # v2
        self.influx_bucket = os.getenv("INFLUXDB_BUCKET") or None           # v2
        self.influx_database = os.getenv("INFLUXDB_DATABASE") or self.influx_bucket  # v1 / v3
        self.influx_retention_policy = os.getenv("INFLUXDB_RETENTION_POLICY") or None  # v1
        self.influx_username = os.getenv("INFLUXDB_USERNAME") or None       # v1
        self.influx_password = os.getenv("INFLUXDB_PASSWORD") or None       # v1
        self.influx_measurement = os.getenv("INFLUXDB_MEASUREMENT") or "leafspy"
        self.influx_tls_ca_certs = os.getenv("INFLUXDB_TLS_CA_CERTS") or None
        self.influx_tls_insecure = env_bool("INFLUXDB_TLS_INSECURE", False)

    @property
    def availability_topic(self) -> str:
        return f"{self.topic}/status"

    def validate(self) -> None:
        if not self.username or not self.password_hash:
            raise SystemExit("USERNAME and PASSWORD_HASH must be set")
        if not self.mqtt_enabled and not self.influx_url:
            raise SystemExit("Nothing to do: MQTT_ENABLED=false and INFLUXDB_URL not set")
        if self.influx_url:
            if self.influx_version not in {"1", "2", "3"}:
                raise SystemExit("INFLUXDB_VERSION must be 1, 2 or 3")
            if self.influx_version == "2" and not (self.influx_org and self.influx_bucket and self.influx_token):
                raise SystemExit("InfluxDB 2.x needs INFLUXDB_TOKEN, INFLUXDB_ORG and INFLUXDB_BUCKET")
            if self.influx_version in {"1", "3"} and not self.influx_database:
                raise SystemExit(f"InfluxDB {self.influx_version}.x needs INFLUXDB_DATABASE")


def to_number(value: str) -> Any:
    try:
        return int(value)
    except ValueError:
        try:
            return float(value)
        except ValueError:
            return value


def normalise(args: dict[str, list[bytes]], now: datetime | None = None) -> dict[str, Any]:
    """Turn LeafSpy query arguments into a clean JSON-able sample."""
    payload: dict[str, Any] = {}
    for key, values in args.items():
        if key in SECRET_FIELDS or not values:
            continue
        payload[key] = to_number(values[0].decode("utf-8", errors="replace"))

    # Temperatures always in Celsius, whatever the phone is set to.
    if str(payload.get("Tunits", "C")).upper() == "F":
        for key in ("BatTemp", "Amb"):
            if isinstance(payload.get(key), (int, float)):
                payload[key] = round((payload[key] - 32) * 5 / 9, 2)
        payload["Tunits"] = "C"

    volts, amps = payload.get("BatVolts"), payload.get("BatAmps")
    if isinstance(volts, (int, float)) and isinstance(amps, (int, float)):
        # Positive = energy leaving the battery (driving), negative = charging/regen.
        payload["BatPower"] = round(volts * amps, 1)

    payload["received_at"] = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return payload


def discovery_messages(cfg: Config, vin: str) -> list[tuple[str, dict[str, Any]]]:
    """Home Assistant discovery (topic, config) pairs for one car."""
    node = f"leafspy_{vin.lower()}"
    device = {
        "identifiers": [node],
        "name": cfg.device_name,
        "manufacturer": "Nissan",
        "model": "Leaf (LeafSpy)",
        "serial_number": vin,
        "sw_version": f"leafspy2mqtt {VERSION}",
    }
    common = {
        "state_topic": cfg.topic,
        "availability_topic": cfg.availability_topic,
        "device": device,
    }
    messages: list[tuple[str, dict[str, Any]]] = []

    for key, extra in SENSORS.items():
        slug = key.lower()
        conf = {**common, **extra,
                "unique_id": f"{node}_{slug}",
                "default_entity_id": f"sensor.{cfg.entity_prefix}_{slug}",
                "value_template": "{{ value_json.%s }}" % key}
        messages.append((f"{cfg.discovery_prefix}/sensor/{node}/{slug}/config", conf))

    for key, extra in BINARY_SENSORS.items():
        conf = {**common, **extra,
                "unique_id": f"{node}_{key}",
                "default_entity_id": f"binary_sensor.{cfg.entity_prefix}_{key}"}
        messages.append((f"{cfg.discovery_prefix}/binary_sensor/{node}/{key}/config", conf))

    tracker = {
        "name": "Location",
        "unique_id": f"{node}_location",
        "default_entity_id": f"device_tracker.{cfg.entity_prefix}",
        "availability_topic": cfg.availability_topic,
        "json_attributes_topic": cfg.topic,
        "json_attributes_template": (
            "{{ {'latitude': value_json.Lat, 'longitude': value_json.Long, 'gps_accuracy': 10,"
            " 'altitude': value_json.Elv} | tojson }}"),
        "source_type": "gps",
        "icon": "mdi:car-electric",
        "device": device,
    }
    messages.append((f"{cfg.discovery_prefix}/device_tracker/{node}/location/config", tracker))
    return messages


class Publisher:
    """Long-lived MQTT connection with LWT and HA birth handling."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.known_vins: set[str] = set()
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cfg.mqtt_client_id)
        if cfg.mqtt_username:
            self.client.username_pw_set(cfg.mqtt_username, cfg.mqtt_password)
        if cfg.mqtt_tls:
            self.client.tls_set(
                ca_certs=cfg.mqtt_tls_ca_certs,
                certfile=cfg.mqtt_tls_cert_file,
                keyfile=cfg.mqtt_tls_key_file,
                cert_reqs=ssl.CERT_NONE if cfg.mqtt_tls_insecure else ssl.CERT_REQUIRED,
            )
            if cfg.mqtt_tls_insecure:
                self.client.tls_insecure_set(True)
        self.client.will_set(cfg.availability_topic, "offline", qos=1, retain=True)
        self.client.reconnect_delay_set(min_delay=1, max_delay=60)
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message

    def start(self) -> None:
        self.client.connect_async(self.cfg.mqtt_host, self.cfg.mqtt_port, keepalive=60)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.publish(self.cfg.availability_topic, "offline", qos=1, retain=True).wait_for_publish(5)
        self.client.disconnect()
        self.client.loop_stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            log.error("MQTT connect failed: %s", reason_code)
            return
        log.info("MQTT connected to %s:%s", self.cfg.mqtt_host, self.cfg.mqtt_port)
        client.publish(self.cfg.availability_topic, "online", qos=1, retain=True)
        if self.cfg.discovery:
            client.subscribe(f"{self.cfg.discovery_prefix}/status")
            for vin in self.known_vins:
                self._publish_discovery(vin)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties) -> None:
        log.warning("MQTT disconnected: %s", reason_code)

    def _on_message(self, client, userdata, msg) -> None:
        # HA restarted: send discovery again so entities come back.
        if msg.topic == f"{self.cfg.discovery_prefix}/status" and msg.payload == b"online":
            for vin in self.known_vins:
                self._publish_discovery(vin)

    def _publish_discovery(self, vin: str) -> None:
        for topic, conf in discovery_messages(self.cfg, vin):
            self.client.publish(topic, json.dumps(conf), qos=1, retain=True)
        log.info("Published Home Assistant discovery for VIN %s", vin)

    def publish(self, payload: dict[str, Any]) -> None:
        vin = str(payload.get("VIN") or "unknown")
        if self.cfg.discovery and vin not in self.known_vins:
            self.known_vins.add(vin)
            self._publish_discovery(vin)
        info = self.client.publish(self.cfg.topic, json.dumps(payload), qos=1, retain=True)
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            log.warning("MQTT publish queued/failed rc=%s (broker down?)", info.rc)


# Not numeric measurements: VIN becomes a tag, the rest is dropped.
INFLUX_SKIP_FIELDS = {"VIN", "Tunits", "received_at"}


def _lp_escape(value: str, specials: str) -> str:
    value = value.replace("\\", "\\\\")
    for ch in specials:
        value = value.replace(ch, "\\" + ch)
    return value.replace("\n", "\\n")


def line_protocol(measurement: str, payload: dict[str, Any], timestamp_ms: int) -> str | None:
    """One InfluxDB line for a normalised sample, or None if it has no numeric field.

    Every number is written as a float: LeafSpy sends e.g. ``ChrgPwr=1300`` and ``Speed=0.0``
    for the same field, and a field whose type changes would be rejected by InfluxDB.
    """
    fields = []
    for key, value in sorted(payload.items()):
        if key in INFLUX_SKIP_FIELDS or isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if not math.isfinite(value):
            continue
        fields.append(f"{_lp_escape(key, ',= ')}={float(value)!r}")
    if not fields:
        return None
    series = _lp_escape(measurement, ", ")
    vin = payload.get("VIN")
    if vin not in (None, ""):
        series += f",vin={_lp_escape(str(vin), ',= ')}"
    return f"{series} {','.join(fields)} {timestamp_ms}"


class InfluxWriter:
    """Buffers samples and writes them to InfluxDB in the background.

    Writes never block or fail the LeafSpy request. If InfluxDB is unreachable the lines are
    kept (up to ``max_buffer``, oldest dropped first) and retried with exponential backoff.
    """

    batch_size = 1000
    max_buffer = 20000
    dedup_size = 512

    def __init__(self, cfg: Config, http_client: tornado.httpclient.AsyncHTTPClient | None = None) -> None:
        self.cfg = cfg
        self.http = http_client or tornado.httpclient.AsyncHTTPClient()
        self.buffer: list[str] = []
        self.dropped = 0
        self._recent: OrderedDict[str, None] = OrderedDict()
        self._last_ts: dict[str, int] = {}
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    # -- request -----------------------------------------------------------------------------
    def request(self, body: str) -> tornado.httpclient.HTTPRequest:
        cfg = self.cfg
        headers = {"Content-Type": "text/plain; charset=utf-8"}
        auth: dict[str, Any] = {}
        if cfg.influx_version == "1":
            params = {"db": cfg.influx_database, "precision": "ms"}
            if cfg.influx_retention_policy:
                params["rp"] = cfg.influx_retention_policy
            path = "/write"
            if cfg.influx_username:
                auth = {"auth_username": cfg.influx_username, "auth_password": cfg.influx_password or ""}
            elif cfg.influx_token:  # v1 compatibility API of InfluxDB 2.x / 3.x
                headers["Authorization"] = f"Token {cfg.influx_token}"
        elif cfg.influx_version == "2":
            params = {"org": cfg.influx_org, "bucket": cfg.influx_bucket, "precision": "ms"}
            path = "/api/v2/write"
            headers["Authorization"] = f"Token {cfg.influx_token}"
        else:
            params = {"db": cfg.influx_database, "precision": "millisecond"}
            path = "/api/v3/write_lp"
            if cfg.influx_token:
                headers["Authorization"] = f"Bearer {cfg.influx_token}"
        return tornado.httpclient.HTTPRequest(
            f"{cfg.influx_url}{path}?{urlencode(params)}", method="POST", body=body, headers=headers,
            request_timeout=10, connect_timeout=5,
            validate_cert=not cfg.influx_tls_insecure, ca_certs=cfg.influx_tls_ca_certs, **auth)

    # -- buffering ---------------------------------------------------------------------------
    def _is_duplicate(self, payload: dict[str, Any]) -> bool:
        """LeafSpy re-sends a sample when it did not get our answer in time. Same values
        (including Seq) seen recently -> skip, otherwise it would be stored twice."""
        key = json.dumps({k: v for k, v in payload.items() if k != "received_at"}, sort_keys=True)
        if key in self._recent:
            return True
        self._recent[key] = None
        if len(self._recent) > self.dedup_size:
            self._recent.popitem(last=False)
        return False

    def _enqueue(self, lines: list[str], front: bool = False) -> None:
        self.buffer = lines + self.buffer if front else self.buffer + lines
        overflow = len(self.buffer) - self.max_buffer
        if overflow > 0:
            del self.buffer[:overflow]
            self.dropped += overflow
            log.warning("InfluxDB buffer full, dropped %d oldest samples", overflow)

    def _timestamp(self, payload: dict[str, Any]) -> int:
        """Milliseconds, strictly increasing per VIN: InfluxDB would merge two different samples of
        the same series with the same timestamp into one point."""
        vin = str(payload.get("VIN") or "")
        ts = max(time.time_ns() // 1_000_000, self._last_ts.get(vin, 0) + 1)
        self._last_ts[vin] = ts
        return ts

    def publish(self, payload: dict[str, Any]) -> None:
        if self._is_duplicate(payload):
            log.info("Duplicate sample (Seq=%s) not written to InfluxDB", payload.get("Seq"))
            return
        line = line_protocol(self.cfg.influx_measurement, payload, self._timestamp(payload))
        if line is None:
            return
        self._enqueue([line])
        self._wake.set()

    # -- writing -----------------------------------------------------------------------------
    async def flush(self) -> bool:
        """Send everything buffered. Returns False if InfluxDB should be retried later."""
        while self.buffer:
            batch, self.buffer = self.buffer[:self.batch_size], self.buffer[self.batch_size:]
            try:
                resp = await self.http.fetch(self.request("\n".join(batch)), raise_error=False)
            except asyncio.CancelledError:
                self._enqueue(batch, front=True)  # shutting down: keep it for the final flush
                raise
            except Exception as exc:  # connection refused, DNS, TLS, timeout...
                log.warning("InfluxDB unreachable (%s), %d samples buffered", exc, len(batch) + len(self.buffer))
                self._enqueue(batch, front=True)
                return False
            if 200 <= resp.code < 300:
                continue
            body = (resp.body or b"")[:300].decode("utf-8", errors="replace")
            if resp.code in (400, 413, 422):
                # Bad data will never succeed; do not block everything behind it.
                log.error("InfluxDB rejected %d lines (HTTP %d): %s", len(batch), resp.code, body)
                self.dropped += len(batch)
                continue
            log.warning("InfluxDB write failed (HTTP %s): %s", resp.code, body or resp.error)
            self._enqueue(batch, front=True)
            return False
        return True

    async def _run(self) -> None:
        backoff = 1.0
        while True:
            await self._wake.wait()
            self._wake.clear()
            try:
                ok = await self.flush()
            except Exception:  # never let the writer task die
                log.exception("InfluxDB writer error")
                ok = False
            if ok:
                backoff = 1.0
            else:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
                self._wake.set()

    def start(self) -> None:
        log.info("Writing to InfluxDB %s.x at %s", self.cfg.influx_version, self.cfg.influx_url)
        self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task  # lets an in-flight batch go back into the buffer
            except asyncio.CancelledError:
                pass
            self._task = None
        if self.buffer:
            try:
                await asyncio.wait_for(self.flush(), timeout=5)
            except Exception:
                log.warning("Could not flush %d samples to InfluxDB on shutdown", len(self.buffer))


def fan_out(sinks: list[Callable[[dict[str, Any]], None]]) -> Callable[[dict[str, Any]], None]:
    """One publish callable for several outputs; a failing output does not affect the others."""
    def publish(payload: dict[str, Any]) -> None:
        for sink in sinks:
            try:
                sink(payload)
            except Exception:
                log.exception("Publishing to %s failed", getattr(sink, "__qualname__", sink))
    return publish


class MainHandler(tornado.web.RequestHandler):
    def initialize(self, cfg: Config, publish: Callable[[dict[str, Any]], None]) -> None:
        self.cfg = cfg
        self.publish = publish

    def get(self) -> None:
        user = self.get_query_argument("user", None)
        password = self.get_query_argument("pass", None)
        if user is None or password is None:
            self.set_status(401)
            self.write("HTTP/401 Unauthorized")
            return

        digest = hashlib.sha3_512(password.encode()).hexdigest()
        user_ok = hmac.compare_digest(user.encode(), (self.cfg.username or "").encode())
        pass_ok = hmac.compare_digest(digest, self.cfg.password_hash)
        if not (user_ok and pass_ok):
            self.set_status(403)
            self.write("HTTP/403 Forbidden")
            return

        self.publish(normalise(self.request.query_arguments))
        self.write('"status":"0"')


class HealthHandler(tornado.web.RequestHandler):
    def get(self) -> None:
        self.write("ok")


def log_request(handler: tornado.web.RequestHandler) -> None:
    """Access log without the query string: it carries the LeafSpy password."""
    status = handler.get_status()
    if handler.request.path == "/healthz" and status < 400:
        return
    level = logging.INFO if status < 400 else logging.WARNING
    logging.getLogger("tornado.access").log(
        level, "%d %s %s (%s) %.1fms", status, handler.request.method, handler.request.path,
        handler.request.remote_ip, 1000.0 * handler.request.request_time())


def make_app(cfg: Config, publish: Callable[[dict[str, Any]], None]) -> tornado.web.Application:
    return tornado.web.Application(
        [
            (r"/", MainHandler, {"cfg": cfg, "publish": publish}),
            (r"/healthz", HealthHandler),
        ],
        log_function=log_request,
        xheaders=True,  # behind Traefik/Cloudflare: log the real client IP
    )


async def main() -> None:
    load_dotenv()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = Config()
    cfg.validate()

    sinks: list[Callable[[dict[str, Any]], None]] = []
    publisher = influx = None
    if cfg.mqtt_enabled:
        publisher = Publisher(cfg)
        publisher.start()
        sinks.append(publisher.publish)
    if cfg.influx_url:
        influx = InfluxWriter(cfg)
        influx.start()
        sinks.append(influx.publish)
    app = make_app(cfg, fan_out(sinks))
    server = app.listen(cfg.listen_port)
    log.info("leafspy2mqtt %s listening on :%d", VERSION, cfg.listen_port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    server.stop()
    if influx:
        await influx.stop()
    if publisher:
        publisher.stop()


if __name__ == "__main__":
    asyncio.run(main())
