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
import ipaddress
import netifaces  # New import for network interface detection

# Parse command-line arguments
def parse_arguments():
    parser = argparse.ArgumentParser(description="WiFi Latency Testing Tool")
    parser.add_argument("wifi_type", nargs="?", default="wifi6", 
                        help="Type of WiFi being tested (e.g., wifi6, wifi5)")
    parser.add_argument("-i", "--iterations", type=int, default=10,
                        help="Number of measurement iterations (default: 10)")
    parser.add_argument("-t", "--timeout", type=int, default=3,
                        help="Discovery timeout in seconds (default: 2)")
    parser.add_argument("-n", "--network", default=None,
                        help="Specific network to use (e.g., 192.168.1.0/24)")
    return parser.parse_args()

# Get command-line arguments
args = parse_arguments()
WIFI_TYPE = args.wifi_type
MEASUREMENT_ITERATIONS = args.iterations
DISCOVERY_TIMEOUT = args.timeout
SPECIFIED_NETWORK = args.network

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

# Updated to store both short_id and wifi mode (6 or 4)
discovered_devices = {}  # ip -> (short_id, wifi_mode)
# Separate delay records for WiFi 6 and WiFi 4 devices
wifi6_delay_records = defaultdict(list)  # ip -> list of delays for WiFi 6 devices
wifi4_delay_records = defaultdict(list)  # ip -> list of delays for WiFi 4 devices
delay_records = defaultdict(list)  # ip -> list of delays (for backward compatibility)

# 用于存储每轮测量的发送时间和序号
pending_commands = {}  # (ip, seq) -> t1


def sync_time_with_ntp(ntp_server='ntp1.aliyun.com'):
    try:
        client = ntplib.NTPClient()
        response = client.request(ntp_server, version=3)
        system_time = time.localtime(response.tx_time)
        print(f" NTP Time synced: {time.strftime('%Y-%m-%d %H:%M:%S', system_time)}")
    except Exception as e:
        print(f"⚠️ NTP sync failed: {e}")


def get_broadcast_addresses():
    """Get broadcast addresses for all available network interfaces"""
    broadcast_addresses = []
    
    # If user specified a network, use that
    if SPECIFIED_NETWORK:
        try:
            network = ipaddress.IPv4Network(SPECIFIED_NETWORK, strict=False)
            broadcast_addresses.append(str(network.broadcast_address))
            print(f" Using specified network: {SPECIFIED_NETWORK}, broadcast: {network.broadcast_address}")
            return broadcast_addresses
        except ValueError as e:
            print(f"⚠️ Invalid network specification: {e}. Will use auto-detection.")
    
    # Auto-detect networks
    try:
        # Get all network interfaces
        for interface in netifaces.interfaces():
            addrs = netifaces.ifaddresses(interface)
            # Check for IPv4 addresses
            if netifaces.AF_INET in addrs:
                for addr in addrs[netifaces.AF_INET]:
                    if 'broadcast' in addr:
                        broadcast = addr['broadcast']
                        # Skip loopback and link-local addresses
                        if not broadcast.startswith('127.') and not broadcast.startswith('169.254.'):
                            broadcast_addresses.append(broadcast)
                            print(f" Found interface: {interface} with broadcast: {broadcast}")
    except Exception as e:
        print(f"⚠️ Error detecting network interfaces: {e}")
        # Fallback to common broadcast addresses
        fallback_broadcasts = ['192.168.1.255', '192.168.0.255']
        print(f" Falling back to common broadcast addresses: {fallback_broadcasts}")
        broadcast_addresses.extend(fallback_broadcasts)
    
    # If no addresses found, add common ones as fallback
    if not broadcast_addresses:
        fallback_broadcasts = ['192.168.1.255', '192.168.0.255']
        print(f" No broadcast addresses found. Using fallback addresses: {fallback_broadcasts}")
        broadcast_addresses.extend(fallback_broadcasts)
    
    return broadcast_addresses

