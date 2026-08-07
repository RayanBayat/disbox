"""A vault must be exportable to a format that outlives this program."""

import json
import sqlite3
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any, Literal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from disbox.core.portability import (
    EXPORT_FORMAT_VERSION,
    export_vault,
    import_vault,
    read_export,
    write_export,
)
from disbox.core.vault import Vault
from disbox.errors import VaultError
from tests.unit.test_vault import KEYS

# Unicode "surrogate" category. Annotated because Hypothesis types this
# parameter as a collection of literals, which a bare tuple widens away.
SURROGATE_CATEGORY: tuple[Literal["Cs"]] = ("Cs",)

# Tables whose contents define the vault's logical identity. Journal history and
# in-flight upload sessions are deliberately excluded from an export.
LOGICAL_TABLES = ("meta", "backends", "nodes", "revisions", "chunks", "revision_chunks")


def logical_dump(vault: Vault) -> dict[str, list[tuple[Any, ...]]]:
    """Snapshot every row that an export is required to preserve."""
    return {
        table: sorted(vault.connection.execute(f"SELECT * FROM {table}").fetchall())  # noqa: S608
        for table in LOGICAL_TABLES
    }


def populate(vault: Vault) -> None:
    """Give the vault a backend, a small tree, a revision, and a chunk."""
    root = uuid.uuid7().bytes
    child = uuid.uuid7().bytes
    with vault.connection as conn:
        conn.execute(
            "INSERT INTO backends (id, kind, label, config_enc, max_blob, is_default) "
            "VALUES (1, 'discord', 'main', X'DEADBEEF', 10485760, 1)"
        )
        conn.execute(
            "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
            "VALUES (?, NULL, 'docs', 'dir', '2026-01-01', '2026-01-01')",
            (root,),
        )
        conn.execute(
            "INSERT INTO nodes (id, parent_id, name, kind, size, created_at, modified_at) "
            "VALUES (?, ?, 'report.pdf', 'file', 2048, '2026-01-02', '2026-01-03')",
            (child, root),
        )
        conn.execute(
            "INSERT INTO revisions (id, node_id, created_at, size, merkle_root, chunk_count) "
            "VALUES (1, ?, '2026-01-03', 2048, X'ABCD', 1)",
            (child,),
        )
        conn.execute(
            "INSERT INTO chunks (hash, size, stored_size, backend_id, message_id, attach_id, "
            "refcount) VALUES (X'0102', 2048, 2100, 1, '999', '888', 1)"
        )
        conn.execute(
            "INSERT INTO revision_chunks (revision_id, idx, chunk_hash) VALUES (1, 0, X'0102')"
        )


@pytest.fixture
def vault(tmp_path: Path) -> Iterator[Vault]:
    with Vault.create(tmp_path / "source.dbx", KEYS) as vault:
        populate(vault)
        yield vault


class TestExportShape:
    def test_manifest_declares_its_format_version(self, vault: Vault) -> None:
        assert export_vault(vault)["format_version"] == EXPORT_FORMAT_VERSION

    def test_manifest_is_json_serialisable(self, vault: Vault) -> None:
        json.dumps(export_vault(vault))  # must not raise

    def test_identifiers_are_written_as_readable_uuids(self, vault: Vault) -> None:
        node = export_vault(vault)["nodes"][0]
        uuid.UUID(node["id"])  # parses, so it is canonical text rather than raw bytes

    def test_hashes_are_written_as_hex(self, vault: Vault) -> None:
        chunk = export_vault(vault)["chunks"][0]
        assert bytes.fromhex(chunk["hash"]) == b"\x01\x02"

    def test_export_includes_what_is_needed_to_refetch_files(self, vault: Vault) -> None:
        """Without the backend references, the exported tree is unusable."""
        chunk = export_vault(vault)["chunks"][0]
        assert chunk["message_id"] == "999"
        assert chunk["attach_id"] == "888"

    def test_written_file_is_human_readable(self, vault: Vault, tmp_path: Path) -> None:
        target = tmp_path / "export.json"
        write_export(vault, target)
        text = target.read_text(encoding="utf-8")
        assert "\n" in text, "an export people are meant to read must not be one long line"
        assert "report.pdf" in text


class TestRoundTrip:
    def test_round_trip_preserves_the_logical_tree(self, vault: Vault, tmp_path: Path) -> None:
        before = logical_dump(vault)
        manifest = export_vault(vault)
        vault.close()

        with import_vault(manifest, tmp_path / "restored.dbx") as restored:
            assert logical_dump(restored) == before

    def test_round_trip_through_a_file(self, vault: Vault, tmp_path: Path) -> None:
        before = logical_dump(vault)
        export_path = tmp_path / "export.json"
        write_export(vault, export_path)
        vault.close()

        with import_vault(read_export(export_path), tmp_path / "restored.dbx") as restored:
            assert logical_dump(restored) == before

    def test_round_trip_preserves_the_vault_identity(self, vault: Vault, tmp_path: Path) -> None:
        """A restored vault must still recognise blobs it uploaded earlier."""
        original = vault.vault_id
        manifest = export_vault(vault)
        vault.close()

        with import_vault(manifest, tmp_path / "restored.dbx") as restored:
            assert restored.vault_id == original

    def test_round_trip_preserves_key_material(self, vault: Vault, tmp_path: Path) -> None:
        manifest = export_vault(vault)
        vault.close()

        with import_vault(manifest, tmp_path / "restored.dbx") as restored:
            assert restored.key_material == KEYS


