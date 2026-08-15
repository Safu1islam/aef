"""Operator UI (DR-004) — the authority surface for F-2 approvals.

A thin generic adapter over the same registry the CLI uses. Every operation
gets a route automatically, so the UI cannot fall behind the repo-callable
surface (F-1, S4).

Two deliberate properties:

  * Server-rendered, no JavaScript required. The approval flow is where a human
    authorises irreversible, legally consequential actions; it must work
    plainly and be keyboard reachable.
  * Authority is NOT decided here. The registry decides it. This module only
    supplies the principal, so a bug in a template cannot grant authority.
"""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from ..config import Config, load as load_config
from ..errors import NotFound, ProMediaError, ValidationError
from ..core import db
from ..core.credentials import CredentialStore
from ..core.principal import Principal, agent as agent_principal, resolve
from ..core.registry import Context, Operation, invoke, load_operations

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Session cookie carrying the operator token. Set from the ?token= parameter in
# the URL printed at startup, so the token is presented once rather than living
# in every link the operator might copy.
COOKIE_NAME = "promedia_operator"

# Namespaced so it can never collide with an operation parameter (finding I3).
AUTH_QUERY_PARAM = "token"

# Finding N9: T-024's rationale — a query string lands in browser history and
# leaks via Referer — applies with more force to the operator token than to a
# platform credential, because the token grants publish authority over every
# account. ?token= therefore survives ONLY as the one-time bootstrap on "/",
# which immediately exchanges it for a cookie and redirects. Everywhere else it
# must arrive as a cookie or this header.
AUTH_HEADER = "X-ProMedia-Token"


_DEFAULT_PORTS = {"http": 80, "https": 443}


# --- the one error -> HTTP status map for this surface (T-032) ---------------
#
# There used to be four of these, inline, one per route that caught a
# ProMediaError: three conditional expressions under /posts/{id}/..., one dict
# in op_submit, one dict in api_op. They had already drifted apart in two
# directions at once — api_op had no RIGHTS_BLOCKED entry, so the rights gate
# reported 403 on the HTML approve route and 400 on the JSON API for the same
# refusal, and the HTML routes had no NOT_FOUND entry, so an unknown post id
# came back 400 where /api/op and /ops answered 404.
#
# That is the failure mode a duplicated table always has: the status an error
# carries became a property of the route that happened to raise it rather than
# of the error itself. One map means a new error class cannot acquire a
# different meaning per route, and adding one is a single edit here.
ERROR_STATUS: dict[str, int] = {
    "FORBIDDEN": 403,
    "APPROVAL_REQUIRED": 403,
    "RIGHTS_BLOCKED": 403,
    "NOT_FOUND": 404,
    "VALIDATION": 400,
    # C-19 contention. 409 Conflict, not 400: nothing is wrong with the
    # request, so the caller should retry later rather than change it. The CLI
    # carries the same signal as exit code 4 (DR-012), and
    # tests/test_parity.py pins the pair.
    "ENTITY_LOCKED": 409,
}

# Anything unmapped. 400 rather than 500 because every ProMediaError is a
# refusal the server chose deliberately, not a fault — including the base class
# invoke() wraps an unexpected exception in, which is reported rather than
# swallowed (protocol 05: fail loudly).
DEFAULT_ERROR_STATUS = 400


def status_for(error: ProMediaError) -> int:
    """The HTTP status this error class carries, wherever it was raised."""
    return ERROR_STATUS.get(error.code, DEFAULT_ERROR_STATUS)


# --- T-035: decision context before the control -------------------------------
#
# Raised by independent review of T-034. The generic /ops/{name} form made every
# capability operable from a browser, approve-post and publish-post included —
# so the operator could authorise on a typed post_id, with the verdict, ruleset
# version and asset hash appearing in the RESPONSE, after the decision. F-2
# makes the UI the authority surface, and it is the authority surface BECAUSE it
# shows the basis; a generic form that approves on a typed id is the CLI with a
# browser skin.
#
# The rule is DERIVED from the operation rather than listed by name, for the
# DR-002 reason that has come up in every task here: a hardcoded set in the
# adapter is a second source of truth that drifts. An operator decision that
# mutates an existing post is exactly the class /posts/{id} exists for.
CONFIRM_FIELD = "__confirm"

# The read-only operation that assembles the basis. Registered, agent-authority,
# and the same one /posts/{id} renders — so the confirmation screen and the
# review screen cannot show different facts about the same post.
DECISION_CONTEXT_OP = "post"


def _needs_decision_context(op: Operation) -> bool:
    """Does authorising this require its basis on screen first?

    True for an operator-authority write to an existing post: approve-post,
    publish-post, release-publish-claim. Deriving it means a future operator
    decision on a post inherits the requirement the day it is registered,
    rather than the day someone remembers to add it to a list.
    """
    return (
        op.authority == "operator"
        and op.mutates
        and op.entity == "post"
        and "post_id" in {p.name for p in op.params}
    )


def _decision_digest(decision: dict[str, Any]) -> str:
    """A fingerprint of the facts that were actually displayed.

    Binding the confirmation to the CONTENT rather than to a bare "yes" is what
    makes "shown first" checkable, and it buys a second guarantee for free: if
    the basis changes between display and confirmation — retention deletes the
    media, new evidence degrades an ancestor's verdict, the account goes to
    'error' — the digest no longer matches and the operator is re-shown the
    facts instead of authorising ones that have stopped being true.
    """
    import hashlib

    from ..core.db import canonical_json

    return hashlib.sha256(canonical_json(decision).encode("utf-8")).hexdigest()


def _operations() -> dict[str, Operation]:
    """Everything this surface exposes: the registry, unfiltered, at request time.

    The single accessor. Listing pages, ``/api/ops`` and ``_operation()`` all
    come through here, so "what the UI advertises" and "what the UI will run"
    cannot be different sets — which is the whole of T-031.
    """
    return load_operations()


