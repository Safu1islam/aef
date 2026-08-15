"""LinkedIn live publisher adapter.

T-019, replacing the LinkedIn half of registered fabrication F-001. DR-010
froze the ``Publisher`` protocol this implements (T-019 corrected one gap in
it — see base.py and the note on ``verify_published`` below).

Every number and process step below is cited against LIVE documentation
fetched 2026-08-13, per project.md O-3 ("rate limits and API pricing must NOT
be written from model memory"). Where a figure was not stated in a primary
LinkedIn/Microsoft-Learn source it is left ``UNKNOWN`` rather than guessed,
even where third-party summaries offered a plausible-looking number — that is
exactly the "trusted because it looks plausible" failure mode DR-010 exists to
prevent.

  * Personal posting uses the ``w_member_social`` scope, granted by adding the
    "Share on LinkedIn" product in the Developer Portal — **self-serve,
    instant, no review**. Posting as an organization (``w_organization_social``)
    is a different, approval-gated product this project does not use (project
    scope is one personal account).
    https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin
  * Endpoint: ``POST https://api.linkedin.com/rest/posts`` (the Posts API,
    which replaces the older ``/v2/ugcPosts`` the self-serve guide still shows
    — the migration note on the Posts API page is explicit that Posts
    supersedes it). Requires headers ``X-Restli-Protocol-Version: 2.0.0`` and
    ``Linkedin-Version: <YYYYMM>`` (see ``promedia.toml``'s
    ``publishing.linkedin_api_version``).
    https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api
  * Rate limit (from the Share-on-LinkedIn page, whose own revision date is
    older than the Posts API page — re-verify before relying on this for
    volume planning): Member 150 requests/day, Application 100,000
    requests/day, both reset at UTC midnight.
  * No pricing is published for personal posting; "Share on LinkedIn" is a
    free self-serve product. This resolves the cost side of O-3's LinkedIn
    risk favourably.
  * Access tokens last 60 days. **Programmatic refresh tokens require approved
    Marketing Developer Platform (MDP) partner status** — this is a
    review-gated product, NOT self-serve, unlike X's refresh grant. A
    self-serve app therefore has NO automatic renewal path: the operator must
    manually re-run the OAuth authorization in a browser before day 60, or
    publishing fails closed rather than silently. This adapter never invents a
    refresh call that self-serve credentials cannot use.
    https://learn.microsoft.com/en-us/linkedin/shared/authentication/programmatic-refresh-tokens
  * The ``author`` field is the member's own Person URN
    (``urn:li:person:{id}``), obtained once via Sign In with LinkedIn /
    ``GET /v2/userinfo`` and stored alongside the token — this adapter does
    not re-derive it on every publish, which would need an extra scope and an
    extra call for a value that does not change.

An operator must create the LinkedIn Developer app, accept its API Terms of
Use, add the "Share on LinkedIn" product, and complete OAuth authorization
themselves — creating accounts and accepting platform terms are not agent
actions (NON-NEGOTIABLES). See docs/HUMAN-ACTIONS.md section 2.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from ...errors import ConfigurationError, PlatformError
from ..credentials import CredentialStore
from ..db import iso
from .base import Capabilities, PublishResult

API_BASE = "https://api.linkedin.com"
POSTS_URL = f"{API_BASE}/rest/posts"
GET_POST_URL = f"{API_BASE}/rest/posts/{{urn}}"
TOKEN_URL = "https://www.linkedin.com/oauth/v2/accessToken"

REQUIRED_CREDENTIAL_FIELDS = ("access_token", "person_urn")

CAPABILITIES = Capabilities(
    platform="linkedin",
    max_body_chars=(
        "UNKNOWN — not stated in LinkedIn's own Posts API reference. Third-party sources"
        " commonly cite 3000 for the LinkedIn UI but this was not found in a primary"
        " LinkedIn/Microsoft-Learn document, so it is not recorded as verified (O-3)."
    ),
    max_media_bytes="UNKNOWN — media upload limits were not verified; this adapter posts text only",
    posts_per_day=150,  # member-level daily throttle (UTC) — see module docstring
    rate_limit_note=(
        "Member: 150 requests/day (UTC). Application: 100,000 requests/day (UTC)."
        " 'Share on LinkedIn' (w_member_social) is free, self-serve, no review. Verified"
        " https://learn.microsoft.com/en-us/linkedin/consumer/integrations/self-serve/share-on-linkedin"
        ", fetched 2026-08-13 — that page's own revision date is older than the Posts API"
        " page, so re-verify before relying on the exact numbers for capacity planning."
    ),
    verified_against_documentation=True,
)


def capabilities() -> Capabilities:
    return CAPABILITIES


class LinkedInPublisher:
    """Publishes to LinkedIn via the Posts API. See module docstring for verified limits."""

    platform = "linkedin"
    simulated = False

    def __init__(self, *, request_timeout_seconds: int, api_version: str) -> None:
        self._timeout = request_timeout_seconds
        self._api_version = api_version
        self._store = CredentialStore()

    def capabilities(self) -> Capabilities:
        return CAPABILITIES

    def _headers(self, access_token: str, *, content_type: str | None = "application/json") -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {access_token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Linkedin-Version": self._api_version,
        }
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    # --- credential handling ---
    def _load_credential(self, credential_ref: str) -> dict[str, Any]:
        raw = self._store.get(credential_ref)
        try:
            data = json.loads(raw)
        except ValueError as exc:
            raise ConfigurationError(
                "LinkedIn credential is not valid JSON. Expected an object with"
                " 'access_token' and 'person_urn' (urn:li:person:{id}), and optionally"
                " 'refresh_token'/'client_id'/'client_secret' if the app has approved MDP"
                " partner status — see docs/HUMAN-ACTIONS.md.",
                credential_ref=credential_ref,
            ) from exc
        missing = [f for f in REQUIRED_CREDENTIAL_FIELDS if not data.get(f)]
        if missing:
            raise ConfigurationError(
                f"LinkedIn credential is missing required field(s): {', '.join(missing)}",
                credential_ref=credential_ref,
            )
        return data

    def _refresh(self, credential_ref: str, data: dict[str, Any]) -> dict[str, Any]:
        if not data.get("refresh_token") or not data.get("client_id") or not data.get("client_secret"):
            raise ConfigurationError(
                "LinkedIn access token was rejected and this credential has no"
                " refresh_token/client_id/client_secret to renew it. Self-serve LinkedIn"
                " apps do not receive refresh tokens (programmatic refresh requires"
                " approved Marketing Developer Platform partner status) — the operator"
                " must manually re-run the OAuth authorization and reconnect the account"
                " before the 60-day access token expires. See docs/HUMAN-ACTIONS.md.",
                credential_ref=credential_ref,
            )
        form = {
            "grant_type": "refresh_token",
            "refresh_token": data["refresh_token"],
            "client_id": data["client_id"],
            "client_secret": data["client_secret"],
        }
        payload = urllib.parse.urlencode(form).encode("ascii")
        req = urllib.request.Request(
            TOKEN_URL,
            data=payload,
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                refreshed = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise PlatformError(
                "LinkedIn token refresh failed",
                platform="linkedin",
                http_status=exc.code,
                body=exc.read().decode("utf-8", "replace")[:2000],
            ) from exc
        except urllib.error.URLError as exc:
            raise PlatformError(
                "LinkedIn token refresh failed: network error", platform="linkedin", reason=str(exc.reason)
            ) from exc
        merged = {**data, **refreshed}
        self._store.put(credential_ref, json.dumps(merged))
        return merged

    # --- Publisher protocol ---
    def publish(self, *, body: str, content_hash: str, credential_ref: str) -> PublishResult:
        data = self._load_credential(credential_ref)
        can_refresh = bool(data.get("refresh_token") and data.get("client_id") and data.get("client_secret"))
        payload = {
            "author": data["person_urn"],
            "commentary": body,
            "visibility": "PUBLIC",
            "distribution": {
                "feedDistribution": "MAIN_FEED",
                "targetEntities": [],
                "thirdPartyDistributionChannels": [],
            },
            "lifecycleState": "PUBLISHED",
            "isReshareDisabledByAuthor": False,
        }
        try:
            post_id, raw_body = self._create_post(payload, data["access_token"])
        except PlatformError as exc:
            if exc.detail.get("http_status") != 401:
                raise
            if not can_refresh:
                raise ConfigurationError(
                    "LinkedIn rejected the access token (401) and this credential has no"
                    " refresh_token/client_id/client_secret to renew it. Self-serve"
                    " LinkedIn apps do not receive refresh tokens (programmatic refresh"
                    " requires approved Marketing Developer Platform partner status) —"
                    " the operator must manually re-run OAuth authorization and reconnect"
                    " the account. See docs/HUMAN-ACTIONS.md.",
                    credential_ref=credential_ref,
                ) from exc
            data = self._refresh(credential_ref, data)
            post_id, raw_body = self._create_post(payload, data["access_token"])

        if not post_id:
            raise PlatformError("LinkedIn publish returned no post id", platform="linkedin", response=raw_body)
        return PublishResult(
            platform_post_id=post_id,
            permalink=f"https://www.linkedin.com/feed/update/{post_id}/",
            published_at=iso(),
            simulated=False,
            detail={"content_hash": content_hash},
        )

    def _create_post(self, payload: dict[str, Any], access_token: str) -> tuple[str | None, Any]:
        req = urllib.request.Request(
            POSTS_URL,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers=self._headers(access_token),
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                post_id = resp.headers.get("x-restli-id")
                raw = resp.read().decode("utf-8")
                body = json.loads(raw) if raw else {}
                return post_id, body
        except urllib.error.HTTPError as exc:
            raise PlatformError(
                "LinkedIn publish failed",
                platform="linkedin",
                http_status=exc.code,
                body=exc.read().decode("utf-8", "replace")[:2000],
            ) from exc
        except urllib.error.URLError as exc:
            raise PlatformError(
                "LinkedIn publish failed: network error", platform="linkedin", reason=str(exc.reason)
            ) from exc

    def verify_published(self, platform_post_id: str, *, credential_ref: str) -> bool:
        data = self._load_credential(credential_ref)
        url = GET_POST_URL.format(urn=urllib.parse.quote(platform_post_id, safe=""))
        req = urllib.request.Request(
            url, method="GET", headers=self._headers(data["access_token"], content_type=None)
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
                resp.read()
                return True
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise PlatformError(
                "LinkedIn verify_published failed",
                platform="linkedin",
                http_status=exc.code,
                body=exc.read().decode("utf-8", "replace")[:2000],
            ) from exc
        except urllib.error.URLError as exc:
            raise PlatformError(
                "LinkedIn verify_published failed: network error", platform="linkedin", reason=str(exc.reason)
            ) from exc
