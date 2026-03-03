"""MessengerHub — coordinates multiple messenger platforms."""
import threading

from config import log


class MessengerHub:
    """Central hub for multi-messenger coordination.

    Routes incoming messages to AI, broadcasts AI responses to all
    connected messengers, and shows cross-platform input notifications.
    """

    def __init__(self):
        self.messengers = {}          # {platform_name: MessengerBase}
        self._message_handler = None  # callback: (text, files, ctx) -> None
        self._callback_handler = None # callback: (callback_id, msg_id, data, ctx) -> None
        self._lock = threading.Lock()

    def add(self, messenger):
        """Register a messenger platform."""
        messenger.hub = self
        self.messengers[messenger.platform_name] = messenger
        log.info("Messenger registered: %s", messenger.platform_name)

    def remove(self, platform_name):
        """Unregister a messenger platform."""
        m = self.messengers.pop(platform_name, None)
        if m:
            m.hub = None
            log.info("Messenger removed: %s", platform_name)

    def get(self, platform_name):
        """Get a messenger by platform name."""
        return self.messengers.get(platform_name)

    def set_message_handler(self, handler):
        """Set the callback for incoming messages: handler(ctx)"""
        self._message_handler = handler

    def set_callback_handler(self, handler):
        """Set the callback for button presses: handler(ctx)"""
        self._callback_handler = handler

    # --- Incoming events (called by messengers) ---

    def on_message(self, from_platform, text, files=None):
        """A user message was received from a messenger platform."""
        from messenger import MessageContext, set_current_messenger

        messenger = self.messengers.get(from_platform)
        if not messenger:
            return

        set_current_messenger(messenger)

        ctx = MessageContext(
            text=text,
            platform=from_platform,
            messenger=messenger,
            hub=self,
            files=files or [],
        )

        # Notify other messengers about the input
        self._notify_input(from_platform, text)

        if self._message_handler:
            self._message_handler(ctx)

    def on_callback(self, from_platform, callback_id, msg_id, data):
        """A button callback was received from a messenger platform."""
        from messenger import MessageContext, set_current_messenger

        messenger = self.messengers.get(from_platform)
        if not messenger:
            return

        set_current_messenger(messenger)

        ctx = MessageContext(
            text="",
            platform=from_platform,
            messenger=messenger,
            hub=self,
            msg_id=msg_id,
            callback_id=callback_id,
            callback_data=data,
        )

        if self._callback_handler:
            self._callback_handler(ctx)

    # --- Outgoing: broadcast to all messengers ---

    def broadcast(self, html_text):
        """Send HTML message to ALL connected messengers."""
        for m in self.messengers.values():
            try:
                m.send_html(html_text)
            except Exception as e:
                log.warning("Broadcast to %s failed: %s", m.platform_name, e)

    def broadcast_long(self, header, body_md, footer=None):
        """Send long markdown message to ALL connected messengers."""
        for m in self.messengers.values():
            try:
                m.send_long(header, body_md, footer=footer)
            except Exception as e:
                log.warning("Broadcast long to %s failed: %s", m.platform_name, e)

    def broadcast_typing(self):
        """Show typing indicator on ALL connected messengers."""
        for m in self.messengers.values():
            try:
                m.send_typing()
            except Exception as e:
                log.warning("Broadcast typing to %s failed: %s", m.platform_name, e)

    def broadcast_except(self, exclude_platform, html_text):
        """Send to all messengers EXCEPT the specified one."""
        for name, m in self.messengers.items():
            if name == exclude_platform:
                continue
            try:
                m.send_html(html_text)
            except Exception as e:
                log.warning("Broadcast to %s failed: %s", name, e)

    # --- Cross-platform notifications ---

    def _notify_input(self, from_platform, text):
        """Notify other messengers that a message was sent from from_platform."""
        if len(self.messengers) <= 1:
            return
        label = from_platform.title()
        preview = text[:100] + ("..." if len(text) > 100 else "")
        for name, m in self.messengers.items():
            if name == from_platform:
                continue
            try:
                m.send_html(f"\U0001f4ac <b>[{label}]</b> {m.format_md(preview)}")
            except Exception as e:
                log.warning("Input notify to %s failed: %s", name, e)

    # --- Lifecycle ---

    def start_all(self):
        """Start all registered messengers."""
        for m in self.messengers.values():
            try:
                m.start()
                log.info("Messenger started: %s", m.platform_name)
            except Exception as e:
                log.error("Failed to start %s: %s", m.platform_name, e)

    def stop_all(self):
        """Stop all registered messengers."""
        for m in self.messengers.values():
            try:
                m.stop()
            except Exception:
                pass
