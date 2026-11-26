import asyncio
import hashlib
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

import pytest
from tornado.testing import AsyncHTTPTestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import leafspy2mqtt as l2m  # noqa: E402

PASSWORD = "Nobody inspects the spammish repetition"
SAMPLE = {
    "user": "q84fh", "pass": PASSWORD, "DevBat": "100", "Gids": "219", "Lat": "53.12", "Long": "18.04",
    "Elv": "35", "Seq": "143", "Trip": "86", "Odo": "37774", "SOC": "46.2757", "AHr": "107.3014",
    "BatTemp": "8.7", "Amb": "7.0", "Wpr": "0", "PlugState": "2", "ChrgMode": "2", "ChrgPwr": "1300",
    "VIN": "SJNFAAZE1U0000001", "PwrSw": "1", "Tunits": "C", "RPM": "0", "SOH": "92.95", "Hx": "102.51",
    "Speed": "0.0", "BatVolts": "352.56", "BatAmps": "-1.006",
}


def make_cfg(monkeypatch, **env):
    base = {"USERNAME": "q84fh", "PASSWORD_HASH": hashlib.sha3_512(PASSWORD.encode()).hexdigest(),
            "MQTT_TOPIC": "leafspy"}
    base.update(env)
    for key, value in base.items():
        monkeypatch.setenv(key, value)
    return l2m.Config()


def args(d):
    return {k: [v.encode()] for k, v in d.items()}


def test_normalise_strips_credentials_and_converts_types():
    now = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)
    out = l2m.normalise(args(SAMPLE), now)
    assert "user" not in out and "pass" not in out
    assert out["Gids"] == 219 and out["SOC"] == pytest.approx(46.2757)
    assert out["VIN"] == "SJNFAAZE1U0000001"
    assert out["BatPower"] == pytest.approx(352.56 * -1.006, abs=0.1)
    assert out["received_at"] == "2026-11-26T12:00:00+00:00"


def test_normalise_fahrenheit_to_celsius():
    out = l2m.normalise(args({**SAMPLE, "Tunits": "F", "BatTemp": "50", "Amb": "32"}))
    assert out["BatTemp"] == 10 and out["Amb"] == 0 and out["Tunits"] == "C"


def test_mqtt_port_is_honoured(monkeypatch):
    assert make_cfg(monkeypatch, MQTT_PORT="1884").mqtt_port == 1884


def test_mqtt_port_defaults_follow_tls(monkeypatch):
    monkeypatch.delenv("MQTT_PORT", raising=False)
    assert make_cfg(monkeypatch).mqtt_port == 8883  # TLS on by default
    assert make_cfg(monkeypatch, MQTT_TLS_ENABLED="false").mqtt_port == 1883


def test_publisher_tls_options(monkeypatch):
    cfg = make_cfg(monkeypatch, MQTT_TLS_ENABLED="true", MQTT_TLS_INSECURE="true")
    pub = l2m.Publisher(cfg)
    assert pub.client._ssl_context is not None
    plain = l2m.Publisher(make_cfg(monkeypatch, MQTT_TLS_ENABLED="false"))
    assert plain.client._ssl_context is None


def test_discovery_messages(monkeypatch):
    cfg = make_cfg(monkeypatch)
    msgs = dict(l2m.discovery_messages(cfg, "SJNFAAZE1U0000001"))
    soc = msgs["homeassistant/sensor/leafspy_sjnfaaze1u0000001/soc/config"]
    assert soc["state_topic"] == "leafspy"
    assert soc["availability_topic"] == "leafspy/status"
    assert soc["value_template"] == "{{ value_json.SOC }}"
    assert soc["default_entity_id"] == "sensor.leafspy_soc"
    assert soc["device"]["identifiers"] == ["leafspy_sjnfaaze1u0000001"]
    assert "homeassistant/device_tracker/leafspy_sjnfaaze1u0000001/location/config" in msgs
    assert "homeassistant/binary_sensor/leafspy_sjnfaaze1u0000001/plugged/config" in msgs
    # unique ids are unique
    uids = [m["unique_id"] for m in msgs.values()]
    assert len(uids) == len(set(uids))
    # everything is JSON-serialisable
    json.dumps(list(msgs.values()))


