# leafspy server

This is simple LeafSpy server that bridges LeafSpy to MQTT broker. 

## Environment variables
Configuration is done via environment variables:
 - `MQTT_PORT` - port of your MQTT server
 - `MQTT_USERNAME` - username in your MQTT server
 - `MQTT_PASSWORD` - password to your MQTT server
 - `MQTT_TOPIC` - topic to which messages should be sent
 - `MQTT_HOST` - address of your MQTT server
 - `PASSWORD_HASH` - hashed password entered in LeafSpy, can be generated with: `python -c 'import hashlib; print(hashlib.sha3_512(b"Nobody inspects the spammish repetition").hexdigest())'`
 - `USERNAME=q84fh` - username entered in LeafSpy

 ## Example docker-compose

 You need MQTT broker. You can install one yourself or for [deploy one built into Home Assistant](https://www.home-assistant.io/integrations/mqtt).

 ```yaml
version: '3'
services:
  leafspy2mqtt:
    image: leafspy2mqtt:latest
    ports:
      -  8888:8888
    restart: unless-stopped
    environment:
      - MQTT_PORT=1883
      - MQTT_USERNAME=leaf
      - MQTT_PASSWORD=secretpassword
      - MQTT_TOPIC=leafspy
      - MQTT_HOST=yourmqttbroker.local
      - PASSWORD_HASH=51b771f538117(...)
      - USERNAME=q84fh
    logging:
      options:
        max-size: "100M"
        max-file: "5"
 ```

## LeafSpy configuration
You need to expose port 8888 so LeafSpy would be able to reach it. You can do it via VPN to your mobile, or by just exposing it to the public Internet.

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

You can configure your LeafSpy to send data to up to 4 different servers (`A`, `B`, `C`, `D`). 

## Data format
LeafSpy sends its data with HTTP `GET` request with data placed in URL query string.

It goes like this:
```
GET /?user=q84fh&pass=Nobodyinspectsthespammishrepetition&DevBat=100&Gids=219&Lat=-11.12421&Long=-71.75095&Elv=35&Seq=143&Trip=86&Odo=37774&SOC=46.2757&AHr=107.3014&BatTemp=8.7&Amb=7.0&Wpr=0&PlugState=2&ChrgMode=2&ChrgPwr=1300&VIN=SJNFAAZE1U0134294&PwrSw=1&Tunits=C&RPM=0&SOH=92.95&Hx=102.51&Speed=0.0&BatVolts=352.56&BatAmps=-1.006
```

LeafSpy expects response HTTP/200 with payload:
```
"status":"0"
```


| Field     | Type  | Unit    | Example                             | Comment                                                      |
| --------- | ----- | --------| ------------------------------------| ------------------------------------------------------------ |
| user 	    | str   | -       | q84fh                               | Username                                                     |
| pass 	    | str   | -       | Nobodyinspectsthespammishrepetition | Password                                                     |
| DevBat 	| int   | ?       | 100                                 | I don't know, PR welcome                                     |
| Gids 	    | int   | ?       | 219                                 | RAW battery SOC reported by BMS                              |
| Lat 	    | float | °       | -11.12421                           | Lattitute from GPS                                           |
| Long 	    | float | °       | -71.75095                           | Longitute from GPS                                           |
| Elv 	    | int   | m       | 35                                  | Elevation from GPS                                           |
| Seq 	    | int   | -       | 117                                 | LeafSpy request number (resets on restart)                   |
| Trip 	    | int   | km      | 86                                  | Current trip distance (resets on restart)                    |
| Odo 	    | int   | km      | 37774                               | Total car milage                                             |
| SOC 	    | float | %       | 46.2157                             | SOC calculated by LeafSpy                                    |
| AHr 	    | float | AH      | 107.3014                            | Maximum capacity of battery                                  |
| BatTemp 	| float | °Tunits |                                     | Avarage battery temperature (check Tunits for used unit)     |
| Amb 	    | float | °Tunits | 7.5                                 | Ambient temperature (check Tunits for used unit)             |
| Wpr 	    | int   | ?       | 0                                   | I don't know, PR welcome                                     |
| PlugState | int   | -       | 2                                   | I don't know, PR welcome                                     |
| ChrgMode  | int   | -       | 2                                   | I don't know, PR welcome                                     |
| ChrgPwr 	| int   | W       | 1300                                | Negotiated charging power                                    |
| VIN 	    | str   | -       | 3VWSB81H8WM210368                   | VIN of the car                                               |
| PwrSw 	| int   | -       | 1                                   | I don't know, PR welcome                                     |
| Tunits 	| str   | -       | C                                   | Unit used to report temperatures                             |
| RPM       | int   | RPM     | 0                                   | Number of revolution per minute of motor                     |
| SOH 	    | float | %       | 92.95                               | State of health reported by BMS                              |
| Hx        | float | %       | 102.51                              | Percent of nominal battery conductivity                      |
| Speed 	| float | km/h    | 0.0                                 | Vehicle speed                                                |
| BatVolts 	| float | V       | 352.55                              | Voltage of battery                                           |
| BatAmps 	| float | A       | -1.189                              | Current flowing into (negative) or out of battery (positive) |

This is converted to JSON payload and sent towards MQTT broker. Message looks like this:

```json
{
  "user": "q84fh",
  "pass": "Nobodyinspectsthespammishrepetition",
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
  "BatAmps": -1.189
}
```

## How to use it
You can use it for example to feed this data to your [Home Assistant](https://www.home-assistant.io/) with its [MQTT integration](https://www.home-assistant.io/integrations/mqtt) and do some cool automation or just data visualisation with it. If you fill find some cool application let me know, I would love to include some examples here.

Example configuration:
```yaml
mqtt:
  sensor:
    - name: leafspy_Gids
      state_topic: "leafspy"
      value_template: "{{ value_json.Gids }}"
      
    - name: leafspy_ODO
      state_topic: "leafspy"
      device_class: distance
      unit_of_measurement: km
      state_class: "total_increasing"
      value_template: "{{ value_json.Odo }}"

    - name: leafspy_SOC
      state_topic: "leafspy"
      unit_of_measurement: "%"
      device_class: battery      
      value_template: "{{ value_json.SOC }}"
      
    - name: leafspy_AHr
      state_topic: "leafspy"
      unit_of_measurement: AHr
      device_class: battery
      value_template: "{{ value_json.AHr }}"
      
    - name: leafspy_BatTemp
      state_topic: "leafspy"
      unit_of_measurement: °C
      device_class: temperature
      value_template: "{{ value_json.BatTemp }}"
      
    - name: leafspy_Amb
      state_topic: "leafspy"
      unit_of_measurement: °C
      device_class: temperature
      value_template: "{{ value_json.Amb }}"
      
    - name: leafspy_RPM
      state_topic: "leafspy"
      unit_of_measurement: RPM
      value_template: "{{ value_json.RPM }}"
 
    - name: leafspy_SOH
      state_topic: "leafspy"
      unit_of_measurement: "%"
      value_template: "{{ value_json.SOH }}"
     
    - name: leafspy_BatVolts
      state_topic: "leafspy"
      unit_of_measurement: V
      device_class: voltage
      value_template: "{{ value_json.BatVolts }}"
      
    - name: leafspy_AvgCellVolts
      state_topic: "leafspy"
      unit_of_measurement: V
      device_class: voltage
      value_template: "{{ value_json.BatVolts | float / 96 }}"
      
    - name: leafspy_BatAmps
      state_topic: "leafspy"
      unit_of_measurement: A
      device_class: current
      value_template: "{{ value_json.BatAmps }}"
      
    - name: leafspy_BatteryEnergy
      state_topic: "leafspy"
      unit_of_measurement: kWh
      device_class: energy
      value_template: "{{ (((value_json.BatVolts | float * value_json.AHr | float) / 100000) * value_json.SOC | float) | round(2) }}"
```