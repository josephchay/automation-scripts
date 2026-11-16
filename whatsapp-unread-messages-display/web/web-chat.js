// ==UserScript==
// @name         Chat Unread Messages Full Overlay
// @namespace    http://tampermonkey.net/
// @version      1.0
// @description  Displays an overlay of ALL notified/unread messages on a chat or group instead of only the brief last message.
// @match        https://web.whatsapp.com/*
// @match        https://web.telegram.org/*
// @grant        none
// ==/UserScript==

(function () {
    'use strict';

    // Adjust selectors for your target web chat platform
    const CONFIG = {
        // Selector for each chat/group row in the sidebar list
        chatRowSelector: '[role="listitem"], [role="row"], .chatlist-chat',
        // Selector for the chat or group title inside a row
        titleSelector: 'span[dir="auto"][title], .peer-title, .chat-title',
        // Selector for the brief last-message preview inside a row
        previewSelector: '.matched-text, .last-message, span[dir="ltr"], .dialog-subtitle',
        // Selector for the unread count badge (used to detect when a chat is read/cleared)
        unreadBadgeSelector: '[aria-label*="unread"], .badge, .unread'
    };

    // Map<chatTitle, Array<{ text: string, time: string, sender?: string }>>
    const messageStore = new Map();

    // Inject Overlay CSS
    const style = document.createElement('style');
    style.textContent = `
        .full-msg-overlay {
            position: fixed;
            z-index: 999999;
            width: 340px;
            max-height: 420px;
            background: rgba(20, 24, 33, 0.96);
            color: #f1f5f9;
            border: 1px solid rgba(255, 255, 255, 0.15);
            border-radius: 10px;
            box-shadow: 0 12px 28px rgba(0, 0, 0, 0.45);
            padding: 12px;
            overflow-y: auto;
            font-family: system-ui, -apple-system, sans-serif;
            font-size: 13px;
            backdrop-filter: blur(8px);
            pointer-events: auto;
            display: none;
        }
        .full-msg-overlay.visible {
            display: block;
        }
        .full-msg-overlay-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-weight: 600;
            font-size: 14px;
            padding-bottom: 8px;
            margin-bottom: 8px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.12);
            color: #38bdf8;
        }
        .full-msg-overlay-clear {
            background: rgba(255, 255, 255, 0.1);
            border: none;
            color: #cbd5e1;
            border-radius: 4px;
            padding: 2px 8px;
            font-size: 11px;
            cursor: pointer;
        }
        .full-msg-overlay-clear:hover {
            background: rgba(239, 68, 68, 0.8);
            color: #fff;
        }
        .full-msg-item {
            padding: 6px 8px;
            margin-bottom: 6px;
            background: rgba(255, 255, 255, 0.06);
            border-left: 3px solid #38bdf8;
            border-radius: 4px;
            line-height: 1.4;
            word-break: break-word;
        }
        .full-msg-time {
            display: block;
            font-size: 10px;
            color: #94a3b8;
            margin-top: 3px;
            text-align: right;
        }
        .chat-stack-indicator {
            display: inline-block;
            background: #0284c7;
            color: #fff;
            font-size: 10px;
            font-weight: 700;
            padding: 1px 6px;
            border-radius: 999px;
            margin-left: 6px;
            cursor: pointer;
        }
    `;
    document.head.appendChild(style);

    // Create shared floating overlay element
    const overlay = document.createElement('div');
    overlay.className = 'full-msg-overlay';
    document.body.appendChild(overlay);

    let hideTimeout = null;

    function recordMessage(chatTitle, messageText) {
        if (!chatTitle || !messageText) return;
        const cleanTitle = chatTitle.trim();
        const cleanText = messageText.trim();
        if (!cleanText) return;

        if (!messageStore.has(cleanTitle)) {
            messageStore.set(cleanTitle, []);
        }

        const list = messageStore.get(cleanTitle);
        const lastEntry = list[list.length - 1];

        // Deduplicate identical consecutive snapshots
        if (lastEntry && lastEntry.text === cleanText) return;

        const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
        list.push({ text: cleanText, time: now });
    }

    // 1. Hook into Browser Notification API to capture every push message verbatim
    const OriginalNotification = window.Notification;
    if (OriginalNotification) {
        const PatchedNotification = function (title, options = {}) {
            if (title && options.body) {
                recordMessage(title, options.body);
            }
            return new OriginalNotification(title, options);
        };
        PatchedNotification.prototype = OriginalNotification.prototype;
        Object.defineProperty(PatchedNotification, 'permission', {
            get: () => OriginalNotification.permission
        });
        PatchedNotification.requestPermission = OriginalNotification.requestPermission.bind(OriginalNotification);
        window.Notification = PatchedNotification;
    }

    // 2. Render Overlay for a specific chat row
    function showOverlayForChat(rowEl, chatTitle) {
        const messages = messageStore.get(chatTitle) || [];
        if (messages.length === 0) return;

        clearTimeout(hideTimeout);
        overlay.innerHTML = '';

        const header = document.createElement('div');
        header.className = 'full-msg-overlay-header';

        const titleSpan = document.createElement('span');
        titleSpan.textContent = `${chatTitle} (${messages.length})`;

        const clearBtn = document.createElement('button');
        clearBtn.className = 'full-msg-overlay-clear';
        clearBtn.textContent = 'Clear';
        clearBtn.onclick = (e) => {
            e.stopPropagation();
            messageStore.delete(chatTitle);
            overlay.classList.remove('visible');
        };

        header.appendChild(titleSpan);
        header.appendChild(clearBtn);
        overlay.appendChild(header);

        messages.forEach((msg) => {
            const item = document.createElement('div');
            item.className = 'full-msg-item';
            item.textContent = msg.text;

            const time = document.createElement('span');
            time.className = 'full-msg-time';
            time.textContent = msg.time;

            item.appendChild(time);
            overlay.appendChild(item);
        });

        // Position beside the hovered chat item
        const rect = rowEl.getBoundingClientRect();
        const topPos = Math.min(rect.top, window.innerHeight - 440);
        const leftPos = Math.min(rect.right + 10, window.innerWidth - 360);

        overlay.style.top = `${Math.max(10, topPos)}px`;
        overlay.style.left = `${Math.max(10, leftPos)}px`;
        overlay.classList.add('visible');
    }

    overlay.addEventListener('mouseenter', () => clearTimeout(hideTimeout));
    overlay.addEventListener('mouseleave', () => {
        overlay.classList.remove('visible');
    });

    // 3. Observe Sidebar DOM for new last-message previews & attach hover listeners
    const observer = new MutationObserver(() => {
        const rows = document.querySelectorAll(CONFIG.chatRowSelector);
        rows.forEach((row) => {
            const titleEl = row.querySelector(CONFIG.titleSelector);
            const previewEl = row.querySelector(CONFIG.previewSelector);
            const unreadEl = row.querySelector(CONFIG.unreadBadgeSelector);

            if (!titleEl) return;
            const chatTitle = (titleEl.getAttribute('title') || titleEl.textContent || '').trim();
            if (!chatTitle) return;

            // Record preview changes whenever an unread badge is present
            if (unreadEl && previewEl) {
                recordMessage(chatTitle, previewEl.textContent);
            }

            // Bind hover and click events once per row element
            if (!row.dataset.overlayBound) {
                row.dataset.overlayBound = 'true';

                row.addEventListener('mouseenter', () => {
                    showOverlayForChat(row, chatTitle);
                });

                row.addEventListener('mouseleave', () => {
                    hideTimeout = setTimeout(() => {
                        overlay.classList.remove('visible');
                    }, 250);
                });

                // Clear accumulated messages when the user clicks into the chat
                row.addEventListener('click', () => {
                    messageStore.delete(chatTitle);
                    overlay.classList.remove('visible');
                });
            }
        });
    });

    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
})();
