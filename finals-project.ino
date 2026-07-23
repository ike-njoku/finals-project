#include "LSM6DS3.h"
#include "Wire.h"

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



void setup() {
  // Initialize serial communication at 115200 baud
  Serial.begin(115200);
  while (!Serial) {;}; // wait for serial to begin
  setupSensor()
}


void loop() {

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