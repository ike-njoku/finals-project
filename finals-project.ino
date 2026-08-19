#include <Wire.h>
#include <WiFiS3.h>
#include <WebSocketsClient.h>
#include "LSM6DS3.h"
#include <ArduinoJson.h>

#define WIFI_SSID     "Galaxy A36 5G 269C"
#define WIFI_PASSWORD "Kemi1234"

// #define WIFI_SSID     "ZWS Iphone"
// #define WIFI_PASSWORD "zwsiphone"

// Name of this specific sensor on your body ("Lumbar", "Thigh", or "Knee")
#define SENSOR_PLACEMENT "Knee" 

// const char* websockets_server_host = "10.133.215.60"; 
const char* websockets_server_host = "172.23.167.163"; 

const uint16_t websockets_server_port = 5001;

LSM6DS3 imu(I2C_MODE, 0x6A);
WebSocketsClient webSocket;

// batch sizing, sample rates and sample intervals
const unsigned long SAMPLE_INTERVAL_US = 20000UL; // 20,000 microseconds (50 Hz)
unsigned long lastSampleTimeUs = 0; 

const int BATCH_SIZE = 10;

struct SensorReading {
  unsigned long timestamp;
  float ax, ay, az;
  float gx, gy, gz;
  int rssi;
};

SensorReading sampleBuffer[BATCH_SIZE];
int sampleCount = 0; 
int currentRssi = -60; // Cached RSSI value

bool webSocketIsConnected = false;
bool sessionStarted = false; 

// websocket
void webSocketEvent(WStype_t type, uint8_t * payload, size_t length) {
  switch(type) {
    case WStype_DISCONNECTED:
      Serial.println("WebSocket Disconnected!");
      webSocketIsConnected = false;
      sessionStarted = false; 
      break;

    case WStype_CONNECTED:
      Serial.println("WebSocket Connected!");
      webSocketIsConnected = true;
      break;

    case WStype_TEXT: {
      StaticJsonDocument<128> doc;
      DeserializationError error = deserializeJson(doc, payload, length);

      if (error) {
        Serial.print("JSON Error: ");
        Serial.println(error.f_str());
        return;
      } 

      if (doc.containsKey("command")) {
        const char* command = doc["command"];

        if (strcmp(command, "START") == 0) {
          sessionStarted = true;
          sampleCount = 0; 
          lastSampleTimeUs = micros(); 
          Serial.println("==========================================");
          Serial.println(" EXPERIMENT STARTED - STREAMING 50 Hz ");
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

// send batches over wifi
void sendBatchOverWiFi() {
  // Update RSSI once per batch (every 200ms) instead of on every 20ms sample
  currentRssi = WiFi.RSSI();

  StaticJsonDocument<2048> doc;
  doc["node"] = SENSOR_PLACEMENT;

  JsonArray samplesArray = doc.createNestedArray("samples");

  for (int i = 0; i < sampleCount; i++) {
    JsonObject sampleObj = samplesArray.createNestedObject();
    sampleObj["timestamp"] = sampleBuffer[i].timestamp;
    sampleObj["ax"] = sampleBuffer[i].ax;
    sampleObj["ay"] = sampleBuffer[i].ay;
    sampleObj["az"] = sampleBuffer[i].az;
    sampleObj["gx"] = sampleBuffer[i].gx;
    sampleObj["gy"] = sampleBuffer[i].gy;
    sampleObj["gz"] = sampleBuffer[i].gz;
    sampleObj["rssi"] = currentRssi; // Use cached RSSI
  }

  String jsonPayload;
  serializeJson(doc, jsonPayload);

  webSocket.sendTXT(jsonPayload);

  sampleCount = 0;
}


void setup() {
  Serial.begin(115200);
  
  Wire.begin();
  Wire.setClock(400000); // OPTIMIZATION: Boost I2C speed to 400 kHz

  // --------------------------------------------------------------------------
  // HARDWARE DATA RATE (ODR = Output Data Rate)
  // Set internal hardware rate to 104 Hz so new readings are ready twice 
  // as fast as our 50 Hz timer (every ~9.6ms vs our 20ms check).
  // --------------------------------------------------------------------------
  imu.settings.gyroEnabled = 1;
  imu.settings.gyroRange = 2000;
  imu.settings.gyroSampleRate = 104; // 104 Hz internal chip speed

  imu.settings.accelEnabled = 1;
  imu.settings.accelRange = 16;
  imu.settings.accelSampleRate = 104; // 104 Hz internal chip speed
  
  if (imu.begin() != 0) {
    Serial.println("IMU Initialization Error!");
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

  if (webSocketIsConnected && sessionStarted) {
    unsigned long nowUs = micros();

    // Check if 20,000 microseconds (20 ms) have passed
    if (nowUs - lastSampleTimeUs >= SAMPLE_INTERVAL_US) {
      
      // Prevent catch-up drift
      if (nowUs - lastSampleTimeUs > SAMPLE_INTERVAL_US * 2) {
        lastSampleTimeUs = nowUs;
      } else {
        lastSampleTimeUs += SAMPLE_INTERVAL_US; // Step strictly by 20,000us
      }

      // Read sensor values from IMU
      sampleBuffer[sampleCount].timestamp = millis();
      sampleBuffer[sampleCount].ax = imu.readFloatAccelX();
      sampleBuffer[sampleCount].ay = imu.readFloatAccelY();
      sampleBuffer[sampleCount].az = imu.readFloatAccelZ();
      sampleBuffer[sampleCount].gx = imu.readFloatGyroX();
      sampleBuffer[sampleCount].gy = imu.readFloatGyroY();
      sampleBuffer[sampleCount].gz = imu.readFloatGyroZ();

      sampleCount++;

      // Mail the batch of 10 samples (5 times per second)
      if (sampleCount >= BATCH_SIZE) {
        sendBatchOverWiFi();
      }
    }
  }
}