def _operation(name: str) -> Operation:
    """Resolve an operation name, or refuse. The adapter's ONE authority (T-031).

    The defect this closes: ``create_app`` captured ``operations =
    load_operations()`` and consulted that local mapping for the T-025 GET guard
    and the T-024 sensitive-parameter guard only, then called ``invoke()``, which
    re-reads the registry itself. The local mapping was therefore not the
    authority on what exists. An operation absent from it still executed — with
    both guards silently skipped. The obvious way to take a capability off the
    web surface removed its protections instead. Verified before the fix: a
    hidden ``publish-post`` published a post over GET and returned 200.

    Two things fix it, and the order matters:

    * **The second source of truth is removed, not reconciled.** There is no
      captured snapshot any more. Every path in this module — the dashboard, the
      capability listing, ``/api/ops``, the guards and execution — reaches the
      registry through this one function, at request time. Keeping two mappings
      in sync with a check would be the same bug with a reconciliation step
      bolted on.
    * **What this function cannot resolve is refused, before anything runs.**
      ``invoke()``'s contract is name-based, shared with the CLI (DR-002), so the
      adapter cannot hand it an already-resolved operation and make the two paths
      one call. It can, however, make its own resolution the gate: nothing
      reaches ``invoke()`` that did not come out of here. That is not a sync
      check between two mappings; it is what makes the single mapping
      authoritative for this surface.

    The failure direction is now the safe one: absent means REFUSED, never
    executed unguarded.

    This deliberately does NOT become a way to hide a capability. Nothing filters
    — the function returns the registry itself, so ``/ops``, ``/api/ops`` and
    ``/api/op/{name}`` enumerate exactly what the CLI does. F-1 and S4 make a
    single-surface capability a build failure, and ``tests/test_parity.py``
    invokes every operation on both surfaces to prove it.

    NOT_FOUND rather than a validation error because an unregistered name is an
    unknown resource, not a malformed parameter; it also carries exit code 1,
    which is the signal ``tests/test_parity.py`` pins for this class.
    """
    registry = _operations()
    op = registry.get(name)
    if op is None:
        raise NotFound(f"unknown operation '{name}'", operation=name, known=sorted(registry))
    return op


def _token_in_query(request: Request) -> tuple[dict[str, Any], int] | None:
    """N9 — the operator token must never travel in a URL of a real operation.

    Extracted from ``api_op`` so T-034's form routes enforce the same rule
    rather than carrying a second copy of it. Returns the error payload and its
    status, not a response, because the two callers render differently: JSON on
    ``/api/op/{name}``, an error page on ``/ops/{name}``.
    """
    if AUTH_QUERY_PARAM not in request.query_params:
        return None
    return (
        {
            "ok": False,
            "error": "VALIDATION",
            "message": (
                f"the operator token must not be sent as '?{AUTH_QUERY_PARAM}=' here;"
                f" send the {AUTH_HEADER} header, or visit / once to obtain a"
                " session cookie"
            ),
            "detail": {"parameter": AUTH_QUERY_PARAM, "use_header": AUTH_HEADER},
        },
        # Through the same table, so a hand-built refusal payload and a raised
        # ValidationError cannot report the same class with different statuses.
        ERROR_STATUS["VALIDATION"],
    )


def _sensitive_in_query(request: Request, op: Operation) -> tuple[dict[str, Any], int] | None:
    """T-024 — a sensitive value must never arrive in a query string.

    The browser records a query string in history and leaks it via Referer. The
    rule is enforced on the form routes too, and on their GET as well as their
    POST: the harm is that the value reached the URL at all, which has already
    happened by the time the request arrives. Refusing tells the operator to
    stop reusing that URL.
    """
    in_query = set(request.query_params)
    for p in op.params:
        if p.sensitive and p.name in in_query:
            return (
                {
                    "ok": False,
                    "error": "VALIDATION",
                    "message": (
                        f"'{p.name}' is sensitive and must not be sent as a query"
                        " parameter; send it in a POST body"
                    ),
                    "detail": {"parameter": p.name},
                },
                ERROR_STATUS["VALIDATION"],
            )
    return None


def _reject_foreign_origin(request: Request, cfg: Config) -> JSONResponse | None:
    """Refuse a state-changing request that did not originate from this app.

    T-025. SameSite=strict on the cookie is a browser-side control, and it was
    the only thing standing between a cross-origin form POST and an irreversible
    publish.

    Finding N8: the first version derived "this app" from
    `request.url.netloc` — which is the client-supplied Host header. An attacker
    controlling both Host and Origin matched its own forgery, and the reviewer
    put a state change through that way. The baseline must come from
    configuration, which the client cannot influence, not from the request.

    Comparison is structural (scheme, hostname, port) rather than string-prefix,
    so `http://host.evil.com` cannot pass as a prefix-extension of `http://host`.

    Requests with no Origin and no Referer are allowed: curl, the CLI and the
    test client carry no ambient cookie authority, which is the thing CSRF
    exploits.
    """
    source = request.headers.get("origin") or request.headers.get("referer")
    if not source:
        return None

    configured_host = str(cfg.get("web", "host")).lower()
    configured_port = int(cfg.get("web", "port"))
    # Loopback spellings of the same machine are the same origin in practice.
    allowed_hosts = {configured_host, "localhost", "127.0.0.1", "[::1]", "::1"}

    parsed = urlsplit(source)
    hostname = (parsed.hostname or "").lower()
    port = parsed.port or _DEFAULT_PORTS.get(parsed.scheme)

    if hostname in allowed_hosts and port == configured_port:
        return None

    return JSONResponse(
        {
            "ok": False,
            "error": "FORBIDDEN",
            "message": "cross-origin state-changing request refused",
            "detail": {
                "origin": source,
                "expected_host": configured_host,
                "expected_port": configured_port,
            },
        },
        status_code=ERROR_STATUS["FORBIDDEN"],
    )


# --- media library (T-050) ----------------------------------------------------
#
# The most glaring hole the frontend brief named: no way to get a file in from
# the browser at all. ``ingest`` (T-008) already IS the rights gate — it takes
# a source_path on this machine and refuses without a declaration, exactly what
# the CLI and the generic /ops form already call. A browser upload arrives as a
# stream, not a path, so the one thing this adapter adds is bridging the two:
# stage the bytes to a temporary file, then hand ``ingest`` a path exactly as
# it already expects. No rights or storage decision is made here — that stays
# entirely inside ``ingest`` (rule 2).


