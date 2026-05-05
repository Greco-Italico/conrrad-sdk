"""
Kernell OS — Shadow Health Monitor
══════════════════════════════════
Monitors the persistent execution logs of the Shadow Mode runner on the VPS.
Detects gaps in traffic, restart loops, and basic anomalies to ensure
the 72-hour experiment remains valid.
"""

import time
import os
import argparse
from datetime import datetime

C_RED = '\033[91m'
C_GREEN = '\033[92m'
C_YELLOW = '\033[93m'
C_RESET = '\033[0m'

def monitor_logs(log_path: str, max_gap_seconds: int):
    print(f"Monitoring log file: {log_path}")
    if not os.path.exists(log_path):
        print(f"{C_RED}ERROR: Log file not found. Ensure the systemd service is running.{C_RESET}")
        return

    last_modified = os.path.getmtime(log_path)
    consecutive_restarts = 0
    last_restart_time = 0

    try:
        with open(log_path, 'r') as f:
            # Go to the end of the file
            f.seek(0, os.SEEK_END)
            
            while True:
                line = f.readline()
                current_time = time.time()
                
                if line:
                    line = line.strip()
                    last_modified = current_time
                    
                    if "Iniciando Kernell Shadow Mode Runner" in line:
                        if current_time - last_restart_time < 60:
                            consecutive_restarts += 1
                        else:
                            consecutive_restarts = 1
                        last_restart_time = current_time
                        
                        if consecutive_restarts >= 3:
                            print(f"{C_RED}[{datetime.now().isoformat()}] CRITICAL: Restart loop detected! Service has restarted {consecutive_restarts} times in short succession.{C_RESET}")
                    
                    if "ERROR" in line or "CRITICAL" in line:
                        print(f"{C_YELLOW}WARNING found in log: {line}{C_RESET}")
                
                else:
                    # No new lines, check for gaps
                    time_since_modified = current_time - last_modified
                    if time_since_modified > max_gap_seconds:
                        print(f"{C_RED}[{datetime.now().isoformat()}] ALERT: No traffic or heartbeat detected for {int(time_since_modified)} seconds!{C_RESET}")
                        # Reset the timer so it doesn't spam every second
                        last_modified = current_time
                        
                    time.sleep(1)
    except KeyboardInterrupt:
        print("\nMonitoring stopped.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--log", type=str, default="/var/log/kernell/shadow_mode_out.log", help="Path to the shadow mode log file")
    parser.add_argument("--gap", type=int, default=120, help="Max gap in seconds before alerting lack of traffic")
    args = parser.parse_args()
    
    monitor_logs(args.log, args.gap)
