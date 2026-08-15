"""ProMedia — single-operator social media production and publishing.

Layering (DR-002). Business logic lives in ``promedia.core`` and is reached
only through the operation registry. ``promedia.cli`` and ``promedia.web`` are
thin adapters over that registry and contain no business logic, so a capability
cannot exist on one surface and not the other (F-1, S4).

Nothing here imports the web framework at module scope: the CLI cold-start
budget is 1s (C-4) and eager imports are what would break it.
"""

__version__ = "0.1.0"
