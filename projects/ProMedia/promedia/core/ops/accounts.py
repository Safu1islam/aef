"""Account connection (T-006).

Connecting an account is operator authority: it establishes the capability to
publish and, once real adapters exist, to spend. An agent may list accounts but
may not create one.

No operation here ever returns a credential value — only a reference (DR-008).
"""

from __future__ import annotations

from typing import Any

from ...errors import ValidationError
from .. import publishers
from ..credentials import REDACTED, CredentialStore
from ..db import iso, new_id
from ..registry import Context, Param, register


@register(
    "connect-account",
    "Connect a platform account and store its credential outside the repository.",
    params=(
        Param("platform", "str", help="Platform key: x or linkedin."),
        Param("handle", "str", help="Account handle as it appears on the platform."),
        Param(
            "secret",
            "str",
            required=False,
            sensitive=True,
            help=(
                "Credential value. Supply via --secret-stdin or --secret-file;"
                " it is never accepted as an inline flag or a query parameter."
            ),
        ),
    ),
    authority="operator",
    mutates=True,
    entity="account",
    danger="Establishes publishing capability for this account.",
    # T-033. Since T-023 this operation is create-OR-UPDATE: a reconnect
    # preserves the account id and rotates the credential, so it writes a row
    # that already exists — but it takes no account_id, so lock_target()'s id
    # rule saw a pure creation and let it run unlocked. platform+handle is the
    # identity it actually has (UNIQUE(platform, handle) in the schema), and
    # locking on it must use the SAME normalisation the handler does below.
    lock_by=("platform", "handle"),
)
def connect_account(ctx: Context, platform: str, handle: str, secret: str | None = None) -> dict[str, Any]:
    key = platform.strip().lower()
    # N13: platform was normalised but handle was not, so "x/Case" and "x/case"
    # became two accounts with two credential refs. Platforms treat handles
    # case-insensitively; the lookup key must too.
    handle = handle.strip().lower()
    if key not in publishers.SUPPORTED_PLATFORMS:
        raise ValidationError(
            f"unsupported platform '{platform}'",
            parameter="platform",
            supported=list(publishers.SUPPORTED_PLATFORMS),
        )
    credential_ref = f"{key}:{handle}"
    status = "connected" if secret else "error"

    # T-023: reconnecting must PRESERVE the account id. INSERT OR REPLACE against
    # UNIQUE(platform, handle) minted a new id and deleted the old row, so
    # anything holding the previous id dangled — and once posts referenced the
    # account, ON DELETE RESTRICT turned it into a raw crash instead. Either way
    # credential rotation, the normal reason to reconnect, had no working path.
    existing = ctx.conn.execute(
        "SELECT id, status FROM accounts WHERE platform = ? AND handle = ?", (key, handle)
    ).fetchone()

    if secret:
        CredentialStore().put(credential_ref, secret)

    if existing is not None:
        account_id = existing["id"]
        if secret:
            ctx.conn.execute(
                "UPDATE accounts SET credential_ref = ?, status = ?, connected_at = ? WHERE id = ?",
                (credential_ref, status, iso(), account_id),
            )
        else:
            # N10: a bare reconnect must not downgrade a working account to
            # 'error'. Under the old INSERT OR REPLACE this expression minted a
            # fresh row and so never corrupted anything; preserving the id
            # (T-023) made the same line destructive. Omitting the secret is a
            # plausible slip, and its cost was a live account marked broken.
            ctx.conn.execute(
                "UPDATE accounts SET credential_ref = ?, connected_at = ? WHERE id = ?",
                (credential_ref, iso(), account_id),
            )
            status = existing["status"]
        reconnected = True
    else:
        account_id = new_id("acct")
        ctx.conn.execute(
            "INSERT INTO accounts (id, platform, handle, credential_ref, status, connected_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (account_id, key, handle, credential_ref, status, iso()),
        )
        reconnected = False

    return {
        "ok": True,
        "account_id": account_id,
        "platform": key,
        "handle": handle,
        "credential_ref": credential_ref,
        "credential_value": REDACTED,
        "status": status,
        "reconnected": reconnected,
        "note": (
            None if secret
            else "no credential supplied; account recorded but cannot publish (T-019)"
        ),
    }


@register("list-accounts", "List connected accounts. Never returns credential values.")
def list_accounts(ctx: Context) -> dict[str, Any]:
    rows = ctx.conn.execute("SELECT * FROM accounts ORDER BY connected_at DESC").fetchall()
    return {
        "ok": True,
        "count": len(rows),
        "accounts": [
            {
                "id": r["id"],
                "platform": r["platform"],
                "handle": r["handle"],
                "credential_ref": r["credential_ref"],
                "credential_value": REDACTED,
                "status": r["status"],
                "connected_at": r["connected_at"],
            }
            for r in rows
        ],
    }


@register(
    "platform-capabilities",
    "Report a platform's limits. Unverified limits read as UNKNOWN, never as a guess.",
    params=(Param("platform", "str"),),
)
def platform_capabilities(ctx: Context, platform: str) -> dict[str, Any]:
    from ..publishers.stub import capabilities_for_real_platform

    return {
        "ok": True,
        **capabilities_for_real_platform(platform.strip().lower()),
        "note": "Rate limits and pricing must be verified against live documentation (O-3).",
    }
