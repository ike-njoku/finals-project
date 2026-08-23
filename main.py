from __future__ import annotations

import asyncio
import json
import os
import time
import webbrowser
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, TextIO

import joblib
import numpy as np
from aiohttp import WSMsgType, web
from scipy.signal import butter, sosfiltfilt


# =============================================================================
# PROJECT CONFIGURATION
# =============================================================================

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MODEL_BUNDLE_PATH = BASE_DIR / "models" / "random_forest_bundle.joblib"
UI_PATH = BASE_DIR / "index.html"

HOST = "0.0.0.0"
PORT = 5001

REQUIRED_NODES = ("knee", "lumbar", "thigh")
SENSOR_CHANNELS = ("ax", "ay", "az", "gx", "gy", "gz")

MODEL_FEATURE_COLUMNS = tuple(
    f"{node}_{channel}"
    for node in REQUIRED_NODES
    for channel in SENSOR_CHANNELS
)

SAMPLING_RATE_HZ = 50
WINDOW_SECONDS = 2.0
WINDOW_SIZE = int(SAMPLING_RATE_HZ * WINDOW_SECONDS)
OVERLAP_RATIO = 0.50
WINDOW_STEP = int(WINDOW_SIZE * (1 - OVERLAP_RATIO))

LOWPASS_CUTOFF_HZ = 4.0
FILTER_ORDER = 4

CSV_HEADERS = (
    "pc_timestamp",
    "arduino_ms",
    "node_position",
    "activity",
    "ax",
    "ay",
    "az",
    "gx",
    "gy",
    "gz",
    "rssi",
)


# =============================================================================
# DOMAIN TYPES
# =============================================================================


class ServerMode(str, Enum):
    """Operating modes supported by the proof-of-concept server."""

    IDLE = "idle"
    DATA_COLLECTION = "data_collection"
    INFERENCE = "inference"


@dataclass
class PredictionResult:
    """One activity prediction returned by the deployed model."""

    activity: str
    confidence: float
    probabilities: dict[str, float]
    latency_ms: float
    created_at_ms: int


@dataclass
class ModelBundle:
    """Model plus the preprocessing metadata required for safe inference."""

    model: Any
    classes: list[str]
    feature_columns: list[str]
    sampling_rate_hz: int
    window_size: int
    window_step: int
    lowpass_cutoff_hz: float
    filter_order: int


@dataclass
class ApplicationState:
    """Shared runtime state for transport, collection, inference, and UI."""

    mode: ServerMode = ServerMode.IDLE
    connected_sockets: set[web.WebSocketResponse] = field(default_factory=set)
    node_sockets: dict[str, web.WebSocketResponse] = field(default_factory=dict)
    last_prediction: PredictionResult | None = None
    current_activity: str = "unlabeled"
    current_user_id: str | None = None
    shutdown_event: asyncio.Event = field(default_factory=asyncio.Event)


# =============================================================================
# GENERAL HELPERS
# =============================================================================


def normalise_node_name(node_name: str) -> str:
    """Return a stable lowercase node identifier."""

    return node_name.strip().lower().replace(" ", "_")


def ensure_project_paths() -> None:
    """Create the runtime data directory and validate the UI file."""

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    if not UI_PATH.exists():
        raise FileNotFoundError(f"Missing UI file: {UI_PATH}")


async def read_terminal_input(prompt: str) -> str:
    """Read terminal input without blocking the asyncio event loop."""

    return await asyncio.to_thread(input, prompt)


def get_next_user_id() -> str:
    """Return the next default user ID based on existing data folders."""

    user_numbers: list[int] = []

    for path in DATA_DIR.glob("user_*"):
        if not path.is_dir():
            continue

        suffix = path.name.removeprefix("user_")
        if suffix.isdigit():
            user_numbers.append(int(suffix))

    next_number = max(user_numbers, default=0) + 1
    return f"user_{next_number}"


# =============================================================================
# MODEL LOADING AND VALIDATION
# =============================================================================


