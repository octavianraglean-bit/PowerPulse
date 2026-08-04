import os
import sys
import time
import json
import sqlite3
import threading
import subprocess
import urllib.parse
import urllib.request
import hashlib
from http.server import HTTPServer, BaseHTTPRequestHandler
import ctypes

# Initialize WMI once if available
try:
    import wmi
    wmi_obj = wmi.WMI()
except Exception as e:
    wmi_obj = None

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "power_logs.db")
PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")

# Lock for SQLite access from multiple threads
db_lock = threading.Lock()

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with db_lock:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS power_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                cpu_w REAL,
                gpu_w REAL,
                monitor_w REAL,
                total_w REAL,
                kwh REAL,
                cost_eur REAL
            )
        """)
        
        # Set default settings if not exists
        defaults = {
            "electricity_price": "0.35",
            "monitor_wattage": "auto",
            "cpu_tdp": "95.0",
            "psu_efficiency": "88.0",
            "logging_interval": "2.0",
            "is_logging": "true",
            "donate_url": "https://ko-fi.com/smallstep"
        }
        for k, v in defaults.items():
            cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (k, v))
        
        conn.commit()
        conn.close()

def get_setting(key, default_val=""):
    with db_lock:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
        row = cursor.fetchone()
        conn.close()
        return row["value"] if row else default_val

def get_all_settings():
    with db_lock:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT key, value FROM settings")
        rows = cursor.fetchall()
        conn.close()
        return {r["key"]: r["value"] for r in rows}

def set_settings(settings_dict):
    with db_lock:
        conn = get_db()
        cursor = conn.cursor()
        for k, v in settings_dict.items():
            cursor.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (k, str(v)))
        conn.commit()
        conn.close()

# Telemetry reader helper functions
def read_gpu_power():
    try:
        cmd = ['nvidia-smi', '--query-gpu=power.draw', '--format=csv,noheader,nounits']
        output = subprocess.check_output(cmd, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0).decode('utf-8').strip()
        return max(0.0, float(output))
    except Exception:
        return 0.0

def read_cpu_power(cpu_tdp):
    try:
        if wmi_obj:
            load = float(wmi_obj.Win32_Processor()[0].LoadPercentage or 0)
        else:
            load = 10.0
        # Estimate CPU power: Base idle (~12W) + load% * (TDP - 12W)
        idle_power = 12.0
        max_power = max(cpu_tdp, 30.0)
        power = idle_power + (load / 100.0) * (max_power - idle_power)
        return round(power, 2)
    except Exception:
        return 15.0

def is_display_on():
    try:
        user32 = ctypes.windll.user32
        fg_window = user32.GetForegroundWindow()
        return fg_window != 0
    except Exception:
        return True

def verify_dynamic_supporter_code(code_str, api_url=None):
    if not code_str:
        return False
    code = code_str.strip()
    code_upper = code.upper()
    
    # --- OPTION A: Local Checksum & Key Verification ---
    # 1. Accept static / supporter keys
    if code_upper in ["SUPPORTER2026", "THANKS2026", "SMALLSTEP", "DONATED"]:
        return True

    # 2. Accept individual transaction keys starting with KP- or KOFI-
    if code_upper.startswith("KP-") or code_upper.startswith("KOFI-"):
        parts = code_upper.split("-")
        if len(parts) >= 2 and len(parts[1]) >= 4:
            return True

    # 3. Dynamic checksum hash verification
    clean_code = code_upper.replace("-", "").replace(" ", "")
    if len(clean_code) >= 8:
        h = hashlib.sha256(("POWERPULSE_SALT_" + clean_code[:-2]).encode('utf-8')).hexdigest().upper()
        if h[:2] == clean_code[-2:] or len(clean_code) == 12:
            return True

    # --- OPTION B: Online API Server Verification (if URL configured or email provided) ---
    if api_url and api_url.startswith("http") and ("@" in code or len(code) >= 5):
        try:
            encoded_val = urllib.parse.quote(code)
            verify_endpoint = f"{api_url}?supporter={encoded_val}"
            req = urllib.request.Request(verify_endpoint, headers={'User-Agent': 'PowerPulseApp/1.0'})
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    res_data = json.loads(resp.read().decode('utf-8'))
                    if res_data.get("verified") is True:
                        return True
        except Exception:
            pass

    return False

def get_connected_monitors():
    monitors = []
    try:
        wmi_root = wmi.WMI(namespace="root\\wmi")
        for m in wmi_root.WmiMonitorID():
            name_chars = [chr(x) for x in m.UserFriendlyName if x != 0]
            name = "".join(name_chars).strip()
            if name:
                monitors.append(name)
    except Exception:
        pass
    return monitors if monitors else ["Standard Monitor"]

def calculate_auto_monitor_wattage():
    monitors = get_connected_monitors()
    total_auto_w = 0.0
    for mon in monitors:
        mon_upper = mon.upper()
        if "TV" in mon_upper:
            total_auto_w += 50.0  # TV / Large screen default
        elif "ULTRAWIDE" in mon_upper or "4K" in mon_upper:
            total_auto_w += 45.0
        else:
            total_auto_w += 25.0  # Standard 24-27" monitor default
    return round(total_auto_w, 1)

# Background Collector Thread
class PowerLoggerThread(threading.Thread):
    def __init__(self):
        super().__init__()
        self.daemon = True
        self.running = True

    def run(self):
        while self.running:
            try:
                settings = get_all_settings()
                is_logging = settings.get("is_logging", "true").lower() == "true"
                interval = float(settings.get("logging_interval", "2.0"))
                
                if is_logging:
                    price = float(settings.get("electricity_price", "0.35"))
                    mon_w_setting = settings.get("monitor_wattage", "auto")
                    if mon_w_setting.lower() == "auto" or mon_w_setting == "0":
                        target_mon_w = calculate_auto_monitor_wattage()
                    else:
                        target_mon_w = float(mon_w_setting)

                    cpu_tdp = float(settings.get("cpu_tdp", "95.0"))
                    base_system_w = float(settings.get("base_system_w", "15.0"))
                    efficiency = float(settings.get("psu_efficiency", "88.0")) / 100.0

                    gpu_w = read_gpu_power()
                    cpu_w = read_cpu_power(cpu_tdp)
                    mon_w = target_mon_w if is_display_on() else 0.5

                    # Complete PC hardware sum (CPU + GPU + Mainboard/RAM/Fans/SSDs) divided by PSU efficiency
                    raw_pc_components = cpu_w + gpu_w + base_system_w
                    total_pc_at_wall = raw_pc_components / max(0.5, efficiency)
                    total_w = round(total_pc_at_wall + mon_w, 2)

                    interval_hours = interval / 3600.0
                    delta_kwh = (total_w / 1000.0) * interval_hours
                    delta_cost = delta_kwh * price

                    with db_lock:
                        conn = get_db()
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO power_logs (cpu_w, gpu_w, monitor_w, total_w, kwh, cost_eur)
                            VALUES (?, ?, ?, ?, ?, ?)
                        """, (cpu_w, gpu_w, mon_w, total_w, delta_kwh, delta_cost))
                        conn.commit()
                        conn.close()

                time.sleep(interval)
            except Exception as e:
                time.sleep(2.0)

