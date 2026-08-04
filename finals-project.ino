#include <Wire.h>
#include <WiFiS3.h>
#include <WebSocketsClient.h>
#include "LSM6DS3.h"
#include <ArduinoJson.h>

#define WIFI_SSID     "Glide_Resident"
#define WIFI_PASSWORD "SkiesMarryMath"

// Change this string per Arduino board: "Lumbar", "Thigh", or "Knee"
#define SENSOR_PLACEMENT "Knee" 

const char* websockets_server_host = "10.133.215.60"; 
const uint16_t websockets_server_port = 5001;

LSM6DS3 imu(I2C_MODE, 0x6A);
WebSocketsClient webSocket;

const unsigned long SEND_INTERVAL = 20UL; // 50 Hz streaming (20ms interval)
unsigned long lastSendTime = 0;
bool webSocketIsConnected = false;
bool sessionStarted = false; // Flag controlled by Python broadcast

void webSocketEvent(WStype_t type, uint8_t * payload, size_t length) {
  switch(type) {
    case WStype_DISCONNECTED:
      Serial.println("WebSocket Disconnected!");
      webSocketIsConnected = false;
      sessionStarted = false; // Reset experiment status on disconnect
      break;

    case WStype_CONNECTED:
      Serial.println("WebSocket Connected!");
      webSocketIsConnected = true;
      break;

    case WStype_TEXT: {
      Serial.print("WebSocket Received Broadcast Payload: ");
      Serial.write(payload, length);
      Serial.println();

      // Parse JSON message using ArduinoJson
      StaticJsonDocument<128> doc;
      DeserializationError error = deserializeJson(doc, payload, length);

      if (error) {
        Serial.print("WebSocket Error JSON Deserialization failed: ");
        Serial.println(error.f_str());
        return;
      }

      // 3. Handle specific command strings
      if (doc.containsKey("command")) {
        const char* command = doc["command"];

        if (strcmp(command, "START") == 0) {
          sessionStarted = true;
          Serial.println("==========================================");
          Serial.println(" EXPERIMENT STARTED - STREAMING DATA ");
          Serial.println("==========================================");
        } 
        else if (strcmp(command, "STOP") == 0) {
          sessionStarted = false;
          Serial.println("==========================================");
          Serial.println(" EXPERIMENT STOPPED - STREAMING PAUSED ");
          Serial.println("==========================================");
        }
      }
      break;
    }

    default:
      break;
  }
}

void setup() {
  Serial.begin(115200);
  Wire.begin();
  
  if (imu.begin() != 0) {
    Serial.println("IMU Error!");
    while (1);
  }

  Serial.print("Connecting to WiFi");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected! IP: " + WiFi.localIP().toString());

  webSocket.begin(websockets_server_host, websockets_server_port, "/");
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(5000);
}

void loop() {
  webSocket.loop(); 

  unsigned long now = millis();
  if (now - lastSendTime >= SEND_INTERVAL) {
    lastSendTime = now;

    // Only transmit data if node is connected AND server issued START command
    if (webSocketIsConnected && sessionStarted) {
      StaticJsonDocument<256> log;

      log["node"] = SENSOR_PLACEMENT;
      log["timestamp"] = now;
      log["ax"] = imu.readFloatAccelX();
      log["ay"] = imu.readFloatAccelY();
      log["az"] = imu.readFloatAccelZ();
      log["gx"] = imu.readFloatGyroX();
      log["gy"] = imu.readFloatGyroY();
      log["gz"] = imu.readFloatGyroZ();
      log["rssi"] = WiFi.RSSI();

      String jsonPayload;
      serializeJson(log, jsonPayload);
      
      webSocket.sendTXT(jsonPayload);
    }
  }
}