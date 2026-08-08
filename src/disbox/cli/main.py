"""Disbox on the command line.

Commands are grouped by what the user is trying to do rather than by which
module implements them, and every one that mutates a vault says what it did.

Two conventions run throughout:

* **The passphrase is never a command-line argument.** Arguments land in shell
  history and in the process list, where any other user on the machine can read
  them. It is prompted for, or taken from ``DISBOX_PASSPHRASE`` for scripting.
* **Errors print a message, not a traceback.** Anything descending from
  ``DisboxError`` is an outcome the program understands and already has a
  sentence for; a stack trace would bury it.
"""

import asyncio
import os
import sys
import uuid
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from disbox.backends.base import StorageBackend
from disbox.backends.discord import DiscordBackend
from disbox.backends.local import LocalBackend
from disbox.config import load_settings
from disbox.core.crypto import calibrate_kdf
from disbox.core.engine import TransferEngine
from disbox.core.filesystem import FileSystem, NameCollision, Node
from disbox.core.maintenance import Maintenance
from disbox.core.portability import write_export
from disbox.core.search import search
from disbox.core.snapshots import SnapshotStore
from disbox.core.tree_transfer import TreeTransfer
from disbox.core.vault import Vault
from disbox.errors import DisboxError
from disbox.log import configure

app = typer.Typer(
    name="disbox",
    help="Encrypted file storage on Discord.",
    no_args_is_help=True,
    add_completion=False,
)
vault_app = typer.Typer(help="Create, inspect, and repair vaults.", no_args_is_help=True)
trash_app = typer.Typer(help="Review and recover deleted files.", no_args_is_help=True)
app.add_typer(vault_app, name="vault")
app.add_typer(trash_app, name="trash")

console = Console()
error_console = Console(stderr=True)

VaultPath = Annotated[Path, typer.Option("--vault", "-v", help="Vault file to operate on.")]
DEFAULT_VAULT = Path("disbox.dbx")

#: Binary, matching how file managers report sizes.
_BYTES_PER_UNIT = 1024


def _passphrase(*, confirm: bool = False) -> str:
    """Obtain the passphrase without ever putting it in argv.

    Command-line arguments are visible in shell history and to anyone who can
    list processes, so the passphrase is prompted for instead. The environment
    variable exists so scripts and CI have a route that is at least no worse.
    """
    if (from_env := os.environ.get("DISBOX_PASSPHRASE")) is not None:
        return from_env
    entered: str = typer.prompt("Passphrase", hide_input=True, confirmation_prompt=confirm)
    return entered


def _open(path: Path) -> Vault:
    """Open a vault, reporting a missing one as a message rather than a trace."""
    if not path.exists():
        error_console.print(
            f"[red]No vault at {path}.[/red] Create one with 'disbox vault create'."
        )
        raise typer.Exit(1)
    return Vault.open(path)


def _backend(vault: Vault) -> StorageBackend:
    """Build the configured backend, falling back to local storage.

    A local backend when Discord is unconfigured is deliberate: everything
    except the upload destination can then be exercised without credentials.
    """
    settings = load_settings()
    if settings.bot_token is not None and settings.channel_id is not None:
        return DiscordBackend(settings.bot_token.get_secret_value(), settings.channel_id)
    return LocalBackend(vault.path.parent / "blobs")