def load_model_bundle(path: Path = MODEL_BUNDLE_PATH) -> ModelBundle:
    """Load the exported Random Forest bundle and validate its metadata."""

    if not path.exists():
        raise FileNotFoundError(
            f"Model bundle not found at {path}. "
            "Export random_forest_bundle.joblib from the notebook first."
        )

    raw_bundle = joblib.load(path)

    required_keys = {
        "model",
        "classes",
        "feature_columns",
        "sampling_rate_hz",
        "window_size",
        "window_step",
        "lowpass_cutoff_hz",
        "filter_order",
    }

    missing_keys = required_keys.difference(raw_bundle)
    if missing_keys:
        raise ValueError(f"Model bundle is missing keys: {sorted(missing_keys)}")

    bundle = ModelBundle(
        model=raw_bundle["model"],
        classes=list(raw_bundle["classes"]),
        feature_columns=list(raw_bundle["feature_columns"]),
        sampling_rate_hz=int(raw_bundle["sampling_rate_hz"]),
        window_size=int(raw_bundle["window_size"]),
        window_step=int(raw_bundle["window_step"]),
        lowpass_cutoff_hz=float(raw_bundle["lowpass_cutoff_hz"]),
        filter_order=int(raw_bundle["filter_order"]),
    )

    validate_model_bundle(bundle)
    return bundle


def validate_model_bundle(bundle: ModelBundle) -> None:
    """Reject a model whose preprocessing metadata does not match the server."""

    expected_features = list(MODEL_FEATURE_COLUMNS)

    if bundle.feature_columns != expected_features:
        raise ValueError(
            "Model feature order does not match the live server.\n"
            f"Expected: {expected_features}\n"
            f"Received: {bundle.feature_columns}"
        )

    if bundle.sampling_rate_hz != SAMPLING_RATE_HZ:
        raise ValueError(
            f"Sampling-rate mismatch: model={bundle.sampling_rate_hz}, "
            f"server={SAMPLING_RATE_HZ}."
        )

    if bundle.window_size != WINDOW_SIZE:
        raise ValueError(
            f"Window-size mismatch: model={bundle.window_size}, server={WINDOW_SIZE}."
        )

    if bundle.window_step != WINDOW_STEP:
        raise ValueError(
            f"Window-step mismatch: model={bundle.window_step}, server={WINDOW_STEP}."
        )


# =============================================================================
# RANDOM FOREST PREPROCESSING
# =============================================================================


def build_lowpass_filter(bundle: ModelBundle) -> np.ndarray:
    """Create the Butterworth SOS filter described by the model bundle."""

    return butter(
        N=bundle.filter_order,
        Wn=bundle.lowpass_cutoff_hz,
        btype="low",
        fs=bundle.sampling_rate_hz,
        output="sos",
    )


def filter_window(window: np.ndarray, sos: np.ndarray) -> np.ndarray:
    """Apply zero-phase filtering independently to every IMU channel."""

    return sosfiltfilt(sos, window, axis=0)


def extract_random_forest_features(window: np.ndarray) -> np.ndarray:
    """Convert one time-series window into the RF statistical feature vector."""

    mean_features = np.mean(window, axis=0)
    std_features = np.std(window, axis=0)
    minimum_features = np.min(window, axis=0)
    maximum_features = np.max(window, axis=0)
    median_features = np.median(window, axis=0)
    rms_features = np.sqrt(np.mean(np.square(window), axis=0))
    mean_absolute_change = np.mean(np.abs(np.diff(window, axis=0)), axis=0)

    features = np.concatenate(
        [
            mean_features,
            std_features,
            minimum_features,
            maximum_features,
            median_features,
            rms_features,
            mean_absolute_change,
        ]
    )

    return features.reshape(1, -1)


def decode_model_class(raw_class: Any, classes: list[str]) -> str:
    """Convert an encoded classifier output back to the activity label."""

    if isinstance(raw_class, str):
        return raw_class

    try:
        class_index = int(raw_class)
    except (TypeError, ValueError):
        return str(raw_class)

    if 0 <= class_index < len(classes):
        return classes[class_index]

    return str(raw_class)


# =============================================================================
# LIVE INFERENCE ENGINE
# =============================================================================


