"""The contract every blob store satisfies.

This abstraction is the project's insurance policy. Discord forbids using it as
a file host, can change its limits without notice, and can terminate an account;
a design welded to it has no answer to any of that. Behind this protocol,
migrating a vault elsewhere is a background job rather than a rewrite.

It is deliberately small. A blob store needs to put bytes somewhere, get them
back, say whether they are still there, and remove them -- everything else
(chunking, encryption, manifests, retries) belongs above this line so it is
written once instead of once per backend.

`max_blob_size` is a property rather than a constant because providers change
their limits. Discovering it at runtime turns a provider changing the rules
into a configuration event instead of a broken build.
"""

from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

__all__ = ["BlobRef", "StorageBackend"]


@dataclass(frozen=True, slots=True)
class BlobRef:
    """A durable pointer to stored bytes.

    Deliberately not a URL. Discord's CDN links are signed and expire within a
    day, so persisting one produces a reference that silently stops working --
    exactly the defect that broke the previous generation of this project.
    Backends store whatever identifiers they can resolve to a fresh URL on
    demand.

    Attributes:
        locator: Backend-specific primary identifier.
        secondary: Optional second identifier, where one is needed to resolve.
        size: Stored size in bytes.
    """

    locator: str
    secondary: str = ""
    size: int = 0


@runtime_checkable
class StorageBackend(Protocol):
    """Somewhere opaque bytes can be put and later retrieved."""

    @property
    def name(self) -> str:
        """Short identifier for logs and the vault's backend row."""
        ...

    @property
    def max_blob_size(self) -> int:
        """Largest blob this backend currently accepts, discovered at runtime."""
        ...

    async def put(self, data: bytes, *, idempotency_key: str) -> BlobRef:
        """Store `data` and return a durable reference.

        Implementations must be idempotent with respect to `idempotency_key`:
        a retry after an ambiguous failure has to return the original reference
        rather than storing a second copy, or a network blip leaks storage
        that nothing will ever reclaim.
        """
        ...

    async def get(self, ref: BlobRef, *, byte_range: tuple[int, int] | None = None) -> bytes:
        """Retrieve stored bytes, optionally just a range of them."""
        ...

    def stream(self, ref: BlobRef) -> AsyncIterator[bytes]:
        """Yield stored bytes progressively, so large blobs never sit in memory."""
        ...

    async def delete(self, ref: BlobRef) -> None:
        """Remove a blob. Deleting something already gone is not an error."""
        ...

    async def exists(self, ref: BlobRef) -> bool:
        """Report whether a blob is still retrievable."""
        ...

    def iter_all(self) -> AsyncIterator[BlobRef]:
        """Enumerate every blob this backend holds.

        This is what makes a vault reconstructible after losing the local file:
        a rescan reads each blob's header and rebuilds the tree from scratch.
        """
        ...

    async def probe(self) -> int:
        """Measure and cache the current maximum blob size."""
        ...

    async def close(self) -> None:
        """Release any held connections."""
        ...


def bulk_delete_supported(backend: object) -> bool:
    """Whether `backend` offers a batched delete."""
    return callable(getattr(backend, "bulk_delete", None))


async def delete_many(backend: StorageBackend, refs: Iterable[BlobRef]) -> None:
    """Delete several blobs, using a batched path when the backend has one."""
    batch = getattr(backend, "bulk_delete", None)
    if callable(batch):
        await batch(list(refs))
        return
    for ref in refs:
        await backend.delete(ref)