def _format_size(size: int) -> str:
    """Render a byte count for display."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < _BYTES_PER_UNIT or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= _BYTES_PER_UNIT
    return f"{value:.1f} TB"  # pragma: no cover - loop returns first


def _resolve_path(fs: FileSystem, path: str) -> uuid.UUID | None:
    """Turn a slash-separated path into a node id.

    Returns:
        The node's id, or None for the root.

    Raises:
        typer.Exit: If any component does not exist, naming the one that failed
            rather than reporting that "the path" was not found.
    """
    current: uuid.UUID | None = None
    for part in [p for p in path.strip("/").split("/") if p]:
        match = next((c for c in fs.children(current) if c.name == part), None)
        if match is None:
            error_console.print(f"[red]No such folder or file:[/red] {part!r} in {path!r}")
            raise typer.Exit(1)
        current = match.id
    return current


# ------------------------------------------------------------------- vault --


@vault_app.command("create")
def vault_create(
    path: Annotated[Path, typer.Argument(help="Where to create the vault.")] = DEFAULT_VAULT,
) -> None:
    """Create a new encrypted vault."""
    if path.exists():
        error_console.print(f"[red]{path} already exists.[/red]")
        raise typer.Exit(1)

    passphrase = _passphrase(confirm=True)
    console.print("Calibrating key derivation to this machine...")
    with Vault.create_encrypted(path, passphrase, calibrate_kdf()) as vault:
        console.print(f"[green]Created[/green] {path}  (id {vault.vault_id})")
    console.print(
        "[yellow]Keep this passphrase safe.[/yellow] "
        "It is the only thing that can decrypt your files, and it cannot be reset."
    )


@vault_app.command("info")
def vault_info(vault_path: VaultPath = DEFAULT_VAULT) -> None:
    """Show what a vault contains."""
    with _open(vault_path) as vault:
        row = vault.connection.execute(
            "SELECT (SELECT count(*) FROM nodes WHERE deleted_at IS NULL), "
            "(SELECT count(*) FROM nodes WHERE deleted_at IS NOT NULL), "
            "(SELECT count(*) FROM chunks), "
            "(SELECT coalesce(sum(size), 0) FROM nodes WHERE deleted_at IS NULL AND kind='file')"
        ).fetchone()
        table = Table(show_header=False, box=None)
        table.add_row("Vault", str(vault_path))
        table.add_row("Id", str(vault.vault_id))
        table.add_row("Schema", str(vault.schema_version))
        table.add_row("Files and folders", str(row[0]))
        table.add_row("In trash", str(row[1]))
        table.add_row("Chunks", str(row[2]))
        table.add_row("Stored", _format_size(row[3]))
        console.print(table)


@vault_app.command("export")
def vault_export(
    output: Annotated[Path, typer.Argument(help="Where to write the export.")],
    vault_path: VaultPath = DEFAULT_VAULT,
) -> None:
    """Write a readable JSON export of the vault's metadata."""
    with _open(vault_path) as vault:
        write_export(vault, output)
    console.print(f"[green]Exported[/green] to {output}")


@vault_app.command("snapshot")
def vault_snapshot(vault_path: VaultPath = DEFAULT_VAULT) -> None:
    """Take a local snapshot of the vault."""
    with _open(vault_path) as vault:
        store = SnapshotStore(vault_path.parent / "snapshots")
        snapshot = store.take(vault)
        store.prune()
    console.print(f"[green]Snapshot[/green] {snapshot.path.name}")


@vault_app.command("doctor")
def vault_doctor(vault_path: VaultPath = DEFAULT_VAULT) -> None:
    """Check a vault's health and report anything wrong."""

    async def run() -> None:
        with _open(vault_path) as vault:
            backend = _backend(vault)
            try:
                report = await Maintenance(vault, backend, vault.unlock(_passphrase())).doctor()
            finally:
                await backend.close()

        table = Table(show_header=False, box=None)
        for label, key in (
            ("Files and folders", "live_nodes"),
            ("In trash", "trashed_nodes"),
            ("Chunks", "chunks"),
            ("Unreferenced chunks", "unreferenced_chunks"),
            ("Remote backups", "remote_backups"),
        ):
            table.add_row(label, str(report[key]))
        table.add_row("Stored", _format_size(int(str(report["stored_bytes"]))))
        console.print(table)

        violations = report["invariant_violations"]
        missing = report["missing_chunks"]
        problems = [
            *(violations if isinstance(violations, list) else []),
            *(missing if isinstance(missing, list) else []),
        ]
        if problems:
            console.print(f"\n[red]{len(problems)} problem(s):[/red]")
            for problem in problems[:20]:
                console.print(f"  - {problem}")
        else:
            console.print("\n[green]Healthy.[/green]")

    asyncio.run(run())


@vault_app.command("backup")
def vault_backup(vault_path: VaultPath = DEFAULT_VAULT) -> None:
    """Store an encrypted copy of the vault on the backend."""

    async def run() -> None:
        with _open(vault_path) as vault:
            backend = _backend(vault)
            try:
                ref = await Maintenance(vault, backend, vault.unlock(_passphrase())).back_up_vault()
            finally:
                await backend.close()
        console.print(f"[green]Backed up[/green] as {ref.locator}")

    asyncio.run(run())


# ------------------------------------------------------------------ browse --


