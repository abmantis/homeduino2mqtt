import argparse
import asyncio
import functools
import paho.mqtt.client as mqtt
import serial_asyncio
import socket
import time

import logging
logging.getLogger(__name__).addHandler(logging.NullHandler())
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger()

parser = argparse.ArgumentParser(description='Simple homeduino to mqtt bridge (raw messages)')
parser.add_argument('--mqtt_broker', required=True)
parser.add_argument('--mqtt_user', required=True)
parser.add_argument('--mqtt_pass', required=True)
parser.add_argument('--mqtt_rf_tx_topic', required=True)
parser.add_argument('--mqtt_rf_tx_state_topic', required=True)
parser.add_argument('--mqtt_rf_rx_topic', required=True)
parser.add_argument('--device', required=True)
parser.add_argument('--rx_pin', required=True)
parser.add_argument('--tx_pin', required=True)

args = parser.parse_args()

async def event_wait(evt, timeout):
    '''Wait for an event or timeout.'''
    try:
        await asyncio.wait_for(evt.wait(), timeout)
    except asyncio.TimeoutError:
        pass
    return evt.is_set()

class MqttClient():
    def __init__(self, host, username, password, rf_tx_topic, rf_tx_state_topic, rf_rx_topic):
        self.on_data_cb = None
        self.rf_rx_topic = rf_rx_topic        
        self.rf_tx_topic = rf_tx_topic
        self.rf_tx_state_topic = rf_tx_state_topic
        
        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.username_pw_set(username, password)

        logger.info("MQTT: connecting to %s", host)
        self.client.connect(host)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, rc):
        logger.info("MQTT: Connected with result code %s", str(rc))

        # Subscribing in on_connect() means that if we lose the connection and
        # reconnect then subscriptions will be renewed.
        self.client.subscribe(self.rf_tx_topic)
    
    def _on_disconnect(self, client, userdata, result_code: int):
        logger.error("MQTT: Disconnected with result code %s", result_code)

        # When disconnected because of calling disconnect()
        if result_code == 0:
            return

        tries = 0
        MAX_RECONNECT_WAIT = 300  # seconds

        while True:
            try:
                if self.client.reconnect() == 0:
                    logger.info("MQTT: Successfully reconnected to the MQTT server")
                    break
            except socket.error:
                pass

            wait_time = min(2**tries, MAX_RECONNECT_WAIT)
            logger.error("MQTT: Disconnected from MQTT. Trying to reconnect in " + wait_time)
            
            # It is ok to sleep here as we are in the MQTT thread.
            time.sleep(wait_time)
            tries += 1

    def _on_message(self, client, userdata, message):
        data = str(message.payload.decode("utf-8"))
        logger.debug("MQTT: received message: %s", data)
        if self.on_data_cb:
            self.on_data_cb(data)
        else:
            logger.error("MQTT: no data cb")

    def disconnect(self):
        self.client.disconnect()
        self.client.loop_stop()

    def send_rf_rx(self, data):
        #logger.debug("MQTT: send_rf_rx %s", data)
        self.client.publish(self.rf_rx_topic, data)

    def send_rf_tx_state(self, data):
        self.client.publish(self.rf_tx_state_topic, data)