class InferenceEngine:
    """Buffer three IMU streams, build windows, and run live RF inference."""

    def __init__(self, bundle: ModelBundle):
        self.bundle = bundle
        self.filter_sos = build_lowpass_filter(bundle)
        self.node_buffers = {node: deque() for node in REQUIRED_NODES}
        self.window_buffer: deque[np.ndarray] = deque(maxlen=bundle.window_size)
        self.samples_since_prediction = 0
        self.has_predicted = False

    def reset(self) -> None:
        """Clear every live buffer before a new inference session."""

        for buffer in self.node_buffers.values():
            buffer.clear()

        self.window_buffer.clear()
        self.samples_since_prediction = 0
        self.has_predicted = False

    def add_samples(self, node: str, samples: list[dict[str, Any]]) -> PredictionResult | None:
        """Add one sensor batch and return a new prediction when a window is ready."""

        if node not in self.node_buffers:
            return None

        valid_samples = [sample for sample in samples if self._sample_is_valid(sample)]
        self.node_buffers[node].extend(valid_samples)

        latest_prediction: PredictionResult | None = None

        for aligned_sample in self._drain_aligned_samples():
            prediction = self._add_aligned_sample(aligned_sample)
            if prediction is not None:
                latest_prediction = prediction

        return latest_prediction

    def get_buffer_status(self) -> dict[str, int]:
        """Return current node and model-window buffer lengths for the UI."""

        status = {node: len(buffer) for node, buffer in self.node_buffers.items()}
        status["window"] = len(self.window_buffer)
        return status

    def _sample_is_valid(self, sample: dict[str, Any]) -> bool:
        """Check that a sample contains numeric values for all six IMU channels."""

        try:
            for channel in SENSOR_CHANNELS:
                float(sample[channel])
        except (KeyError, TypeError, ValueError):
            return False

        return True

    def _drain_aligned_samples(self) -> list[np.ndarray]:
        """Pair available knee, lumbar, and thigh samples by acquisition order."""

        aligned_samples: list[np.ndarray] = []

        while all(self.node_buffers[node] for node in REQUIRED_NODES):
            values: list[float] = []

            for node in REQUIRED_NODES:
                sample = self.node_buffers[node].popleft()
                values.extend(float(sample[channel]) for channel in SENSOR_CHANNELS)

            aligned_samples.append(np.asarray(values, dtype=np.float64))

        return aligned_samples

    def _add_aligned_sample(self, sample: np.ndarray) -> PredictionResult | None:
        """Append one 18-channel sample and predict at the configured window step."""

        self.window_buffer.append(sample)

        if len(self.window_buffer) < self.bundle.window_size:
            return None

        if not self.has_predicted:
            self.has_predicted = True
            self.samples_since_prediction = 0
            return self._predict_current_window()

        self.samples_since_prediction += 1

        if self.samples_since_prediction < self.bundle.window_step:
            return None

        self.samples_since_prediction = 0
        return self._predict_current_window()

    def _predict_current_window(self) -> PredictionResult:
        """Filter, featurise, and classify the most recent complete window."""

        raw_window = np.stack(self.window_buffer, axis=0)

        started_ns = time.perf_counter_ns()
        filtered_window = filter_window(raw_window, self.filter_sos)
        features = extract_random_forest_features(filtered_window)
        prediction = self.bundle.model.predict(features)[0]
        probabilities = self._predict_probabilities(features)
        finished_ns = time.perf_counter_ns()

        activity = decode_model_class(prediction, self.bundle.classes)
        confidence = probabilities.get(activity, max(probabilities.values(), default=0.0))

        return PredictionResult(
            activity=activity,
            confidence=confidence,
            probabilities=probabilities,
            latency_ms=(finished_ns - started_ns) / 1_000_000,
            created_at_ms=int(time.time() * 1000),
        )

    def _predict_probabilities(self, features: np.ndarray) -> dict[str, float]:
        """Return class probabilities using the classifier's own class order."""

        if not hasattr(self.bundle.model, "predict_proba"):
            return {}

        values = self.bundle.model.predict_proba(features)[0]
        model_classes = getattr(self.bundle.model, "classes_", range(len(values)))

        return {
            decode_model_class(raw_class, self.bundle.classes): float(probability)
            for raw_class, probability in zip(model_classes, values)
        }


# =============================================================================
# DATA COLLECTION SERVICE
# =============================================================================