@app.command("ls")
def list_directory(
    path: Annotated[str, typer.Argument(help="Folder to list.")] = "/",
    vault_path: VaultPath = DEFAULT_VAULT,
) -> None:
    """List a folder's contents."""
    with _open(vault_path) as vault:
        fs = FileSystem(vault)
        children = fs.children(_resolve_path(fs, path))

        if not children:
            console.print("[dim]empty[/dim]")
            return
        table = Table(box=None, pad_edge=False)
        table.add_column("Name")
        table.add_column("Size", justify="right")
        table.add_column("Type")
        for child in children:
            table.add_row(
                child.name,
                "" if child.kind == "dir" else _format_size(child.size),
                "folder" if child.kind == "dir" else "file",
            )
        console.print(table)


@app.command("tree")
def show_tree(
    path: Annotated[str, typer.Argument(help="Folder to walk.")] = "/",
    vault_path: VaultPath = DEFAULT_VAULT,
) -> None:
    """Print a folder and everything beneath it."""
    with _open(vault_path) as vault:
        fs = FileSystem(vault)

        def walk(parent: uuid.UUID | None, depth: int) -> None:
            for child in fs.children(parent):
                marker = "/" if child.kind == "dir" else ""
                console.print("  " * depth + f"{child.name}{marker}")
                if child.kind == "dir":
                    walk(child.id, depth + 1)

        walk(_resolve_path(fs, path), 0)


@app.command("find")
def find_files(
    query: Annotated[str, typer.Argument(help="Text to search for.")],
    vault_path: VaultPath = DEFAULT_VAULT,
) -> None:
    """Search names anywhere in the vault."""
    with _open(vault_path) as vault:
        hits = search(vault.connection, query)
        if not hits:
            console.print("[dim]no matches[/dim]")
            return
        for hit in hits:
            console.print(f"{hit.name}  [dim]{hit.kind}[/dim]")


# -------------------------------------------------------------- transfers --


@app.command("put")
def upload(
    source: Annotated[Path, typer.Argument(help="Local file or folder to upload.")],
    destination: Annotated[str, typer.Argument(help="Vault folder to upload into.")] = "/",
    vault_path: VaultPath = DEFAULT_VAULT,
) -> None:
    """Upload a file or a whole folder."""

    async def run() -> None:
        if not source.exists():
            error_console.print(f"[red]No such file or folder:[/red] {source}")
            raise typer.Exit(1)

        with _open(vault_path) as vault:
            fs = FileSystem(vault)
            parent = _resolve_path(fs, destination)
            backend = _backend(vault)
            engine = TransferEngine(vault, backend, vault.unlock(_passphrase()))
            try:
                if source.is_dir():
                    result = await TreeTransfer(fs, engine).upload_folder(source, parent)
                    console.print(
                        f"[green]Uploaded[/green] {result.files} file(s) "
                        f"in {result.folders} folder(s), {_format_size(result.bytes_moved)}"
                    )
                    for failure in result.failures:
                        error_console.print(f"[yellow]skipped[/yellow] {failure}")
                    return

                name = fs.available_name(parent, source.name, NameCollision.KEEP_BOTH)
                node = fs.create_file(parent, name)
                with source.open("rb") as handle:
                    await engine.upload(node, handle)
                console.print(
                    f"[green]Uploaded[/green] {name}  ({_format_size(source.stat().st_size)})"
                )
            finally:
                await backend.close()

    asyncio.run(run())


@app.command("get")
def download(
    path: Annotated[str, typer.Argument(help="Vault file or folder to download.")],
    destination: Annotated[Path, typer.Argument(help="Where to write it.")] = Path(),
    vault_path: VaultPath = DEFAULT_VAULT,
) -> None:
    """Download a file or a whole folder."""

    async def run() -> None:
        with _open(vault_path) as vault:
            fs = FileSystem(vault)
            node_id = _resolve_path(fs, path)
            if node_id is None:
                error_console.print("[red]Specify a file or folder, not the root.[/red]")
                raise typer.Exit(1)

            node = fs.resolve(node_id)
            backend = _backend(vault)
            engine = TransferEngine(vault, backend, vault.unlock(_passphrase()))
            try:
                if node.kind == "dir":
                    result = await TreeTransfer(fs, engine).download_folder(node_id, destination)
                    console.print(
                        f"[green]Downloaded[/green] {result.files} file(s), "
                        f"{_format_size(result.bytes_moved)}"
                    )
                    for failure in result.failures:
                        error_console.print(f"[yellow]skipped[/yellow] {failure}")
                    return

                destination.mkdir(parents=True, exist_ok=True)
                target = destination / node.name
                with target.open("wb") as handle:
                    await engine.download(node_id, handle)
                console.print(f"[green]Downloaded[/green] {target}")
            finally:
                await backend.close()

    asyncio.run(run())


