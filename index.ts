import express, { Request, Response } from "express";
import http from "http";
import { WebSocketServer, WebSocket } from "ws";
import fs from "fs/promises";
import path from "path";
import { fileURLToPath } from "url";
const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

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

const createSessionCSV = async (): Promise<string> => {
  const safeTimestamp = new Date().toISOString().replace(/[:.]/g, "-");
  const fileName = `sensor_log_${safeTimestamp}.csv`;
  const filePath = path.join(__dirname, fileName);

  const csvHeaders = "pc_timestamp,arduino_ms,ax,ay,az,gx,gy,gz,rssi\n";

  // Use async writeFile with await
  await fs.writeFile(filePath, csvHeaders, "utf8");

  console.log(`Created new CSV file: ${fileName}`);
  return filePath;
};

const logSensorData = async (
  filePath: string,
  log: SensorLog,
): Promise<void> => {
  try {
    // 1. Format the SensorLog object into a CSV row string
    const csvRow = `${log.pc_timestamp},${log.arduino_ms},${log.ax},${log.ay},${log.az},${log.gx},${log.gy},${log.gz},${log.rssi}\n`;

    // 2. Append the row to the file specified by filePath asynchronously
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
app.use(express.json());

const router = express.Router();
router.post(
  "/logs",
  async (
    req: Request<{}, {}, SensorLog>,
    res: Response<LogResponse | ErrorResponse>,
  ) => {
    const { pc_timestamp, arduino_ms, ax, ay, az, gx, gy, gz, rssi } = req.body;

    if (
      !pc_timestamp ||
      !arduino_ms ||
      !ax ||
      !ay ||
      !az ||
      !gx ||
      !gy ||
      !gz ||
      !rssi
    ) {
      return res.status(400).json({ error: "Incomplete sample" });
    }

    return res.status(201).json({ status: "success" });
  },
);

router.get("/ping", (_req: Request, res: Response) => {
  res.send({ message: "Pinged!!!!" });
});

app.use(router);

// --- HTTP & WebSocket Server Setup ---
const PORT = Number(process.env.PORT) || 5001;
const HOST = "0.0.0.0"; // Allows external local network devices (like Arduino) to connect

const server = http.createServer(app);
const wss = new WebSocketServer({ server });

wss.on("connection", async (ws: WebSocket, req) => {
  const clientIp = req.socket.remoteAddress;
  console.log("------------starting new session ---------------");
  console.log(`[WebSocket] Client connected from ${clientIp}`);
  console.log("creating session csv");
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

      // Process or store your formatted sensor log object here
      logSensorData(sessionCSV, logData);
    }
  });

  ws.on("close", () => {
    console.log("[WebSocket] Client disconnected");
  });

  ws.on("error", (err) => {
    console.error("[WebSocket Error]:", err);
  });
});

// --- Start Listening ---
server.listen(PORT, HOST, () => {
  console.log(`Server is running on http://${HOST}:${PORT}`);
  console.log(`WebSocket Server listening on ws://${HOST}:${PORT}`);
});
