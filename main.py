import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
import websockets

# Configuration
PORT = 5001
EXPECTED_NODES = ["Lumbar", "Thigh", "Knee"]  # Adjust to match your node names
BUFFER_TIMEOUT_SECONDS = 0.035  # 35ms safety timeout buffer for 50Hz (20ms)

# Global State
open_file_handles = {}
current_frame_buffer = {}
frame_timeout_task = None

connected_sockets = set()
experiment_running = False

session_folder_path: Path = None
current_activity: str = "unlabeled"


def get_data_dir() -> Path:
    """Returns the base /data directory, creating it if necessary."""
    data_dir = Path(__file__).parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir


def get_next_user_id() -> str:
    """
    Calculates default user ID based on immediate subdirectories in /data/.
    If empty, returns 'user_1'.
    """
    data_dir = get_data_dir()
    subdirs = [d for d in data_dir.iterdir() if d.is_dir()]
    return f"user_{len(subdirs) + 1}"


def close_all_file_handles():
    """Safely flushes, syncs, and closes all open CSV file handles."""
    global open_file_handles
    for f in open_file_handles.values():
        try:
            f.flush()
            os.fsync(f.fileno())
            f.close()
        except Exception:
            pass
    open_file_handles.clear()


def get_or_create_node_file(node_name: str) -> object:
    """
    Returns an open file handle for a node inside /data/{user_id}/{activity}/.
    Creates the CSV file and writes headers if it doesn't exist yet.
    """
    formatted_name = node_name.lower().replace(" ", "_")

    if formatted_name not in open_file_handles:
        file_name = f"session-{formatted_name}.csv"
        file_path = session_folder_path / file_name

        file_exists = file_path.exists()

        # Open file handle with line buffering (buffering=1)
        f = open(file_path, "a", encoding="utf-8", buffering=1)

        if not file_exists:
            headers = [
                "pc_timestamp", "arduino_ms", "node_position", "activity",
                "ax", "ay", "az", "gx", "gy", "gz", "rssi"
            ]
            f.write(",".join(headers) + "\n")
            f.flush()
            os.fsync(f.fileno())

            print(f"[CSV Created]: {file_path.relative_to(get_data_dir().parent)}")

        open_file_handles[formatted_name] = f

    return open_file_handles[formatted_name]


def atomic_append_row(f, line: str):
    """Appends a single CSV row atomically to disk."""
    f.write(line)
    f.flush()
    os.fsync(f.fileno())


async def flush_all_nodes_parallel():
    """
    Triggers concurrent, parallel atomic disk writes 
    for all buffered node data at the exact same instant.
    """
    global current_frame_buffer, frame_timeout_task

    if not current_frame_buffer or not experiment_running:
        return

    pc_timestamp = int(time.time() * 1000)

    if frame_timeout_task and not frame_timeout_task.done():
        frame_timeout_task.cancel()
        frame_timeout_task = None

    snapshot = current_frame_buffer.copy()
    current_frame_buffer.clear()

    write_tasks = []

    for node_name, data in snapshot.items():
        file_handle = get_or_create_node_file(node_name)

        row = [
            str(pc_timestamp),
            str(data.get("timestamp", "")),
            str(node_name),
            str(current_activity),
            str(data.get("ax", "")),
            str(data.get("ay", "")),
            str(data.get("az", "")),
            str(data.get("gx", "")),
            str(data.get("gy", "")),
            str(data.get("gz", "")),
            str(data.get("rssi", ""))
        ]
        line_to_write = ",".join(row) + "\n"

        write_tasks.append(
            asyncio.to_thread(atomic_append_row, file_handle, line_to_write)
        )

    await asyncio.gather(*write_tasks, return_exceptions=True)


async def timeout_worker():
    """Flushes buffered frames if one node delays or drops packet."""
    try:
        await asyncio.sleep(BUFFER_TIMEOUT_SECONDS)
        await flush_all_nodes_parallel()
    except asyncio.CancelledError:
        pass


