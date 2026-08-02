import express, { Request, Response } from "express";
import http from "http";
import { WebSocketServer, WebSocket } from "ws";
import fs from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

import { networkInterfaces } from "os";

// --- Types ---
interface SensorLog {
  pc_timestamp: string;
  arduino_ms: string;
  ax: string;
  ay: string;
  az: string;
  gx: string;
  gy: string;
  gz: string;
  rssi: string;
}

const logInfoToConsole = (message: string) => {
  const timestamp = new Date().toUTCString();
  console.log("--------------------------------------------------------");
  console.log(` ${timestamp} - ${message}`);
  console.log("--------------------------------------------------------");
};

const createSessionCSV = async (): Promise<string> => {
  const safeTimestamp = new Date().toISOString().replace(/[:.]/g, "-");
  const fileName = `sensor_log_${safeTimestamp}.csv`;
  const filePath = path.join(__dirname, fileName);

  const csvHeaders = "pc_timestamp,arduino_ms,ax,ay,az,gx,gy,gz,rssi\n";

  // Use async writeFile with await
  await fs.writeFile(filePath, csvHeaders, "utf8");

  logInfoToConsole(`Created new CSV file: ${fileName}`);
  return filePath;
};

const logSensorData = async (
  filePath: string,
  log: SensorLog,
): Promise<void> => {
  try {
    // Format the SensorLog object into a CSV row string
    const csvRow = `${log.pc_timestamp},${log.arduino_ms},${log.ax},${log.ay},${log.az},${log.gx},${log.gy},${log.gz},${log.rssi}\n`;

    // Append the row to the file specified by filePath asynchronously
    return await fs.appendFile(filePath, csvRow, "utf8");
  } catch (error) {
    console.error(`Error appending sensor data to ${filePath}:`, error);
  }
};

interface LogResponse {
  status: string;
}

interface ErrorResponse {
  error: string;
}

// --- Express App Setup ---
const app = express();

// --- HTTP & WebSocket Server Setup ---
const PORT = Number(process.env.PORT) || 5001;
const HOST = "0.0.0.0"; // Allows external local network devices (like Arduino) to connect

const server = http.createServer(app);
const wss = new WebSocketServer({ server });

let activeConnections = 0;
const connectedNodes = {
  "Lower Lumber": false,
  "Anterior Thigh": false,
  "Sub-Patellar Shank": false,
};

wss.on("connection", async (ws: WebSocket, req) => {
  const clientIp = req.socket.remoteAddress;
  activeConnections++;

  if (activeConnections == 1) {
    logInfoToConsole(`Lower Lumber node Connected from ${clientIp}`);
    connectedNodes["Lower Lumber"] = true;
  } else if (activeConnections == 2) {
    logInfoToConsole(`Anterior Thigh node Connected from ${clientIp}`);
    connectedNodes["Anterior Thigh"] = true;
  } else if (activeConnections == 3) {
    logInfoToConsole(`Sub-Patellar Shank node Connected from ${clientIp}`);
    connectedNodes["Sub-Patellar Shank"] = true;
  }

  if (activeConnections < 2) return;
  logInfoToConsole("Creating Session CSV for logging data logging...");

  const sessionCSV = await createSessionCSV();

  ws.on("message", (data: Buffer | string) => {
    const message = data.toString();
    console.log(`[Data Received]: ${message}`);

    // Parse comma-separated string from Arduino
    // Format: "now, ax, ay, az, gx, gy, gz, rssi"
    const parts = message.split(",");

    if (parts.length === 8) {
      const logData: SensorLog = {
        pc_timestamp: new Date().toISOString(),
        arduino_ms: parts[0],
        ax: parts[1],
        ay: parts[2],
        az: parts[3],
        gx: parts[4],
        gy: parts[5],
        gz: parts[6],
        rssi: parts[7],
      };

      // log values
      logSensorData(sessionCSV, logData);
    }
  });

  ws.on("close", () => {
    logInfoToConsole(`Node disconnected from ${clientIp}`);
    activeConnections--;
    logInfoToConsole(
      `Stopping data logging. Active connections: ${activeConnections}`,
    );
  });

  ws.on("error", (err) => {
    console.error("[WebSocket Error]:", err);
  });
});

server.listen(PORT, HOST, () => {
  console.log("\n\n********************************************************\n");
  logInfoToConsole(`WebSocket Server started on ws://${HOST}:${PORT}`);

  if (!activeConnections) {
    logInfoToConsole("Waiting for Lower Lumber Node to connect...");
  }
});