def _stage_upload(upload: UploadFile) -> Path:
    """Write a browser upload to a temp file ``ingest`` can read as a path.

    Streamed in chunks rather than read into memory at once: masters run to
    the C-12 ballpark of ~1.5 GB, and this machine has 7.7 GB of RAM total
    (DR-016) — the same reason ``ingest.hash_file`` chunks its own read.

    Staged under a fresh directory named for the ORIGINAL filename, not a
    generated one: ``ingest_file`` records ``src.name`` as the asset's
    ``original_filename``, so a random temp name would replace the filename
    the operator actually recognises everywhere the library shows it. A
    directory of its own, rather than the system temp root directly, is what
    lets the name be exactly the upload's own name (sanitised to strip any
    path component a hostile client might send) without colliding with a
    second upload of a same-named file arriving at the same moment.
    """
    original_name = Path(upload.filename or "").name.strip() or "upload"
    staging_dir = Path(tempfile.mkdtemp(prefix="promedia-upload-"))
    destination = staging_dir / original_name
    with destination.open("wb") as out:
        shutil.copyfileobj(upload.file, out, length=1024 * 1024)
    return destination


def _declaration_from_form(form: dict[str, Any]) -> dict[str, Any]:
    """Build the declaration ``ingest`` expects out of the upload form fields.

    Marshalling form fields into the shape an operation parameter expects is
    what every route in this module already does (e.g. ``project_render``'s
    ``quality`` field); it is not the rights decision rule 2 keeps out of the
    adapter — ``ingest_layer._validate_declaration`` still runs inside
    ``ingest`` and still refuses a missing or invalid authorship.
    """
    declaration: dict[str, Any] = {
        "authorship": (form.get("authorship") or "").strip(),
        "third_party_material": [
            line.strip()
            for line in (form.get("third_party_material") or "").splitlines()
            if line.strip()
        ],
    }
    for field in (
        "source_url", "licence_grantor", "licence_scope",
        "licence_evidence_ref", "public_domain_source",
    ):
        value = (form.get(field) or "").strip()
        if value:
            declaration[field] = value
    return declaration


# --- per-clip editing (T-051) --------------------------------------------------
#
# The edit stays one document (DR-016) — this only changes how the document is
# authored. Structured fields are marshalled into the same ``clips`` list the
# JSON textarea already sends to ``set-edl``, which is where EDL.validate()
# actually enforces every rule (ranges, known effects/transitions). Nothing
# here decides what is valid; it decides which of the submitted rows survive
# into that call.

_CLIP_FIELDS = (
    ("start", float, 0.0), ("end", float, None), ("speed", float, 1.0),
    ("transition_duration", float, 0.5), ("volume", float, 1.0),
)


def _clip_row(form: dict[str, Any], index: str) -> dict[str, Any] | None:
    """One clip out of the form, or None if it was removed or left blank.

    Returns a dict carrying a private ``_position`` key the caller sorts on
    and strips — kept out of the EDL's own Clip shape so a stray field cannot
    reach ``set-edl`` and fail its own validation with a confusing message.
    """
    asset_id = (form.get(f"clip-{index}-asset_id") or "").strip()
    if not asset_id or form.get(f"clip-{index}-remove"):
        return None
    row: dict[str, Any] = {"asset_id": asset_id}
    for name, caster, default in _CLIP_FIELDS:
        raw = form.get(f"clip-{index}-{name}")
        if raw is None or str(raw).strip() == "":
            row[name] = default
            continue
        try:
            row[name] = caster(raw)
        except (TypeError, ValueError):
            raise ValidationError(
                f"clip {index}: '{name}' must be a number", parameter=f"clip-{index}-{name}"
            )
    row["effect"] = form.get(f"clip-{index}-effect") or "none"
    row["transition_in"] = form.get(f"clip-{index}-transition_in") or "cut"
    row["mute"] = bool(form.get(f"clip-{index}-mute"))
    raw_position = form.get(f"clip-{index}-position")
    try:
        row["_position"] = float(raw_position) if raw_position not in (None, "") else 0.0
    except (TypeError, ValueError):
        raise ValidationError(f"clip {index}: 'position' must be a number",
                              parameter=f"clip-{index}-position")
    return row


def _clips_from_form(form: dict[str, Any], existing_count: int) -> list[dict[str, Any]]:
    """Every surviving clip, in the order their position fields ask for.

    A stable sort, so two clips left at the same position keep their relative
    order — which for the untouched majority of a large edit is the original
    order, not an arbitrary one.
    """
    rows = [_clip_row(form, str(i)) for i in range(existing_count)]
    rows.append(_clip_row(form, "new"))
    surviving = [r for r in rows if r is not None]
    surviving.sort(key=lambda r: r["_position"])
    for r in surviving:
        r.pop("_position")
    if not surviving:
        raise ValidationError(
            "an edit needs at least one clip; nothing was submitted or every"
            " clip was removed",
            parameter="clips",
        )
    return surviving


