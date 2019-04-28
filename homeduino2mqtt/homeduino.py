import argparse
import asyncio
import functools
import paho.mqtt.client as mqtt
import serial_asyncio
import socket
import time

parser = argparse.ArgumentParser(description='Simple homeduino to mqtt bridge (raw messages)')
parser.add_argument('--mqtt_broker', required=True)
parser.add_argument('--mqtt_user', required=True)
parser.add_argument('--mqtt_pass', required=True)
parser.add_argument('--mqtt_sub_topic', required=True)
parser.add_argument('--mqtt_pub_topic', required=True)
parser.add_argument('--device', required=True)
parser.add_argument('--rx_pin', required=True)
parser.add_argument('--tx_pin', required=True)

args = parser.parse_args()

class MqttClient():
    def __init__(self, host, username, password, sub_topic, pub_topic):
        self.on_data_cb = None
        self.pub_topic = pub_topic
        self.sub_topic = sub_topic
        
        self.client = mqtt.Client()
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        self.client.username_pw_set(username, password)

        print("#MQTT: connecting to", host)
        self.client.connect(host)
        self.client.loop_start()

    def _on_connect(self, client, userdata, flags, rc):
        print("#MQTT: Connected with result code "+str(rc))

        # Subscribing in on_connect() means that if we lose the connection and
        # reconnect then subscriptions will be renewed.
        self.client.subscribe(self.sub_topic)
    
    def _on_disconnect(self, client, userdata, result_code: int):
        print("#MQTT: Disconnected with result code " + str(result_code))

        # When disconnected because of calling disconnect()
        if result_code == 0:
            return

        tries = 0
        MAX_RECONNECT_WAIT = 300  # seconds

        while True:
            try:
                if self.client.reconnect() == 0:
                    print("#MQTT: Successfully reconnected to the MQTT server")
                    break
            except socket.error:
                pass

            wait_time = min(2**tries, MAX_RECONNECT_WAIT)
            print("#MQTT: Disconnected from MQTT. Trying to reconnect in " + wait_time)
            
            # It is ok to sleep here as we are in the MQTT thread.
            time.sleep(wait_time)
            tries += 1

    def _on_message(self, client, userdata, message):
        data = str(message.payload.decode("utf-8"))
        #print("#MQTT: received message =", data)
        if self.on_data_cb:
            self.on_data_cb(data)
        else:
            print("#MQTT: no data cb")

    def disconnect(self):
        self.client.disconnect()
        self.client.loop_stop()

    def send_data(self, data):
        #print("#MQTT: publishing", data)
        self.client.publish(self.pub_topic, data)


class SerialProtocol(asyncio.Protocol):
    def __init__(self):
        self.tx_pin = args.tx_pin
        self.rx_pin = args.rx_pin
        self.receiving = False
        self.rx_buffer = ""
        self.tx_queue = []

    def connection_made(self, transport):
        self.transport = transport
        print('#Serial: connection made: ', transport)
        
        self.mqtt = MqttClient(args.mqtt_broker, args.mqtt_user, args.mqtt_pass, args.mqtt_sub_topic, args.mqtt_pub_topic)        
    
    def connection_lost(self, exc):
        print('#Serial: connection lost')
        self.transport.loop.stop()

    def data_received(self, data):
        #print("#Serial: MESSAGE: ", data)
        data_str = data.decode("utf-8")
        
        msg = data_str.rstrip()
        if self.receiving:            
            self.rx_buffer = self.rx_buffer + msg
        elif msg == "ready":
            self.transport.loop.call_soon(functools.partial(self.add_to_tx_queue, "RF receive {pin}".format(pin=self.rx_pin)))
            self.mqtt.on_data_cb = self.on_mqtt_data
        elif msg.startswith("RF receive"):
            self.receiving = True
            self.rx_buffer = msg[len("RF receive "):]

        if self.receiving and data_str.endswith("\n"):
            self.receiving = False
            self.mqtt.send_data(self.rx_buffer)
            self.rx_buffer = ""
            self.process_tx_queue()

#    def pause_writing(self):
#        print('#Serial: pause writing')
#        print(self.transport.get_write_buffer_size())
#
#    def resume_writing(self):
#        print(self.transport.get_write_buffer_size())
#        print('#Serial: resume writing')

    def on_mqtt_data(self, data):
        '''Runs on the mqtt thread.'''
        #print("#Serial: on_mqtt_data")

        full_data = "RF send {pin} {repeats} {data}".format(pin=self.tx_pin, repeats=5, data=data)        
        self.transport.loop.call_soon_threadsafe(functools.partial(self.add_to_tx_queue, full_data))
    
    def add_to_tx_queue(self, data):
        #print("#Serial: adding to queue")

        self.tx_queue.append(data)
        if not self.receiving:
            self.process_tx_queue()

    def process_tx_queue(self):
        while len(self.tx_queue) > 0:
            #print("#Serial: processing queue")

            self.write_data(self.tx_queue.pop(0))
            
            if self.receiving:
                return

            # TODO: the sleep should be based on nr of repeats
            time.sleep(0.3) # lets give the radio time to send 

    def write_data(self, data):
        #print("#Serial: TX: ", data)
        self.transport.write((data + '\n').encode("utf-8"))

loop = asyncio.get_event_loop()
coro = serial_asyncio.create_serial_connection(loop, SerialProtocol, args.device, baudrate=115200)
loop.run_until_complete(coro)
loop.run_forever()
loop.close()
