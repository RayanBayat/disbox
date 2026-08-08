"""Command line interface.

Everything the desktop client can do is reachable from here, because a storage
tool people cannot script is a storage tool they cannot automate backups with.
The CLI drives exactly the same core as the GUI -- there is no separate
codepath, so a behaviour verified in one is verified in both.
"""

from disbox.cli.main import app

__all__ = ["app"]
