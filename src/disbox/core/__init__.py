"""Backend-agnostic core: the vault, crypto, chunking, and the transfer engine.

Nothing in this package may import from ``disbox.gui`` or ``disbox.backends``;
dependencies point inward only, so the core stays testable without a GUI or a
network.
"""
