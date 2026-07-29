import socket
import datetime
import os
import sys

ARDUINO_IP = "172.20.10.3"
PORT = 5001

def main():

    print(f"Connecting to Arduino at {ARDUINO_IP}:{PORT} ...")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(5.0)

    try:
        sock.connect((ARDUINO_IP, PORT))
        print("Connected successfully!")

    except Exception as e:

        print(f"\n[ERROR] Could not connect to Arduino: {e}")
        print("Check that:")
        print("- Arduino is powered")
        print("- Both devices are on the same WiFi")
        print("- The IP address is correct")
        sock.close()
        sys.exit(1)

    # Once connected we no longer need a timeout.
    sock.settimeout(None)

    # Read one complete line at a time.
    sock_file = sock.makefile("r", encoding="utf-8")

    script_dir = os.path.dirname(os.path.realpath(__file__))

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")

    filename = os.path.join(
        script_dir,
        f"imu_log_{timestamp}.csv"
    )

    print(f"Logging data to: {filename}\n")

    with open(filename, "a", encoding="utf-8") as f:

        f.write("pc_timestamp,arduino_ms,ax,ay,az,gx,gy,gz,rssi\n")
        f.flush()

        try:

            while True:

                line = sock_file.readline()
                print('LINE -------------><>>>>> ', line)

                if not line:
                    print("Arduino disconnected.")
                    break

                line = line.strip()

                if not line:
                    continue

                if line.startswith("#"):
                    print(f"[COMMENT] {line}")
                    continue

                fields = line.split(",")

                if len(fields) != 8:
                    print(f"[BAD PACKET] {line}")
                    continue

                iso_time = datetime.datetime.now().isoformat(
                    timespec="seconds"
                )

                print(f"Received at {iso_time}: {line}")

                f.write(f"{iso_time},{line}\n")
                f.flush()
                os.fsync(f.fileno())

        except KeyboardInterrupt:

            print("\nInterrupted by user.")

        finally:

            sock_file.close()
            sock.close()

            print("Socket closed.")


if __name__ == "__main__":
    main()