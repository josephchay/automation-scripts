import os
import sys
import time
import json
import math
import uuid
import socket
import sqlite3
import platform
import threading
import queue
from datetime import datetime, timezone
from pathlib import Path

import psutil
import numpy as np
import cv2
import mediapipe as mp
from pynput import mouse, keyboard
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

try:
    import pygetwindow as gw
except ImportError:
    gw = None

try:
    import win32gui
    import win32process
except ImportError:
    win32gui = None
    win32process = None


# =====================================================================
# 1. CENTRAL THREAD-SAFE TELEMETRY SINK (SQLite + JSONL)
# =====================================================================
class TelemetrySink:
    def __init__(self, output_dir="telemetry_logs"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        session_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.jsonl_path = self.output_dir / f"events_{session_ts}.jsonl"
        self.db_path = self.output_dir / "telemetry.db"
        
        self.event_queue = queue.Queue(maxsize=10000)
        self.running = True
        self._init_db()
        
        self.worker_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self.worker_thread.start()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS telemetry_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    epoch_ms INTEGER NOT NULL,
                    category TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON telemetry_events(epoch_ms)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_cat ON telemetry_events(category)")

    def emit(self, category: str, event_type: str, payload: dict):
        now = datetime.now(timezone.utc)
        record = {
            "timestamp": now.isoformat(),
            "epoch_ms": int(now.timestamp() * 1000),
            "category": category,
            "event_type": event_type,
            "payload": payload
        }
        try:
            self.event_queue.put_nowait(record)
        except queue.Full:
            pass  # Drop under extreme backpressure to avoid blocking OS hooks

    def _writer_loop(self):
        conn = sqlite3.connect(self.db_path)
        jsonl_file = open(self.jsonl_path, "a", encoding="utf-8")
        batch = []
        last_flush = time.time()

        while self.running or not self.event_queue.empty():
            try:
                record = self.event_queue.get(timeout=0.2)
                batch.append(record)
            except queue.Empty:
                pass

            if len(batch) >= 50 or (batch and time.time() - last_flush >= 1.0):
                try:
                    with conn:
                        conn.executemany(
                            "INSERT INTO telemetry_events (timestamp, epoch_ms, category, event_type, payload) VALUES (?, ?, ?, ?, ?)",
                            [(r["timestamp"], r["epoch_ms"], r["category"], r["event_type"], json.dumps(r["payload"])) for r in batch]
                        )
                    for r in batch:
                        jsonl_file.write(json.dumps(r) + "\n")
                    jsonl_file.flush()
                except Exception as e:
                    sys.stderr.write(f"[Sink Error] {e}\n")
                batch.clear()
                last_flush = time.time()

        jsonl_file.close()
        conn.close()

    def stop(self):
        self.running = False
        self.worker_thread.join(timeout=5.0)


# =====================================================================
# 2. SYSTEM & HARDWARE METADATA SCRAPER
# =====================================================================
class SystemMetadataScraper(threading.Thread):
    def __init__(self, sink: TelemetrySink, interval: float = 5.0):
        super().__init__(daemon=True)
        self.sink = sink
        self.interval = interval
        self.running = True

    def scrape_static_metadata(self):
        uname = platform.uname()
        net_ifaces = {}
        for iface, addrs in psutil.net_if_addrs().items():
            net_ifaces[iface] = [
                {"family": str(a.family), "address": a.address, "netmask": a.netmask}
                for a in addrs
            ]

        static_info = {
            "hostname": socket.gethostname(),
            "mac_address": ":".join(f"{(uuid.getnode() >> ele) & 0xff:02x}" for ele in range(0, 8 * 6, 8))[::-1],
            "os_system": uname.system,
            "os_release": uname.release,
            "os_version": uname.version,
            "machine": uname.machine,
            "processor": uname.processor,
            "python_version": platform.python_version(),
            "cpu_physical_cores": psutil.cpu_count(logical=False),
            "cpu_logical_cores": psutil.cpu_count(logical=True),
            "total_ram_bytes": psutil.virtual_memory().total,
            "boot_time_utc": datetime.fromtimestamp(psutil.boot_time(), tz=timezone.utc).isoformat(),
            "users": [u._asdict() for u in psutil.users()],
            "network_interfaces": net_ifaces,
            "partitions": [p._asdict() for p in psutil.disk_partitions(all=False)]
        }
        self.sink.emit("metadata", "static_system_snapshot", static_info)

    def run(self):
        self.scrape_static_metadata()
        while self.running:
            try:
                vm = psutil.virtual_memory()
                disk_io = psutil.disk_io_counters()
                net_io = psutil.net_io_counters()
                battery = psutil.sensors_battery()

                active_conns = []
                try:
                    for c in psutil.net_connections(kind="inet"):
                        if c.status == "ESTABLISHED":
                            active_conns.append({
                                "laddr": f"{c.laddr.ip}:{c.laddr.port}" if c.laddr else None,
                                "raddr": f"{c.raddr.ip}:{c.raddr.port}" if c.raddr else None,
                                "pid": c.pid
                            })
                except (psutil.AccessDenied, psutil.NoSuchProcess):
                    pass

                dynamic_metrics = {
                    "cpu_percent_per_core": psutil.cpu_percent(interval=None, percpu=True),
                    "cpu_freq_mhz": psutil.cpu_freq()._asdict() if psutil.cpu_freq() else None,
                    "ram_used_bytes": vm.used,
                    "ram_percent": vm.percent,
                    "disk_read_bytes": disk_io.read_bytes if disk_io else None,
                    "disk_write_bytes": disk_io.write_bytes if disk_io else None,
                    "net_bytes_sent": net_io.bytes_sent if net_io else None,
                    "net_bytes_recv": net_io.bytes_recv if net_io else None,
                    "established_connections_count": len(active_conns),
                    "established_connections_sample": active_conns[:20],
                    "battery": {
                        "percent": battery.percent,
                        "power_plugged": battery.power_plugged,
                        "secsleft": battery.secsleft
                    } if battery else None
                }
                self.sink.emit("metadata", "dynamic_resource_poll", dynamic_metrics)
            except Exception as e:
                self.sink.emit("error", "metadata_poll_error", {"error": str(e)})
            time.sleep(self.interval)


# =====================================================================
# 3. ACTIVE WINDOW & PROCESS LIFECYCLE TRACKER
# =====================================================================
class WindowAndProcessTracker(threading.Thread):
    def __init__(self, sink: TelemetrySink, poll_interval: float = 0.5):
        super().__init__(daemon=True)
        self.sink = sink
        self.poll_interval = poll_interval
        self.running = True
        self.last_window_signature = None
        self.known_pids = set(psutil.pids())

    def _get_active_window_info(self):
        info = {"title": None, "pid": None, "process_name": None, "exe": None, "bounds": None}
        
        if win32gui and win32process:
            try:
                hwnd = win32gui.GetForegroundWindow()
                if hwnd:
                    info["title"] = win32gui.GetWindowText(hwnd)
                    info["bounds"] = win32gui.GetWindowRect(hwnd)
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    info["pid"] = pid
                    if pid and psutil.pid_exists(pid):
                        proc = psutil.Process(pid)
                        info["process_name"] = proc.name()
                        info["exe"] = proc.exe()
                    return info
            except Exception:
                pass

        if gw:
            try:
                win = gw.getActiveWindow()
                if win:
                    info["title"] = win.title
                    info["bounds"] = (win.left, win.top, win.width, win.height)
            except Exception:
                pass
        return info

    def run(self):
        while self.running:
            # 1. Foreground Window Switch Detection
            win_info = self._get_active_window_info()
            sig = (win_info.get("title"), win_info.get("pid"))
            if sig != self.last_window_signature and any(sig):
                self.last_window_signature = sig
                self.sink.emit("activity", "foreground_window_changed", win_info)

            # 2. Process Spawn & Termination Diffing
            current_pids = set(psutil.pids())
            spawned = current_pids - self.known_pids
            terminated = self.known_pids - current_pids

            for pid in spawned:
                try:
                    p = psutil.Process(pid)
                    self.sink.emit("activity", "process_spawned", {
                        "pid": pid,
                        "name": p.name(),
                        "cmdline": p.cmdline()[:10],
                        "username": p.username(),
                        "create_time": p.create_time()
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    pass

            for pid in terminated:
                self.sink.emit("activity", "process_terminated", {"pid": pid})

            self.known_pids = current_pids
            time.sleep(self.poll_interval)


# =====================================================================
# 4. FILE SYSTEM ACTIVITY WATCHER
# =====================================================================
class TelemetryFileHandler(FileSystemEventHandler):
    def __init__(self, sink: TelemetrySink, ignored_dir: Path):
        self.sink = sink
        self.ignored_dir = str(ignored_dir.resolve())

    def on_any_event(self, event):
        src = os.path.abspath(event.src_path)
        if src.startswith(self.ignored_dir):
            return
        payload = {
            "event_type": event.event_type,
            "is_directory": event.is_directory,
            "src_path": src,
            "dest_path": getattr(event, "dest_path", None)
        }
        self.sink.emit("filesystem", f"fs_{event.event_type}", payload)


# =====================================================================
# 5. PERIPHERAL & COMPUTER MOVEMENT TRACKER (MOUSE & KEYBOARD KINEMATICS)
# =====================================================================
class InputKinematicsTracker:
    def __init__(self, sink: TelemetrySink, move_sample_interval: float = 0.05):
        self.sink = sink
        self.move_sample_interval = move_sample_interval
        
        self.last_move_ts = 0.0
        self.last_pos = None
        self.cumulative_distance_px = 0.0

        self.key_press_times = {}
        self.last_key_release_ts = None

        self.mouse_listener = mouse.Listener(
            on_move=self._on_move,
            on_click=self._on_click,
            on_scroll=self._on_scroll
        )
        self.kb_listener = keyboard.Listener(
            on_press=self._on_press,
            on_release=self._on_release
        )

    def start(self):
        self.mouse_listener.start()
        self.kb_listener.start()

    def stop(self):
        self.mouse_listener.stop()
        self.kb_listener.stop()

    def _on_move(self, x, y):
        now = time.perf_counter()
        if self.last_pos is not None:
            dx = x - self.last_pos[0]
            dy = y - self.last_pos[1]
            dist = math.hypot(dx, dy)
            self.cumulative_distance_px += dist
        else:
            dx, dy, dist = 0, 0, 0.0

        dt = now - self.last_move_ts
        if dt >= self.move_sample_interval:
            velocity_px_s = round(dist / dt, 2) if dt > 0 else 0.0
            angle_deg = round(math.degrees(math.atan2(dy, dx)), 2)
            self.sink.emit("computer_movement", "mouse_trajectory", {
                "x": x,
                "y": y,
                "dx": dx,
                "dy": dy,
                "step_distance_px": round(dist, 2),
                "cumulative_distance_px": round(self.cumulative_distance_px, 2),
                "velocity_px_per_sec": velocity_px_s,
                "heading_deg": angle_deg
            })
            self.last_move_ts = now
            self.last_pos = (x, y)

    def _on_click(self, x, y, button, pressed):
        self.sink.emit("computer_movement", "mouse_click", {
            "x": x,
            "y": y,
            "button": str(button),
            "action": "pressed" if pressed else "released"
        })

    def _on_scroll(self, x, y, dx, dy):
        self.sink.emit("computer_movement", "mouse_scroll", {
            "x": x,
            "y": y,
            "scroll_dx": dx,
            "scroll_dy": dy
        })

    def _format_key(self, key):
        if hasattr(key, "char") and key.char is not None:
            return "CHAR_KEY", str(key.char)
        return "SPECIAL_KEY", str(key)

    def _on_press(self, key):
        now = time.perf_counter()
        key_class, key_val = self._format_key(key)
        
        if key_val not in self.key_press_times:
            self.key_press_times[key_val] = now
            flight_time_ms = (
                round((now - self.last_key_release_ts) * 1000, 2)
                if self.last_key_release_ts is not None else None
            )
            self.sink.emit("computer_movement", "key_press", {
                "key_class": key_class,
                "key": key_val,
                "flight_time_ms": flight_time_ms
            })

    def _on_release(self, key):
        now = time.perf_counter()
        key_class, key_val = self._format_key(key)
        press_ts = self.key_press_times.pop(key_val, None)
        dwell_time_ms = round((now - press_ts) * 1000, 2) if press_ts is not None else None
        self.last_key_release_ts = now

        self.sink.emit("computer_movement", "key_release", {
            "key_class": key_class,
            "key": key_val,
            "dwell_time_ms": dwell_time_ms
        })


# =====================================================================
# 6. BODY-COMPUTER INTERACTION (BCI) & POSTURE TRACKER (MEDIAPIPE)
# =====================================================================
class BodyInteractionTracker(threading.Thread):
    """
    Extracts non-invasive biometric/kinematic body movements relative to the display:
    - Head yaw/pitch/roll proxies via nose & ear landmarks
    - Ergonomic posture (shoulder tilt angle, neck inclination)
    - Screen proximity proxy (normalized inter-ocular distance)
    - Hand presence and wrist movement coordinates
    """
    def __init__(self, sink: TelemetrySink, camera_index: int = 0, sample_fps: float = 5.0):
        super().__init__(daemon=True)
        self.sink = sink
        self.camera_index = camera_index
        self.frame_interval = 1.0 / sample_fps
        self.running = True

    def run(self):
        mp_holistic = mp.solutions.holistic
        cap = cv2.VideoCapture(self.camera_index)

        if not cap.isOpened():
            self.sink.emit("body_interaction", "camera_unavailable", {"camera_index": self.camera_index})
            return

        with mp_holistic.Holistic(
            static_image_mode=False,
            model_complexity=1,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        ) as holistic:
            while self.running and cap.isOpened():
                t0 = time.perf_counter()
                success, frame = cap.read()
                if not success:
                    time.sleep(1.0)
                    continue

                rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                rgb_frame.flags.writeable = False
                results = holistic.process(rgb_frame)

                bci_payload = {
                    "user_present": False,
                    "head_kinematics": None,
                    "posture_kinematics": None,
                    "left_hand_wrist": None,
                    "right_hand_wrist": None
                }

                if results.pose_landmarks:
                    lm = results.pose_landmarks.landmark
                    bci_payload["user_present"] = True

                    # Key Pose Landmarks: 0=Nose, 2=Left Eye, 5=Right Eye, 7=Left Ear, 8=Right Ear, 11=Left Shoulder, 12=Right Shoulder
                    nose = lm[0]
                    l_eye, r_eye = lm[2], lm[5]
                    l_ear, r_ear = lm[7], lm[8]
                    l_sh, r_sh = lm[11], lm[12]

                    # Inter-ocular distance in normalized frame space (proxy for physical distance to monitor)
                    inter_ocular_dist = math.hypot(l_eye.x - r_eye.x, l_eye.y - r_eye.y)

                    # Head roll angle (eye-line tilt in degrees)
                    head_roll_deg = math.degrees(math.atan2(l_eye.y - r_eye.y, l_eye.x - r_eye.x))

                    # Head yaw proxy (asymmetry between nose-to-left-ear and nose-to-right-ear)
                    dist_l_ear = math.hypot(nose.x - l_ear.x, nose.y - l_ear.y)
                    dist_r_ear = math.hypot(nose.x - r_ear.x, nose.y - r_ear.y)
                    yaw_ratio = (dist_l_ear - dist_r_ear) / (dist_l_ear + dist_r_ear + 1e-6)

                    # Shoulder alignment & slouch proxy (vertical distance from nose to shoulder midpoint)
                    sh_mid_x = (l_sh.x + r_sh.x) / 2.0
                    sh_mid_y = (l_sh.y + r_sh.y) / 2.0
                    shoulder_tilt_deg = math.degrees(math.atan2(l_sh.y - r_sh.y, l_sh.x - r_sh.x))
                    neck_vertical_extension = sh_mid_y - nose.y

                    bci_payload["head_kinematics"] = {
                        "nose_xyz": [round(nose.x, 4), round(nose.y, 4), round(nose.z, 4)],
                        "head_roll_deg": round(head_roll_deg, 2),
                        "head_yaw_asymmetry": round(yaw_ratio, 3),
                        "screen_proximity_index": round(inter_ocular_dist, 4)
                    }

                    bci_payload["posture_kinematics"] = {
                        "shoulder_midpoint_xy": [round(sh_mid_x, 4), round(sh_mid_y, 4)],
                        "shoulder_tilt_deg": round(shoulder_tilt_deg, 2),
                        "neck_vertical_extension": round(neck_vertical_extension, 4)
                    }

                if results.left_hand_landmarks:
                    w = results.left_hand_landmarks.landmark[0]
                    bci_payload["left_hand_wrist"] = [round(w.x, 4), round(w.y, 4), round(w.z, 4)]

                if results.right_hand_landmarks:
                    w = results.right_hand_landmarks.landmark[0]
                    bci_payload["right_hand_wrist"] = [round(w.x, 4), round(w.y, 4), round(w.z, 4)]

                self.sink.emit("body_interaction", "bci_frame_kinematics", bci_payload)

                elapsed = time.perf_counter() - t0
                sleep_time = max(0.0, self.frame_interval - elapsed)
                time.sleep(sleep_time)

        cap.release()


# =====================================================================
# 7. ORCHESTRATOR
# =====================================================================
def main():
    watch_path = Path.home()
    sink = TelemetrySink(output_dir="telemetry_logs")

    print(f"[*] Starting PC Live Telemetry & BCI Logger...")
    print(f"[*] JSONL Stream : {sink.jsonl_path.resolve()}")
    print(f"[*] SQLite DB    : {sink.db_path.resolve()}")
    print(f"[*] Watching Dir : {watch_path}")
    print("[*] Press Ctrl+C to stop and flush logs.\n")

    # Start System & Hardware Scraper
    meta_scraper = SystemMetadataScraper(sink, interval=5.0)
    meta_scraper.start()

    # Start Window & Process Activity Tracker
    win_tracker = WindowAndProcessTracker(sink, poll_interval=0.5)
    win_tracker.start()

    # Start File System Watcher
    fs_observer = Observer()
    fs_handler = TelemetryFileHandler(sink, ignored_dir=sink.output_dir)
    fs_observer.schedule(fs_handler, str(watch_path), recursive=False)
    fs_observer.start()

    # Start Peripheral Input Kinematics (Mouse & Keyboard)
    input_tracker = InputKinematicsTracker(sink, move_sample_interval=0.05)
    input_tracker.start()

    # Start Webcam Body-Computer Interaction Tracker
    bci_tracker = BodyInteractionTracker(sink, camera_index=0, sample_fps=4.0)
    bci_tracker.start()

    try:
        while True:
            time.sleep(1.0)
            q_size = sink.event_queue.qsize()
            sys.stdout.write(f"\r[LIVE] Queue Buffer: {q_size:4d} events | Active Window: {win_tracker.last_window_signature}   ")
            sys.stdout.flush()
    except KeyboardInterrupt:
        print("\n[*] Shutting down sensors and flushing telemetry queue...")
        meta_scraper.running = False
        win_tracker.running = False
        bci_tracker.running = False
        input_tracker.stop()
        fs_observer.stop()
        fs_observer.join(timeout=3.0)
        sink.stop()
        print("[*] Telemetry safely saved.")


if __name__ == "__main__":
    main()
    