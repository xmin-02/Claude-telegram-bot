"""Messenger abstraction layer for multi-platform support."""
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional, List, Any

# Thread-local: tracks which messenger is handling the current request
_local = threading.local()


def get_current_messenger():
    """Get the messenger handling the current request (thread-local)."""
    return getattr(_local, "messenger", None)


def set_current_messenger(messenger):
    """Set the messenger for the current thread context."""
    _local.messenger = messenger


class MessengerBase(ABC):
    """Abstract base class for messenger platforms."""

    hub = None  # set by MessengerHub.add()

    @property
    @abstractmethod
    def platform_name(self) -> str:
        """Platform identifier: 'telegram', 'discord', etc."""

    @property
    @abstractmethod
    def max_message_length(self) -> int:
        """Maximum single message length for this platform."""

    # --- Message sending ---

    @abstractmethod
    def send_text(self, text: str, **kwargs) -> Optional[str]:
        """Send plain text. Returns message ID or None."""

    @abstractmethod
    def send_html(self, html: str, **kwargs) -> Optional[str]:
        """Send HTML-formatted text. Impl converts to platform format. Returns msg ID."""

    @abstractmethod
    def send_long(self, header: str, body_md: str, footer: str = None):
        """Send a long markdown message, split into chunks if needed."""

    @abstractmethod
    def edit_message(self, msg_id: str, text: str, **kwargs):
        """Edit an existing message."""

    @abstractmethod
    def delete_message(self, msg_id: str):
        """Delete a message by ID."""

    @abstractmethod
    def send_typing(self):
        """Show typing indicator."""

    # --- Rich UI ---

    @abstractmethod
    def send_keyboard(self, text: str, buttons: list, **kwargs) -> Optional[str]:
        """Send message with inline buttons. Returns msg ID."""

    @abstractmethod
    def answer_callback(self, callback_id: str, text: str = ""):
        """Answer a callback query (button press acknowledgment)."""

    # --- Files ---

    @abstractmethod
    def download_file(self, file_ref: Any) -> Optional[str]:
        """Download an attached file. Returns local file path or None."""

    # --- Formatting ---

    @abstractmethod
    def format_md(self, markdown: str) -> str:
        """Convert markdown to platform-specific format."""

    # --- Lifecycle ---

    @abstractmethod
    def start(self):
        """Start receiving messages (runs event loop, may spawn thread)."""

    @abstractmethod
    def stop(self):
        """Stop the messenger."""

    def on_message(self, text: str, files: list = None):
        """Called when a user message is received. Routes to hub."""
        if self.hub:
            self.hub.on_message(self.platform_name, text, files or [])

    def on_callback(self, callback_id: str, msg_id: str, data: str):
        """Called when a button callback is received. Routes to hub."""
        if self.hub:
            self.hub.on_callback(self.platform_name, callback_id, msg_id, data)


@dataclass
class MessageContext:
    """Context passed to command handlers for platform-agnostic responses."""
    text: str
    platform: str
    messenger: MessengerBase
    hub: Any = None  # MessengerHub
    files: List[str] = field(default_factory=list)
    msg_id: Optional[str] = None
    callback_id: Optional[str] = None
    callback_data: Optional[str] = None

    def reply(self, text: str, **kwargs) -> Optional[str]:
        """Reply on the originating platform only."""
        return self.messenger.send_html(text, **kwargs)

    def reply_long(self, header: str, body_md: str, footer: str = None):
        """Reply with a long message on the originating platform."""
        self.messenger.send_long(header, body_md, footer=footer)

    def reply_keyboard(self, text: str, buttons: list, **kwargs) -> Optional[str]:
        """Reply with inline keyboard on the originating platform."""
        return self.messenger.send_keyboard(text, buttons, **kwargs)

    def edit(self, msg_id: str, text: str, **kwargs):
        """Edit a message on the originating platform."""
        self.messenger.edit_message(msg_id, text, **kwargs)

    def delete(self, msg_id: str):
        """Delete a message on the originating platform."""
        self.messenger.delete_message(msg_id)

    def broadcast(self, text: str):
        """Send to ALL connected messengers."""
        if self.hub:
            self.hub.broadcast(text)
        else:
            self.messenger.send_html(text)

    def answer(self, text: str = ""):
        """Answer a callback query."""
        if self.callback_id:
            self.messenger.answer_callback(self.callback_id, text)