def send_broadcast_and_collect_responses():
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(1)
    sock.bind(('', LISTEN_PORT))

    # Get all broadcast addresses
    broadcast_addresses = get_broadcast_addresses()
    
    # Send broadcast to all addresses
    for broadcast_addr in broadcast_addresses:
        try:
            sock.sendto(b'ESP_DISCOVER_RECEIVERS', (broadcast_addr, BROADCAST_PORT))
            print(f" Broadcast sent to {broadcast_addr}:{BROADCAST_PORT}")
        except Exception as e:
            print(f"⚠️ Failed to send broadcast to {broadcast_addr}: {e}")
    
    print(f" Listening on port {LISTEN_PORT} for {DISCOVERY_TIMEOUT} seconds...\n")

    start_time = time.time()
    last_printed_second = None
    
    # Counters for device types
    wifi6_count = 0
    wifi4_count = 0

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
                parts = message.split(":")
                short_id = parts[1].strip()
                
                # Extract WiFi mode if available (format: ESP_RECEIVER_ID:short_id:wifi_mode)
                wifi_mode = 6  # Default to WiFi 6 if not specified
                if len(parts) > 2:
                    try:
                        wifi_mode = int(parts[2].strip())
                    except ValueError:
                        print(f"⚠️ Invalid WiFi mode format from {ip}: {message}")
                
                if ip not in discovered_devices:
                    discovered_devices[ip] = (short_id, wifi_mode)
                    if wifi_mode == 6:
                        wifi6_count += 1
                        wifi_type = "WiFi 6"
                    else:
                        wifi4_count += 1
                        wifi_type = "WiFi 4"
                    print(f"✅ Response from {ip}: {message} ({wifi_type})")
        except socket.timeout:
            time.sleep(0.1)
            continue

    sock.close()
    print(f"\n Discovery phase ended. Found {wifi6_count} WiFi 6 devices and {wifi4_count} WiFi 4 devices.\n")


def send_color_command(ip, r, g, b, seq, sock=None):
    should_close = False
    if sock is None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        should_close = True
    try:
        t1 = int(time.time() * 1_000_000)
        message = struct.pack("<IQBBBB", seq, t1, CMD_LED_COLOR, r, g, b)
        sock.sendto(message, (ip, UNICAST_PORT))
        print(f" Sent color to {ip}: RGB({r},{g},{b}), seq={seq}")
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
                    
                    # Get WiFi mode for this device if available
                    wifi_type = "Unknown"
                    if ip in discovered_devices:
                        _, wifi_mode = discovered_devices[ip]
                        if wifi_mode == 6:
                            wifi_type = "WiFi 6"
                            wifi6_delay_records[ip].append(delay)
                        else:
                            wifi_type = "WiFi 4"
                            wifi4_delay_records[ip].append(delay)
                    
                    # Also store in general delay records for backward compatibility
                    delay_records[ip].append(delay)
                    
                    print(f" Response from {ip} ({wifi_type})")
                    print(f"    ➤ Estimated One-way Delay ≈ {delay:.2f} ms")
                    
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
    
    print(" 持续监听器已启动，等待响应...")
    
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
                    
                    # Get WiFi mode for this device if available
                    wifi_type = "Unknown"
                    if ip in discovered_devices:
                        _, wifi_mode = discovered_devices[ip]
                        if wifi_mode == 6:
                            wifi_type = "WiFi 6"
                            wifi6_delay_records[ip].append(delay)
                        else:
                            wifi_type = "WiFi 4"
                            wifi4_delay_records[ip].append(delay)
                    
                    # Also store in general delay records for backward compatibility
                    delay_records[ip].append(delay)
                    
                    print(f" Response from {ip} (seq={seq}, {wifi_type})")
                    print(f"    ➤ Estimated One-way Delay ≈ {delay:.2f} ms")
                    
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
    
    print(" 持续监听器关闭中...")
    sock.close()


