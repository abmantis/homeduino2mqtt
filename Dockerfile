FROM arm32v7/python:3

COPY homeduino2mqtt /homeduino2mqtt
RUN pip install -r /homeduino2mqtt/requirements.txt
CMD python homeduino.py --mqtt_broker rpi3 --mqtt_broker H2M_MQTT_BROKER --mqtt_user H2M_MQTT_USER --mqtt_pass H2M_MQTT_PASS --mqtt_sub_topic H2M_MQTT_SUB_TOPIC --mqtt_pub_topic H2M_MQTT_PUB_TOPIC --device H2M_DEVICE --rx_pin H2M_RX_PIN --tx_pin H2M_TX_PIN 