def create_app(config: Config | None = None, *, store: CredentialStore | None = None) -> FastAPI:
    cfg = config or load_config()
    credential_store = store or CredentialStore()
    app = FastAPI(title="ProMedia", docs_url=None, redoc_url=None)

    def context(request: Request) -> Context:
        """Resolve the caller's authority from a presented token.

        The UI must NOT grant operator authority merely because a request
        reached the port. Localhost is not an authentication boundary: an agent
        can issue local HTTP requests as easily as the operator's browser can,
        and F-2 exists precisely to stop an agent publishing. Binding to
        127.0.0.1 keeps other machines out; it does nothing about other
        processes on this one.

        So the same token the CLI requires must be presented here, as a cookie
        (set once from the startup URL) or as a ?token= parameter. An agent that
        cannot read the credential store cannot authenticate — which is the same
        boundary the CLI has, rather than a weaker one.
        """
        conn = db.connect(
            cfg.db_path, busy_timeout_ms=int(cfg.get("database", "busy_timeout_ms"))
        )
        db.apply_schema(conn)
        return Context(config=cfg, conn=conn, principal=principal_of(request))

    def principal_of(request: Request) -> Principal:
        """The caller's authority, without opening a database connection.

        Split out of context() for T-034: the operation form page needs to say
        whether this browser session can actually run an operator-authority
        operation, and rendering a page should not cost a connection. It stays
        the ONE place a principal is derived, so the page's claim and the
        registry's enforcement cannot disagree.
        """
        expected = credential_store.operator_token()
        supplied = (
            request.cookies.get(COOKIE_NAME)
            or request.headers.get(AUTH_HEADER)
            # Bootstrap only: "/" exchanges this for a cookie and redirects.
            # api_op and the form routes refuse it outright (N9).
            or request.query_params.get(AUTH_QUERY_PARAM)
        )
        if expected and supplied:
            return resolve(supplied, expected, identifier="ui")
        return agent_principal("ui")

    def run(request: Request, name: str, params: dict[str, Any]) -> dict[str, Any]:
        # Resolved here too, so the HTML routes below cannot execute something
        # this adapter does not expose either. Costs one dict lookup; buys the
        # property that every call out of this module went through _operation().
        op = _operation(name)
        ctx = context(request)
        try:
            return invoke(ctx, op.name, params)
        finally:
            ctx.conn.close()

    def guarded(request: Request, name: str, params: dict[str, Any]) -> Any:
        """run(), with the cross-origin refusal applied first (T-025)."""
        denied = _reject_foreign_origin(request, cfg)
        if denied is not None:
            return denied
        return run(request, name, params)

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> Any:
        # Exchange ?token=... for a cookie once, then drop it from the URL so it
        # does not linger in history or get copied into a shared link.
        supplied = request.query_params.get(AUTH_QUERY_PARAM)
        if supplied:
            response = RedirectResponse(url="/", status_code=303)
            response.set_cookie(
                COOKIE_NAME, supplied, httponly=True, samesite="strict", path="/"
            )
            return response

        # T-052. The v1 dashboard was a storage/rights panel that never
        # mentioned a project — an approval surface with no way into the
        # workspace. This leads with "what needs the human" instead: posts
        # awaiting a decision, and recent renders (flagging any that did not
        # do everything as asked, Constitution section 6). Storage, the rights
        # ruleset, every asset and every account move to /settings, where they
        # are a reference rather than the first thing on screen.
        ctx = context(request)
        try:
            status = invoke(ctx, "status", {})
            posts = invoke(ctx, "list-posts", {})
            projects = invoke(ctx, "list-projects", {})
            outputs = invoke(ctx, "renders", {})
        finally:
            ctx.conn.close()

        pending_posts = [
            p for p in posts["posts"] if p["status"] in ("queued", "approved", "publishing")
        ]
        return TEMPLATES.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "status": status,
                "pending_posts": pending_posts,
                "projects": projects["projects"][:5],
                "project_count": projects["count"],
                "recent_renders": outputs["renders"][:6],
            },
        )

    @app.get("/posts", response_class=HTMLResponse)
    def posts_index(request: Request) -> Any:
        status_filter = request.query_params.get("status") or None
        ctx = context(request)
        try:
            listing = invoke(ctx, "list-posts", {"status": status_filter} if status_filter else {})
        finally:
            ctx.conn.close()
        return TEMPLATES.TemplateResponse(
            request=request,
            name="posts.html",
            context={"posts": listing["posts"], "status_filter": status_filter or ""},
        )

    @app.get("/publications", response_class=HTMLResponse)
    def publications_index(request: Request) -> Any:
        ctx = context(request)
        try:
            pubs = invoke(ctx, "publications", {})
        finally:
            ctx.conn.close()
        return TEMPLATES.TemplateResponse(
            request=request,
            name="publications.html",
            context={"publications": pubs["publications"]},
        )

    @app.get("/settings", response_class=HTMLResponse)
    def settings_index(request: Request) -> Any:
        ctx = context(request)
        try:
            status = invoke(ctx, "status", {})
            accounts = invoke(ctx, "list-accounts", {})
            capabilities = invoke(ctx, "media-capabilities", {})
            scope = invoke(ctx, "backup-scope", {})
        finally:
            ctx.conn.close()
        return TEMPLATES.TemplateResponse(
            request=request,
            name="settings.html",
            context={
                "status": status,
                "accounts": accounts["accounts"],
                "capabilities": capabilities,
                "backup_scope": scope,
                "principal": principal_of(request),
            },
        )

    @app.post("/settings/accounts")
    def settings_connect_account(request: Request, platform: str = Form(...),
                                 handle: str = Form(...), secret: str = Form("")) -> Any:
        try:
            outcome = guarded(request, "connect-account", {
                "platform": platform, "handle": handle, "secret": secret or None,
            })
            if isinstance(outcome, JSONResponse):
                return outcome
        except ProMediaError as exc:
            return _error_page(request, exc)
        return RedirectResponse(url="/settings", status_code=303)

    def _error_page(request: Request, exc: ProMediaError) -> Any:
        """A refusal as a page, with the status the class already dictates.

        Routes through the same ERROR_STATUS map every other surface uses
        (DR-012), so a rights refusal is 403 here exactly as it is on the API.
        """
        return TEMPLATES.TemplateResponse(
            request=request,
            name="error.html",
            context={"error": exc.to_dict(), "principal": principal_of(request)},
            status_code=status_for(exc),
        )

    # --- media workspace (T-049) ---------------------------------------------
    #
    # Raised by the operator on seeing the running app: the v1 surface is a
    # storage dashboard plus a TABLE of operations, which is an approval surface
    # and not a place to make anything. These routes are a projection of the
    # capabilities T-042 registered — no business logic lives here, so the CLI
    # and this UI cannot drift (F-1, DR-002).
    #
    # No JavaScript, deliberately. DR-004 chose that so the APPROVAL path stays
    # dependable, and a project view plus an HTML5 <video> element needs none,
    # so that guarantee survives this change rather than being superseded.
    #
    # FINDING, fixed in passing (T-050/051/052): the three mutating routes below
    # called invoke() directly instead of guarded(), so they carried none of
    # T-025's cross-origin refusal. That matters more here than it looks: every
    # operation these routes call is agent-authority (create-project, set-edl,
    # render-project), so none of them needed the operator's SameSite=strict
    # cookie to run at all — an evil page could have auto-submitted a form to
    # overwrite a project's EDL with no authentication whatsoever. Routed
    # through guarded() now, matching every /posts/{id} route.

    @app.get("/projects", response_class=HTMLResponse)
    def projects_index(request: Request) -> Any:
        ctx = context(request)
        try:
            listing = invoke(ctx, "list-projects", {})
            capabilities = invoke(ctx, "media-capabilities", {})
        finally:
            ctx.conn.close()
        return TEMPLATES.TemplateResponse(
            request=request,
            name="projects.html",
            context={"projects": listing["projects"], "capabilities": capabilities},
        )

    @app.post("/projects")
    def projects_create(request: Request, title: str = Form(...)) -> Any:
        try:
            created = guarded(request, "create-project", {"title": title})
            if isinstance(created, JSONResponse):  # cross-origin refusal (T-025)
                return created
        except ProMediaError as exc:
            return _error_page(request, exc)
        # Post/redirect/get: a refresh after creating must not create a second.
        return RedirectResponse(url=f"/projects/{created['project_id']}", status_code=303)

    @app.get("/projects/{project_id}", response_class=HTMLResponse)
    def project_detail(request: Request, project_id: str) -> Any:
        ctx = context(request)
        try:
            project = invoke(ctx, "project", {"project_id": project_id})
            history = invoke(ctx, "project-versions", {"project_id": project_id})
            outputs = invoke(ctx, "renders", {"project_id": project_id})
            assets = invoke(ctx, "list-assets", {})
            capabilities = invoke(ctx, "media-capabilities", {})
        except ProMediaError as exc:
            return _error_page(request, exc)
        finally:
            ctx.conn.close()
        return TEMPLATES.TemplateResponse(
            request=request,
            name="project.html",
            context={
                "project": project,
                "edl_json": json.dumps(project["edl"], indent=2),
                "versions": history["versions"],
                "renders": outputs["renders"],
                "assets": assets["assets"],
                "capabilities": capabilities,
            },
        )

    @app.post("/projects/{project_id}/edl")
    def project_set_edl(request: Request, project_id: str, edl: str = Form(...),
                        note: str = Form("")) -> Any:
        try:
            outcome = guarded(request, "set-edl", {"project_id": project_id, "edl": edl, "note": note})
            if isinstance(outcome, JSONResponse):
                return outcome
        except ProMediaError as exc:
            return _error_page(request, exc)
        return RedirectResponse(url=f"/projects/{project_id}", status_code=303)

    @app.post("/projects/{project_id}/clips")
    async def project_edit_clips(request: Request, project_id: str) -> Any:
        """Structured clip editing (T-051) — the same set-edl call, a form instead
        of a JSON textarea. Reads the current document first so aspect, text and
        audio survive untouched; only ``clips`` is replaced, from the form.
        """
        denied = _reject_foreign_origin(request, cfg)
        if denied is not None:
            return denied
        form_data = {k: v for k, v in (await request.form()).items()}
        ctx = context(request)
        try:
            current = invoke(ctx, "project", {"project_id": project_id})
        except ProMediaError as exc:
            ctx.conn.close()
            return _error_page(request, exc)
        try:
            edl = dict(current["edl"])
            edl["clips"] = _clips_from_form(form_data, existing_count=len(current["edl"]["clips"]))
            note = (form_data.get("note") or "edited clips via the clip editor").strip()
            invoke(ctx, "set-edl", {"project_id": project_id, "edl": edl, "note": note})
        except ProMediaError as exc:
            return _error_page(request, exc)
        finally:
            ctx.conn.close()
        return RedirectResponse(url=f"/projects/{project_id}", status_code=303)

    @app.post("/projects/{project_id}/render")
    def project_render(request: Request, project_id: str, quality: str = Form("")) -> Any:
        try:
            outcome = guarded(request, "render-project",
                              {"project_id": project_id, "quality": quality or None})
            if isinstance(outcome, JSONResponse):
                return outcome
        except ProMediaError as exc:
            return _error_page(request, exc)
        return RedirectResponse(url=f"/projects/{project_id}", status_code=303)

    @app.get("/renders/{render_id}/file")
    def render_file(request: Request, render_id: str) -> Any:
        """Serve a rendered video for playback in the browser.

        The path comes from the RENDERS TABLE, never from the URL — the caller
        supplies an id and the database supplies the path. A route that took a
        path would be a directory traversal into the operator's filesystem, and
        this one serves media on a surface that also holds publish authority.
        """
        from fastapi.responses import FileResponse

        ctx = context(request)
        try:
            found = [
                r for r in invoke(ctx, "renders", {})["renders"] if r["id"] == render_id
            ]
        finally:
            ctx.conn.close()
        if not found:
            return _error_page(request, NotFound(f"no render {render_id}", render_id=render_id))
        path = Path(found[0]["output_path"])
        if not path.is_file():
            return _error_page(
                request,
                NotFound("this render's file is no longer on disk", render_id=render_id),
            )
        return FileResponse(path, media_type="video/mp4", filename=path.name)

    # --- media library (T-050) -------------------------------------------------

    @app.get("/media", response_class=HTMLResponse)
    def media_index(request: Request) -> Any:
        ctx = context(request)
        try:
            listing = invoke(ctx, "list-assets", {})
            queue = invoke(ctx, "ingest-queue", {})
            storage = invoke(ctx, "storage-status", {})
        finally:
            ctx.conn.close()

        q = (request.query_params.get("q") or "").strip().lower()
        verdict_filter = request.query_params.get("verdict") or ""
        state_filter = request.query_params.get("state") or ""
        assets = listing["assets"]
        if q:
            assets = [a for a in assets if q in (a["original_filename"] or "").lower()]
        if verdict_filter:
            if verdict_filter == "none":
                assets = [a for a in assets if not a["latest_verdict"]]
            else:
                assets = [a for a in assets if a["latest_verdict"] == verdict_filter]
        if state_filter:
            assets = [a for a in assets if a["state"] == state_filter]

        return TEMPLATES.TemplateResponse(
            request=request,
            name="media.html",
            context={
                "assets": assets,
                "queued": queue["queued"],
                "storage": storage,
                "filters": {
                    "q": request.query_params.get("q", ""),
                    "verdict": verdict_filter,
                    "state": state_filter,
                },
            },
        )

    @app.post("/media")
    async def media_upload(
        request: Request,
        file: UploadFile = File(...),
        authorship: str = Form(""),
        third_party_material: str = Form(""),
        source_url: str = Form(""),
        licence_grantor: str = Form(""),
        licence_scope: str = Form(""),
        licence_evidence_ref: str = Form(""),
        public_domain_source: str = Form(""),
    ) -> Any:
        denied = _reject_foreign_origin(request, cfg)
        if denied is not None:
            return denied

        declaration = _declaration_from_form({
            "authorship": authorship,
            "third_party_material": third_party_material,
            "source_url": source_url,
            "licence_grantor": licence_grantor,
            "licence_scope": licence_scope,
            "licence_evidence_ref": licence_evidence_ref,
            "public_domain_source": public_domain_source,
        })

        staged = _stage_upload(file)
        ctx = context(request)
        try:
            result = invoke(
                ctx, "ingest", {"source_path": str(staged), "declaration": declaration}
            )
        except ProMediaError as exc:
            return _error_page(request, exc)
        finally:
            ctx.conn.close()
            # ingest_file() copies the bytes into its own content-addressed
            # store; the whole staging directory (not just the file — see
            # _stage_upload) is disposable either way, success or refusal.
            shutil.rmtree(staged.parent, ignore_errors=True)
        return RedirectResponse(url=f"/media/{result['asset_id']}", status_code=303)

    @app.get("/media/{asset_id}", response_class=HTMLResponse)
    def media_detail(request: Request, asset_id: str) -> Any:
        ctx = context(request)
        try:
            detail = invoke(ctx, "asset", {"asset_id": asset_id})
            rights = invoke(ctx, "rights", {"asset_id": asset_id})
        except ProMediaError as exc:
            return _error_page(request, exc)
        finally:
            ctx.conn.close()
        return TEMPLATES.TemplateResponse(
            request=request,
            name="asset.html",
            context={"a": detail, "rights": rights, "principal": principal_of(request)},
        )

    @app.get("/media/{asset_id}/file")
    def media_file(request: Request, asset_id: str) -> Any:
        """Serve a source asset's bytes for playback (T-055's source monitor).

        Same shape as ``render_file`` above, for the same reason: the path
        comes from the database via the ``asset`` operation, never from the
        URL, so this cannot become a directory-traversal route on a surface
        that also holds publish authority. Refuses (rather than 404s) when
        the media is not 'stored' — MediaUnavailable is the honest signal
        that the record exists but the bytes do not (T-029).
        """
        from fastapi.responses import FileResponse

        from ..errors import MediaUnavailable

        ctx = context(request)
        try:
            detail = invoke(ctx, "asset", {"asset_id": asset_id})
        except ProMediaError as exc:
            return _error_page(request, exc)
        finally:
            ctx.conn.close()
        asset = detail["asset"]
        if asset["state"] != "stored":
            return _error_page(
                request,
                MediaUnavailable(
                    f"this asset's media is '{asset['state']}', not stored", asset_id=asset_id
                ),
            )
        path = Path(asset["object_path"])
        if not path.is_file():
            return _error_page(request, NotFound("the file is not on disk", asset_id=asset_id))
        return FileResponse(path, filename=asset["original_filename"])

    @app.post("/media/{asset_id}/determine-rights")
    def media_determine_rights(request: Request, asset_id: str) -> Any:
        try:
            outcome = guarded(request, "determine-rights", {"asset_id": asset_id})
            if isinstance(outcome, JSONResponse):
                return outcome
        except ProMediaError as exc:
            return _error_page(request, exc)
        return RedirectResponse(url=f"/media/{asset_id}", status_code=303)

    @app.post("/media/{asset_id}/attest")
    def media_attest(request: Request, asset_id: str) -> Any:
        try:
            outcome = guarded(request, "attest-declaration", {"asset_id": asset_id})
            if isinstance(outcome, JSONResponse):
                return outcome
        except ProMediaError as exc:
            return _error_page(request, exc)
        return RedirectResponse(url=f"/media/{asset_id}", status_code=303)

    @app.post("/media/{asset_id}/seal")
    def media_seal(request: Request, asset_id: str) -> Any:
        try:
            outcome = guarded(request, "seal-provenance", {"asset_id": asset_id})
            if isinstance(outcome, JSONResponse):
                return outcome
        except ProMediaError as exc:
            return _error_page(request, exc)
        return RedirectResponse(url=f"/media/{asset_id}", status_code=303)

    @app.post("/media/{asset_id}/evidence")
    def media_add_evidence(request: Request, asset_id: str, kind: str = Form(...),
                           body: str = Form(...)) -> Any:
        # produced_by is derived from the caller's own authenticated principal,
        # never taken from the form — the same reasoning ops/rights.py applies:
        # a self-declared produced_by would let a browser posing as an agent
        # write evidence attributed to 'operator', which guards a permitting
        # rule (F-5).
        principal = principal_of(request)
        try:
            outcome = guarded(request, "add-evidence", {
                "asset_id": asset_id, "kind": kind, "body": body,
                "produced_by": principal.kind,
            })
            if isinstance(outcome, JSONResponse):
                return outcome
        except ProMediaError as exc:
            return _error_page(request, exc)
        return RedirectResponse(url=f"/media/{asset_id}", status_code=303)

    @app.get("/posts/{post_id}", response_class=HTMLResponse)
    def post_detail(request: Request, post_id: str) -> Any:
        try:
            detail = run(request, "post", {"post_id": post_id})
        except ProMediaError as exc:
            # Was an unconditional 404 for every ProMediaError. The only error
            # this read can realistically raise is NOT_FOUND (the 'post'
            # operation is agent-authority and read-only, so it takes no lock
            # and cannot be refused on authority), which the map answers 404
            # exactly as before. Anything else — an unexpected failure wrapped
            # by invoke() — now reports its own status instead of claiming the
            # post does not exist.
            return TEMPLATES.TemplateResponse(
                request=request,
                name="error.html",
                context={"error": exc.to_dict()},
                status_code=status_for(exc),
            )
        return TEMPLATES.TemplateResponse(
            request=request, name="post.html", context={"d": detail}
        )

    @app.post("/posts/{post_id}/approve")
    def approve(request: Request, post_id: str, decision: str = Form("approved")) -> Any:
        try:
            denied = guarded(request, "approve-post", {"post_id": post_id, "decision": decision})
            if isinstance(denied, JSONResponse):
                return denied
        except ProMediaError as exc:
            return TEMPLATES.TemplateResponse(
                request=request,
                name="error.html",
                context={"error": exc.to_dict()},
                status_code=status_for(exc),
            )
        return RedirectResponse(url=f"/posts/{post_id}", status_code=303)

    @app.post("/posts/{post_id}/publish")
    def publish(request: Request, post_id: str) -> Any:
        try:
            denied = guarded(request, "publish-post", {"post_id": post_id})
            if isinstance(denied, JSONResponse):
                return denied
        except ProMediaError as exc:
            return TEMPLATES.TemplateResponse(
                request=request,
                name="error.html",
                context={"error": exc.to_dict()},
                status_code=status_for(exc),
            )
        return RedirectResponse(url=f"/posts/{post_id}", status_code=303)

    @app.post("/posts/{post_id}/release-claim")
    def release_claim(request: Request, post_id: str) -> Any:
        try:
            denied = guarded(request, "release-publish-claim", {"post_id": post_id})
            if isinstance(denied, JSONResponse):
                return denied
        except ProMediaError as exc:
            return TEMPLATES.TemplateResponse(
                request=request,
                name="error.html",
                context={"error": exc.to_dict()},
                status_code=status_for(exc),
            )
        return RedirectResponse(url=f"/posts/{post_id}", status_code=303)

    # --- generic surface: one route per registered operation (S4) -------------

    @app.get("/ops", response_class=HTMLResponse)
    def ops_index(request: Request) -> Any:
        return TEMPLATES.TemplateResponse(
            request=request,
            name="ops.html",
            context={"operations": sorted(_operations().values(), key=lambda o: o.name)},
        )

    # T-034. /ops listed 29 capabilities and could operate none of them; the
    # only operable HTML was /posts/{id}. These two routes close that, and they
    # are a PROJECTION, not a new surface: the form is generated from the same
    # Operation/Param metadata the listing already renders as prose, and the
    # submission goes through guarded() -> run() -> _operation() -> invoke(),
    # which is the identical path /api/op/{name} takes. Nothing about
    # authority (F-2), locking (C-19) or rights (F-3) is decided here.

    def render_op(
        request: Request,
        op: Operation,
        *,
        submitted: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        error: dict[str, Any] | None = None,
        status_code: int = 200,
        decision: dict[str, Any] | None = None,
        confirm_digest: str | None = None,
    ) -> Any:
        return TEMPLATES.TemplateResponse(
            request=request,
            name="op.html",
            status_code=status_code,
            context={
                "op": op,
                # T-035. The decision context, when this operation is one that
                # must show its basis before its control. None for everything
                # else, so the template renders exactly as it did before.
                "decision": decision,
                "confirm_digest": confirm_digest,
                "confirm_field": CONFIRM_FIELD,
                # Sensitive values are stripped rather than re-rendered: a
                # re-populated password field puts the secret back into the
                # response body on every subsequent error (T-024's reasoning
                # applied to the response rather than the request).
                "submitted": {
                    p.name: (submitted or {}).get(p.name, "")
                    for p in op.params
                    if not p.sensitive
                },
                "result": (
                    json.dumps(result, indent=2, sort_keys=True, default=str)
                    if result is not None
                    else None
                ),
                "error": error,
                "principal": principal_of(request),
            },
        )

    def op_page_refusal(request: Request, op: Operation) -> Any | None:
        """The URL-borne-secret rules, rendered as a page rather than JSON."""
        refusal = _token_in_query(request) or _sensitive_in_query(request, op)
        if refusal is None:
            return None
        payload, status = refusal
        return TEMPLATES.TemplateResponse(
            request=request, name="error.html", context={"error": payload}, status_code=status
        )

    def op_not_found(request: Request, exc: NotFound) -> Any:
        return TEMPLATES.TemplateResponse(
            request=request,
            name="error.html",
            context={"error": exc.to_dict()},
            status_code=status_for(exc),
        )

    @app.get("/ops/{name}", response_class=HTMLResponse)
    def op_form(request: Request, name: str) -> Any:
        """Render the form. Executes nothing, whatever the operation is.

        This is what lets the route exist at all for a mutating or
        operator-authority operation without reopening T-025: a GET here reads
        the registry and renders, and there is no path from it to invoke().
        """
        try:
            op = _operation(name)
        except NotFound as exc:
            return op_not_found(request, exc)
        refused = op_page_refusal(request, op)
        return refused if refused is not None else render_op(request, op)

    @app.post("/ops/{name}", response_class=HTMLResponse)
    async def op_submit(request: Request, name: str) -> Any:
        """Run the operation from its form and render what came back.

        Deliberately NOT post/redirect/get: the result of an operation is the
        point of running it, and a redirect would discard it. The cost is that
        a browser refresh re-submits — which is why the irreversible operations
        keep their own PRG routes under /posts/{id}, and why this page tells the
        operator so.
        """
        try:
            op = _operation(name)
        except NotFound as exc:
            return op_not_found(request, exc)

        refused = op_page_refusal(request, op)
        if refused is not None:
            return refused

        form = await request.form()
        # Query parameters are ignored entirely here, so a value typed into the
        # URL cannot become an operation parameter on this route.
        params: dict[str, Any] = {k: v for k, v in form.items()}
        # T-035. Never an operation parameter — stripped before validate() sees
        # it, exactly as the operator token is on /api/op (T-026).
        confirmed = params.pop(CONFIRM_FIELD, None)

        if _needs_decision_context(op) and params.get("post_id"):
            # The basis is fetched THROUGH the registry, not read from the
            # database here: 'post' is an operation, so this cannot show the
            # operator something the CLI's `post` command would not.
            try:
                decision = guarded(request, DECISION_CONTEXT_OP, {"post_id": params["post_id"]})
            except ProMediaError as exc:
                return render_op(
                    request, op, submitted=params, error=exc.to_dict(),
                    status_code=status_for(exc),
                )
            if isinstance(decision, JSONResponse):  # cross-origin refusal (T-025)
                return decision
            digest = _decision_digest(decision)
            if confirmed != digest:
                # Nothing has executed. Either no confirmation was presented, or
                # it was for a DIFFERENT set of facts than the ones true now —
                # both mean the operator has not yet seen what they are
                # authorising, so show it and require a second, deliberate act.
                return render_op(
                    request, op, submitted=params, decision=decision, confirm_digest=digest
                )

        try:
            outcome = guarded(request, op.name, params)
        except ProMediaError as exc:
            return render_op(
                request, op, submitted=params, error=exc.to_dict(),
                status_code=status_for(exc),
            )
        if isinstance(outcome, JSONResponse):  # cross-origin refusal (T-025)
            return outcome
        return render_op(request, op, submitted=params, result=outcome)

    @app.get("/api/ops")
    def api_ops() -> Any:
        return JSONResponse(
            {"ok": True, "operations": [op.to_dict() for op in _operations().values()]}
        )

    @app.api_route("/api/op/{name}", methods=["GET", "POST"])
    async def api_op(request: Request, name: str) -> Any:
        """Every operation, reachable over HTTP with identical semantics.

        This is what makes dual-surface parity structural rather than a
        convention someone has to remember.
        """
        # T-031: resolve first, and refuse what does not resolve. Everything
        # below — both guards and the call itself — is about THIS operation, so
        # an unresolvable name must stop here rather than fall through to
        # invoke() with the guards skipped.
        try:
            op = _operation(name)
        except NotFound as exc:
            return JSONResponse(exc.to_dict(), status_code=status_for(exc))

        # T-025: a state-changing operation must not be reachable by GET.
        # A GET is fetched by prefetchers, link previews and history restores,
        # and publish-post is irreversible. Refuse before anything else runs.
        if request.method == "GET" and (op.mutates or op.authority == "operator"):
            return JSONResponse(
                {
                    "ok": False,
                    "error": "METHOD_NOT_ALLOWED",
                    "message": f"'{name}' changes state and must be POSTed, not fetched",
                    "detail": {"operation": name, "method": "POST"},
                },
                status_code=405,
            )

        # N9: the operator token must not travel in the URL of a real operation.
        refusal = _token_in_query(request)
        if refusal is not None:
            return JSONResponse(refusal[0], status_code=refusal[1])

        params: dict[str, Any] = dict(request.query_params)

        if request.method == "POST":
            content_type = request.headers.get("content-type", "")
            if content_type.startswith("application/json"):
                try:
                    body = await request.json()
                    if isinstance(body, dict):
                        params.update(body)
                except ValueError:
                    pass
            else:
                form = await request.form()
                params.update({k: v for k, v in form.items()})

        # T-024: a sensitive value must never arrive in a query string, where the
        # browser records it in history and leaks it via Referer.
        refusal = _sensitive_in_query(request, op)
        if refusal is not None:
            return JSONResponse(refusal[0], status_code=refusal[1])

        if request.method == "POST":
            denied = _reject_foreign_origin(request, cfg)
            if denied is not None:
                return denied

        try:
            # op.name, not the raw path segment: what executes is exactly what
            # was guarded above.
            result = run(request, op.name, params)
        except ProMediaError as exc:
            return JSONResponse(exc.to_dict(), status_code=status_for(exc))
        return JSONResponse(result)

    # --- Pro Media v2 rich client (T-053, DR-017) ------------------------------
    #
    # A static bundle, mounted alongside the pages above rather than replacing
    # them: DR-017 extends DR-004 rather than superseding it, so the Jinja2
    # pages stay the no-JS fallback. This block adds NO business logic and NO
    # new capability — the built app calls only /api/op/* and /api/ops, which
    # already exist above. Auth is the SAME operator-token cookie: it is set
    # with path="/" by "/", so it is already valid here without a second
    # bootstrap — the one below exists only for the convenience of a link
    # straight into /studio?token=... .
    frontend_dist = Path(__file__).parent / "frontend" / "dist"
    frontend_index = frontend_dist / "index.html"

    if frontend_index.is_file():
        from fastapi.staticfiles import StaticFiles

        app.mount("/studio/assets", StaticFiles(directory=str(frontend_dist / "assets")), name="studio-assets")

        @app.get("/studio")
        @app.get("/studio/{_path:path}")
        def studio(request: Request, _path: str = "") -> Any:
            from fastapi.responses import FileResponse

            supplied = request.query_params.get(AUTH_QUERY_PARAM)
            if supplied:
                response = RedirectResponse(url="/studio", status_code=303)
                response.set_cookie(
                    COOKIE_NAME, supplied, httponly=True, samesite="strict", path="/"
                )
                return response
            # Every path under /studio serves the same shell; vue-router
            # resolves the route client-side. Static assets are served by the
            # mount above, which FastAPI matches BEFORE this catch-all.
            return FileResponse(frontend_index)

    else:

        @app.get("/studio")
        @app.get("/studio/{_path:path}")
        def studio_not_built(_path: str = "") -> Any:
            return JSONResponse(
                {
                    "ok": False,
                    "error": "NOT_BUILT",
                    "message": (
                        "the rich client has not been built yet — run "
                        "'npm install && npm run build' in promedia/web/frontend"
                    ),
                },
                status_code=503,
            )

    return app


app = None  # built by run_server so importing this module stays cheap


def run_server() -> None:  # pragma: no cover - operator entry point
    import uvicorn

    cfg = load_config()
    store = CredentialStore()
    token = store.ensure_operator_token()
    host = str(cfg.get("web", "host"))
    port = int(cfg.get("web", "port"))

    # The token is printed, never embedded in the app. Opening this URL is what
    # grants operator authority to the browser session; without it the UI runs
    # with agent authority and refuses to approve or publish.
    print("\nProMedia UI. Open this URL to authenticate as the operator:", flush=True)
    print(f"  http://{host}:{port}/?token={token}\n", flush=True)

    # access_log=False is deliberate and is a security control, not a
    # convenience. Uvicorn's access log records full request lines including
    # query strings, so the ?token= exchange would write the operator token
    # into a log file — prohibited by NON-NEGOTIABLES list A ("printing,
    # committing, or logging a secret in any form").
    #
    # Nothing is lost that matters: promedia's own audit_log records principal,
    # operation, entity and outcome for every authority-gated attempt including
    # denials, which is the security-relevant record. An HTTP access log is not.
    uvicorn.run(create_app(cfg, store=store), host=host, port=port, access_log=False)
