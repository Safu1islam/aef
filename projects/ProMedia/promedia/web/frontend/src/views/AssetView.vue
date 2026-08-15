<script setup lang="ts">
import { onMounted, ref } from "vue";
import { api, ApiError } from "../api";

const props = defineProps<{ assetId: string }>();

const loading = ref(true);
const error = ref<string | null>(null);
const busy = ref(false);
const detail = ref<any>(null);
const rights = ref<any>(null);
const evidenceKind = ref("");
const evidenceBody = ref("");
const principalKind = ref("agent");

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const [d, r, status] = await Promise.all([api.asset(props.assetId), api.rights(props.assetId), api.status()]);
    detail.value = d;
    rights.value = r;
    principalKind.value = status.principal?.kind ?? "agent";
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

const determineRights = () => run(() => api.determineRights(props.assetId));
const attest = () => run(() => api.attestDeclaration(props.assetId));
const seal = () => run(() => api.sealProvenance(props.assetId));
async function addEvidence() {
  if (!evidenceKind.value.trim() || !evidenceBody.value.trim()) return;
  // produced_by reflects the CALLING PRINCIPAL's own real kind, fetched from
  // the server (never a value this form lets someone type in) — the server
  // independently enforces the same rule regardless (F-5's permitting
  // boundary: an agent may not self-declare as 'operator').
  await run(() =>
    api.addEvidence(props.assetId, evidenceKind.value.trim(), evidenceBody.value.trim(), principalKind.value),
  );
  evidenceKind.value = "";
  evidenceBody.value = "";
}

function fmtMB(bytes: number): string {
  return (bytes / 1048576).toFixed(1);
}
</script>

<template>
  <section class="asset">
    <router-link to="/media" class="back muted">&larr; Media</router-link>
    <div v-if="loading" class="state muted">Loading…</div>
    <div v-else-if="error && !detail" class="state banner bad">{{ error }}</div>
    <template v-else>
      <h1>{{ detail.asset.original_filename }}</h1>
      <p class="muted mono">{{ detail.asset.id }}</p>

      <div v-if="!rights.media_available" class="banner bad">
        <strong>This asset's media is gone</strong> (state: {{ detail.asset.state }}).
        <span v-if="detail.asset.state === 'deleted'">
          Retention deleted it and that is final — the rights and provenance records below remain
          valid and readable (F-8), but there is nothing left to render or publish from this asset.
        </span>
        <span v-else>The record survived a backup that the bytes did not; re-ingesting the same file
          restores it under this same asset id.</span>
      </div>

      <div class="grid">
        <div class="card"><h3>Rights verdict</h3>
          <p class="big"><span class="pill" :class="`tone-${rights.verdict === 'PERMITTED' ? 'green' : rights.verdict === 'BLOCKED' ? 'red' : 'amber'}`">{{ rights.verdict }}</span></p>
          <p class="muted mono small">{{ rights.matched_rule ?? "—" }}</p></div>
        <div class="card"><h3>Size</h3><p class="big">{{ fmtMB(detail.asset.byte_size) }} MB</p></div>
        <div class="card"><h3>Duration</h3>
          <p class="big">{{ detail.asset.duration_seconds ? Math.round(detail.asset.duration_seconds) + "s" : "—" }}</p>
          <p class="muted small">probe {{ detail.asset.probe_status }}</p></div>
        <div class="card"><h3>Provenance</h3><p class="big">{{ detail.provenance ? "sealed" : "not sealed" }}</p></div>
      </div>

      <div v-if="rights.differs_from_stored" class="banner bad">
        The verdict shown above governs this asset now and differs from the last one recorded
        directly on it — an ancestor's rights changed since. Re-run determination.
      </div>

      <div class="card wide">
        <h2>Rights</h2>
        <dl class="kv">
          <dt>Governing verdict</dt>
          <dd><span class="pill" :class="`tone-${rights.verdict === 'PERMITTED' ? 'green' : rights.verdict === 'BLOCKED' ? 'red' : 'amber'}`">{{ rights.verdict }}</span>
            <span v-if="rights.governing_asset && rights.governing_asset !== detail.asset.id" class="muted">
              — inherited from ancestor <span class="mono">{{ rights.governing_asset }}</span></span></dd>
          <dt>Reason</dt><dd>{{ rights.reason ?? "—" }}</dd>
          <dt>Ruleset</dt><dd class="mono">{{ rights.ruleset_version ? `${rights.ruleset_version} · jurisdiction ${rights.jurisdiction}` : "—" }}</dd>
          <dt>Decided</dt><dd class="mono">{{ rights.decided_at ?? "—" }}</dd>
          <dt>Publishable</dt><dd>{{ rights.publishable ? "yes" : "no" }}</dd>
        </dl>
        <button class="btn" :disabled="busy" @click="determineRights">
          {{ detail.verdicts.length ? "Re-run" : "Run" }} rights determination
        </button>
        <span class="muted small">Deterministic over recorded evidence (C-20).</span>
      </div>

      <div class="card wide">
        <h2>Declaration</h2>
        <template v-if="detail.declaration">
          <dl class="kv">
            <dt>Authorship</dt><dd class="mono">{{ detail.declaration.authorship }}</dd>
            <dt>Third-party material</dt>
            <dd>
              <ul v-if="detail.declaration.third_party_material?.length">
                <li v-for="item in detail.declaration.third_party_material" :key="item">{{ item }}</li>
              </ul>
              <span v-else>none declared</span>
            </dd>
            <dt>Declared by</dt>
            <dd class="mono">
              {{ detail.declaration.declared_by }}
              <span v-if="detail.declaration.declared_by_kind === 'operator'" class="muted">(operator — attested)</span>
              <strong v-else class="pill tone-amber">agent proposal — not yet attested</strong>
            </dd>
          </dl>
          <button
            v-if="detail.declaration.declared_by_kind !== 'operator'"
            class="btn"
            :disabled="busy"
            @click="attest"
          >
            Attest this declaration
          </button>
        </template>
        <p v-else class="muted">No declaration on file, which should not be reachable — ingest refuses without one.</p>
      </div>

      <div class="card wide">
        <h2>Evidence</h2>
        <p class="muted small">Evidence is never a verdict (F-5) — it is what determination reads.</p>
        <table v-if="detail.evidence.length">
          <thead><tr><th>Kind</th><th>Body</th><th>By</th><th>When</th></tr></thead>
          <tbody>
            <tr v-for="e in detail.evidence" :key="e.id">
              <td class="mono">{{ e.kind }}</td>
              <td>{{ e.body }}</td>
              <td class="mono">{{ e.produced_by }}</td>
              <td class="mono">{{ e.created_at.slice(0, 16).replace("T", " ") }}</td>
            </tr>
          </tbody>
        </table>
        <p v-else class="muted">No evidence recorded yet.</p>
        <div class="evidence-form">
          <input v-model="evidenceKind" type="text" placeholder="Kind, e.g. public_domain_verification" />
          <textarea v-model="evidenceBody" rows="2" placeholder="What the evidence says"></textarea>
          <button class="btn" :disabled="busy || !evidenceKind || !evidenceBody" @click="addEvidence">Add evidence as {{ principalKind }}</button>
        </div>
      </div>

      <div class="card wide">
        <h2>Provenance</h2>
        <template v-if="detail.provenance">
          <dl class="kv">
            <dt>Sealed</dt><dd class="mono">{{ detail.provenance.sealed_at }}</dd>
            <dt>Record</dt><dd class="mono">{{ detail.provenance.provenance_id }}</dd>
          </dl>
          <p class="muted small">Embeds the declaration, evidence and verdict that governed publication, and stays readable after the media is deleted (F-8).</p>
        </template>
        <template v-else>
          <p class="muted">Not sealed yet. Sealing freezes the current rights position into a self-contained record that survives deletion.</p>
          <button class="btn" :disabled="busy || !detail.verdicts.length" @click="seal">Seal provenance</button>
          <span v-if="!detail.verdicts.length" class="muted small">Run a rights determination first.</span>
        </template>
      </div>

      <div v-if="error" class="banner bad">{{ error }}</div>
      <p class="muted mono small">content hash {{ detail.asset.content_hash }}</p>
    </template>
  </section>
