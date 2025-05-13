import socket
import struct
import time
import ntplib
from collections import defaultdict
import sys
from datetime import datetime
import re
import matplotlib.pyplot as plt
import numpy as np
import threading
import argparse  # Add this import for command-line argument parsing

# Parse command-line arguments
def parse_arguments():
    parser = argparse.ArgumentParser(description="WiFi Latency Testing Tool")
    parser.add_argument("wifi_type", nargs="?", default="wifi6", 
                        help="Type of WiFi being tested (e.g., wifi6, wifi5)")
    parser.add_argument("-i", "--iterations", type=int, default=50,
                        help="Number of measurement iterations (default: 10)")
    parser.add_argument("-t", "--timeout", type=int, default=2,
                        help="Discovery timeout in seconds (default: 2)")
    return parser.parse_args()

# Get command-line arguments
args = parse_arguments()
WIFI_TYPE = args.wifi_type
MEASUREMENT_ITERATIONS = args.iterations
DISCOVERY_TIMEOUT = args.timeout

# Redirect print output to a log file with a timestamped name
log_file_path = f"{WIFI_TYPE}_test_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
log_file = open(log_file_path, "w", encoding="utf-8")
sys.stdout = log_file
sys.stderr = log_file

# Configuration constants
BROADCAST_PORT = 5688
LISTEN_PORT = 5688
UNICAST_PORT = 5683
RESPONSE_PORT = 5684

CMD_LED_COLOR = 3
COLORS = [
    (255, 0, 0), (0, 255, 0), (0, 0, 255),
    (255, 255, 0), (255, 0, 255), (16, 16, 16)
]

discovered_devices = {}  # ip -> short_id
delay_records = defaultdict(list)  # ip -> list of delays

# 新增：用于存储每轮测量的发送时间和序号
pending_commands = {}  # (ip, seq) -> t1


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


def send_color_command(ip, r, g, b, seq, sock=None):
    should_close = False
    if sock is None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        should_close = True
    try:
        t1 = int(time.time() * 1_000_000)
        message = struct.pack("<IQBBBB", seq, t1, CMD_LED_COLOR, r, g, b)
        sock.sendto(message, (ip, UNICAST_PORT))
        print(f"🎨 Sent color to {ip}: RGB({r},{g},{b}), seq={seq}")
    finally:
        if should_close:
            sock.close()
    return t1


def response_listener(stop_event, timeout=2):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('', RESPONSE_PORT))
    sock.settimeout(0.2)
    end_time = time.time() + timeout
    while not stop_event.is_set() and time.time() < end_time:
        try:
            data, addr = sock.recvfrom(1024)
            t4 = int(time.time() * 1_000_000)
            ip = addr[0]
            if len(data) >= 22:
                seq, t2, t3, rid = struct.unpack("<IQQH", data[:22])
                key = (ip, seq)
                t1 = pending_commands.get(key)
                if t1 is not None:
                    delay = ((t4 - t1) - (t3 - t2)) / 2 / 1000.0
                    print(f"📨 Response from {ip}")
                    print(f"    ➤ Estimated One-way Delay ≈ {delay:.2f} ms")
                    delay_records[ip].append(delay)
                    # 一个响应只处理一次
                    del pending_commands[key]
                else:
                    print(f"⚠️ Response from {ip} with unknown seq={seq}")
            else:
                print(f"⚠️ Incomplete or unexpected data from {ip} ({len(data)} bytes)")
        except socket.timeout:
            continue
    sock.close()


def response_listener_continuous(stop_event, response_queue):
    """持续监听响应的线程函数，直到收到停止信号"""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('', RESPONSE_PORT))
    sock.settimeout(0.2)  # 短超时，使线程能定期检查停止事件
    
    print("📡 持续监听器已启动，等待响应...")
    
    while not stop_event.is_set():
        try:
            data, addr = sock.recvfrom(1024)
            t4 = int(time.time() * 1_000_000)
            ip = addr[0]
            
            if len(data) >= 22:
                seq, t2, t3, rid = struct.unpack("<IQQH", data[:22])
                key = (ip, seq)
                t1 = pending_commands.get(key)
                if t1 is not None:
                    delay = ((t4 - t1) - (t3 - t2)) / 2 / 1000.0
                    print(f"📨 Response from {ip} (seq={seq})")
                    print(f"    ➤ Estimated One-way Delay ≈ {delay:.2f} ms")
                    delay_records[ip].append(delay)
                    # 将处理过的响应放入队列（可用于其他分析）
                    response_queue.append((ip, seq, delay))
                    # 一个响应只处理一次
                    del pending_commands[key]
                else:
                    print(f"⚠️ Response from {ip} with unknown seq={seq}")
            else:
                print(f"⚠️ Incomplete or unexpected data from {ip} ({len(data)} bytes)")
        except socket.timeout:
            # 超时继续循环，这样可以检查stop_event
            continue
        except Exception as e:
            print(f"❌ Error in response listener: {e}")
    
    print("📡 持续监听器关闭中...")
    sock.close()


