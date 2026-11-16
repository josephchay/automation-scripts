import asyncio
import datetime
import tkinter as tk
from collections import defaultdict
import win32api
import uiautomation as auto
from winsdk.windows.ui.notifications import NotificationKinds, KnownNotificationBindings
from winsdk.windows.ui.notifications.management import (
    UserNotificationListener,
    UserNotificationListenerAccessStatus,
)

# Target desktop apps (leave empty set to capture from all desktop chat apps)
TARGET_APPS = {"whatsapp", "telegram", "slack", "discord", "teams"}


class DesktopChatOverlayApp:
    def __init__(self):
        # Dictionary mapping Chat/Group Title -> List of {time, text}
        self.message_store = defaultdict(list)
        self.seen_notification_ids = set()
        self.current_hovered_chat = None
        self.hide_job = None

        # Initialize Tkinter frameless always-on-top overlay
        self.root = tk.Tk()
        self.root.withdraw()  # Hidden until a chat with unread messages is hovered
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", 0.96)
        self.root.configure(bg="#141821", highlightthickness=1, highlightbackground="#334155")

        # Header Frame
        self.header_frame = tk.Frame(self.root, bg="#141821", padx=10, pady=8)
        self.header_frame.pack(fill=tk.X)

        self.title_label = tk.Label(
            self.header_frame,
            text="",
            fg="#38bdf8",
            bg="#141821",
            font=("Segoe UI", 10, "bold"),
            anchor="w",
        )
        self.title_label.pack(side=tk.LEFT, fill=tk.X, expand=True)

        self.clear_btn = tk.Button(
            self.header_frame,
            text="Clear",
            fg="#f8fafc",
            bg="#ef4444",
            activebackground="#dc2626",
            activeforeground="#ffffff",
            bd=0,
            padx=8,
            pady=2,
            font=("Segoe UI", 8, "bold"),
            cursor="hand2",
            command=self.clear_current_chat,
        )
        self.clear_btn.pack(side=tk.RIGHT)

        # Separator
        tk.Frame(self.root, bg="#334155", height=1).pack(fill=tk.X)

        # Messages List Container
        self.msg_container = tk.Frame(self.root, bg="#141821", padx=10, pady=8)
        self.msg_container.pack(fill=tk.BOTH, expand=True)

        # Keep overlay open while cursor is inside the overlay itself
        self.root.bind("<Enter>", self.cancel_hide)
        self.root.bind("<Leave>", lambda e: self.schedule_hide(250))

        # Initialize async Windows Notification Listener
        self.loop = asyncio.new_event_loop()
        self.listener = UserNotificationListener.current
        self.has_access = self.loop.run_until_complete(self.request_notification_access())

        # Start periodic polling loops
        self.poll_notifications()
        self.poll_cursor_hover()

    async def request_notification_access(self):
        status = await self.listener.request_access_async()
        if status != UserNotificationListenerAccessStatus.ALLOWED:
            print(f"[!] Notification access denied: {status}")
            return False
        print("[+] Notification listener active. Waiting for chat notifications...")
        return True

    async def fetch_new_notifications(self):
        if not self.has_access:
            return
        notifications = await self.listener.get_notifications_async(NotificationKinds.TOAST)
        for notif in notifications:
            if notif.id in self.seen_notification_ids:
                continue

            self.seen_notification_ids.add(notif.id)

            # Filter by target desktop chat apps if configured
            try:
                app_name = notif.app_info.display_info.display_name.lower()
                if TARGET_APPS and not any(target in app_name for target in TARGET_APPS):
                    continue
            except Exception:
                pass

            binding = notif.notification.visual.get_binding(
                KnownNotificationBindings.toast_generic
            )
            if not binding:
                continue

            text_elements = binding.get_text_elements()
            texts = [el.text for el in text_elements if el.text]
            if len(texts) >= 2:
                chat_title = texts[0].strip()
                message_body = " — ".join(t.strip() for t in texts[1:])
                self.record_message(chat_title, message_body)

    def record_message(self, chat_title: str, message_body: str):
        history = self.message_store[chat_title]
        # Avoid duplicate consecutive snapshots
        if history and history[-1]["text"] == message_body:
            return

        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        history.append({"time": timestamp, "text": message_body})
        print(f"[Recorded] {chat_title}: {message_body} (Total: {len(history)})")

        # Live-refresh if overlay is already showing this chat
        if self.current_hovered_chat == chat_title:
            self.render_overlay_contents(chat_title)

    def poll_notifications(self):
        try:
            self.loop.run_until_complete(self.fetch_new_notifications())
        except Exception as e:
            print(f"Notification polling error: {e}")
        self.root.after(1000, self.poll_notifications)

    def poll_cursor_hover(self):
        """Inspects the desktop UI element under the mouse cursor to match unread chats."""
        if self.message_store:
            try:
                x, y = win32api.GetCursorPos()

                # Ignore if cursor is currently inside our own overlay window
                if self.root.state() == "normal":
                    ox, oy = self.root.winfo_rootx(), self.root.winfo_rooty()
                    ow, oh = self.root.winfo_width(), self.root.winfo_height()
                    if ox <= x <= ox + ow and oy <= y <= oy + oh:
                        self.root.after(200, self.poll_cursor_hover)
                        return

                ctrl = auto.ControlFromPoint(x, y)
                if ctrl:
                    # Collect text from the hovered control and up to 2 parent containers (e.g. ListItem)
                    candidate_names = []
                    curr = ctrl
                    for _ in range(3):
                        if not curr:
                            break
                        if curr.Name:
                            candidate_names.append(curr.Name)
                        curr = curr.GetParentControl()

                    matched_chat = None
                    for chat_title in list(self.message_store.keys()):
                        if any(chat_title.lower() in name.lower() for name in candidate_names):
                            matched_chat = chat_title
                            break

                    if matched_chat and self.message_store[matched_chat]:
                        self.show_overlay(matched_chat, x, y)
                    else:
                        self.schedule_hide(300)
            except Exception:
                pass

        self.root.after(250, self.poll_cursor_hover)

    def show_overlay(self, chat_title: str, cursor_x: int, cursor_y: int):
        self.cancel_hide()
        if self.current_hovered_chat != chat_title or self.root.state() != "normal":
            self.current_hovered_chat = chat_title
            self.render_overlay_contents(chat_title)

            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            pos_x = min(cursor_x + 18, screen_w - 360)
            pos_y = min(cursor_y + 10, screen_h - 420)

            self.root.geometry(f"340x360+{max(10, pos_x)}+{max(10, pos_y)}")
            self.root.deiconify()

    def render_overlay_contents(self, chat_title: str):
        for widget in self.msg_container.winfo_children():
            widget.destroy()

        messages = self.message_store.get(chat_title, [])
        self.title_label.config(text=f"{chat_title} ({len(messages)} unread)")

        # Display recent messages inside the overlay
        for item in messages[-12:]:
            card = tk.Frame(self.msg_container, bg="#1e293b", padx=8, pady=5)
            card.pack(fill=tk.X, pady=3)

            msg_lbl = tk.Label(
                card,
                text=item["text"],
                fg="#f1f5f9",
                bg="#1e293b",
                font=("Segoe UI", 9),
                wraplength=295,
                justify=tk.LEFT,
                anchor="w",
            )
            msg_lbl.pack(fill=tk.X)

            time_lbl = tk.Label(
                card,
                text=item["time"],
                fg="#94a3b8",
                bg="#1e293b",
                font=("Segoe UI", 7),
                anchor="e",
            )
            time_lbl.pack(fill=tk.X)

    def clear_current_chat(self):
        if self.current_hovered_chat and self.current_hovered_chat in self.message_store:
            del self.message_store[self.current_hovered_chat]
        self.hide_overlay()

    def cancel_hide(self, event=None):
        if self.hide_job:
            self.root.after_cancel(self.hide_job)
            self.hide_job = None

    def schedule_hide(self, delay_ms=250):
        if not self.hide_job and self.root.state() == "normal":
            self.hide_job = self.root.after(delay_ms, self.hide_overlay)

    def hide_overlay(self):
        self.hide_job = None
        self.current_hovered_chat = None
        self.root.withdraw()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = DesktopChatOverlayApp()
    app.run()
    