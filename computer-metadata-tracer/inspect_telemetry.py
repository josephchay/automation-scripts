import json
import sqlite3
from pathlib import Path

DB_PATH = Path("telemetry_logs/telemetry.db")

def summarize_telemetry():
    if not DB_PATH.exists():
        print(f"Database not found at {DB_PATH}")
        return

    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()

        print("=== 1. EVENT COUNTS BY CATEGORY & TYPE ===")
        cursor.execute("""
            SELECT category, event_type, COUNT(*) 
            FROM telemetry_events 
            GROUP BY category, event_type 
            ORDER BY COUNT(*) DESC
        """)
        for cat, etype, count in cursor.fetchall():
            print(f"  {cat:<20} | {etype:<28} | {count:>8,}")

        print("\n=== 2. FOREGROUND APPLICATION SWITCHES (LAST 10) ===")
        cursor.execute("""
            SELECT timestamp, payload 
            FROM telemetry_events 
            WHERE event_type = 'foreground_window_changed' 
            ORDER BY epoch_ms DESC LIMIT 10
        """)
        for ts, payload_str in cursor.fetchall():
            p = json.loads(payload_str)
            print(f"  [{ts}] {p.get('process_name')} -> {p.get('title')}")

        print("\n=== 3. BODY-COMPUTER INTERACTION (POSTURE & PROXIMITY AVERAGES) ===")
        cursor.execute("""
            SELECT payload 
            FROM telemetry_events 
            WHERE event_type = 'bci_frame_kinematics' 
            ORDER BY epoch_ms DESC LIMIT 200
        """)
        rows = cursor.fetchall()
        present_frames = 0
        prox_vals, neck_vals = [], []
        for (p_str,) in rows:
            p = json.loads(p_str)
            if p.get("user_present"):
                present_frames += 1
                if p.get("head_kinematics"):
                    prox_vals.append(p["head_kinematics"]["screen_proximity_index"])
                if p.get("posture_kinematics"):
                    neck_vals.append(p["posture_kinematics"]["neck_vertical_extension"])

        if rows:
            print(f"  Presence Rate (Last {len(rows)} frames): {(present_frames / len(rows)) * 100:.1f}%")
        if prox_vals:
            print(f"  Avg Screen Proximity Index       : {sum(prox_vals)/len(prox_vals):.4f}")
            print(f"  Avg Neck Vertical Extension      : {sum(neck_vals)/len(neck_vals):.4f}")

if __name__ == "__main__":
    summarize_telemetry()
    