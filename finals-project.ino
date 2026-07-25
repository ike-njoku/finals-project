#include "LSM6DS3.h"
#include "Wire.h"
#include <string>
#include <WiFiS3.h>
#include <R4HttpClient.h>

struct SensorData {
  float ax;
  float ay;
  float az;

  float gx;
  float gy;
  float gz;
};

// Create an instance of the LSM6DS3 sensor using I2C (Address 0x6A)
LSM6DS3 sensor(I2C_MODE, 0x6A);

void setupSensor() {
 // Initialize the sensor
  if (sensor.begin() != 0) {
    Serial.println("Error: Failed to initialize LSM6DS3 accelerometer/gyroscope!");
    while (1); // stop execution if sensor is not detected
  }
  
  Serial.println("LSM6DS3 Sensor Initialized Successfully!");
  Serial.println("----------------------------------------");
  Serial.println("Accel X (g) | Accel Y (g) | Accel Z (g) | Gyro X (dps) | Gyro Y (dps) | Gyro Z (dps)");
};

const char* WIFI_SSID     = "Glide_Resident";
const char* WIFI_PASSWORD = "SkiesMarryMath";
const char* INFLUX_DB_URL = "http://192.168.1.100:5000/api/sensor-data";
WiFiClient wifiClient;
R4HttpClient http;

void connectToWiFi() {
  // Check if the onboard ESP32-S3 Wi-Fi module is responsive
  if (WiFi.status() == WL_NO_MODULE) {
    Serial.println(F("Fatal Error: Communication with WiFi module failed!"));
    while (true); // Freeze execution if hardware is not responding
  }

  // Check firmware version (optional warning)
  String firmwareVersion = WiFi.firmwareVersion();
  if (firmwareVersion < WIFI_FIRMWARE_LATEST_VERSION) {
    Serial.println(F("Notice: Please consider updating the Wi-Fi module firmware."));
  }

  Serial.print(F("Connecting to Wi-Fi network: "));
  Serial.println(WIFI_SSID);

  // Attempt connection
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  // Wait for connection with a timeout (15 seconds max)
  uint8_t attempts = 0;
  constexpr uint8_t MAX_ATTEMPTS = 30; // 30 * 500ms = 15 seconds

  while (WiFi.status() != WL_CONNECTED && attempts < MAX_ATTEMPTS) {
    delay(500);
    Serial.print(F("."));
    attempts++;
  }

  // Verify connection result
  if (WiFi.status() == WL_CONNECTED) {
    Serial.println();
    Serial.println(F("========================================"));
    Serial.println(F(" Wi-Fi Connected Successfully!"));
    Serial.print(F(" IP Address: "));
    Serial.println(WiFi.localIP());
    Serial.print(F(" Signal Strength (RSSI): "));
    Serial.print(WiFi.RSSI());
    Serial.println(F(" dBm"));
    Serial.println(F("========================================"));
  } else {
    Serial.println();
    Serial.println(F("Wi-Fi Connection Failed! Check SSID/Password or router proximity."));
  }
}

SensorData collectSensorData() {
  // Read Accelerometer values (measured in g-force)
  SensorData sensorData;
  sensorData.ax = sensor.readFloatAccelX();
  sensorData.ay = sensor.readFloatAccelY();
  sensorData.az = sensor.readFloatAccelZ();

  // Read Gyroscope values (measured in degrees per second - dps)
  sensorData.gx = sensor.readFloatGyroX();
  sensorData.gy = sensor.readFloatGyroY();
  sensorData.gz = sensor.readFloatGyroZ();
  return sensorData;
};

void uploadSensorData(int user, std::string activity) {

};

void setup() {
  // Initialize serial communication at 115200 baud
  Serial.begin(115200);
  while (!Serial) {;}; // wait for serial to begin
  // setupWIFI(); 
  connectToWiFi();
  setupSensor();
};

void loop() {
  
  if (Serial.available()) {
    String name = Serial.readStringUntil('\n'); // Reads until you hit Enter
    name.trim(); // Optional: removes trailing whitespace or newline characters
    Serial.print(name);
  }

  SensorData sensorData = collectSensorData();
  Serial.print("A: ");
  Serial.print(sensorData.ax, 3); Serial.print("\t");
  Serial.print(sensorData.ay, 3); Serial.print("\t");
  Serial.print(sensorData.az, 3); Serial.print("\t|\tG: ");
  Serial.print(sensorData.gx, 2); Serial.print("\t");
  Serial.print(sensorData.gy, 2); Serial.print("\t");
  Serial.println(sensorData.gz, 2);

  // Delay 100ms (~10Hz sample rate) ---->>> change to 20hz (confirm from prof whether we can collect our own data at 20hz to match WISDM)
  delay(100);
}