def print_average_delays():
    print("\n Average One-Way Delays per Device:")
    
    # For WiFi 6 devices
    wifi6_total_delays = 0
    wifi6_total_responses = 0
    
    if wifi6_delay_records:
        print("\n=== WiFi 6 Devices ===")
        for ip, delays in wifi6_delay_records.items():
            if delays:
                avg_delay = sum(delays) / len(delays)
                print(f"{ip:<16} : {avg_delay:.2f} ms")
                wifi6_total_delays += sum(delays)
                wifi6_total_responses += len(delays)
            else:
                print(f"{ip:<16} : No responses")
    
    # For WiFi 4 devices
    wifi4_total_delays = 0
    wifi4_total_responses = 0
    
    if wifi4_delay_records:
        print("\n=== WiFi 4 Devices ===")
        for ip, delays in wifi4_delay_records.items():
            if delays:
                avg_delay = sum(delays) / len(delays)
                print(f"{ip:<16} : {avg_delay:.2f} ms")
                wifi4_total_delays += sum(delays)
                wifi4_total_responses += len(delays)
            else:
                print(f"{ip:<16} : No responses")
    
    # Calculate and display total average time by WiFi type
    print("\n=== Summary ===")
    if wifi6_total_responses > 0:
        wifi6_avg_delay = wifi6_total_delays / wifi6_total_responses
        print(f"WiFi 6 Average Delay: {wifi6_avg_delay:.2f} ms (from {wifi6_total_responses} responses)")
    else:
        print("WiFi 6 Average Delay: No responses")
        
    if wifi4_total_responses > 0:
        wifi4_avg_delay = wifi4_total_delays / wifi4_total_responses
        print(f"WiFi 4 Average Delay: {wifi4_avg_delay:.2f} ms (from {wifi4_total_responses} responses)")
    else:
        print("WiFi 4 Average Delay: No responses")
    
    # Also print combined total for backward compatibility
    total_responses = wifi6_total_responses + wifi4_total_responses
    total_delays = wifi6_total_delays + wifi4_total_delays
    
    if total_responses > 0:
        total_avg_delay = total_delays / total_responses
        print(f"Total Average Delay: {total_avg_delay:.2f} ms (from {total_responses} responses)")
    else:
        print("Total Average Delay: No responses")


def analyze_wifi_time(file_path, wifi_type):
    # Read the WiFi time file
    with open(file_path, 'r', encoding='utf-8') as file:
        content = file.read()

    # Find all discovery responses to identify WiFi modes
    wifi_modes = {}
    mode_pattern = r'Response from (192\.168\.1\.\d+).*?\((WiFi \d+)\)'
    mode_matches = re.findall(mode_pattern, content)
    for ip, mode in mode_matches:
        wifi_modes[ip] = 6 if mode == "WiFi 6" else 4

    # Find all IP addresses and their delays
    pattern = r'Response from (192\.168\.1\.\d+).*?\n.*?One-way Delay ≈ ([\d.]+) ms'
    matches = re.findall(pattern, content)

    # Dictionaries to store all delays for each IP
    ip_delays = defaultdict(list)  # All devices
    wifi6_ip_delays = defaultdict(list)  # WiFi 6 devices
    wifi4_ip_delays = defaultdict(list)  # WiFi 4 devices

    # Collect all delays for each IP in order
    for ip, delay in matches:
        delay_val = float(delay)
        ip_delays[ip].append(delay_val)
        
        # Sort by WiFi mode if known
        if ip in wifi_modes:
            if wifi_modes[ip] == 6:
                wifi6_ip_delays[ip].append(delay_val)
            else:
                wifi4_ip_delays[ip].append(delay_val)

    return ip_delays, wifi6_ip_delays, wifi4_ip_delays


def plot_wifi_data(ip_delays, wifi_type, y_min=None, y_max=None):
    # Create a single figure
    plt.figure(figsize=(15, 8))

    # Track if we have plotted anything
    has_data = False
    
    # Collect all delay values to determine automatic range if needed
    all_delays = []

    # Plot all IPs' data on the same figure
    for ip, delays in ip_delays.items():
        if delays:  # Only plot if we have delay data
            all_delays.extend(delays)
            x = np.arange(1, len(delays) + 1)  # Test numbers
            avg_delay = np.mean(delays)
            # Plot the actual measurements with label including average
            plt.plot(x, delays, 'o-', label=f'IP: {ip} (Avg: {avg_delay:.2f}ms)', linewidth=1, markersize=3)
            has_data = True

    # Set y-axis limits if provided, otherwise use auto-range with padding
    if has_data and (y_min is not None and y_max is not None):
        plt.ylim(y_min, y_max)
    elif has_data and all_delays:
        # Add 10% padding above and below the min/max values
        data_min = min(all_delays)
        data_max = max(all_delays)
        range_padding = (data_max - data_min) * 0.1 if data_max > data_min else 10
        plt.ylim(max(0, data_min - range_padding), data_max + range_padding)

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


