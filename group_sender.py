import socket
import struct
import time
import ntplib
from collections import defaultdict

# Configuration constants
BROADCAST_PORT = 5688
LISTEN_PORT = 5688
UNICAST_PORT = 5683
RESPONSE_PORT = 5684
DISCOVERY_TIMEOUT = 10
MEASUREMENT_ITERATIONS = 10

CMD_LED_COLOR = 3
COLORS = [
    (255, 0, 0), (0, 255, 0), (0, 0, 255),
    (255, 255, 0), (255, 0, 255), (16, 16, 16)
]

discovered_devices = {}  # ip -> short_id
delay_records = defaultdict(list)  # ip -> list of delays


def sync_time_with_ntp(ntp_server='ntp1.aliyun.com'):
    try:
        client = ntplib.NTPClient()
        response = client.request(ntp_server, version=3)
        system_time = time.localtime(response.tx_time)
        print(f"🕒 NTP Time synced: {time.strftime('%Y-%m-%d %H:%M:%S', system_time)}")
    except Exception as e:
        print(f"⚠️ NTP sync failed: {e}")


def send_broadcast_and_collect_responses():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(1)
    sock.bind(('', LISTEN_PORT))

    sock.sendto(b'ESP_DISCOVER_RECEIVERS', ('255.255.255.255', BROADCAST_PORT))
    print(f"📡 Broadcast sent to 255.255.255.255:{BROADCAST_PORT}")
    print(f"📥 Listening on port {LISTEN_PORT} for {DISCOVERY_TIMEOUT} seconds...\n")

    start_time = time.time()
    last_printed_second = None

    while time.time() - start_time < DISCOVERY_TIMEOUT:
        remaining = int(DISCOVERY_TIMEOUT - (time.time() - start_time))
        if remaining != last_printed_second and remaining >= 0:
            print(f"⏳ Waiting: {remaining:>2}s remaining...")
            last_printed_second = remaining

        try:
            data, addr = sock.recvfrom(1024)
            ip = addr[0]
            message = data.decode().strip()
            if message.startswith("ESP_RECEIVER_ID:"):
                short_id = message.split(":")[1].strip()
                if ip not in discovered_devices:
                    discovered_devices[ip] = short_id
                    print(f"✅ Response from {ip}: {message}")
        except socket.timeout:
            time.sleep(0.1)
            continue

    sock.close()
    print("\n🛑 Discovery phase ended.\n")


def send_color_command(ip, r, g, b, seq):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        t1 = int(time.time() * 1_000_000)
        message = struct.pack("<IQBBBB", seq, t1, CMD_LED_COLOR, r, g, b)
        sock.sendto(message, (ip, UNICAST_PORT))
        print(f"🎨 Sent color to {ip}: RGB({r},{g},{b}), seq={seq}")
    finally:
        sock.close()
    return t1


def listen_for_response_and_calc_delay(ip, t1, timeout=2):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('', RESPONSE_PORT))
    sock.settimeout(timeout)

    try:
        while True:
            data, addr = sock.recvfrom(1024)
            t4 = int(time.time() * 1_000_000)

            if addr[0] != ip:
                continue

            if len(data) >= 22:
                seq, t2, t3, rid = struct.unpack("<IQQH", data[:22])
                delay = ((t4 - t1) - (t3 - t2)) / 2 / 1000.0
                print(f"📨 Response from {ip}")
                print(f"    ➤ Estimated One-way Delay ≈ {delay:.2f} ms")
                delay_records[ip].append(delay)
                break
            else:
                print(f"⚠️ Incomplete or unexpected data from {addr[0]} ({len(data)} bytes)")
    except socket.timeout:
        print(f"⏰ Timeout waiting for response from {ip}")
    finally:
        sock.close()


def print_average_delays():
    # print("\n📊 Average One-Way Delays per Device:")
    # for ip, delays in delay_records.items():
    #     if delays:
    #         avg_delay = sum(delays) / len(delays)
    #         print(f"{ip:<16} : {avg_delay:.2f} ms")
    #     else:
    #         print(f"{ip:<16} : No responses")
    print("\n📊 Average One-Way Delays per Device:")
    total_delays = 0
    total_responses = 0

    for ip, delays in delay_records.items():
        if delays:
            avg_delay = sum(delays) / len(delays)
            print(f"{ip:<16} : {avg_delay:.2f} ms")
            total_delays += sum(delays)
            total_responses += len(delays)
        else:
            print(f"{ip:<16} : No responses")

    # Calculate and display total average time
    if total_responses > 0:
        total_avg_delay = total_delays / total_responses
        print(f"\n📊 Total Average Delay: {total_avg_delay:.2f} ms")
    else:
        print("\n📊 Total Average Delay: No responses")





def main():
    num_iterations = 5  # Change this to whatever number of times you want the loop to run
    for _ in range(num_iterations):
        sync_time_with_ntp()
        send_broadcast_and_collect_responses()
        if not discovered_devices:
            print("⚠️ No devices found. Exiting.")
            return

        seq = 1
        for i in range(MEASUREMENT_ITERATIONS):
            r, g, b = COLORS[i % len(COLORS)]
            print(f"\n🚀 Sending color [{i + 1}/{MEASUREMENT_ITERATIONS}]: RGB({r},{g},{b})")
            for ip in discovered_devices:
                t1 = send_color_command(ip, r, g, b, seq)
                listen_for_response_and_calc_delay(ip, t1)
            seq += 1
            time.sleep(1)

        print_average_delays()

    print("Completed all iterations.")
  
# Run the main function
if __name__ == "__main__":
    main()



if __name__ == "__main__":
    main()

