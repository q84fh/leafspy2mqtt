#!/usr/bin/env python
import asyncio
import tornado
import json
import paho.mqtt.publish as publish
import hashlib
import os
import ssl
from dotenv import load_dotenv

load_dotenv()
MQTT_HOST = os.getenv("MQTT_HOST")
MQTT_PORT = int(os.getenv("MQTT_PORT", 8883))  # Default TLS port
MQTT_USERNAME = os.getenv("MQTT_USERNAME")
MQTT_PASSWORD = os.getenv("MQTT_PASSWORD")
MQTT_TOPIC = os.getenv("MQTT_TOPIC")
USERNAME = os.getenv("USERNAME")
PASSWORD_HASH = os.getenv("PASSWORD_HASH")

# TLS configuration
MQTT_TLS_ENABLED = os.getenv("MQTT_TLS_ENABLED", "true").lower() == "true"
MQTT_TLS_CA_CERTS = os.getenv("MQTT_TLS_CA_CERTS")  # Path to CA certificate file
MQTT_TLS_CERT_FILE = os.getenv("MQTT_TLS_CERT_FILE")  # Optional client certificate
MQTT_TLS_KEY_FILE = os.getenv("MQTT_TLS_KEY_FILE")    # Optional client key
MQTT_TLS_INSECURE = os.getenv("MQTT_TLS_INSECURE", "false").lower() == "true"  # Skip certificate verification if true

tornado.log.enable_pretty_logging()

class MainHandler(tornado.web.RequestHandler):
    def get(self):
        try:
            user = self.get_argument("user")
            password = self.get_argument("pass")
        except tornado.web.MissingArgumentError:
            self.set_status(401, "Unauthorized")
            self.write("HTTP/401 Unauthorized")
            return
            
        if user != USERNAME or hashlib.sha3_512(password.encode()).hexdigest() != PASSWORD_HASH:
            self.set_status(403, "Forbidden")
            self.write("HTTP/403 Forbidden")
            return
            
        payload = {}
        # Convert string values to numbers when possible
        for key, value in self.request.arguments.items():
            payload[key] = value[0].decode("utf-8")
            try:
                payload[key] = int(payload[key])
            except ValueError:
                try:
                    payload[key] = float(payload[key])
                except ValueError:
                    pass
        
        # Prepare MQTT auth
        auth = {'username': MQTT_USERNAME, 'password': MQTT_PASSWORD} if MQTT_USERNAME else None
        
        # Prepare TLS configuration if enabled
        tls = None
        if MQTT_TLS_ENABLED:
            tls = {
                'ca_certs': MQTT_TLS_CA_CERTS,
                'certfile': MQTT_TLS_CERT_FILE,
                'keyfile': MQTT_TLS_KEY_FILE,
                'tls_version': ssl.PROTOCOL_TLS,
                'insecure': MQTT_TLS_INSECURE
            }
            
            # Remove None values from TLS config
            tls = {k: v for k, v in tls.items() if v is not None}
            
       
        publish.single(
            MQTT_TOPIC, 
            json.dumps(payload), 
            hostname=MQTT_HOST,
            port=MQTT_PORT,
            auth=auth,
            tls=tls
        )
        self.write('"status":"0"')

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