def plot_wifi_comparison(wifi6_delays, wifi4_delays, y_min, y_max):
    """Create a plot comparing WiFi 6 vs WiFi 4 performance"""
    plt.figure(figsize=(15, 8))
    plt.ylim(y_min, y_max)
    
    has_data = False
    
    # Plot WiFi 6 data
    wifi6_all_delays = []
    wifi6_colors = plt.cm.Blues(np.linspace(0.4, 0.8, len(wifi6_delays) or 1))
    for i, (ip, delays) in enumerate(wifi6_delays.items()):
        if delays:
            wifi6_all_delays.extend(delays)
            x = np.arange(1, len(delays) + 1)
            avg_delay = np.mean(delays)
            plt.plot(x, delays, 'o-', color=wifi6_colors[i], alpha=0.7, 
                     label=f'WiFi 6: {ip} (Avg: {avg_delay:.2f}ms)', 
                     linewidth=1, markersize=3)
            has_data = True
    
    # Plot WiFi 4 data
    wifi4_all_delays = []
    wifi4_colors = plt.cm.Reds(np.linspace(0.4, 0.8, len(wifi4_delays) or 1))
    for i, (ip, delays) in enumerate(wifi4_delays.items()):
        if delays:
            wifi4_all_delays.extend(delays)
            x = np.arange(1, len(delays) + 1)
            avg_delay = np.mean(delays)
            plt.plot(x, delays, 'o-', color=wifi4_colors[i], alpha=0.7,
                     label=f'WiFi 4: {ip} (Avg: {avg_delay:.2f}ms)', 
                     linewidth=1, markersize=3)
            has_data = True
    
    # Add average lines for each type
    if wifi6_all_delays:
        wifi6_avg = np.mean(wifi6_all_delays)
        plt.axhline(y=wifi6_avg, color='blue', linestyle='-', linewidth=2,
                    label=f'WiFi 6 Average: {wifi6_avg:.2f}ms')
    
    if wifi4_all_delays:
        wifi4_avg = np.mean(wifi4_all_delays)
        plt.axhline(y=wifi4_avg, color='red', linestyle='-', linewidth=2,
                    label=f'WiFi 4 Average: {wifi4_avg:.2f}ms')

    plt.title('WiFi 6 vs WiFi 4 Response Times Comparison')
    plt.xlabel('Test Number')
    plt.ylabel('Delay (ms)')
    plt.grid(True)

    if has_data:
        # Create custom legend with WiFi type averages first, then individual IPs
        handles, labels = plt.gca().get_legend_handles_labels()
        
        # Sort so that average lines are at the top
        avg_indices = [i for i, label in enumerate(labels) if label.startswith('WiFi 6 Average') or label.startswith('WiFi 4 Average')]
        other_indices = [i for i in range(len(labels)) if i not in avg_indices]
        
        # Reorder handles and labels
        handles = [handles[i] for i in avg_indices + other_indices]
        labels = [labels[i] for i in avg_indices + other_indices]
        
        legend = plt.legend(handles, labels,
            bbox_to_anchor=(1.05, 1),  # Position outside the plot
            loc='upper left',
            ncol=2,  # Number of columns
            fontsize=8,  # Smaller font size
            framealpha=0.5,
            title='WiFi Type Performance',
            title_fontsize=10
        )
    else:
        # Display a message when there's no data
        plt.text(0.5, 0.5, "No data available to plot", 
                 ha='center', va='center', 
                 transform=plt.gca().transAxes, 
                 fontsize=14)

    plt.tight_layout()
    plt.savefig('wifi6_vs_wifi4_comparison.png', bbox_inches='tight', dpi=300)
    plt.close()