async def broadcast_command(command: str):
    """Sends JSON command broadcast to all active Arduino WebSocket clients."""
    if not connected_sockets:
        print("[WARNING] No Arduinos connected to receive the broadcast.")
        return

    payload = json.dumps({"command": command})
    await asyncio.gather(
        *[ws.send(payload) for ws in connected_sockets],
        return_exceptions=True
    )
    print(f"\n[BROADCAST SENT]: Command '{command}' sent to {len(connected_sockets)} node(s).")


async def terminal_input_loop():
    """CLI loop for handling START/STOP commands, user IDs, and activities."""
    global experiment_running, session_folder_path, current_activity

    print("\n=======================================================")
    print("Commands:")
    print("  START (or press ENTER) -> Begin new recording session")
    print("  STOP                   -> Stop current recording session")
    print("=======================================================\n")

    while True:
        cmd_input = await asyncio.to_thread(input, "Terminal Prompt > ")
        cmd = cmd_input.strip().upper()

        if cmd == "" or cmd == "START":
            if experiment_running:
                print("[WARNING] Session already running! Type 'STOP' first.")
                continue

            # 1. Prompt for User ID
            default_user = get_next_user_id()
            user_input = await asyncio.to_thread(
                input, f"Enter User ID [default: {default_user}]: "
            )
            user_input = user_input.strip()

            if not user_input:
                user_id = default_user
            else:
                user_id = user_input if user_input.startswith("user_") else f"user_{user_input}"

            # 2. Prompt for Activity
            act_input = await asyncio.to_thread(
                input, "Enter Activity (e.g., tug, walk, sit, stand) [default: walk]: "
            )
            act_input = act_input.strip().lower().replace(" ", "_")
            current_activity = act_input if act_input else "walk"

            # 3. Setup Target Directory: /data/{user_id}/{activity}/
            session_folder_path = get_data_dir() / user_id / current_activity
            session_folder_path.mkdir(parents=True, exist_ok=True)

            print(f"\n[SESSION INITIALIZED]")
            print(f" -> Folder:   {session_folder_path.resolve()}")
            print(f" -> User ID:  {user_id}")
            print(f" -> Activity: {current_activity}\n")

            experiment_running = True
            await broadcast_command("START")

        elif cmd == "STOP":
            if not experiment_running:
                print("[INFO] No active session to stop.")
                continue

            experiment_running = False
            await broadcast_command("STOP")
            close_all_file_handles()
            print("[INFO] Recording stopped. File handles closed and synced to disk.\n")
        else:
            print(f"[UNKNOWN COMMAND]: '{cmd}'. Use 'START' or 'STOP'.")


async def handle_incoming_data(data: dict):
    """Buffers incoming telemetry and triggers parallel disk writes."""
    global frame_timeout_task

    if not experiment_running:
        return

    node_name = data.get("node", "unknown")

    if node_name in current_frame_buffer:
        await flush_all_nodes_parallel()

    current_frame_buffer[node_name] = data

    if all(node in current_frame_buffer for node in EXPECTED_NODES):
        await flush_all_nodes_parallel()
    else:
        if frame_timeout_task is None or frame_timeout_task.done():
            frame_timeout_task = asyncio.create_task(timeout_worker())


async def websocket_handler(websocket):
    connected_sockets.add(websocket)
    print(f"\n[Connected]: Node joined. Total connected Arduinos: {len(connected_sockets)}")

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                await handle_incoming_data(data)
            except json.JSONDecodeError:
                print("JSON parse error:", message)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        connected_sockets.remove(websocket)
        print(f"\n[Disconnected]: Node disconnected. Remaining: {len(connected_sockets)}")


async def main():
    get_data_dir()
    asyncio.create_task(terminal_input_loop())

    try:
        async with websockets.serve(websocket_handler, "0.0.0.0", PORT):
            print(f"WebSocket Server listening on port {PORT}")
            await asyncio.Future()
    finally:
        print("\nClosing open CSV file handles...")
        close_all_file_handles()


if __name__ == "__main__":
    asyncio.run(main())