"""The project's exception hierarchy.

Every error Disbox raises deliberately descends from `DisboxError`, so callers
can distinguish "this operation failed for a reason we understand" from a bug,
and the GUI can present the former without a stack trace.
"""


class DisboxError(Exception):
    """Base class for every error Disbox raises deliberately."""


class VaultError(DisboxError):
    """A vault could not be opened, migrated, or maintained."""


class VaultLockedError(VaultError):
    """Another process already holds the vault open for writing."""


class MigrationError(VaultError):
    """The migration set is malformed, or a migration failed to apply."""


class CryptoError(DisboxError):
    """A key could not be derived, or data failed to decrypt or authenticate."""


class TransferError(DisboxError):
    """A file could not be uploaded or reassembled."""


class IntegrityError(VaultError):
    """The vault failed an internal consistency check."""
