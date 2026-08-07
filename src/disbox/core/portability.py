"""Export a vault to a self-describing document, and rebuild one from it.

This is the escape hatch. A vault is a SQLite file with a schema only this
program understands, and the bytes on the backend are worthless without it. If
Disbox is abandoned, or a schema migration goes wrong, or the user simply wants
their metadata somewhere they can read, the export is what makes that possible:
plain indented JSON, every field named, recoverable with nothing but a text
editor and patience.

Encoding is chosen per field for legibility rather than uniformity, since being
readable is the entire point:

* Node and vault identifiers -- canonical UUID text.
* Hashes and Merkle roots -- hex, as hashes are conventionally written.
* Opaque binary such as wrapped keys -- base64, which has no readable form.

Journal history and in-flight upload sessions are deliberately excluded. An
export answers "what do I have and where is it stored", not "what happened to
it"; sessions are transient by definition and history can be large.
"""

import base64
import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any, Final

from disbox.core.vault import KeyMaterial, Vault
from disbox.errors import VaultError
from disbox.log import get_logger

__all__ = [
    "EXPORT_FORMAT_VERSION",
    "export_vault",
    "import_vault",
    "read_export",
    "write_export",
]

logger = get_logger(__name__)

EXPORT_FORMAT_VERSION: Final = 1

# Column name -> how its value is rendered in the export.
_UUID_COLUMNS: Final = frozenset({"id", "parent_id", "node_id", "vault_id"})
_HEX_COLUMNS: Final = frozenset({"hash", "chunk_hash", "merkle_root", "kdf_salt", "mk_check"})
_BASE64_COLUMNS: Final = frozenset({"config_enc", "wrapped_mk"})

_TABLES: Final = ("backends", "nodes", "revisions", "chunks", "revision_chunks")
_REQUIRED_SECTIONS: Final = ("vault", "key_material", *_TABLES)


def _encode(column: str, value: object) -> object:
    """Render one column value for the export document."""
    if value is None or not isinstance(value, bytes):
        return value
    if column in _UUID_COLUMNS:
        return str(uuid.UUID(bytes=value))
    if column in _HEX_COLUMNS:
        return value.hex()
    if column in _BASE64_COLUMNS:
        return base64.b64encode(value).decode("ascii")
    return base64.b64encode(value).decode("ascii")


def _decode(column: str, value: object) -> object:
    """Reverse `_encode` for one column value.

    Raises:
        VaultError: If the value is not in the form the column requires.
    """
    if value is None or not isinstance(value, str):
        return value
    try:
        if column in _UUID_COLUMNS:
            return uuid.UUID(value).bytes
        if column in _HEX_COLUMNS:
            return bytes.fromhex(value)
        if column in _BASE64_COLUMNS:
            return base64.b64decode(value, validate=True)
    except (ValueError, TypeError) as exc:
        msg = f"column {column!r} holds a malformed value in the export: {exc}"
        raise VaultError(msg) from exc
    return value


def _rows(conn: sqlite3.Connection, table: str) -> list[dict[str, object]]:
    """Read a whole table as a list of encoded, column-keyed dictionaries."""
    cursor = conn.execute(f"SELECT * FROM {table}")  # noqa: S608 - table names are module constants
    columns = [description[0] for description in cursor.description]
    return [
        {column: _encode(column, value) for column, value in zip(columns, row, strict=True)}
        for row in cursor.fetchall()
    ]


def export_vault(vault: Vault) -> dict[str, Any]:
    """Build an export document describing everything `vault` knows.

    Args:
        vault: An open vault.

    Returns:
        A JSON-serialisable document. The master key travels in its wrapped
        form, so an export is no more sensitive than the vault itself -- but no
        less, either.
    """
    conn = vault.connection
    meta = conn.execute(
        "SELECT vault_id, schema_version, created_at, kdf_salt, kdf_params, "
        "wrapped_mk, mk_check FROM meta"
    ).fetchone()

    document: dict[str, Any] = {
        "format_version": EXPORT_FORMAT_VERSION,
        "vault": {
            "vault_id": str(uuid.UUID(bytes=meta[0])),
            "schema_version": meta[1],
            "created_at": meta[2],
        },
        "key_material": {
            "kdf_salt": meta[3].hex(),
            "kdf_params": meta[4],
            "wrapped_mk": base64.b64encode(meta[5]).decode("ascii"),
            "mk_check": meta[6].hex(),
        },
    }
    for table in _TABLES:
        document[table] = _rows(conn, table)

    logger.info("vault exported", nodes=len(document["nodes"]), chunks=len(document["chunks"]))
    return document


