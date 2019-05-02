FROM arm32v7/python:3

RUN apt-get update && \
    apt-get install --no-install-recommends -y git && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/* 

RUN git clone --depth 1 https://github.com/abmantis/homeduino2mqtt

WORKDIR /homeduino2mqtt/homeduino2mqtt
RUN pip install -r requirements.txt
CMD python homeduino.py --mqtt_broker $H2M_MQTT_BROKER --mqtt_user $H2M_MQTT_USER --mqtt_pass $H2M_MQTT_PASS --mqtt_rf_tx_topic $H2M_MQTT_RF_TX_TOPIC --mqtt_rf_tx_state_topic $H2M_MQTT_RF_TX_STATE_TOPIC --mqtt_rf_rx_topic $H2M_MQTT_RF_RX_TOPIC --device $H2M_DEVICE --rx_pin $H2M_RX_PIN --tx_pin $H2M_TX_PIN 