def print_average_delays():
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


def analyze_wifi_time(file_path, wifi_type):
    # Read the WiFi time file
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.read()

    # Find all IP addresses and their delays
    # Update the regex pattern to match the actual log format with (seq=X) and the delay on the next line
    pattern = r'Response from (192\.168\.1\.\d+).*?\n.*?One-way Delay ≈ ([\d.]+) ms'
    matches = re.findall(pattern, content)

    # Dictionary to store all delays for each IP
    ip_delays = defaultdict(list)

    # Collect all delays for each IP in order
    for ip, delay in matches:
        ip_delays[ip].append(float(delay))

    return ip_delays


def plot_wifi_data(ip_delays, wifi_type, y_min, y_max):
    # Create a single figure
    plt.figure(figsize=(15, 8))

    # Set y-axis limits
    plt.ylim(y_min, y_max)
    
    # Track if we have plotted anything
    has_data = False

    # Plot all IPs' data on the same figure
    for ip, delays in ip_delays.items():
        if delays:  # Only plot if we have delay data
            x = np.arange(1, len(delays) + 1)  # Test numbers
            avg_delay = np.mean(delays)
            # Plot the actual measurements with label including average
            plt.plot(x, delays, 'o-', label=f'IP: {ip} (Avg: {avg_delay:.2f}ms)', linewidth=1, markersize=3)
            has_data = True

    plt.title('{} Response Times for All IPs'.format(wifi_type))
    plt.xlabel('Test Number')
    plt.ylabel('Delay (ms)')
    plt.grid(True)

    # Only create a legend if we actually plotted data
    if has_data:
        # Improved legend settings
        legend = plt.legend(
            bbox_to_anchor=(1.05, 1),  # Position outside the plot
            loc='upper left',
            ncol=3,  # Number of columns
            fontsize=8,  # Smaller font size
            framealpha=0.5,  # Semi-transparent background
            title='IP Addresses (with averages)',
            title_fontsize=10
        )
    else:
        # Display a message when there's no data
        plt.text(0.5, 0.5, "No data available to plot", 
                 ha='center', va='center', 
                 transform=plt.gca().transAxes, 
                 fontsize=14)

    # Adjust layout to make room for the legend
    plt.tight_layout()
    plt.savefig('{}_response_times.png'.format(wifi_type), bbox_inches='tight', dpi=300)
    plt.close()


def main():
    print(f"🚀 Starting {WIFI_TYPE} testing with {MEASUREMENT_ITERATIONS} iterations")
    num_iterations = 1
    for _ in range(num_iterations):
        sync_time_with_ntp()
        send_broadcast_and_collect_responses()
        if not discovered_devices:
            print("⚠️ No devices found. Exiting.")
            return
            
        seq = 1
        # 创建一个共享的发送socket
        send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # 创建一个全局的停止事件和一个持续运行的监听线程
            global_stop_event = threading.Event()
            response_queue = []
            listener = threading.Thread(
                target=response_listener_continuous, 
                args=(global_stop_event, response_queue)
            )
            # 设置为守护线程，这样主线程结束时它也会结束
            listener.daemon = True
            listener.start()
            print("🔄 监听线程已启动，持续监听中...")
            
            for i in range(MEASUREMENT_ITERATIONS):
                r, g, b = COLORS[i % len(COLORS)]
                print(f"\n🚀 Sending color [{i + 1}/{MEASUREMENT_ITERATIONS}]: RGB({r},{g},{b})")
                
                # 记录本轮所有设备的 t1，并发送命令
                for ip in discovered_devices:
                    t1 = send_color_command(ip, r, g, b, seq, send_sock)
                    pending_commands[(ip, seq)] = t1
                
                # 给足够的时间让响应到达并被处理
                time.sleep(2)
                seq += 1
            
            # 所有测量完成后，停止监听线程
            print("📥 等待最后的响应...")
            time.sleep(2)  # 给最后的响应一些处理时间
            global_stop_event.set()
            listener.join()
            print("🛑 监听线程已结束")
            
        finally:
            # 确保socket正确关闭
            send_sock.close()

        print_average_delays()

    print("Completed all iterations.")


if __name__ == "__main__":
    try:
        main()
    finally:
        # Ensure the log file is closed properly
        log_file.close()
        sys.stdout = sys.__stdout__
        sys.stderr = sys.__stderr__

        # Automatically analyze the log file and generate plots
        ip_delays = analyze_wifi_time(log_file_path, WIFI_TYPE)

        # Find global min and max for consistent y-axis
        # all_delays = [delay for delays in ip_delays.values() for delay in delays]
        # y_min = min(all_delays) - 0.1
        # y_max = max(all_delays) + 0.1
        y_min = 0
        y_max = 400

        plot_wifi_data(ip_delays, WIFI_TYPE, y_min, y_max)