class HandlerTest(AsyncHTTPTestCase):
    @pytest.fixture(autouse=True)
    def _caplog(self, caplog):
        self.caplog = caplog

    def get_app(self):
        self.published = []
        cfg = l2m.Config()
        cfg.username = "q84fh"
        cfg.password_hash = hashlib.sha3_512(PASSWORD.encode()).hexdigest()
        return l2m.make_app(cfg, self.published.append)

    def test_ok(self):
        with self.caplog.at_level(logging.INFO, logger="tornado.access"):
            resp = self.fetch("/?" + urlencode(SAMPLE))
        assert resp.code == 200 and resp.body == b'"status":"0"'
        assert len(self.published) == 1 and "pass" not in self.published[0]
        assert "spammish" not in self.caplog.text  # password never logged
        assert "pass=" not in self.caplog.text

    def test_missing_credentials(self):
        assert self.fetch("/?SOC=1").code == 401
        assert self.published == []

    def test_wrong_password(self):
        resp = self.fetch("/?" + urlencode({**SAMPLE, "pass": "nope"}))
        assert resp.code == 403 and self.published == []

    def test_wrong_user(self):
        resp = self.fetch("/?" + urlencode({**SAMPLE, "user": "someone"}))
        assert resp.code == 403

    def test_healthz(self):
        assert self.fetch("/healthz").body == b"ok"


def test_version_from_environment(monkeypatch):
    import importlib
    monkeypatch.setenv("LEAFSPY2MQTT_VERSION", "9.9.9")
    try:
        assert importlib.reload(l2m).VERSION == "9.9.9"
        cfg = make_cfg(monkeypatch)
        conf = dict(l2m.discovery_messages(cfg, "VIN1"))["homeassistant/sensor/leafspy_vin1/soc/config"]
        assert conf["device"]["sw_version"] == "leafspy2mqtt 9.9.9"
    finally:
        monkeypatch.delenv("LEAFSPY2MQTT_VERSION")
        importlib.reload(l2m)


# --- InfluxDB ---------------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, code, body=b""):
        self.code, self.body, self.error = code, body, None


class FakeHTTP:
    def __init__(self, *codes):
        self.codes = list(codes)
        self.requests = []

    async def fetch(self, request, raise_error=True):
        self.requests.append(request)
        code = self.codes.pop(0) if self.codes else 204
        if isinstance(code, Exception):
            raise code
        return FakeResponse(code)


def influx_cfg(monkeypatch, **env):
    base = {"INFLUXDB_URL": "http://influx:8086/", "INFLUXDB_VERSION": "2", "INFLUXDB_TOKEN": "tok",
            "INFLUXDB_ORG": "home", "INFLUXDB_BUCKET": "leaf"}
    base.update(env)
    return make_cfg(monkeypatch, **base)


def test_line_protocol():
    payload = l2m.normalise(args(SAMPLE))
    line = l2m.line_protocol("leafspy", payload, 1764158400000)
    series, fields, ts = line.split(" ")
    assert series == "leafspy,vin=SJNFAAZE1U0000001"
    assert ts == "1764158400000"
    parsed = dict(f.split("=") for f in fields.split(","))
    assert parsed["Gids"] == "219.0"       # ints written as floats: no field type conflicts
    assert parsed["SOC"] == "46.2757"
    assert "BatPower" in parsed
    assert not {"VIN", "Tunits", "received_at", "user", "pass"} & parsed.keys()


def test_line_protocol_escaping_and_empty():
    line = l2m.line_protocol("leaf spy", {"VIN": "A B,C=D", "my key": 1, "Bad": float("nan")}, 1)
    assert line == r"leaf\ spy,vin=A\ B\,C\=D my\ key=1.0 1"
    assert l2m.line_protocol("leafspy", {"VIN": "X", "Tunits": "C"}, 1) is None


