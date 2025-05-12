import re
from collections import defaultdict

import matplotlib.pyplot as plt
import numpy as np


def analyze_wifi_time(file_path, wifi_type):
    # Read the WiFi time file
    with open(file_path, 'r') as file:
        content = file.read()

    # Find all IP addresses and their delays
    pattern = r'Response from (192\.168\.1\.\d+)\s+➤ Estimated One-way Delay ≈ ([\d.]+) ms'
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

    # Plot all IPs' data on the same figure
    for ip, delays in ip_delays.items():
        x = np.arange(1, len(delays) + 1)  # Test numbers
        avg_delay = np.mean(delays)
        # Plot the actual measurements with label including average
        plt.plot(x, delays, 'o-', label=f'IP: {ip} (Avg: {avg_delay:.2f}ms)', linewidth=1, markersize=3)

    plt.title('{} Response Times for All IPs'.format(wifi_type))
    plt.xlabel('Test Number')
    plt.ylabel('Delay (ms)')
    plt.grid(True)

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

    # Adjust layout to make room for the legend
    plt.tight_layout()
    plt.savefig('{}_response_times.png'.format(wifi_type), bbox_inches='tight', dpi=300)
    plt.close()


if __name__ == "__main__":
    # Get data from both files
    wifi4_delays = analyze_wifi_time("./OFDMA/OFDMA/wifi4_time", "wifi4")
    wifi6_delays = analyze_wifi_time("./OFDMA/OFDMA/wifi6_time", "wifi6")

    # Find global min and max for consistent y-axis
    all_delays = []
    for delays in wifi4_delays.values():
        all_delays.extend(delays)
    for delays in wifi6_delays.values():
        all_delays.extend(delays)

    # Use the wider range for both plots
    y_min = min(all_delays) - 0.1
    y_max = max(all_delays) + 0.1

    # Plot both graphs with the same y-axis limits
    plot_wifi_data(wifi4_delays, "wifi4", y_min, y_max)
    plot_wifi_data(wifi6_delays, "wifi6", y_min, y_max)



