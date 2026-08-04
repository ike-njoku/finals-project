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


def create_session_folder() -> Path:
    """Creates a dedicated directory named with the current timestamp."""
    # Folder name format: YYYY-MM-DD_HH-MM-SS
    folder_timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    folder_path = Path(__file__).parent / folder_timestamp

    # Create the folder if it doesn't exist
    folder_path.mkdir(parents=True, exist_ok=True)
    print(f"\n[SESSION CREATED]: Log directory initialized at -> {folder_path.resolve()}\n")
    return folder_path


def get_or_create_node_file(node_name: str) -> object:
    """
    Returns an open file handle for a node inside the session folder.
    Creates the CSV file and writes headers if it doesn't exist yet.
    """
    formatted_name = node_name.lower().replace(" ", "_")

    if formatted_name not in open_file_handles:
        # File path inside the session directory: {session_folder}/session-lumbar.csv
        file_name = f"session-{formatted_name}.csv"
        file_path = session_folder_path / file_name

        file_exists = file_path.exists()

        # Open file handle with line buffering (buffering=1)
        f = open(file_path, "a", encoding="utf-8", buffering=1)

        if not file_exists:
            headers = [
                "pc_timestamp", "arduino_ms", "node_position",
                "ax", "ay", "az", "gx", "gy", "gz", "rssi"
            ]
            f.write(",".join(headers) + "\n")
            f.flush()
            os.fsync(f.fileno())  # Force OS buffer commit to hardware

            print(f"[CSV Created]: {file_path.name} inside folder '{session_folder_path.name}'")

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

    if not current_frame_buffer:
        return

    pc_timestamp = int(time.time() * 1000)

    # Cancel background safety timer task if running
    if frame_timeout_task and not frame_timeout_task.done():
        frame_timeout_task.cancel()
        frame_timeout_task = None

    # Take a copy of the current buffer and reset memory state
    snapshot = current_frame_buffer.copy()
    current_frame_buffer.clear()

    write_tasks = []

    for node_name, data in snapshot.items():
        file_handle = get_or_create_node_file(node_name)

        row = [
            str(pc_timestamp),
            str(data.get("timestamp", "")),
            str(node_name),
            str(data.get("ax", "")),
            str(data.get("ay", "")),
            str(data.get("az", "")),
            str(data.get("gx", "")),
            str(data.get("gy", "")),
            str(data.get("gz", "")),
            str(data.get("rssi", ""))
        ]
        line_to_write = ",".join(row) + "\n"

        # Queue write task to execute in parallel thread pool
        write_tasks.append(
            asyncio.to_thread(atomic_append_row, file_handle, line_to_write)
        )

    # Execute all file writes concurrently
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
    """Asynchronous CLI input task."""
    global experiment_running

    print("\n=======================================================")
    print("Press ENTER or type START to begin recording.")
    print("Type STOP to pause recording.")
    print("=======================================================\n")

    while True:
        user_input = await asyncio.to_thread(input, "Terminal Prompt > ")
        cmd = user_input.strip().upper()

        if cmd == "" or cmd == "START":
            experiment_running = True
            await broadcast_command("START")
        elif cmd == "STOP":
            experiment_running = False
            await broadcast_command("STOP")
            print("[INFO] Experiment stopped. Data logging paused.")
        else:
            print(f"[UNKNOWN COMMAND]: '{cmd}'. Use 'START' or 'STOP'.")


async def handle_incoming_data(data: dict):
    """Buffers data per node and triggers parallel writes."""
    global frame_timeout_task

    if not experiment_running:
        return

    node_name = data.get("node", "unknown")

    # Flush window if current node already sent data in this window
    if node_name in current_frame_buffer:
        await flush_all_nodes_parallel()

    current_frame_buffer[node_name] = data

    # Trigger parallel writes when all expected nodes have reported
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
    global session_folder_path
    
    # Initialize the date-stamped folder
    session_folder_path = create_session_folder()

    # Start CLI task
    asyncio.create_task(terminal_input_loop())

    try:
        async with websockets.serve(websocket_handler, "0.0.0.0", PORT):
            print(f"WebSocket Server listening on port {PORT}")
            await asyncio.Future()
    finally:
        # Safely flush and close all file handles on server shutdown
        print("\nClosing open CSV file handles...")
        for f in open_file_handles.values():
            try:
                f.flush()
                os.fsync(f.fileno())
                f.close()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())