"""WeChat version adapter interface - abstract base for version-specific implementations."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional


class WeChatAdapter(ABC):
    """Abstract interface for WeChat version adapters.

    Each supported WeChat version implements this interface to handle
    version-specific UI element locations and interaction patterns.
    """

    @property
    @abstractmethod
    def version(self) -> str:
        """Return the WeChat version this adapter supports."""

    @abstractmethod
    def find_chat_list(self) -> bool:
        """Locate the chat list panel. Return True if found."""

    @abstractmethod
    def search_contact(self, name: str) -> bool:
        """Search and select a contact/group by name. Return True if found."""

    @abstractmethod
    def get_input_box_position(self) -> Optional[tuple[int, int]]:
        """Return the (x, y) center of the message input box."""

    @abstractmethod
    def get_send_button_position(self) -> Optional[tuple[int, int]]:
        """Return the (x, y) center of the send button."""

    @abstractmethod
    def get_chat_list_items(self) -> list[dict[str, str]]:
        """Return list of visible chat items with name and last message."""

    @abstractmethod
    def is_in_chat_view(self) -> bool:
        """Check if currently viewing a chat (input box visible)."""

    @abstractmethod
    def send_text(self, text: str, target: Optional[str] = None) -> bool:
        """Send a text message in the current chat. Return True on success.

        `target` is the intended recipient name. When provided (or when
        ``search_contact()`` was called first), the adapter re-verifies the
        active chat right before pressing Enter and **refuses to send** if it
        does not match. Passing ``None`` with no prior ``search_contact()``
        disables the guard (logged as a warning).
        """

    @abstractmethod
    def send_image_from_file(self, file_path: str, target: Optional[str] = None) -> bool:
        """Send an image from file path. Return True on success."""

    @abstractmethod
    def send_image_from_bytes(self, data: bytes, filename: str = "image.png", target: Optional[str] = None) -> bool:
        """Send an image from raw bytes. Return True on success."""

    @abstractmethod
    def get_current_chat_name(self) -> Optional[str]:
        """Return the name of the currently open chat, or None."""
