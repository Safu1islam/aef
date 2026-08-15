"""Operation modules.

Importing this package registers every capability. Both surfaces call
``registry.load_operations()``, which imports this, so neither can see a
different set of operations than the other (F-1, S4).
"""

from . import (  # noqa: F401
    accounts,
    acquire,
    analyse,
    assets,
    backup,
    brandkits,
    posts,
    projects,
    providers,
    provenance,
    rights,
    schedule,
    storage,
    system,
)

__all__ = [
    "accounts",
    "acquire",
    "analyse",
    "assets",
    "backup",
    "brandkits",
    "posts",
    "projects",
    "providers",
    "provenance",
    "rights",
    "schedule",
    "storage",
    "system",
]
