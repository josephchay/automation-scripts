Capturing all notified messages per chat or group on a desktop app—and displaying them in an overlay anchored to that specific conversation—can be achieved either via a native OS background script (which works across existing desktop apps like WhatsApp Desktop, Telegram Desktop, Slack, Teams, and Discord) or directly inside an Electron desktop app if you are building or modding the client yourself.

1. Standalone Desktop Script (Python for Windows Desktop Apps)
This script hooks into the Windows Notification Listener API (winsdk) to capture every incoming desktop toast notification before it is overwritten, groups the messages by chat or group name, and uses Windows UI Automation (uiautomation) to detect when your mouse hovers over that specific chat or group inside the desktop app—popping up a frameless, always-on-top overlay right beside the chat row.

Prerequisites
Install the required packages in an elevated terminal, and ensure Notification Access is enabled in Windows Settings:

```bash
pip install winsdk uiautomation pywin32
```

2. Electron Desktop App Integration (Preload / Renderer Script)
If you are building your own desktop chat application in Electron (or injecting into an Electron-based desktop messenger such as Discord, Slack, or Teams via a preload script), you can intercept the native window.Notification constructor and IPC message events directly in the renderer process to attach the overlay directly onto each sidebar chat element.

Choosing Between the Two
Use the Python Script (#1) if you want an external utility that works on top of official, unmodified Windows desktop chat apps (WhatsApp Desktop from the Microsoft Store, Telegram Desktop, Slack, Teams, or Discord).

Use the Electron Preload Script (#2) if you are developing your own Electron desktop chat client or injecting custom JavaScript into an Electron app's BrowserWindow preload configuration.
