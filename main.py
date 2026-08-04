import asyncio
import json
import os
import time
from datetime import datetime
from pathlib import Path
import websockets

# use locks to ensure atomicity during file writes
try:
    import fcntl
    def lock_file(f): fcntl.flock(f, fcntl.LOCK_EX)
    def unlock_file(f): fcntl.flock(f, fcntl.LOCK_UN)
except ImportError:
    # Windows fallback
    import msvcrt
    def lock_file(f): msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
    def unlock_file(f): msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)

PORT = 5001

open_file_handles = {}  # Store open file objects per node
connected_sockets = set()
experiment_running = False


def get_or_create_node_file(node_name: str, safe_timestamp: str):
    """
    Returns an open file handle for a node. 
    Creates the file and writes the header safely if it doesn't exist yet.
    """
    formatted_name = node_name.lower().replace(" ", "_")

    if formatted_name not in open_file_handles:
        file_name = f"session-{formatted_name}_{safe_timestamp}.csv"
        file_path = Path(__file__).parent / file_name
        
        file_exists = file_path.exists()
        
        f = open(file_path, "a", encoding="utf-8", buffering=1)

        if not file_exists:
            headers = [
                "pc_timestamp", "arduino_ms", "node_position",
                "ax", "ay", "az", "gx", "gy", "gz", "rssi"
            ]
            lock_file(f)
            try:
                f.write(",".join(headers) + "\n")
                f.flush()
                os.fsync(f.fileno())  # Atomic flush to disk hardware
            finally:
                unlock_file(f)

            print(f"[CSV Created for {node_name}]: {file_name}")

        open_file_handles[formatted_name] = f

    return open_file_handles[formatted_name]


def atomic_append_row(f, line: str):
    """
    Synchronous atomic write: Locks the file, writes the row, 
    flushes the stream buffer, and syncs OS disk state before releasing lock.
    """
    lock_file(f)
    try:
        f.write(line)
        f.flush()            # Flush Python internal buffer to OS
        os.fsync(f.fileno()) # Force OS kernel buffer onto physical storage (atomic write)
    finally:
        unlock_file(f)


async def broadcast_command(command: str):
    """Sends a JSON command to all connected Arduino nodes."""
    if not connected_sockets:
        print("No Nodes connected to receive the broadcast.")
        return

    payload = json.dumps({"command": command})
    await asyncio.gather(
        *[ws.send(payload) for ws in connected_sockets],
        return_exceptions=True
    )
    print(f"\n Command '{command}' sent to {len(connected_sockets)} node(s).")


async def terminal_input_loop():
    """Asynchronous loop waiting for keyboard input in the terminal."""
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
            print("Experiment stopped. Data logging paused.")
        else:
            print(f"UNKNOWN COMMAND: '{cmd}'. Use 'START' or 'STOP'.")


async def handle_incoming_data(data: dict, session_timestamp: str):
    """Processes incoming data and performs an atomic write to the CSV file."""
    if not experiment_running:
        return

    node_name = data.get("node", "unknown")
    file_handle = get_or_create_node_file(node_name, session_timestamp)

    pc_timestamp = int(time.time() * 1000)

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

    # Execute atomic write off the main event loop thread
    await asyncio.to_thread(atomic_append_row, file_handle, line_to_write)


async def websocket_handler(websocket, session_timestamp: str):
    connected_sockets.add(websocket)
    print(f"\n Node joined. Total connected Nodes: {len(connected_sockets)}")

    try:
        async for message in websocket:
            try:
                data = json.loads(message)
                await handle_incoming_data(data, session_timestamp)
            except json.JSONDecodeError:
                print("JSON parse error:", message)
    except websockets.exceptions.ConnectionClosed:
        pass
    finally:
        connected_sockets.remove(websocket)
        print(f"\n[Disconnected]: Node disconnected. Remaining: {len(connected_sockets)}")


async def main():
    session_timestamp = datetime.now().isoformat().replace(":", "-").replace(".", "-")

    asyncio.create_task(terminal_input_loop())

    try:
        async with websockets.serve(
            lambda ws: websocket_handler(ws, session_timestamp),
            "0.0.0.0",
            PORT
        ):
            print(f"WebSocket Server listening on port {PORT}")
            await asyncio.Future()
    finally:
        # Ensure all open file handles are safely closed on program exit
        print("\nClosing CSV file handles...")
        for f in open_file_handles.values():
            try:
                f.flush()
                os.fsync(f.fileno())
                f.close()
            except Exception:
                pass


if __name__ == "__main__":
    asyncio.run(main())