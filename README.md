# leafspy2mqtt

[![Docker Pulls](https://badgen.net/docker/pulls/q84fh/leafspy2mqtt?icon=docker&label=pulls)](https://hub.docker.com/r/q84fh/leafspy2mqtt/)
[![Docker Stars](https://badgen.net/docker/stars/q84fh/leafspy2mqtt?icon=docker&label=stars)](https://hub.docker.com/r/q84fh/leafspy2mqtt/)
[![Docker Image Size](https://badgen.net/docker/size/q84fh/leafspy2mqtt?icon=docker&label=image%20size)](https://hub.docker.com/r/q84fh/leafspy2mqtt/)
[![Github stars](https://badgen.net/github/stars/q84fh/leafspy2mqtt?icon=github&label=stars)](https://github.com/q84fh/leafspy2mqtt)
[![Github forks](https://badgen.net/github/forks/q84fh/leafspy2mqtt?icon=github&label=forks)](https://github.com/q84fh/leafspy2mqtt)
[![Github issues](https://img.shields.io/github/issues/q84fh/leafspy2mqtt)](https://github.com/q84fh/leafspy2mqtt/issues)
[![Github last-commit](https://img.shields.io/github/last-commit/q84fh/leafspy2mqtt)](https://github.com/q84fh/leafspy2mqtt)
[![Built with Devbox](https://www.jetify.com/img/devbox/shield_galaxy.svg)](https://www.jetify.com/devbox/docs/contributor-quickstart/)

This is simple server that bridges [LeafSpy](https://play.google.com/store/apps/details?id=com.Turbo3.Leaf_Spy_Pro&hl=en-US) to MQTT broker. 

## Environment variables
Configuration is done via environment variables (or a `.env` file):

| Variable | Default | Description |
| --- | --- | --- |
| `USERNAME` | – (required) | username entered in LeafSpy (`ID` field) |
| `PASSWORD_HASH` | – (required) | SHA3-512 of the password entered in LeafSpy (`PW` field), see below |
| `MQTT_HOST` | `localhost` | MQTT broker address |
| `MQTT_PORT` | `8883` with TLS, `1883` without | MQTT broker port |
| `MQTT_USERNAME` / `MQTT_PASSWORD` | – | MQTT credentials |
| `MQTT_TLS_ENABLED` | `true` | connect to the broker over TLS |
| `MQTT_TLS_CA_CERTS` | system CA bundle | optional path to CA certificate file |
| `MQTT_TLS_CERT_FILE` / `MQTT_TLS_KEY_FILE` | – | optional client certificate and key |
| `MQTT_TLS_INSECURE` | `false` | skip certificate verification if `true` |
| `MQTT_CLIENT_ID` | `leafspy2mqtt` | MQTT client id |
| `MQTT_TOPIC` | `leafspy` | state topic; availability is published to `<MQTT_TOPIC>/status` |
| `HA_DISCOVERY` | `true` | publish [Home Assistant MQTT discovery](https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery) |
| `HA_DISCOVERY_PREFIX` | `homeassistant` | discovery prefix |
| `HA_DEVICE_NAME` | `Nissan Leaf` | device name in Home Assistant |
| `HA_ENTITY_PREFIX` | `leafspy` | entity ids become `sensor.leafspy_soc`, `device_tracker.leafspy`, … |
| `LISTEN_PORT` | `8888` | HTTP port |
| `LOG_LEVEL` | `INFO` | Python log level |

Generate the password hash with:
```
python -c 'import hashlib; print(hashlib.sha3_512(b"Nobody inspects the spammish repetition").hexdigest())'
```

`GET /healthz` returns `ok` (for liveness/readiness probes).

 ## Example docker-compose

 You need MQTT broker. You can install one yourself or for [deploy one built into Home Assistant](https://www.home-assistant.io/integrations/mqtt).

 ```yaml
services:
  leafspy2mqtt:
    image: ghcr.io/q84fh/leafspy2mqtt:2
    ports:
      -  8888:8888
    restart: unless-stopped
    environment:
      - MQTT_HOST=yourmqttbroker.local
      - MQTT_PORT=1883
      - MQTT_TLS_ENABLED=false
      - MQTT_USERNAME=leaf
      - MQTT_PASSWORD=secretpassword
      - MQTT_TOPIC=leafspy
      - PASSWORD_HASH=51b771f538117(...)
      - USERNAME=q84fh
 ```

Images are multi-arch (`linux/amd64`, `linux/arm64`, e.g. Raspberry Pi 4) and run as an unprivileged user.

## Security notes
 - LeafSpy sends the password in the URL query string. Expose the endpoint **only over HTTPS** (reverse proxy / tunnel) or over a VPN.
 - The query string is never logged and `user`/`pass` are removed before anything is published to MQTT.
 - Consider rate limiting on the reverse proxy; the endpoint is meant for one car.

## LeafSpy configuration
You need to expose port 8888 so [LeafSpy](https://play.google.com/store/apps/details?id=com.Turbo3.Leaf_Spy_Pro&hl=en-US) would be able to reach it. You can do it via VPN to your mobile, or by just exposing it to the public Internet.

Then:
 1. Go to the settings using `≡` menu
 2. Find `Server` section
 3. Check `Enable`
 4. Select desired `Send Interval` (ex. 5s)
 5. Check `A: Enable`
 6. Enter username in `ID` field
 7. Enter password in `PW` field
 8. Check if your `leafspy2mqtt` is reachable over HTTP or HTTPS
 9. Enter `URL` (without http:// or https:// prefix)

https://github.com/user-attachments/assets/38f6c019-b40b-4f0c-8840-9eabd31f0c31

You can configure your [LeafSpy](https://play.google.com/store/apps/details?id=com.Turbo3.Leaf_Spy_Pro&hl=en-US) to send data to up to 4 different servers (`A`, `B`, `C`, `D`). 

## Data format
[LeafSpy](https://play.google.com/store/apps/details?id=com.Turbo3.Leaf_Spy_Pro&hl=en-US) sends its data with HTTP `GET` request with data placed in URL query string.

It goes like this:
```
GET /?user=q84fh&pass=Nobodyinspectsthespammishrepetition&DevBat=100&Gids=219&Lat=-11.12421&Long=-71.75095&Elv=35&Seq=143&Trip=86&Odo=37774&SOC=46.2757&AHr=107.3014&BatTemp=8.7&Amb=7.0&Wpr=0&PlugState=2&ChrgMode=2&ChrgPwr=1300&VIN=3VWSB81H8WM210368&PwrSw=1&Tunits=C&RPM=0&SOH=92.95&Hx=102.51&Speed=0.0&BatVolts=352.56&BatAmps=-1.006
```

[LeafSpy](https://play.google.com/store/apps/details?id=com.Turbo3.Leaf_Spy_Pro&hl=en-US) expects response HTTP/200 with payload:
```
"status":"0"
```


| Field     | Type  | Unit    | Example                             | Comment                                                      |
| --------- | ----- | --------| ------------------------------------| ------------------------------------------------------------ |
| user 	    | str   | -       | q84fh                               | Username                                                     |
| pass 	    | str   | -       | Nobodyinspectsthespammishrepetition | Password                                                     |
| DevBat 	| int   | %        | 100                                 | Phone battery level                                            |
| Gids 	    | int   | ?       | 219                                 | RAW battery SOC reported by BMS                              |
| Lat 	    | float | °       | -11.12421                           | Lattitute from GPS                                           |
| Long 	    | float | °       | -71.75095                           | Longitute from GPS                                           |
| Elv 	    | int   | m       | 35                                  | Elevation from GPS                                           |
| Seq 	    | int   | -       | 117                                 | [LeafSpy](https://play.google.com/store/apps/details?id=com.Turbo3.Leaf_Spy_Pro&hl=en-US) request number (resets on restart)                   |
| Trip 	    | int   | km      | 86                                  | Current trip distance (resets on restart)                    |
| Odo 	    | int   | km      | 37774                               | Total car milage                                             |
| SOC 	    | float | %       | 46.2157                             | SOC calculated by [LeafSpy](https://play.google.com/store/apps/details?id=com.Turbo3.Leaf_Spy_Pro&hl=en-US)                                    |
| AHr 	    | float | AH      | 107.3014                            | Maximum capacity of battery                                  |
| BatTemp 	| float | °Tunits |                                     | Avarage battery temperature (check Tunits for used unit)     |
| Amb 	    | float | °Tunits | 7.5                                 | Ambient temperature (check Tunits for used unit)             |
| Wpr 	    | int   | -       | 0                                   | Front wiper status                                      |
| PlugState | int   | -       | 2                                   | I don't know, PR welcome                                     |
| ChrgMode  | int   | -       | 2                                   | I don't know, PR welcome                                     |
| ChrgPwr 	| int   | W       | 1300                                | Negotiated charging power                                    |
| VIN 	    | str   | -       | 3VWSB81H8WM210368                   | VIN of the car                                               |
| PwrSw 	| int   | -       | 1                                   | Power switch state 0=off, 1=on                                     |
| Tunits 	| str   | -       | C                                   | Unit used to report temperatures (C/F)                             |
| RPM       | int   | RPM     | 0                                   | Number of revolution per minute of motor                     |
| SOH 	    | float | %       | 92.95                               | State of health reported by BMS                              |
| Hx        | float | %       | 102.51                              | Percent of nominal battery conductivity                      |
| Speed 	| float | m/s    | 0.0                                 | Vehicle speed                                                |
| BatVolts 	| float | V       | 352.55                              | Voltage of battery                                           |
| BatAmps 	| float | A       | -1.189                              | Current flowing into (negative) or out of battery (positive) |

This is converted to JSON payload and sent towards MQTT broker (credentials removed). Message looks like this:

```json
{
  "DevBat": 100,
  "Gids": 219,
  "Lat": -11.12421,
  "Long": -71.75095,
  "Elv": 35,
  "Seq": 117,
  "Trip": 86,
  "Odo": 37774,
  "SOC": 46.2157,
  "AHr": 107.3014,
  "BatTemp": 8.7,
  "Amb": 7.5,
  "Wpr": 0,
  "PlugState": 2,
  "ChrgMode": 2,
  "ChrgPwr": 1300,
  "VIN": "3VWSB81H8WM210368",
  "PwrSw": 1,
  "Tunits": "C",
  "RPM": 0,
  "SOH": 92.95,
  "Hx": 102.51,
  "Speed": 0.0,
  "BatVolts": 352.55,
  "BatAmps": -1.189,
  "BatPower": -419.4,
  "received_at": "2026-11-26T12:00:00+00:00"
}
```

## How to use it
With `HA_DISCOVERY=true` (default) Home Assistant creates a **Nissan Leaf** device automatically as soon as
the first sample arrives (one device per VIN). No YAML needed. You get:

 - sensors: SOC, GIDs, battery capacity (Ah), SOH, Hx, battery/ambient temperature, battery voltage, current and
   power (`BatPower`, W, positive = discharging), charging power, speed, motor RPM, odometer, trip, elevation,
   phone battery, plug state, charge mode, last update;
 - binary sensors: plugged in, charging, power switch;
 - `device_tracker.leafspy` from the phone GPS.

The device goes `unavailable` when the bridge stops (MQTT last will on `<MQTT_TOPIC>/status`). Discovery is
re-sent when Home Assistant restarts (`homeassistant/status` = `online`).

The JSON state message on `MQTT_TOPIC` is kept (retained) for other consumers. Compared with v1 it no longer
contains `user` and `pass`, temperatures are always °C, and `BatPower` and `received_at` (ISO 8601 UTC) are added.

`PlugState`: 0 = not plugged, 1 = partially plugged, 2 = plugged. `ChrgMode`: 0 = not charging, 1 = L1, 2 = L2,
3 = L3/CHAdeMO.

## Development
```
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -q
```