# ------------------------------------------------------------------ edits --


@app.command("mkdir")
def make_directory(
    path: Annotated[str, typer.Argument(help="Folder to create, as a path.")],
    vault_path: VaultPath = DEFAULT_VAULT,
) -> None:
    """Create a folder."""
    with _open(vault_path) as vault:
        fs = FileSystem(vault)
        parent_path, _, name = path.strip("/").rpartition("/")
        fs.create_directory(_resolve_path(fs, parent_path), name)
    console.print(f"[green]Created[/green] {path}")


@app.command("mv")
def rename_or_move(
    path: Annotated[str, typer.Argument(help="Item to move or rename.")],
    new_name: Annotated[str, typer.Argument(help="New name for it.")],
    vault_path: VaultPath = DEFAULT_VAULT,
) -> None:
    """Rename a file or folder."""
    with _open(vault_path) as vault:
        fs = FileSystem(vault)
        node_id = _resolve_path(fs, path)
        if node_id is None:
            error_console.print("[red]Cannot rename the root.[/red]")
            raise typer.Exit(1)
        fs.rename(node_id, new_name)
    console.print(f"[green]Renamed[/green] to {new_name}")


@app.command("rm")
def remove(
    path: Annotated[str, typer.Argument(help="Item to move to the trash.")],
    vault_path: VaultPath = DEFAULT_VAULT,
) -> None:
    """Move a file or folder to the trash. Nothing is erased."""
    with _open(vault_path) as vault:
        fs = FileSystem(vault)
        node_id = _resolve_path(fs, path)
        if node_id is None:
            error_console.print("[red]Cannot delete the root.[/red]")
            raise typer.Exit(1)
        moved = fs.delete(node_id)
    console.print(
        f"[green]Moved {moved} item(s) to the trash.[/green] Restore with 'disbox trash restore'."
    )


# ------------------------------------------------------------------ trash --


@trash_app.command("list")
def trash_list(vault_path: VaultPath = DEFAULT_VAULT) -> None:
    """Show what is in the trash."""
    with _open(vault_path) as vault:
        items: list[Node] = FileSystem(vault).trash()
        if not items:
            console.print("[dim]trash is empty[/dim]")
            return
        for item in items:
            console.print(f"{item.name}  [dim]{item.kind}[/dim]")


@trash_app.command("restore")
def trash_restore(
    name: Annotated[str, typer.Argument(help="Name of the item to restore.")],
    vault_path: VaultPath = DEFAULT_VAULT,
) -> None:
    """Restore something from the trash."""
    with _open(vault_path) as vault:
        fs = FileSystem(vault)
        match = next((item for item in fs.trash() if item.name == name), None)
        if match is None:
            error_console.print(f"[red]Nothing named {name!r} in the trash.[/red]")
            raise typer.Exit(1)
        restored = fs.restore(match.id)
    console.print(f"[green]Restored[/green] {restored} item(s)")


@trash_app.command("empty")
def trash_empty(
    vault_path: VaultPath = DEFAULT_VAULT,
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation.")] = False,
) -> None:
    """Permanently delete everything in the trash. This cannot be undone."""

    async def run() -> None:
        with _open(vault_path) as vault:
            fs = FileSystem(vault)
            items = fs.trash()
            if not items:
                console.print("[dim]trash is already empty[/dim]")
                return
            if not yes and not typer.confirm(f"Permanently delete {len(items)} item(s)?"):
                console.print("Cancelled.")
                return

            backend = _backend(vault)
            care = Maintenance(vault, backend, vault.unlock(_passphrase()))
            try:
                for item in items:
                    await care.purge(item.id)
                removed = await care.collect()
            finally:
                await backend.close()
        console.print(f"[green]Purged[/green] {len(items)} item(s); reclaimed {removed} blob(s)")

    asyncio.run(run())


def main() -> None:
    """Entry point that turns understood failures into messages."""
    configure(level=os.environ.get("DISBOX_LOG_LEVEL", "WARNING"))
    try:
        app()
    except DisboxError as exc:
        # An understood outcome already carries a sentence explaining itself; a
        # traceback would only bury it.
        error_console.print(f"[red]{exc}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
