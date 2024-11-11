#!/usr/bin/env python

import asyncio
import tornado
import json
import paho.mqtt.publish as publish
import hashlib

import os
from dotenv import load_dotenv

load_dotenv()

MQTT_HOST = os.getenv("MQTT_HOST")
MQTT_PORT = os.getenv("MQTT_PORT")
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
MQTT_TOPIC = os.getenv("MQTT_TOPIC")
USERNAME = os.getenv("USERNAME")
# python -c 'import hashlib; print(hashlib.sha3_512(b"Nobody inspects the spammish repetition").hexdigest())'
PASSWORD_HASH = os.getenv("PASSWORD_HASH")


class MainHandler(tornado.web.RequestHandler):
    def get(self):

        try:
            self.get_argument("user")
            self.get_argument("pass")
        except tornado.web.MissingArgumentError:
            self.set_status(401, "Unauthorized")
            self.write("HTTP/401 Unauthorized")
            return

        payload = {}
        # ugly way to convert string to numbers
        for key, value in self.request.arguments.items():
            payload[key] = value[0].decode("utf-8")
            try:
                payload[key] = int(payload[key])
            except ValueError:
                try:
                    payload[key] = float(payload[key])
                except ValueError:
                    pass
        
        publish.single(MQTT_TOPIC, json.dumps(payload), hostname=MQTT_HOST, auth={'username': MQTT_USERNAME, 'password': MQTT_PASSWORD})

        self.write(json.dumps(payload))

def make_app():
    return tornado.web.Application([
        (r"/", MainHandler),
    ])

async def main():
    app = make_app()
    app.listen(8888)
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