</template>

<style scoped>
.asset {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px 28px 40px;
}
.back {
  display: inline-block;
  margin-bottom: 8px;
}
h1 {
  margin: 0;
  font: 600 22px var(--font-ui);
}
.state {
  padding: 30px 0;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(158px, 1fr));
  gap: 10px;
  margin: 16px 0;
}
.card {
  background: var(--bg-panel);
  border: 1px solid var(--line-3);
  border-radius: var(--radius-lg);
  padding: 13px 15px;
  margin-bottom: 14px;
}
.card.wide {
  padding: 14px 16px;
}
.card h3 {
  margin: 0 0 4px;
  font: 500 11px var(--font-ui);
  color: var(--fg-muted-2);
}
.card h2 {
  margin: 0 0 10px;
  font: 600 15px var(--font-ui);
}
.big {
  font: 600 24px var(--font-ui);
  letter-spacing: -0.02em;
  margin: 0.2rem 0 0;
}
.small {
  font-size: 11px;
}
.kv {
  display: grid;
  grid-template-columns: max-content 1fr;
  gap: 0.35rem 1rem;
  margin: 0 0 12px;
}
.kv dt {
  color: var(--fg-muted-2);
  font-size: 0.85rem;
}
.kv dd {
  margin: 0;
}
.btn {
  all: unset;
  cursor: pointer;
  padding: 0.5rem 1rem;
  border: 1px solid var(--line-5);
  border-radius: 6px;
  background: var(--bg-chip);
  color: var(--fg-dim);
  font: 500 12px var(--font-ui);
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.banner {
  border-left: 4px solid var(--amber);
  background: var(--bg-panel);
  padding: 0.75rem 1rem;
  border-radius: 0 6px 6px 0;
  margin-bottom: 14px;
}
.banner.bad {
  border-left-color: var(--red);
}
table {
  border-collapse: collapse;
  width: 100%;
  margin-bottom: 10px;
}
th,
td {
  text-align: left;
  padding: 0.4rem 0.5rem;
  border-bottom: 1px solid var(--line-2);
  font-size: 12px;
}
th {
  font: 500 10px var(--font-mono);
  color: var(--fg-muted-3);
}
.evidence-form {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 10px;
}
.evidence-form input,
.evidence-form textarea {
  font: inherit;
  padding: 0.45rem 0.55rem;
  border: 1px solid var(--line-5);
  border-radius: 6px;
  background: var(--bg-field);
  color: inherit;
  width: 100%;
  box-sizing: border-box;
}
</style>
