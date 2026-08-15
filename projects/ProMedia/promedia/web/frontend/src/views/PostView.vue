<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, ApiError } from "../api";

const props = defineProps<{ postId: string }>();
const router = useRouter();

const loading = ref(true);
const error = ref<string | null>(null);
const busy = ref(false);
const d = ref<any>(null);

async function load() {
  loading.value = true;
  error.value = null;
  try {
    d.value = await api.post(props.postId);
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "could not reach the server";
  } finally {
    loading.value = false;
  }
}
onMounted(load);

async function run(action: () => Promise<unknown>) {
  busy.value = true;
  error.value = null;
  try {
    await action();
    await load();
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "action failed";
  } finally {
    busy.value = false;
  }
}

function approveReason(): string | null {
  if (!d.value.approvable) {
    if (!d.value.rights) return "Waiting on a rights verdict — determine-rights has not run.";
    if (d.value.rights.verdict !== "PERMITTED") return `Rights verdict is ${d.value.rights.verdict}, not PERMITTED.`;
    if (!d.value.media_available) return "This asset's media is not available to publish.";
    if (d.value.status !== "queued") return `Post is ${d.value.status}, not queued.`;
    return "Not currently approvable.";
  }
  return null;
}

function approve() {
  // A disabled attribute removes the control from the tab order and
  // assistive-technology reach entirely — so the operator's most frequent
  // state (no verdict yet) had no way to discover an Approve control exists
  // at all via keyboard or screen reader (found in independent review). The
  // button stays focusable; aria-disabled plus this guard keep it inert.
  if (busy.value || !d.value.approvable) return;
  run(() => api.approvePost(props.postId, "approved"));
}
const reject = () => run(() => api.approvePost(props.postId, "rejected"));
const publish = () => run(() => api.publishPost(props.postId));
const releaseClaim = () => run(() => api.releasePublishClaim(props.postId));

function fmtMB(bytes: number): string {
  return (bytes / 1048576).toFixed(1);
}
</script>

