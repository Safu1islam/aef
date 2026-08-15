"""X (Twitter) live publisher adapter.

T-019, replacing the X half of registered fabrication F-001. DR-010 froze the
``Publisher`` protocol this implements; nothing here changes it.

Every number below is cited against LIVE documentation fetched 2026-08-13, per
the operator instruction recorded in project.md O-3 ("rate limits and API
pricing must NOT be written from model memory"). Where a figure was not found
in a primary source it is left ``UNKNOWN`` rather than guessed — the same
discipline T-012 AC-3 established for the placeholder this replaces.

  * Pricing: pay-per-use has been the default for every new developer since
    2026-02-06. No free tier, no subscription, no minimum spend.
    $0.015 per post created; $0.20 per post if it contains a URL.
    https://docs.x.com/x-api/getting-started/pricing
  * Endpoint: ``POST /2/tweets``, OAuth 2.0 **user context** (app-only auth
    cannot post on a user's behalf). Required scope: ``tweet.write``.
    https://docs.x.com/x-api/posts/creation-of-a-post
  * Character limit: 280, WEIGHTED — most characters count as 1, every emoji
    counts as 2, and any URL counts as a flat 23 regardless of its real
    length. https://docs.x.com/fundamentals/counting-characters
  * Access tokens expire after 2 hours; the refresh_token grant is standard
    OAuth 2.0 with no separate approval (unlike LinkedIn's gated
    programmatic-refresh-token product — see linkedin.py).

Deliberately NOT reimplemented here: X's exact weighted character-counting
algorithm. A partial reimplementation from a documentation summary risks being
subtly wrong in either direction (wrongly blocking a valid post, or wrongly
passing an invalid one) — a mistake in an auto-publishing system, not a
convenience. The real endpoint is authoritative for length and its 400 is
surfaced structurally as ``PlatformError`` rather than approximated client
side.

An operator must create the X Developer app, accept its Developer Agreement,
and complete the OAuth 2.0 authorization themselves — creating accounts and
accepting platform terms are not agent actions (NON-NEGOTIABLES). See
docs/HUMAN-ACTIONS.md section 2.
"""

from __future__ import annotations

import base64
import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ...errors import ConfigurationError, PlatformError
from ..credentials import CredentialStore
from ..db import iso
from .base import Capabilities, PublishResult

API_BASE = "https://api.x.com"
TOKEN_URL = f"{API_BASE}/2/oauth2/token"
POST_URL = f"{API_BASE}/2/tweets"
GET_POST_URL = f"{API_BASE}/2/tweets/{{id}}"

REQUIRED_CREDENTIAL_FIELDS = ("access_token",)

CAPABILITIES = Capabilities(
    platform="x",
    max_body_chars=280,  # weighted, not a raw character count — see module docstring
    max_media_bytes=(
        "UNKNOWN — media upload limits were not verified; this adapter posts text only"
    ),
    posts_per_day=(
        "UNKNOWN — no fixed per-endpoint write-rate limit is published for pay-per-use;"
        " billing is the effective constraint (see rate_limit_note)"
    ),
    rate_limit_note=(
        "Pay-per-use since 2026-02-06 (no free tier, no subscription, no minimum spend):"
        " $0.015 per post created, $0.20 per post if it contains a URL. Verified against"
        " https://docs.x.com/x-api/getting-started/pricing, fetched 2026-08-13."
    ),
    verified_against_documentation=True,
)


def capabilities() -> Capabilities:
    return CAPABILITIES


def _post_json(url: str, *, body: dict[str, Any] | None, headers: dict[str, str], timeout: int) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed https host, not user input
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        raise PlatformError(
            "X API request failed",
            platform="x",
            http_status=exc.code,
            body=exc.read().decode("utf-8", "replace")[:2000],
        ) from exc
    except urllib.error.URLError as exc:
        raise PlatformError("X API request failed: network error", platform="x", reason=str(exc.reason)) from exc