class DataCollector:
    """Manage labelled CSV collection without leaking file logic into transport."""

    def __init__(self):
        self.session_folder: Path | None = None
        self.activity = "unlabeled"
        self.open_files: dict[str, TextIO] = {}

    def start_session(self, user_id: str, activity: str) -> Path:
        """Create the user/activity folder and prepare a new recording session."""

        self.stop_session()

        self.activity = activity
        self.session_folder = DATA_DIR / user_id / activity
        self.session_folder.mkdir(parents=True, exist_ok=True)
        return self.session_folder

    async def append_batch(self, node: str, samples: list[dict[str, Any]]) -> None:
        """Persist one incoming Arduino batch without blocking the event loop."""

        if self.session_folder is None or not samples:
            return

        batch_text = self._build_csv_batch(node, samples)
        file_handle = self._get_node_file(node)

        await asyncio.to_thread(self._write_and_sync, file_handle, batch_text)

    def stop_session(self) -> None:
        """Flush and close every open CSV file."""

        for file_handle in self.open_files.values():
            try:
                file_handle.flush()
                os.fsync(file_handle.fileno())
                file_handle.close()
            except OSError:
                pass

        self.open_files.clear()
        self.session_folder = None
        self.activity = "unlabeled"

    def _get_node_file(self, node: str) -> TextIO:
        """Return a reusable CSV file handle for one sensor node."""

        if self.session_folder is None:
            raise RuntimeError("No data-collection session is active.")

        node = normalise_node_name(node)

        if node in self.open_files:
            return self.open_files[node]

        file_path = self.session_folder / f"session-{node}.csv"
        file_exists = file_path.exists()
        file_handle = open(file_path, "a", encoding="utf-8", buffering=1)

        if not file_exists:
            file_handle.write(",".join(CSV_HEADERS) + "\n")
            file_handle.flush()
            os.fsync(file_handle.fileno())

        self.open_files[node] = file_handle
        return file_handle

    def _build_csv_batch(self, node: str, samples: list[dict[str, Any]]) -> str:
        """Convert an Arduino sensor batch into CSV rows."""

        pc_timestamp = int(time.time() * 1000)
        rows: list[str] = []

        for sample in samples:
            row = [
                pc_timestamp,
                sample.get("timestamp", ""),
                node,
                self.activity,
                sample.get("ax", ""),
                sample.get("ay", ""),
                sample.get("az", ""),
                sample.get("gx", ""),
                sample.get("gy", ""),
                sample.get("gz", ""),
                sample.get("rssi", ""),
            ]
            rows.append(",".join(str(value) for value in row))

        return "\n".join(rows) + "\n"

    @staticmethod
    def _write_and_sync(file_handle: TextIO, batch_text: str) -> None:
        """Write one complete batch and force it to disk."""

        file_handle.write(batch_text)
        file_handle.flush()
        os.fsync(file_handle.fileno())


# =============================================================================
# APPLICATION SERVICES
# =============================================================================


state = ApplicationState()
data_collector = DataCollector()
inference_engine: InferenceEngine | None = None


def register_node_socket(node: str, socket: web.WebSocketResponse) -> None:
    """Associate a connected WebSocket with a physical sensor node."""

    node = normalise_node_name(node)

    if node not in REQUIRED_NODES:
        return

    state.node_sockets[node] = socket


def unregister_socket(socket: web.WebSocketResponse) -> None:
    """Remove a disconnected socket from all connection registries."""

    state.connected_sockets.discard(socket)

    disconnected_nodes = [
        node
        for node, registered_socket in state.node_sockets.items()
        if registered_socket is socket
    ]

    for node in disconnected_nodes:
        del state.node_sockets[node]


async def broadcast_command(command: str) -> None:
    """Send a command to every currently connected Arduino client."""

    if not state.connected_sockets:
        print("[WARNING] No Arduinos connected to receive the command.")
        return

    payload = json.dumps({"command": command})

    results = await asyncio.gather(
        *(socket.send_str(payload) for socket in list(state.connected_sockets)),
        return_exceptions=True,
    )

    failures = sum(isinstance(result, Exception) for result in results)
    successful = len(results) - failures
    print(f"[BROADCAST] {command} -> {successful} Arduino(s)")


