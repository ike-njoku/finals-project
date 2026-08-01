#include <Wire.h>
#include <WiFiS3.h>
#include <WebSocketsClient.h>
#include "LSM6DS3.h"

// #define WIFI_SSID     "ZWS Iphone"
// #define WIFI_PASSWORD "zwsiphone"

#define WIFI_SSID     "Glide_Resident"
#define WIFI_PASSWORD "SkiesMarryMath"

// REPLACE 'X' WITH YOUR COMPUTER'S LOCAL IP ADDRESS ON THE HOTSPOT
const char* websockets_server_host = "10.133.215.60"; 
const uint16_t websockets_server_port = 5001;

LSM6DS3 imu(I2C_MODE, 0x6A);
WebSocketsClient webSocket;

const unsigned long SEND_INTERVAL = 1000UL;
unsigned long lastSendTime = 0;
bool isWebSocketConnected = false;

// Event handler to track connection status
void webSocketEvent(WStype_t type, uint8_t * payload, size_t length) {
  switch(type) {
    case WStype_DISCONNECTED:
      Serial.println("[WebSocket] Disconnected!");
      isWebSocketConnected = false;
      break;
    case WStype_CONNECTED:
      Serial.print("[WebSocket] Connected to url: %s\n");
      // Serial.print( payload);
      isWebSocketConnected = true;
      break;
    case WStype_TEXT:
      Serial.print("[WebSocket] Received text: %s\n");
      // Serial.print( payload);

      break;
    default:
      break;
  }
}

void setup() {
  Serial.begin(115200);
  delay(1500);

  Wire.begin();
  if (imu.begin() != 0) {
    Serial.println("IMU Error!");
    while (1);
  }

  // Connect to Wi-Fi
  Serial.print("Connecting to WiFi");
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected! IP: " + WiFi.localIP().toString());

  // Initialize WebSocket connection to Node.js server
  webSocket.begin(websockets_server_host, websockets_server_port, "/");
  webSocket.onEvent(webSocketEvent);
  webSocket.setReconnectInterval(5000); // Auto retry connection every 5s if disconnected
}

void loop() {
  // Required: handles WebSocket keep-alives and incoming frames
  webSocket.loop(); 

  unsigned long now = millis();
  if (now - lastSendTime >= SEND_INTERVAL) {
    lastSendTime = now;

    if (isWebSocketConnected) {
      float ax = imu.readFloatAccelX();
      float ay = imu.readFloatAccelY();
      float az = imu.readFloatAccelZ();
      float gx = imu.readFloatGyroX();
      float gy = imu.readFloatGyroY();
      float gz = imu.readFloatGyroZ();

      String line = String(now) + "," + String(ax, 3) + "," + String(ay, 3) + "," + 
                    String(az, 3) + "," + String(gx, 3) + "," + String(gy, 3) + "," + 
                    String(gz, 3) + "," + String(WiFi.RSSI());

      // Send text frame over WebSocket
      webSocket.sendTXT(line);
      Serial.println("Sent WebSocket message: " + line);
    }
  }
}