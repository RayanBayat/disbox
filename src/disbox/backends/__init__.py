"""Pluggable blob stores. Discord is one implementation, never the only one."""

from disbox.backends.base import BlobRef, StorageBackend
from disbox.backends.local import LocalBackend

__all__ = ["BlobRef", "LocalBackend", "StorageBackend"]