async def route_sensor_packet(
    socket: web.WebSocketResponse,
    payload: dict[str, Any],
) -> None:
    """Route a decoded Arduino message to registration, collection, or inference."""

    message_type = str(payload.get("type", "")).lower()
    node = normalise_node_name(str(payload.get("node", "unknown")))

    if message_type == "register":
        register_node_socket(node, socket)
        print(f"[REGISTERED] {node}")
        return

    samples = payload.get("samples", [])
    if not isinstance(samples, list) or not samples:
        return

    register_node_socket(node, socket)

    if state.mode == ServerMode.DATA_COLLECTION:
        await data_collector.append_batch(node, samples)
        return

    if state.mode == ServerMode.INFERENCE and inference_engine is not None:
        prediction = inference_engine.add_samples(node, samples)

        if prediction is not None:
            state.last_prediction = prediction
            print(
                f"[INFERENCE] {prediction.activity.upper():<8} "
                f"confidence={prediction.confidence:.3f} "
                f"latency={prediction.latency_ms:.2f} ms"
            )


# =============================================================================
# HTTP + WEBSOCKET ROUTES
# =============================================================================


async def root_handler(request: web.Request) -> web.StreamResponse:
    """Serve the dashboard for HTTP requests or accept Arduino WebSockets."""

    websocket_probe = web.WebSocketResponse()

    if websocket_probe.can_prepare(request).ok:
        return await arduino_websocket_handler(request)

    return web.FileResponse(UI_PATH)


async def arduino_websocket_handler(request: web.Request) -> web.WebSocketResponse:
    """Keep one Arduino WebSocket alive and route its JSON messages."""

    websocket = web.WebSocketResponse(heartbeat=30)
    await websocket.prepare(request)

    state.connected_sockets.add(websocket)
    print(f"[CONNECTED] Arduino clients: {len(state.connected_sockets)}")

    try:
        async for message in websocket:
            if message.type != WSMsgType.TEXT:
                continue

            try:
                payload = json.loads(message.data)
            except json.JSONDecodeError:
                print(f"[JSON ERROR] {message.data}")
                continue

            await route_sensor_packet(websocket, payload)
    finally:
        unregister_socket(websocket)
        print(f"[DISCONNECTED] Arduino clients: {len(state.connected_sockets)}")

    return websocket


async def api_state_handler(_: web.Request) -> web.Response:
    """Return the current server, sensor, buffer, and prediction state."""

    prediction = state.last_prediction
    buffers = inference_engine.get_buffer_status() if inference_engine else {}

    response = {
        "mode": state.mode.value,
        "connected_arduinos": len(state.connected_sockets),
        "required_arduinos": len(REQUIRED_NODES),
        "nodes": {
            node: node in state.node_sockets
            for node in REQUIRED_NODES
        },
        "buffers": buffers,
        "window_size": WINDOW_SIZE,
        "window_step": WINDOW_STEP,
        "sampling_rate_hz": SAMPLING_RATE_HZ,
        "current_user_id": state.current_user_id,
        "current_activity": state.current_activity,
        "prediction": None,
    }

    if prediction is not None:
        response["prediction"] = {
            "activity": prediction.activity,
            "confidence": prediction.confidence,
            "probabilities": prediction.probabilities,
            "latency_ms": prediction.latency_ms,
            "created_at_ms": prediction.created_at_ms,
        }

    return web.json_response(response)


async def health_handler(_: web.Request) -> web.Response:
    """Provide a small health endpoint for local debugging."""

    return web.json_response({"status": "ok", "mode": state.mode.value})


def create_web_application() -> web.Application:
    """Create the single aiohttp application used by both Arduino and browser."""

    application = web.Application()
    application.router.add_get("/", root_handler)
    application.router.add_get("/api/state", api_state_handler)
    application.router.add_get("/health", health_handler)
    return application


# =============================================================================
# TERMINAL MODE CONTROL
# =============================================================================


async def wait_for_arduinos() -> None:
    """Pause mode selection until the expected number of Arduino clients connect."""

    required_count = len(REQUIRED_NODES)

    while len(state.connected_sockets) < required_count:
        connected = len(state.connected_sockets)
        print(
            f"\rWaiting for Arduinos... {connected}/{required_count} connected",
            end="",
            flush=True,
        )
        await asyncio.sleep(1)

    print(f"\rArduinos connected: {required_count}/{required_count}           ")


async def choose_mode() -> str:
    """Prompt for the next application mode."""

    print("\n" + "=" * 62)
    print("PERSONALIZED GAIT ANALYSIS SERVER")
    print("=" * 62)
    print("  [1] Collect labelled sensor data")
    print("  [2] Run real-time activity inference")
    print("  [Q] Quit")

    return (await read_terminal_input("\nSelection > ")).strip().lower()


