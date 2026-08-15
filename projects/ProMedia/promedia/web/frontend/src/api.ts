// The ONLY way this app reaches ProMedia. Every call goes through
// /api/op/{name} — the same JSON surface DR-002's registry exposes to any
// caller. This file may not contain a rights decision, a lock decision, or an
// authority decision: it POSTs a name and parameters and renders whatever the
// server decides. See frontend-brief.md rule 2 and DR-017.
//
// Always POST, even for reads: the server allows POST for every operation
// regardless of whether it mutates (only GET is restricted for mutating /
// operator-authority operations), so one code path covers all of them and no
// parameter — sensitive or not — is ever placed in a query string.

export interface OperationParam {
  name: string;
  type: "str" | "int" | "float" | "bool" | "json";
  required: boolean;
  default: unknown;
  help: string;
  sensitive: boolean;
}

export interface Operation {
  name: string;
  summary: string;
  authority: "agent" | "operator";
  mutates: boolean;
  entity: string | null;
  danger: string | null;
  lock_by: string[];
  params: OperationParam[];
}

export class ApiError extends Error {
  readonly code: string;
  readonly detail: Record<string, unknown>;
  readonly status: number;

  constructor(code: string, message: string, detail: Record<string, unknown>, status: number) {
    super(message);
    this.code = code;
    this.detail = detail;
    this.status = status;
  }
}

async function post(name: string, params: Record<string, unknown> = {}): Promise<any> {
  const response = await fetch(`/api/op/${encodeURIComponent(name)}`, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(params),
  });
  const body = await response.json().catch(() => ({
    ok: false,
    error: "PARSE_ERROR",
    message: `${name}: response was not JSON (HTTP ${response.status})`,
    detail: {},
  }));
  if (!response.ok || body.ok === false) {
    throw new ApiError(
      body.error ?? "ERROR",
      body.message ?? `operation '${name}' failed`,
      body.detail ?? {},
      response.status,
    );
  }
  return body;
}

export const api = {
  call: post,

  async operations(): Promise<Operation[]> {
    const response = await fetch("/api/ops", { credentials: "same-origin" });
    const body = await response.json();
    return body.operations as Operation[];
  },

  // Thin, explicit wrappers for the calls views actually make. Not a second
  // implementation of anything — each is one line naming the operation and
  // its parameters, so a view never hand-builds a params object inline and
  // silently drifts from what the operation actually expects.
  status: () => post("status"),
  listPosts: (status?: string) => post("list-posts", status ? { status } : {}),
  post: (postId: string) => post("post", { post_id: postId }),
  approvePost: (postId: string, decision: "approved" | "rejected" = "approved") =>
    post("approve-post", { post_id: postId, decision }),
  publishPost: (postId: string) => post("publish-post", { post_id: postId }),
  releasePublishClaim: (postId: string) => post("release-publish-claim", { post_id: postId }),
  publications: () => post("publications"),
  listAccounts: () => post("list-accounts"),
  connectAccount: (platform: string, handle: string, secret?: string) =>
    post("connect-account", { platform, handle, secret: secret || undefined }),

  listAssets: () => post("list-assets"),
  asset: (assetId: string) => post("asset", { asset_id: assetId }),
  rights: (assetId: string) => post("rights", { asset_id: assetId }),
  determineRights: (assetId: string) => post("determine-rights", { asset_id: assetId }),
  attestDeclaration: (assetId: string) => post("attest-declaration", { asset_id: assetId }),
  sealProvenance: (assetId: string) => post("seal-provenance", { asset_id: assetId }),
  addEvidence: (assetId: string, kind: string, body: string, producedBy: string) =>
    post("add-evidence", { asset_id: assetId, kind, body, produced_by: producedBy }),

  listProjects: () => post("list-projects"),
  createProject: (title: string) => post("create-project", { title }),
  project: (projectId: string, version?: number) =>
    post("project", version ? { project_id: projectId, version } : { project_id: projectId }),
  projectVersions: (projectId: string) => post("project-versions", { project_id: projectId }),
  diffProjectVersions: (projectId: string, fromVersion: number, toVersion: number) =>
    post("diff-project-versions", { project_id: projectId, from_version: fromVersion, to_version: toVersion }),
  setEdl: (projectId: string, edl: unknown, note?: string, expectedVersion?: number) =>
    post("set-edl", {
      project_id: projectId,
      edl,
      note: note ?? "",
      ...(expectedVersion !== undefined ? { expected_version: expectedVersion } : {}),
    }),
  renderProject: (projectId: string, quality?: string) =>
    post("render-project", quality ? { project_id: projectId, quality } : { project_id: projectId }),
  renders: (projectId?: string) => post("renders", projectId ? { project_id: projectId } : {}),
  mediaCapabilities: () => post("media-capabilities"),

  storageStatus: () => post("storage-status"),
  ingestQueue: () => post("ingest-queue"),
  locks: () => post("locks"),
};