<template>
  <section class="post">
    <div v-if="loading" class="state muted">Loading…</div>
    <div v-else-if="error && !d" class="state banner bad">{{ error }}</div>
    <template v-else>
      <h1>Review post</h1>
      <p class="muted mono">{{ d.post_id }}</p>

      <div v-if="d.warning" class="banner bad"><strong>{{ d.warning }}</strong></div>

      <!-- Decision context BEFORE any control (T-035, F-2). This surface is
           the authority surface BECAUSE it shows the basis first, not after. -->
      <div class="card">
        <h2>What you are authorising</h2>
        <dl class="kv">
          <dt>Account</dt>
          <dd>
            <strong v-if="d.account">{{ d.account.platform }}</strong>
            <span v-if="d.account"> {{ d.account.handle }}</span>
            <span v-else>—</span>
          </dd>
          <dt>Rights verdict</dt>
          <dd>
            <span v-if="d.rights" class="pill" :class="`tone-${d.rights.verdict === 'PERMITTED' ? 'green' : d.rights.verdict === 'BLOCKED' ? 'red' : 'amber'}`">{{ d.rights.verdict }}</span>
            <span v-else class="pill tone-amber">NO VERDICT</span>
            <span v-if="d.rights" class="muted"> via {{ d.rights.matched_rule }}</span>
          </dd>
          <dt>Ruleset</dt>
          <dd class="mono">{{ d.rights ? `${d.rights.ruleset} v${d.rights.ruleset_version} · jurisdiction ${d.rights.jurisdiction}` : "—" }}</dd>
          <dt>Rights claim</dt>
          <dd v-if="d.declaration">
            {{ d.declaration.authorship }}
            <span v-if="d.declaration.attested_by_operator" class="muted">— attested by you</span>
            <div v-else><strong class="pill tone-amber">Declared by an agent ({{ d.declaration.declared_by }}), not attested by you.</strong></div>
          </dd>
          <dd v-else>—</dd>
          <dt>Asset hash</dt><dd class="mono">{{ d.asset?.content_hash ?? "—" }}</dd>
          <dt>File</dt>
          <dd>{{ d.asset ? `${d.asset.original_filename} (${fmtMB(d.asset.byte_size)} MB)` : "—" }}</dd>
          <dt>Provenance</dt>
          <dd>
            <span v-if="d.provenance_sealed">sealed <span class="mono muted">{{ d.provenance_id }}</span></span>
            <strong v-else>not sealed — publication will be refused</strong>
          </dd>
          <dt>Status</dt><dd>{{ d.status }}</dd>
          <dt>Queued by</dt><dd class="muted">{{ d.created_by }}</dd>
        </dl>
      </div>

      <div class="card"><h2>Body</h2><p>{{ d.body }}</p></div>

      <div v-if="d.rights && d.rights.verdict !== 'PERMITTED'" class="banner bad">
        This asset's rights verdict is <strong>{{ d.rights.verdict }}</strong>. Approval is refused
        by the server, not merely hidden here. Transforming the material does not change this — a
        derivative inherits its source's verdict.
      </div>
      <div v-else-if="!d.rights" class="banner bad">
        <strong>No rights verdict yet.</strong> This asset has not been evaluated against the
        ruleset — <span class="mono">determine-rights</span> has not run for it. Approval is refused
        by the server until a verdict is recorded; this is the starting state for every newly
        queued post, not a fault specific to this one.
      </div>
      <div v-if="!d.media_available" class="banner bad">
        <strong>This asset's media is gone</strong> (state: {{ d.media_state }}). Retention deleted
        the bytes, and deletion is final. The rights verdict and the sealed provenance record remain
        valid and readable — nothing is wrong with them — but there is nothing to publish, so
        approval and publication are both refused by the server.
      </div>

      <div class="actions">
        <template v-if="d.status === 'queued'">
          <button
            class="btn primary"
            :aria-disabled="busy || !d.approvable"
            :aria-describedby="!d.approvable ? 'approve-reason' : undefined"
            @click="approve"
          >
            Approve for publication
          </button>
          <p v-if="!d.approvable" id="approve-reason" class="muted small">{{ approveReason() }}</p>
          <button class="btn danger" :disabled="busy" @click="reject">Reject</button>
        </template>
        <template v-else-if="d.status === 'approved'">
          <button class="btn primary" :disabled="busy" @click="publish">Publish now</button>
          <p class="muted small">Publishing sends content to an external platform. Not reversible once seen.</p>
        </template>
        <template v-else-if="d.status === 'publishing'">
          <div class="banner bad">
            <strong>Stuck mid-publish.</strong> A publish attempt claimed this post and did not
            finish — most likely the process stopped. No publication was recorded.
            <strong>Check {{ d.account?.platform ?? "the platform" }} yourself before retrying</strong>,
            then release the claim.
          </div>
          <button class="btn" :disabled="busy" @click="releaseClaim">Release claim and return to approved</button>
        </template>
        <template v-else-if="d.status === 'published'">
          <div v-if="d.was_simulated" class="banner bad">
            <strong>NEVER PUBLISHED.</strong> This was recorded by the simulated publisher
            (fabrication F-001). Nothing reached {{ d.account?.platform ?? "any platform" }}.<br>
            <span class="mono muted">{{ d.publication.platform_post_id }}</span>
          </div>
          <div v-else class="banner">
            Published to <strong>{{ d.account?.platform ?? "—" }}</strong> at {{ d.publication.published_at }}.<br>
            <span class="mono muted">{{ d.publication.platform_post_id }}</span>
          </div>
        </template>
      </div>

      <div v-if="error" class="banner bad">{{ error }}</div>
      <button class="link-back" @click="router.push('/dashboard')">&larr; Back to dashboard</button>
    </template>
  </section>
</template>

<style scoped>
.post {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px 28px 40px;
  max-width: 60rem;
}
h1 {
  margin: 0;
  font: 600 22px var(--font-ui);
}
.state {
  padding: 30px 0;
}
.card {
  background: var(--bg-panel);
  border: 1px solid var(--line-3);
  border-radius: var(--radius-xl);
  padding: 14px 16px;
  margin: 14px 0;
}
.card h2 {
  margin: 0 0 10px;
  font: 600 15px var(--font-ui);
}
.kv {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.4rem 1rem;
  margin: 0;
}
.kv dt {
  color: var(--fg-muted-2);
  font-size: 0.85rem;
}
.kv dd {
  margin: 0;
}
.banner {
  border-left: 4px solid var(--amber);
  background: var(--bg-panel);
  padding: 0.75rem 1rem;
  border-radius: 0 6px 6px 0;
  margin: 12px 0;
}
.banner.bad {
  border-left-color: var(--red);
}
.actions {
  display: flex;
  flex-direction: column;
  gap: 10px;
  align-items: flex-start;
  margin-top: 6px;
}
.btn {
  all: unset;
  cursor: pointer;
  padding: 0.6rem 1.1rem;
  border-radius: 8px;
  background: var(--bg-chip);
  border: 1px solid var(--line-5);
  color: var(--fg-dim);
  font: 600 13px var(--font-ui);
}
.btn.primary {
  background: var(--green);
  color: var(--green-ink);
  border-color: var(--green);
}
.btn.danger {
  color: var(--red-soft);
  border-color: var(--red-border);
}
.btn:disabled,
.btn[aria-disabled="true"] {
  opacity: 0.5;
  cursor: not-allowed;
}
.small {
  font-size: 11.5px;
}
.link-back {
  all: unset;
  cursor: pointer;
  color: var(--blue);
  margin-top: 16px;
  display: inline-block;
}
</style>