async def run_data_collection_session() -> None:
    """Prompt for labels, collect data, and return to the main menu on ENTER."""

    default_user = get_next_user_id()
    user_input = (await read_terminal_input(f"User ID [default: {default_user}] > ")).strip()

    user_id = user_input or default_user
    if not user_id.startswith("user_"):
        user_id = f"user_{user_id}"

    activity_input = (
        await read_terminal_input("Activity [walk] (sit / stand / walk / tug) > ")
    ).strip()

    activity = normalise_node_name(activity_input or "walk")
    folder = data_collector.start_session(user_id, activity)

    state.current_user_id = user_id
    state.current_activity = activity
    state.mode = ServerMode.DATA_COLLECTION

    print("\n[DATA COLLECTION]")
    print(f"User:     {user_id}")
    print(f"Activity: {activity}")
    print(f"Folder:   {folder}")
    print("Press ENTER when the recording is complete.\n")

    await broadcast_command("START")
    await read_terminal_input("")
    await broadcast_command("STOP")

    data_collector.stop_session()
    state.mode = ServerMode.IDLE
    state.current_user_id = None
    state.current_activity = "unlabeled"

    print("[DATA COLLECTION] Recording stopped and files closed.")


async def run_inference_session() -> None:
    """Load the deployment model, open the UI, and run until ENTER is pressed."""

    global inference_engine

    try:
        bundle = load_model_bundle()
    except (FileNotFoundError, ValueError) as error:
        print(f"\n[INFERENCE ERROR] {error}\n")
        return

    inference_engine = InferenceEngine(bundle)
    inference_engine.reset()

    state.last_prediction = None
    state.mode = ServerMode.INFERENCE

    dashboard_url = f"http://127.0.0.1:{PORT}/"

    print("\n[REAL-TIME INFERENCE]")
    print(f"Dashboard: {dashboard_url}")
    print(f"Window:    {bundle.window_size} samples")
    print(f"Step:      {bundle.window_step} samples")
    print("Press ENTER to stop inference and return to the menu.\n")

    await broadcast_command("START")

    await asyncio.to_thread(webbrowser.open, dashboard_url)
    await read_terminal_input("")

    await broadcast_command("STOP")

    state.mode = ServerMode.IDLE
    state.last_prediction = None
    inference_engine.reset()

    print("[INFERENCE] Session stopped.")


async def terminal_control_loop() -> None:
    """Run the proof-of-concept mode menu for the lifetime of the server."""

    while not state.shutdown_event.is_set():
        await wait_for_arduinos()
        selection = await choose_mode()

        if selection == "1":
            await run_data_collection_session()
        elif selection == "2":
            await run_inference_session()
        elif selection in {"q", "quit", "exit"}:
            state.shutdown_event.set()
        else:
            print(f"Unknown selection: {selection!r}")


# =============================================================================
# SERVER LIFECYCLE
# =============================================================================


async def start_server() -> web.AppRunner:
    """Start the single HTTP/WebSocket server on the configured port."""

    application = create_web_application()
    runner = web.AppRunner(application)
    await runner.setup()

    site = web.TCPSite(runner, HOST, PORT)
    await site.start()

    print("=" * 62)
    print("PERSONALIZED GAIT ANALYSIS & HAR SERVER")
    print("=" * 62)
    print(f"Arduino WebSocket: ws://<this-computer-ip>:{PORT}/")
    print(f"Dashboard:         http://127.0.0.1:{PORT}/")
    print("One Python process. One server. One port.\n")

    return runner


async def shutdown_server(runner: web.AppRunner) -> None:
    """Stop recording, stop Arduinos, close WebSockets, and clean up aiohttp."""

    state.mode = ServerMode.IDLE
    data_collector.stop_session()

    try:
        await broadcast_command("STOP")
    except Exception:
        pass

    for socket in list(state.connected_sockets):
        try:
            await socket.close()
        except Exception:
            pass

    await runner.cleanup()


async def main() -> None:
    """Application entry point."""

    ensure_project_paths()
    runner = await start_server()

    terminal_task = asyncio.create_task(terminal_control_loop())

    try:
        await state.shutdown_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        state.shutdown_event.set()
    finally:
        terminal_task.cancel()
        await shutdown_server(runner)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