# HTTP Request Handler
class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Suppress noisy HTTP logs in console
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(body)

    def serve_file(self, file_path, mime_type):
        if os.path.exists(file_path):
            self.send_response(200)
            self.send_header('Content-Type', mime_type)
            with open(file_path, 'rb') as f:
                content = f.read()
            self.send_header('Content-Length', str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        else:
            self.send_error(404, "File Not Found")

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == '/api/telemetry':
            settings = get_all_settings()
            cpu_tdp = float(settings.get("cpu_tdp", "95.0"))
            mon_w_setting = settings.get("monitor_wattage", "auto")
            if mon_w_setting.lower() == "auto" or mon_w_setting == "0":
                target_mon_w = calculate_auto_monitor_wattage()
            else:
                target_mon_w = float(mon_w_setting)

            base_system_w = float(settings.get("base_system_w", "15.0"))
            efficiency = float(settings.get("psu_efficiency", "88.0")) / 100.0

            gpu_w = read_gpu_power()
            cpu_w = read_cpu_power(cpu_tdp)
            mon_w = target_mon_w if is_display_on() else 0.5
            
            raw_pc_components = cpu_w + gpu_w + base_system_w
            pc_at_wall = raw_pc_components / max(0.5, efficiency)
            total_w = round(pc_at_wall + mon_w, 2)

            with db_lock:
                conn = get_db()
                cursor = conn.cursor()
                
                # Today stats
                cursor.execute("""
                    SELECT SUM(kwh) as today_kwh, SUM(cost_eur) as today_cost
                    FROM power_logs
                    WHERE DATE(timestamp) = DATE('now', 'localtime')
                """)
                today_row = cursor.fetchone()
                
                # Total stats
                cursor.execute("""
                    SELECT SUM(kwh) as total_kwh, SUM(cost_eur) as total_cost, COUNT(*) as log_count
                    FROM power_logs
                """)
                total_row = cursor.fetchone()
                conn.close()

            today_kwh = round(today_row["today_kwh"] or 0.0, 4)
            today_cost = round(today_row["today_cost"] or 0.0, 4)
            all_kwh = round(total_row["total_kwh"] or 0.0, 4)
            all_cost = round(total_row["total_cost"] or 0.0, 4)

            self.send_json({
                "cpu_w": cpu_w,
                "gpu_w": gpu_w,
                "monitor_w": mon_w,
                "total_w": total_w,
                "today_kwh": today_kwh,
                "today_cost": today_cost,
                "all_kwh": all_kwh,
                "all_cost": all_cost,
                "connected_monitors": get_connected_monitors(),
                "is_logging": settings.get("is_logging", "true").lower() == "true",
                "is_supporter": verify_dynamic_supporter_code(
                    settings.get("supporter_code", ""),
                    api_url=settings.get("supporter_api_url", "")
                ),
                "settings": settings
            })

        elif path == '/api/update_check':
            current_version = "v1.0.0"
            github_url = "https://api.github.com/repos/octavianraglean-bit/PowerPulse/releases/latest"
            update_data = {
                "current_version": current_version,
                "latest_version": current_version,
                "update_available": False,
                "download_url": "https://github.com/octavianraglean-bit/PowerPulse/releases"
            }
            try:
                req = urllib.request.Request(github_url, headers={'User-Agent': 'PowerPulseApp/1.0'})
                with urllib.request.urlopen(req, timeout=3) as resp:
                    if resp.status == 200:
                        rel_info = json.loads(resp.read().decode('utf-8'))
                        tag_name = rel_info.get("tag_name", current_version)
                        if tag_name != current_version:
                            update_data["update_available"] = True
                            update_data["latest_version"] = tag_name
                            update_data["download_url"] = rel_info.get("html_url", update_data["download_url"])
            except Exception:
                pass
            self.send_json(update_data)

        elif path == '/api/history':
            with db_lock:
                conn = get_db()
                cursor = conn.cursor()

                if period == "recent":
                    cursor.execute("""
                        SELECT strftime('%H:%M:%S', timestamp) as time_label, cpu_w, gpu_w, monitor_w, total_w
                        FROM power_logs
                        ORDER BY id DESC LIMIT 60
                    """)
                    rows = [dict(r) for r in cursor.fetchall()]
                    rows.reverse()
                elif period == "today":
                    cursor.execute("""
                        SELECT strftime('%H:00', timestamp) as time_label,
                               AVG(total_w) as avg_total_w,
                               AVG(cpu_w) as avg_cpu_w,
                               AVG(gpu_w) as avg_gpu_w,
                               SUM(kwh) as hourly_kwh,
                               SUM(cost_eur) as hourly_cost
                        FROM power_logs
                        WHERE DATE(timestamp) = DATE('now', 'localtime')
                        GROUP BY strftime('%H:00', timestamp)
                        ORDER BY time_label ASC
                    """)
                    rows = [dict(r) for r in cursor.fetchall()]
                elif period == "daily":
                    cursor.execute("""
                        SELECT DATE(timestamp) as time_label,
                               AVG(total_w) as avg_total_w,
                               SUM(kwh) as daily_kwh,
                               SUM(cost_eur) as daily_cost
                        FROM power_logs
                        GROUP BY DATE(timestamp)
                        ORDER BY time_label DESC LIMIT 30
                    """)
                    rows = [dict(r) for r in cursor.fetchall()]
                    rows.reverse()
                else:
                    rows = []
                conn.close()

            self.send_json(rows)

        elif path == '/api/export':
            with db_lock:
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("SELECT timestamp, cpu_w, gpu_w, monitor_w, total_w, kwh, cost_eur FROM power_logs ORDER BY id ASC")
                rows = cursor.fetchall()
                conn.close()

            csv_lines = ["Timestamp,CPU (W),GPU (W),Monitor (W),Total (W),Energy (kWh),Cost (EUR)"]
            for r in rows:
                csv_lines.append(f"{r['timestamp']},{r['cpu_w']},{r['gpu_w']},{r['monitor_w']},{r['total_w']},{r['kwh']:.6f},{r['cost_eur']:.6f}")
            csv_data = "\n".join(csv_lines).encode('utf-8')

            self.send_response(200)
            self.send_header('Content-Type', 'text/csv')
            self.send_header('Content-Disposition', 'attachment; filename="power_logs_export.csv"')
            self.send_header('Content-Length', str(len(csv_data)))
            self.end_headers()
            self.wfile.write(csv_data)

        elif path == '/' or path == '/index.html':
            self.serve_file(os.path.join(PUBLIC_DIR, 'index.html'), 'text/html; charset=utf-8')
        elif path.endswith('.css'):
            self.serve_file(os.path.join(PUBLIC_DIR, os.path.basename(path)), 'text/css')
        elif path.endswith('.js'):
            self.serve_file(os.path.join(PUBLIC_DIR, os.path.basename(path)), 'application/javascript')
        else:
            self.send_error(404, "Not Found")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length).decode('utf-8') if length > 0 else ""

        if path == '/api/settings':
            try:
                data = json.loads(body)
                set_settings(data)
                self.send_json({"status": "ok", "message": "Settings updated"})
            except Exception as e:
                self.send_json({"error": str(e)}, status=400)

        elif path == '/api/reset':
            with db_lock:
                conn = get_db()
                cursor = conn.cursor()
                cursor.execute("DELETE FROM power_logs")
                conn.commit()
                conn.close()
            self.send_json({"status": "ok", "message": "Logs reset"})
        else:
            self.send_error(404, "Not Found")

def main():
    init_db()
    
    # Start logger background thread
    logger_thread = PowerLoggerThread()
    logger_thread.start()

    server_address = ('127.0.0.1', 5000)
    httpd = HTTPServer(server_address, RequestHandler)
    print("==================================================")
    print(" PowerPulse Server Running on http://127.0.0.1:5000")
    print(" Press Ctrl+C to stop.")
    print("==================================================")
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        logger_thread.running = False
        print("\nStopping PowerPulse Server...")
        httpd.server_close()

if __name__ == '__main__':
    main()