def write_export(vault: Vault, path: Path) -> None:
    """Write an export of `vault` to `path` as indented UTF-8 JSON.

    Strict UTF-8 is safe here: SQLite already refuses to store a string that
    cannot be encoded, so a name the vault holds is always a name the export
    can write. See ``test_a_name_that_cannot_be_encoded_is_refused_by_storage``.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(export_vault(vault), indent=2, ensure_ascii=False, sort_keys=False),
        encoding="utf-8",
    )


def read_export(path: Path) -> dict[str, Any]:
    """Load an export document from `path`.

    Raises:
        VaultError: If the file is missing or is not valid JSON.
    """
    if not path.is_file():
        msg = f"no export at {path}"
        raise VaultError(msg)
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        msg = f"{path} is not a valid export document: {exc}"
        raise VaultError(msg) from exc
    if not isinstance(loaded, dict):
        msg = f"{path} does not contain an export object"
        raise VaultError(msg)
    return loaded


def import_vault(document: dict[str, Any], path: Path) -> Vault:
    """Rebuild a vault at `path` from an export document.

    Args:
        document: A document produced by `export_vault`.
        path: Where to create the vault. Must not already exist.

    Returns:
        The newly created, open vault.

    Raises:
        VaultError: If the document is malformed, was produced by a newer
            version of Disbox, or `path` is occupied.
    """
    version = document.get("format_version")
    if version != EXPORT_FORMAT_VERSION:
        if isinstance(version, int) and version > EXPORT_FORMAT_VERSION:
            msg = (
                f"export uses format version {version}, newer than this build "
                f"supports ({EXPORT_FORMAT_VERSION}); upgrade Disbox to import it"
            )
        else:
            msg = f"unrecognised export format version {version!r}"
        raise VaultError(msg)

    missing = [section for section in _REQUIRED_SECTIONS if section not in document]
    if missing:
        msg = f"export is missing required sections: {', '.join(missing)}"
        raise VaultError(msg)

    keys = document["key_material"]
    try:
        key_material = KeyMaterial(
            kdf_salt=bytes.fromhex(keys["kdf_salt"]),
            kdf_params=keys["kdf_params"],
            wrapped_mk=base64.b64decode(keys["wrapped_mk"], validate=True),
            mk_check=bytes.fromhex(keys["mk_check"]),
        )
    except (KeyError, ValueError, TypeError) as exc:
        msg = f"export contains malformed key material: {exc}"
        raise VaultError(msg) from exc

    vault = Vault.create(path, key_material)
    try:
        with vault.connection as conn:
            # Overwrite the identity Vault.create minted: a restored vault must
            # keep its original id, or it can no longer recognise the blobs it
            # already uploaded.
            conn.execute(
                "UPDATE meta SET vault_id = ?, created_at = ?",
                (uuid.UUID(document["vault"]["vault_id"]).bytes, document["vault"]["created_at"]),
            )
            for table in _TABLES:
                _restore_table(conn, table, document[table])
    except Exception:
        # Leave nothing half-written; the caller still has the export.
        vault.close()
        path.unlink(missing_ok=True)
        raise

    logger.info("vault imported", path=str(path))
    return vault


def _restore_table(conn: sqlite3.Connection, table: str, rows: list[dict[str, Any]]) -> None:
    """Insert exported rows back into `table`.

    Raises:
        VaultError: If a row does not match the table's shape.
    """
    if not rows:
        return
    columns = list(rows[0])
    placeholders = ", ".join("?" for _ in columns)
    statement = f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})"  # noqa: S608
    try:
        conn.executemany(
            statement,
            [tuple(_decode(column, row.get(column)) for column in columns) for row in rows],
        )
    except (sqlite3.Error, AttributeError) as exc:
        msg = f"could not restore table {table!r} from the export: {exc}"
        raise VaultError(msg) from exc