class SerialProtocol(asyncio.Protocol):
    def __init__(self):
        self.tx_pin = args.tx_pin
        self.rx_pin = args.rx_pin
        self.receiving = False
        self.rx_buffer = ""
        self.rf_send_queue = []
        self.rf_send_waiting_ack = ""
        self.rf_send_request_event = asyncio.Event()
        self.rf_send_ack_event = asyncio.Event()
        self.receiving_finished_event = asyncio.Event()
        self.init_done = False

    def connection_made(self, transport):
        self.transport = transport
        logger.info('Serial: connection made')
        
        self.mqtt = MqttClient(args.mqtt_broker, args.mqtt_user, args.mqtt_pass, 
            args.mqtt_rf_tx_topic, args.mqtt_rf_tx_state_topic, args.mqtt_rf_rx_topic)        
    
    def connection_lost(self, exc):
        logger.error('Serial: connection lost')
        self.transport.loop.stop()

    def data_received(self, data):
        asyncio.ensure_future(self.handle_data_rx(data))

    async def handle_data_rx(self, data):
        logger.debug("Serial: MESSAGE: %s", data)
        data_str = data.decode("utf-8")
        
        msg = data_str.rstrip()

        if msg == "ready":
            self.write_data("RF receive {pin}".format(pin=self.rx_pin))
            if not self.init_done:
                asyncio.ensure_future(self.rf_send_queue_loop())
                self.mqtt.on_data_cb = self.on_mqtt_data
                self.init_done = True
            else:
                logger.error("Serial: received 'redy' more than once. Connection droped?")

        if self.receiving:            
            self.rx_buffer = self.rx_buffer + msg
       
        elif msg == "ACK":
            if len(self.rf_send_waiting_ack) > 0:
                logger.debug("Serial: received ACK for %s", self.rf_send_waiting_ack)
                self.mqtt.send_rf_tx_state(self.rf_send_waiting_ack)
                self.rf_send_waiting_ack = ""
                self.rf_send_ack_event.set()

        elif msg.startswith("ERR"):
            if len(self.rf_send_waiting_ack) > 0:
                logger.debug("Serial: received <{err}> for {msg}".format(err=msg, msg=self.rf_send_waiting_ack))
                self.rf_send_ack_event.set()

        elif msg.startswith("RF receive"):
            self.receiving = True
            self.rx_buffer = msg[len("RF receive "):]

        if self.receiving and data_str.endswith("\n"):
            self.receiving = False
            self.mqtt.send_rf_rx(self.rx_buffer)
            self.rx_buffer = ""
        
        if not self.receiving:
            self.receiving_finished_event.set()

    def on_mqtt_data(self, data):
        '''Runs on the mqtt thread.'''
        logger.debug("Serial: on_mqtt_data")

        self.transport.loop.call_soon_threadsafe(functools.partial(self.add_to_rf_send_queue, data))
    
    def add_to_rf_send_queue(self, data):
        logger.debug("Serial: adding to queue")

        self.rf_send_queue.append(data)
        self.rf_send_request_event.set()

    async def rf_send_queue_loop(self):
        while True:            
            await self.rf_send_request_event.wait()
            self.rf_send_request_event.clear()

            while len(self.rf_send_queue) > 0:
                if self.receiving:
                    await event_wait(self.receiving_finished_event, 3)
                    self.receiving_finished_event.clear()
                else:
                    self.rf_send_queue_pop()
                    await event_wait(self.rf_send_ack_event, 3)
                    self.rf_send_ack_event.clear()

    def rf_send_queue_pop(self):
        self.rf_send_waiting_ack = self.rf_send_queue.pop(0)
        logger.debug("Serial: sending %s", self.rf_send_waiting_ack)
        self.rf_send(self.rf_send_waiting_ack)

    def rf_send(self, data):
        full_msg = "RF send {pin} {repeats} {data}".format(pin=self.tx_pin, repeats=5, data=data)
        self.rf_send_waiting_ack = data
        self.write_data(full_msg)

    def write_data(self, data):
        #print("Serial: TX: ", data)        
        self.transport.write((data + '\n').encode("utf-8"))

#    def pause_writing(self):
#        print('Serial: pause writing')
#        print(self.transport.get_write_buffer_size())
#
#    def resume_writing(self):
#        print(self.transport.get_write_buffer_size())
#        print('Serial: resume writing')

loop = asyncio.get_event_loop()
coro = serial_asyncio.create_serial_connection(loop, SerialProtocol, args.device, baudrate=115200)
loop.run_until_complete(coro)
loop.run_forever()
loop.close()