def send_commands_to_devices_by_type(devices_dict, wifi_mode, r, g, b, seq, send_sock, thread_name):
    """Send commands only to devices of a specific WiFi mode"""
    print(f"\n [{thread_name}] Sending color: RGB({r},{g},{b}) to WiFi {wifi_mode} devices")
    
    # Filter devices by WiFi mode
    target_devices = {ip: info for ip, info in devices_dict.items() 
                      if info[1] == wifi_mode}
    
    if not target_devices:
        print(f" [{thread_name}] No WiFi {wifi_mode} devices found")
        return
        
    print(f" [{thread_name}] Sending to {len(target_devices)} device(s)")
    
    # Send commands to all devices of this type
    for ip in target_devices:
        t1 = send_color_command(ip, r, g, b, seq, send_sock)
        pending_commands[(ip, seq)] = t1
        # Small delay between sends to different devices
        #time.sleep(0.001)
    
    print(f" [{thread_name}] Completed sending commands")


def run_wifi_type_test(wifi_mode, iterations, colors, global_seq):
    """Run test for a specific WiFi type in a separate thread"""
    thread_name = f"WiFi {wifi_mode}"
    print(f"\n Starting {thread_name} thread with {iterations} iterations")
    
    # Create a thread-local socket for sending
    send_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for i in range(iterations):
            r, g, b = colors[i % len(colors)]
            seq = global_seq + i
            send_commands_to_devices_by_type(discovered_devices, wifi_mode, r, g, b, seq, send_sock, thread_name)
            # Give time for responses
            time.sleep(1)
    finally:
        send_sock.close()
    
    print(f" {thread_name} thread completed")


def main():
    print(f" Starting {WIFI_TYPE} testing with {MEASUREMENT_ITERATIONS} iterations")
    num_iterations = 1
    for _ in range(num_iterations):
        sync_time_with_ntp()
        send_broadcast_and_collect_responses()
        if not discovered_devices:
            print("⚠️ No devices found. Exiting.")
            return
            
        # Check if we have both WiFi 4 and WiFi 6 devices
        wifi6_devices = {ip: info for ip, info in discovered_devices.items() if info[1] == 6}
        wifi4_devices = {ip: info for ip, info in discovered_devices.items() if info[1] != 6}
        
        print(f"\n Found {len(wifi6_devices)} WiFi 6 device(s) and {len(wifi4_devices)} WiFi 4 device(s)")
        
        # Set up a global stop event and response queue for the listener
        global_stop_event = threading.Event()
        response_queue = []
        
        # Start the continuous listener thread
        listener = threading.Thread(
            target=response_listener_continuous, 
            args=(global_stop_event, response_queue)
        )
        listener.daemon = True
        listener.start()
        print(" Response listener thread started...")
        
        # Define starting sequence numbers for each WiFi type to avoid conflicts
        wifi6_seq_start = 1000
        wifi4_seq_start = 2000
        
        # Create threads for each WiFi type
        threads = []
        
        if wifi6_devices:
            wifi6_thread = threading.Thread(
                target=run_wifi_type_test,
                args=(6, MEASUREMENT_ITERATIONS, COLORS, wifi6_seq_start)
            )
            threads.append(wifi6_thread)
            
        if wifi4_devices:
            wifi4_thread = threading.Thread(
                target=run_wifi_type_test,
                args=(4, MEASUREMENT_ITERATIONS, COLORS, wifi4_seq_start)
            )
            threads.append(wifi4_thread)
        
        # Start all threads
        for thread in threads:
            thread.start()
        
        # Wait for all threads to complete
        for thread in threads:
            thread.join()
        
        # Clean up the listener
        print(" Waiting for final responses...")
        time.sleep(2)  # Give time for last responses
        global_stop_event.set()
        listener.join()
        print(" Response listener thread ended")

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
        ip_delays, wifi6_delays, wifi4_delays = analyze_wifi_time(log_file_path, WIFI_TYPE)

        # y_min = 0
        # y_max = 400
        y_min = None
        y_max = None
        # Generate regular plot for all devices
        plot_wifi_data(ip_delays, WIFI_TYPE, y_min, y_max)
        
        # Generate separate plots for WiFi 6 devices
        if wifi6_delays:
            plot_wifi_data(wifi6_delays, "WiFi6", y_min, y_max)
            
        # Generate separate plots for WiFi 4 devices
        if wifi4_delays:
            plot_wifi_data(wifi4_delays, "WiFi4", y_min, y_max)
            
        # Generate comparison plot if we have both types of devices
        if wifi6_delays and wifi4_delays:
            plot_wifi_comparison(wifi6_delays, wifi4_delays, 0, 50)

