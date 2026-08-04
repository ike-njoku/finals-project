import express from "express";
import http from "http";
import { WebSocketServer, WebSocket } from "ws";
import fs from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

interface SensorData {
  node: string;
  timestamp: number; // arduino_ms
  ax: number;
  ay: number;
  az: number;
  gx: number;
  gy: number;
  gz: number;
  rssi: number;
}

// Expected node names (Must match SENSOR_PLACEMENT defined on Arduinos)
const EXPECTED_NODES = ["Lumbar", "Knee"];

// Buffer holding latest readings from each node for the current frame window
let currentFrameBuffer: Map<string, SensorData> = new Map();
let frameTimeout: NodeJS.Timeout | null = null;
let sessionCSVPath: string | null = null;

// 1. CSV Setup with Wide Headers
const createSessionCSV = async (): Promise<string> => {
  const safeTimestamp = new Date().toISOString().replace(/[:.]/g, "-");
  const fileName = `synchronized_sensor_log_${safeTimestamp}.csv`;
  const filePath = path.join(__dirname, fileName);

  const headers =
    [
      "pc_timestamp",
      // Lumbar
      "ax_lumbar",
      "ay_lumbar",
      "az_lumbar",
      "gx_lumbar",
      "gy_lumbar",
      "gz_lumbar",
      "arduino_ms_lumbar",
      // Thigh
      "ax_thigh",
      "ay_thigh",
      "az_thigh",
      "gx_thigh",
      "gy_thigh",
      "gz_thigh",
      "arduino_ms_thigh",
      // Knee
      "ax_knee",
      "ay_knee",
      "az_knee",
      "gx_knee",
      "gy_knee",
      "gz_knee",
      "arduino_ms_knee",
    ].join(",") + "\n";

  await fs.writeFile(filePath, headers, "utf8");
  console.log(`[CSV Created]: ${fileName}`);
  return filePath;
};

// 2. Combine Buffer into a Single CSV Row
const flushBufferToCSV = async () => {
  if (!sessionCSVPath || currentFrameBuffer.size === 0) return;

  const pcTimestamp = Date.now();
  const getVal = (nodeName: string, field: keyof SensorData) => {
    const data = currentFrameBuffer.get(nodeName);
    return data ? data[field] : ""; // empty string if node missed this frame
  };

  const row =
    [
      pcTimestamp,
      // Lumbar
      getVal("Lumbar", "ax"),
      getVal("Lumbar", "ay"),
      getVal("Lumbar", "az"),
      getVal("Lumbar", "gx"),
      getVal("Lumbar", "gy"),
      getVal("Lumbar", "gz"),
      getVal("Lumbar", "timestamp"),
      // Thigh
      getVal("Thigh", "ax"),
      getVal("Thigh", "ay"),
      getVal("Thigh", "az"),
      getVal("Thigh", "gx"),
      getVal("Thigh", "gy"),
      getVal("Thigh", "gz"),
      getVal("Thigh", "timestamp"),
      // Knee
      getVal("Knee", "ax"),
      getVal("Knee", "ay"),
      getVal("Knee", "az"),
      getVal("Knee", "gx"),
      getVal("Knee", "gy"),
      getVal("Knee", "gz"),
      getVal("Knee", "timestamp"),
    ].join(",") + "\n";

  // Reset frame buffer and timer
  currentFrameBuffer.clear();
  if (frameTimeout) {
    clearTimeout(frameTimeout);
    frameTimeout = null;
  }

  // Non-blocking file append
  await fs.appendFile(sessionCSVPath, row, "utf8");
};

// 3. Process Incoming Readings
const handleIncomingData = (data: SensorData) => {
  if (connectedClients < 2) return; // Ignore data if not enough clients are connected
  // If node already sent data in this window, flush current buffer and start new window
  if (currentFrameBuffer.has(data.node)) {
    flushBufferToCSV();
  }

  currentFrameBuffer.set(data.node, data);

  // If all 3 nodes have reported, flush immediately
  if (EXPECTED_NODES.every((node) => currentFrameBuffer.has(node))) {
    flushBufferToCSV();
  } else {
    // Safety timeout: If 1 node disconnects, write incomplete frame after 35ms
    // so data logging doesn't hang completely
    if (!frameTimeout) {
      frameTimeout = setTimeout(() => {
        flushBufferToCSV();
      }, 4000);
    }
  }
};

// --- WebSockets Server ---
const PORT = Number(process.env.PORT) || 5001;
const server = http.createServer(express());
const wss = new WebSocketServer({ server });

createSessionCSV().then((path) => {
  sessionCSVPath = path;
});

let connectedClients = 0;

wss.on("connection", (ws: WebSocket) => {
  connectedClients++;
  ws.on("message", (rawBuffer: Buffer) => {
    try {
      const data: SensorData = JSON.parse(rawBuffer.toString());
      handleIncomingData(data);
    } catch (err) {
      console.error("JSON parse error:", err);
    }
  });
});

server.listen(PORT, "0.0.0.0", () => {
  console.log(`WebSocket Synchronized Logger listening on port ${PORT}`);
});