class XPublisher:
    """Publishes to X via API v2. See module docstring for verified limits."""

    platform = "x"
    simulated = False

    def __init__(self, *, request_timeout_seconds: int) -> None:
        self._timeout = request_timeout_seconds
        self._store = CredentialStore()

    def capabilities(self) -> Capabilities:
        return CAPABILITIES

    # --- credential handling ---
    def _load_credential(self, credential_ref: str) -> dict[str, Any]:
        raw = self._store.get(credential_ref)
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ConfigurationError(
                "X credential is not valid JSON. Expected an object with at least"
                " 'access_token', and optionally 'refresh_token'/'client_id'/'client_secret'"
                " for automatic renewal — see docs/HUMAN-ACTIONS.md.",
                credential_ref=credential_ref,
            ) from exc
        missing = [f for f in REQUIRED_CREDENTIAL_FIELDS if not data.get(f)]
        if missing:
            raise ConfigurationError(
                f"X credential is missing required field(s): {', '.join(missing)}",
                credential_ref=credential_ref,
            )
        return data

    def _refresh(self, credential_ref: str, data: dict[str, Any]) -> dict[str, Any]:
        if not data.get("refresh_token") or not data.get("client_id"):
            raise ConfigurationError(
                "X access token was rejected and no refresh_token/client_id is stored to"
                " renew it automatically; the operator must reconnect the account with a"
                " fresh authorization (connect-account).",
                credential_ref=credential_ref,
            )
        form = {
            "grant_type": "refresh_token",
            "refresh_token": data["refresh_token"],
            "client_id": data["client_id"],
        }
        payload = urllib.parse.urlencode(form).encode("ascii")
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if data.get("client_secret"):
            basic = base64.b64encode(f"{data['client_id']}:{data['client_secret']}".encode()).decode("ascii")
            headers["Authorization"] = f"Basic {basic}"
        req = urllib.request.Request(TOKEN_URL, data=payload, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                refreshed = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise PlatformError(
                "X token refresh failed",
                platform="x",
                http_status=exc.code,
                body=exc.read().decode("utf-8", "replace")[:2000],
            ) from exc
        except urllib.error.URLError as exc:
            raise PlatformError("X token refresh failed: network error", platform="x", reason=str(exc.reason)) from exc
        merged = {**data, **refreshed}
        # X does not always return a fresh refresh_token; keep the working one.
        merged.setdefault("refresh_token", data["refresh_token"])
        self._store.put(credential_ref, json.dumps(merged))
        return merged

    # --- Publisher protocol ---
    def publish(self, *, body: str, content_hash: str, credential_ref: str) -> PublishResult:
        data = self._load_credential(credential_ref)
        can_refresh = bool(data.get("refresh_token") and data.get("client_id"))
        headers = {"Content-Type": "application/json", "Authorization": f"Bearer {data['access_token']}"}
        try:
            parsed = _post_json(POST_URL, body={"text": body}, headers=headers, timeout=self._timeout)
        except PlatformError as exc:
            if exc.detail.get("http_status") != 401:
                raise
            if not can_refresh:
                raise ConfigurationError(
                    "X rejected the access token (401) and this credential has no"
                    " refresh_token/client_id to renew it automatically. The operator"
                    " must reconnect the account with a fresh authorization.",
                    credential_ref=credential_ref,
                ) from exc
            data = self._refresh(credential_ref, data)
            headers["Authorization"] = f"Bearer {data['access_token']}"
            parsed = _post_json(POST_URL, body={"text": body}, headers=headers, timeout=self._timeout)

        post_id = (parsed or {}).get("data", {}).get("id")
        if not post_id:
            raise PlatformError("X publish returned no post id", platform="x", response=parsed)
        return PublishResult(
            platform_post_id=post_id,
            permalink=f"https://x.com/i/web/status/{post_id}",
            published_at=iso(),
            simulated=False,
            detail={"content_hash": content_hash},
        )

    def verify_published(self, platform_post_id: str, *, credential_ref: str) -> bool:
        # A GET on a public post needs no special scope beyond a valid bearer
        # token; the operator's own stored access_token is reused rather than
        # minting a second app-only credential the store has no slot for.
        data = self._load_credential(credential_ref)
        url = GET_POST_URL.format(id=urllib.parse.quote(platform_post_id, safe=""))
        req = urllib.request.Request(
            url, method="GET", headers={"Authorization": f"Bearer {data['access_token']}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                json.loads(resp.read().decode("utf-8"))
                return True
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise PlatformError(
                "X verify_published failed",
                platform="x",
                http_status=exc.code,
                body=exc.read().decode("utf-8", "replace")[:2000],
            ) from exc
        except urllib.error.URLError as exc:
            raise PlatformError(
                "X verify_published failed: network error", platform="x", reason=str(exc.reason)
            ) from exc
