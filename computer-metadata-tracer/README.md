Develop and write the code for:

Live logger for all activities of own PC
Scrap all metadata, activities, computer movements, body computer interaction movements, etc.

1. Environment & Dependencies
Install the required cross-platform libraries for system introspection, input hooks, file watching, and computer vision:

pip install psutil pynput watchdog opencv-python mediapipe numpy pygetwindow

(Optional for Windows process-to-window mapping):

pip install pywin32

2. Full Live Telemetry & Body-Interaction Logger (pc_telemetry_logger.py)This script launches concurrent daemon threads that feed into a thread-safe central event queue:SystemMetadataScraper: Snapshots static hardware/OS identifiers on startup and polls dynamic CPU, RAM, disk I/O, network sockets, and battery metrics.WindowAndProcessTracker: Monitors foreground application switches, window titles, and new/terminated processes.FileSystemActivityWatcher: Hooks into OS file events (create, modify, move, delete) using watchdog.InputKinematicsTracker: Tracks mouse coordinates $(x, y)$, instantaneous cursor velocity ($\text{px/s}$), travel distance, clicks, scroll vectors, and keyboard cadence (dwell time and flight time between keys).BodyInteractionTracker: Uses OpenCV and MediaPipe Pose/Face/Hands to extract real-time body-computer interaction coordinates—head pitch/yaw proxies, shoulder tilt angle, inter-ocular distance (screen distance proxy), and hand landmarks—without recording raw video to disk.

3. Telemetry Query & Summary Inspector (inspect_telemetry.py)
Run this companion script at any time (even while the logger is active) to aggregate event counts, inspect mouse travel distance, calculate average keystroke dwell/flight cadence, and review posture/head-tracking metrics from the SQLite database.

4. Captured Telemetry Schema OverviewCategoryEvent TypesScraped Metrics & Metadatametadatastatic_system_snapshot, dynamic_resource_pollHostname, MAC, OS kernel, CPU core topology, boot time, active users, disk partitions, per-core CPU load, RAM usage, disk/network I/O bytes, established TCP sockets, battery state.activityforeground_window_changed, process_spawned, process_terminatedActive window title, screen coordinates/bounds, owning PID, executable path, command-line arguments, process spawn/exit timestamps.filesystemfs_created, fs_modified, fs_deleted, fs_movedSource path, destination path, directory vs. file flag, exact modification timestamp.computer_movementmouse_trajectory, mouse_click, mouse_scroll, key_press, key_releaseCursor $(x, y)$, delta $(dx, dy)$, cumulative pixels traveled, cursor speed ($\text{px/s}$), vector heading angle, scroll vectors, keystroke dwell time ($\text{ms}$ held), flight time ($\text{ms}$ between keys).body_interactionbci_frame_kinematics, camera_unavailableUser presence boolean, 3D normalized nose coordinates, head roll angle, head yaw asymmetry, screen proximity index (inter-ocular scale), shoulder tilt angle, neck vertical extension (slouch detection), 3D left/right wrist coordinates.
