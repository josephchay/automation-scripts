const { ipcRenderer } = require('electron');

// Store unread messages per chat or group ID/Title
const unreadStacks = new Map();

function pushUnreadMessage(chatKey, sender, text) {
    if (!chatKey || !text) return;
    if (!unreadStacks.has(chatKey)) {
        unreadStacks.set(chatKey, []);
    }

    const stack = unreadStacks.get(chatKey);
    const formatted = sender ? `${sender}: ${text}` : text;

    // Prevent consecutive duplicates
    if (stack.length > 0 && stack[stack.length - 1].text === formatted) return;

    stack.push({
        text: formatted,
        time: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })
    });
}

// 1. Intercept desktop Notification calls from the Electron renderer
window.addEventListener('DOMContentLoaded', () => {
    const NativeNotification = window.Notification;

    window.Notification = function (title, options = {}) {
        pushUnreadMessage(title.trim(), null, options.body || '');
        return new NativeNotification(title, options);
    };
    window.Notification.permission = NativeNotification.permission;
    window.Notification.requestPermission = NativeNotification.requestPermission.bind(NativeNotification);

    // Optional: Listen to custom IPC events if your Electron main process dispatches incoming messages
    ipcRenderer.on('incoming-chat-message', (_event, payload) => {
        // payload: { chatTitle, sender, message }
        pushUnreadMessage(payload.chatTitle, payload.sender, payload.message);
    });

    initDesktopSidebarOverlay();
});

// 2. Attach overlay UI to sidebar chat items inside the desktop window
function initDesktopSidebarOverlay() {
    const overlay = document.createElement('div');
    Object.assign(overlay.style, {
        position: 'fixed',
        zIndex: '2147483647',
        width: '320px',
        maxHeight: '380px',
        overflowY: 'auto',
        backgroundColor: '#111827',
        color: '#f9fafb',
        border: '1px solid #374151',
        borderRadius: '8px',
        boxShadow: '0 10px 25px rgba(0,0,0,0.5)',
        padding: '10px',
        fontFamily: 'system-ui, sans-serif',
        fontSize: '13px',
        display: 'none'
    });
    document.body.appendChild(overlay);

    let hideTimer = null;

    document.addEventListener('mouseover', (e) => {
        const chatRow = e.target.closest('[data-chat-title], [role="listitem"], [role="treeitem"]');
        if (!chatRow) return;

        const chatTitle = (
            chatRow.getAttribute('data-chat-title') ||
            chatRow.getAttribute('aria-label') ||
            chatRow.innerText.split('\n')[0] ||
            ''
        ).trim();

        // Find matching key in unreadStacks
        const matchedKey = Array.from(unreadStacks.keys()).find(
            (k) => chatTitle.includes(k) || k.includes(chatTitle)
        );
        if (!matchedKey) return;

        const messages = unreadStacks.get(matchedKey);
        if (!messages || messages.length === 0) return;

        clearTimeout(hideTimer);
        const rect = chatRow.getBoundingClientRect();

        overlay.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;border-bottom:1px solid #374151;padding-bottom:6px;margin-bottom:8px;">
                <strong style="color:#38bdf8;">${matchedKey} (${messages.length})</strong>
                <button id="clear-stack-btn" style="background:#ef4444;color:#fff;border:none;border-radius:4px;padding:2px 8px;font-size:11px;cursor:pointer;">Clear</button>
            </div>
            ${messages.map(m => `
                <div style="background:#1f2937;padding:6px 8px;border-left:3px solid #38bdf8;border-radius:4px;margin-bottom:6px;">
                    <div>${m.text}</div>
                    <div style="font-size:10px;color:#9ca3af;text-align:right;margin-top:2px;">${m.time}</div>
                </div>
            `).join('')}
        `;

        overlay.style.top = `${Math.min(rect.top, window.innerHeight - 390)}px`;
        overlay.style.left = `${Math.min(rect.right + 12, window.innerWidth - 340)}px`;
        overlay.style.display = 'block';

        overlay.querySelector('#clear-stack-btn').onclick = (ev) => {
            ev.stopPropagation();
            unreadStacks.delete(matchedKey);
            overlay.style.display = 'none';
        };

        // Clear stack automatically when the user clicks to open the chat
        chatRow.addEventListener('click', () => {
            unreadStacks.delete(matchedKey);
            overlay.style.display = 'none';
        }, { once: true });
    });

    document.addEventListener('mouseout', (e) => {
        if (!overlay.contains(e.relatedTarget)) {
            hideTimer = setTimeout(() => {
                overlay.style.display = 'none';
            }, 250);
        }
    });

    overlay.addEventListener('mouseenter', () => clearTimeout(hideTimer));
    overlay.addEventListener('mouseleave', () => {
        overlay.style.display = 'none';
    });
}