@pytest.mark.parametrize("version, env, url, auth", [
    ("1", {"INFLUXDB_DATABASE": "leafdb", "INFLUXDB_USERNAME": "u", "INFLUXDB_PASSWORD": "p",
           "INFLUXDB_RETENTION_POLICY": "autogen"},
     "http://influx:8086/write?db=leafdb&precision=ms&rp=autogen", None),
    ("2", {}, "http://influx:8086/api/v2/write?org=home&bucket=leaf&precision=ms", "Token tok"),
    ("3", {"INFLUXDB_DATABASE": "leafdb"},
     "http://influx:8086/api/v3/write_lp?db=leafdb&precision=millisecond", "Bearer tok"),
])
def test_influx_request_per_version(monkeypatch, version, env, url, auth):
    cfg = influx_cfg(monkeypatch, INFLUXDB_VERSION=version, **env)
    cfg.validate()
    req = l2m.InfluxWriter(cfg, FakeHTTP()).request("x")
    assert req.url == url and req.method == "POST"
    assert req.headers.get("Authorization") == auth
    if version == "1":
        assert (req.auth_username, req.auth_password) == ("u", "p")


def test_influx_v3_database_falls_back_to_bucket(monkeypatch):
    cfg = influx_cfg(monkeypatch, INFLUXDB_VERSION="3")
    assert cfg.influx_database == "leaf"


@pytest.mark.parametrize("env", [
    {"INFLUXDB_VERSION": "4"},
    {"INFLUXDB_VERSION": "2", "INFLUXDB_ORG": ""},
    {"INFLUXDB_VERSION": "1", "INFLUXDB_BUCKET": ""},
    {"INFLUXDB_URL": "", "MQTT_ENABLED": "false"},
])
def test_influx_config_validation(monkeypatch, env):
    with pytest.raises(SystemExit):
        influx_cfg(monkeypatch, **env).validate()


def test_influx_dedup_and_flush(monkeypatch):
    http = FakeHTTP()
    writer = l2m.InfluxWriter(influx_cfg(monkeypatch), http)
    payload = l2m.normalise(args(SAMPLE))
    writer.publish(payload)
    writer.publish({**payload, "received_at": "later"})  # LeafSpy resend: same values, same Seq
    writer.publish({**payload, "Seq": 144})
    assert len(writer.buffer) == 2
    assert asyncio.run(writer.flush()) is True
    assert writer.buffer == [] and len(http.requests) == 1
    assert http.requests[0].body.decode().count("\n") == 1


def test_influx_retries_keep_data(monkeypatch):
    http = FakeHTTP(503, 204)
    writer = l2m.InfluxWriter(influx_cfg(monkeypatch), http)
    writer.publish(l2m.normalise(args(SAMPLE)))
    assert asyncio.run(writer.flush()) is False and len(writer.buffer) == 1
    assert asyncio.run(writer.flush()) is True and writer.buffer == []


def test_influx_connection_error_keeps_data(monkeypatch):
    http = FakeHTTP(ConnectionRefusedError(111, "Connection refused"), 204)
    writer = l2m.InfluxWriter(influx_cfg(monkeypatch), http)
    writer.publish(l2m.normalise(args(SAMPLE)))
    assert asyncio.run(writer.flush()) is False and len(writer.buffer) == 1
    assert asyncio.run(writer.flush()) is True and writer.buffer == []


def test_influx_bad_data_is_dropped(monkeypatch):
    writer = l2m.InfluxWriter(influx_cfg(monkeypatch), FakeHTTP(400))
    writer.publish(l2m.normalise(args(SAMPLE)))
    assert asyncio.run(writer.flush()) is True
    assert writer.buffer == [] and writer.dropped == 1


def test_influx_buffer_is_bounded(monkeypatch):
    writer = l2m.InfluxWriter(influx_cfg(monkeypatch), FakeHTTP())
    writer.max_buffer = 3
    for seq in range(5):
        writer.publish(l2m.normalise(args({**SAMPLE, "Seq": str(seq)})))
    assert len(writer.buffer) == 3 and writer.dropped == 2
    assert "Seq=2.0" in writer.buffer[0]


def test_fan_out_isolates_failures():
    got = []

    def broken(payload):
        raise RuntimeError("boom")

    l2m.fan_out([broken, got.append])({"SOC": 1})
    assert got == [{"SOC": 1}]