class TestImportGuards:
    def test_import_refuses_a_newer_format(self, vault: Vault, tmp_path: Path) -> None:
        manifest = export_vault(vault)
        manifest["format_version"] = EXPORT_FORMAT_VERSION + 1
        vault.close()

        with pytest.raises(VaultError, match="newer"):
            import_vault(manifest, tmp_path / "restored.dbx")

    def test_import_refuses_to_overwrite_an_existing_vault(self, vault: Vault) -> None:
        manifest = export_vault(vault)
        occupied = vault.path
        vault.close()

        with pytest.raises(VaultError, match="already exists"):
            import_vault(manifest, occupied)

    def test_import_rejects_a_manifest_missing_required_sections(self, tmp_path: Path) -> None:
        with pytest.raises(VaultError):
            import_vault({"format_version": EXPORT_FORMAT_VERSION}, tmp_path / "restored.dbx")

    def test_failed_import_leaves_no_partial_vault(self, tmp_path: Path) -> None:
        target = tmp_path / "restored.dbx"
        with pytest.raises(VaultError):
            import_vault({"format_version": EXPORT_FORMAT_VERSION}, target)
        assert not target.exists()


class TestUndecodableNames:
    """Names that cannot be encoded as UTF-8 never enter the vault.

    Python turns undecodable bytes in a path into unpaired surrogates via
    surrogateescape, so such names are reachable in principle. The property
    test below generated one and exposed the question.

    SQLite settles it: the driver encodes string parameters as strict UTF-8 and
    refuses the insert. That is the outcome worth having, because it means the
    vault can never hold a name the export is unable to write -- which is what
    keeps the recovery path total.
    """

    def test_a_name_that_cannot_be_encoded_is_refused_by_storage(self, vault: Vault) -> None:
        with pytest.raises(UnicodeEncodeError), vault.connection as conn:
            conn.execute(
                "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
                "VALUES (?, NULL, ?, 'file', '2026-01-01', '2026-01-01')",
                (uuid.uuid7().bytes, "broken-\ud800-name.txt"),
            )

    def test_ordinary_non_ascii_names_round_trip(self, vault: Vault, tmp_path: Path) -> None:
        """Only unpaired surrogates are refused; real-world names must work."""
        for name in ("naive-café.txt", "日本語.pdf", "party-\U0001f389.zip"):
            with vault.connection as conn:
                conn.execute(
                    "INSERT INTO nodes (id, parent_id, name, kind, created_at, modified_at) "
                    "VALUES (?, NULL, ?, 'file', '2026-01-01', '2026-01-01')",
                    (uuid.uuid7().bytes, name),
                )
        before = logical_dump(vault)
        export_path = tmp_path / "export.json"
        write_export(vault, export_path)
        vault.close()

        with import_vault(read_export(export_path), tmp_path / "restored.dbx") as restored:
            assert logical_dump(restored) == before


class TestRoundTripProperty:
    @settings(max_examples=25, deadline=None)
    @given(
        names=st.lists(
            st.text(
                # Surrogates are excluded because storage rejects them outright.
                # TestUndecodableNames covers that behaviour directly.
                alphabet=st.characters(
                    min_codepoint=32,
                    blacklist_characters="/\x7f",
                    blacklist_categories=SURROGATE_CATEGORY,
                ),
                min_size=1,
                max_size=40,
            ).filter(lambda s: s.strip() == s and s != ""),
            min_size=1,
            max_size=12,
            unique=True,
        ),
        sizes=st.lists(st.integers(min_value=0, max_value=2**40), min_size=1, max_size=12),
    )
    def test_any_tree_survives_a_round_trip(
        self, tmp_path_factory: pytest.TempPathFactory, names: list[str], sizes: list[int]
    ) -> None:
        """Whatever the tree contains, exporting and importing must reproduce it."""
        base = tmp_path_factory.mktemp("prop")
        with Vault.create(base / "source.dbx", KEYS) as source:
            parents: list[bytes | None] = [None]
            with source.connection as conn:
                for index, name in enumerate(names):
                    node_id = uuid.uuid7().bytes
                    parent = parents[index % len(parents)]
                    try:
                        conn.execute(
                            "INSERT INTO nodes (id, parent_id, name, kind, size, created_at, "
                            "modified_at) VALUES (?, ?, ?, 'dir', ?, '2026-01-01', '2026-01-01')",
                            (node_id, parent, name, sizes[index % len(sizes)]),
                        )
                    except sqlite3.IntegrityError:
                        continue  # duplicate sibling name; the schema is right to refuse
                    parents.append(node_id)

            before = logical_dump(source)
            manifest = export_vault(source)

        # Without this the test would still pass if every insert had been
        # skipped, silently proving nothing.
        assert before["nodes"], "the generated tree must contain at least one node"

        with import_vault(manifest, base / "restored.dbx") as restored:
            assert logical_dump(restored) == before
