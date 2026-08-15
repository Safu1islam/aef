<script setup lang="ts">
// T-056: real run status + a real version-to-version diff, replacing the
// mockup's decorative "accept/reject individual diff hunks" (which the EDL
// model cannot honestly support — a version is atomic, not a set of
// independently acceptable fields; frontend brief section 6).
//
// Everything here traces to a real operation. "Run status" is entity_locks
// (C-19) — the same table App.vue's presence avatars already read, filtered
// to the selected project. The diff is diff-project-versions, computed from
// the two real EDL documents server-side. Nothing here is a model's summary
// of itself.
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";
import { api, ApiError } from "../api";

const route = useRoute();
const router = useRouter();

const loading = ref(true);
const error = ref<string | null>(null);
const acting = ref(false);
const actionNote = ref<string | null>(null);

const projects = ref<any[]>([]);
const selectedId = ref<string>("");
const project = ref<any>(null);
const history = ref<any[]>([]);
const locks = ref<any[]>([]);

const fromVersion = ref<number | null>(null);
const toVersion = ref<number | null>(null);
const diff = ref<any>(null);
const diffLoading = ref(false);
const diffError = ref<string | null>(null);

async function loadProjects() {
  loading.value = true;
  error.value = null;
  try {
    const [list, lockList] = await Promise.all([api.listProjects(), api.locks()]);
    projects.value = list.projects;
    locks.value = lockList.locks;
    const fromRoute = typeof route.query.projectId === "string" ? route.query.projectId : "";
    selectedId.value = fromRoute && list.projects.some((p: any) => p.id === fromRoute)
      ? fromRoute
      : list.projects[0]?.id ?? "";
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "could not reach the server";
  } finally {
    loading.value = false;
  }
}
onMounted(loadProjects);

async function loadProject() {
  if (!selectedId.value) {
    project.value = null;
    history.value = [];
    return;
  }
  error.value = null;
  try {
    const [proj, hist, lockList] = await Promise.all([
      api.project(selectedId.value),
      api.projectVersions(selectedId.value),
      api.locks(),
    ]);
    project.value = proj;
    history.value = hist.versions;
    locks.value = lockList.locks;
    // Default: review the latest change against the version right before it
    // — the case the frontend brief actually asks for ("what did the agent
    // just do"). Falls back to comparing the only version to itself when a
    // project has no history yet, which the diff endpoint reports as
    // identical rather than erroring.
    toVersion.value = proj.edl_version;
    fromVersion.value = hist.versions.length > 1 ? hist.versions[1].version : proj.edl_version;
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "could not load this project";
  }
}
watch(selectedId, loadProject, { immediate: false });

async function loadDiff() {
  if (!selectedId.value || fromVersion.value == null || toVersion.value == null) return;
  diffLoading.value = true;
  diffError.value = null;
  diff.value = null;
  try {
    diff.value = await api.diffProjectVersions(selectedId.value, fromVersion.value, toVersion.value);
  } catch (err) {
    diffError.value = err instanceof ApiError ? err.message : "could not compute this diff";
  } finally {
    diffLoading.value = false;
  }
}
watch([fromVersion, toVersion], loadDiff);
watch(selectedId, () => { diff.value = null; });
watch(project, () => { if (project.value) loadDiff(); });

function router_push(id: string) {
  router.replace({ query: { ...route.query, projectId: id } });
}
watch(selectedId, (id) => { if (id) router_push(id); });

const runStatus = computed(() => {
  if (!selectedId.value) return null;
  return locks.value.find((l: any) => l.entity_type === "project" && l.entity_id === selectedId.value) ?? null;
});

// Reject only makes structural sense against the version CURRENTLY live —
// reverting an already-superseded version would silently discard whatever
// came after it without saying so. The button is disabled, with an
// explanation, the rest of the time rather than hidden (the frontend brief's
// own rule for a control that cannot fire right now).
const canReject = computed(
  () => project.value && toVersion.value === project.value.edl_version && fromVersion.value !== toVersion.value,
);

async function acceptChange() {
  actionNote.value = `v${toVersion.value} is already the current version — nothing to write. Reviewing it is enough.`;
}

async function rejectChange() {
  if (!canReject.value || !project.value || fromVersion.value == null) return;
  const older = history.value.find((v: any) => v.version === fromVersion.value);
  if (!window.confirm(
    `Reject v${toVersion.value}? This appends a NEW version (v${project.value.edl_version + 1}) equal to` +
    ` v${fromVersion.value}${older ? ` (${older.note || "no note"})` : ""}. v${toVersion.value} stays readable` +
    ` in history — nothing is deleted.`,
  )) {
    return;
  }
  acting.value = true;
  error.value = null;
  actionNote.value = null;
  try {
    const older_edl = await api.project(selectedId.value, fromVersion.value);
    const result = await api.setEdl(
      selectedId.value, older_edl.edl,
      `rejected v${toVersion.value} — reverted to match v${fromVersion.value}`,
      project.value.edl_version,
    );
    actionNote.value = `Rejected. v${result.edl_version} is now current, matching v${fromVersion.value}.`;
    await loadProject();
  } catch (err) {
    if (err instanceof ApiError && err.code === "VALIDATION" && "current_version" in err.detail) {
      error.value =
        `Someone changed this project to v${err.detail.current_version} while this page was open` +
        ` — the reject was NOT applied. Reload and try again.`;
    } else {
      error.value = err instanceof ApiError ? err.message : "could not reject this version";
    }
  } finally {
    acting.value = false;
  }
}

const KIND_TONE: Record<string, string> = {
  clip_added: "green", caption_added: "green", audio_track_added: "green", subtitles_changed: "blue",
  clip_removed: "red", caption_removed: "red", audio_track_removed: "red",
  clip_reordered: "blue",
};
function toneFor(kind: string): string {
  return KIND_TONE[kind] ?? "amber";
}
function fmtDate(iso: string): string {
  return iso.slice(0, 16).replace("T", " ");
}
</script>

<template>
  <section class="agent-workspace">
    <div class="head">
      <div class="head-text">
        <h1>Agent workspace</h1>
        <p class="muted">The IDE agent and the UI agent are one agent on one project store. Real run status, and a real diff between two versions — never a summary invented by a model.</p>
      </div>
      <select v-if="projects.length" v-model="selectedId" class="project-pick">
        <option v-for="p in projects" :key="p.id" :value="p.id">{{ p.title }}</option>
      </select>
    </div>

    <div v-if="loading" class="state muted">Loading…</div>
    <div v-else-if="error && !project" class="state banner bad">{{ error }}</div>
    <div v-else-if="!projects.length" class="state muted">
      No projects yet. <router-link to="/projects">Create one</router-link>.
    </div>

    <div v-else-if="project" class="scroll">
      <div class="card">
        <div class="card-head"><span class="card-title">Run status</span></div>
        <div class="card-body">
          <p v-if="runStatus" class="status-line">
            <span class="pill tone-amber">holding a lock</span>
            <span class="mono">{{ runStatus.agent }}</span> is currently working on this project
            (lock expires {{ fmtDate(runStatus.expires_at) }}). Editing here may be refused with
            <span class="mono">ENTITY_LOCKED</span> until it releases — that reads as "someone else is
            working on this right now, try shortly", not an error.
          </p>
          <p v-else class="status-line muted">
            <span class="pill tone-grey">no lock held</span> No agent currently holds a lock on this project.
          </p>
        </div>
      </div>

      <div class="card">
        <div class="card-head">
          <span class="card-title">Version diff</span>
          <span class="muted small">{{ history.length }} version{{ history.length === 1 ? "" : "s" }} total</span>
        </div>
        <div class="card-body">
          <div class="version-pickers">
            <label>Compare from
              <select v-model.number="fromVersion">
                <option v-for="v in history" :key="v.version" :value="v.version">
                  v{{ v.version }} — {{ v.authored_kind }} · {{ v.note || "no note" }}
                </option>
              </select>
            </label>
            <span class="arrow">→</span>
            <label>to
              <select v-model.number="toVersion">
                <option v-for="v in history" :key="v.version" :value="v.version">
                  v{{ v.version }} — {{ v.authored_kind }} · {{ v.note || "no note" }}
                </option>
              </select>
            </label>
          </div>

          <div v-if="diffLoading" class="muted">Computing diff…</div>
          <div v-else-if="diffError" class="banner bad">{{ diffError }}</div>
          <template v-else-if="diff">
            <p v-if="diff.identical" class="muted">v{{ diff.from_version }} and v{{ diff.to_version }} are identical.</p>
            <ul v-else class="diff-list">
              <li v-for="(c, i) in diff.changes" :key="i" class="diff-item">
                <span class="pill" :class="`tone-${toneFor(c.kind)}`">{{ c.kind.replaceAll('_', ' ') }}</span>
                <span>{{ c.detail }}</span>
              </li>
            </ul>

            <div class="actions">
              <button class="btn" :disabled="acting" @click="acceptChange">Accept</button>
              <button class="btn danger" :disabled="acting || !canReject" @click="rejectChange">
                {{ acting ? "Rejecting…" : "Reject" }}
              </button>
              <span v-if="!canReject" class="muted small">
                Reject is only available when comparing the CURRENT version against an earlier one.
              </span>
            </div>
            <p v-if="actionNote" class="banner note">{{ actionNote }}</p>
          </template>
        </div>
      </div>

      <div v-if="error" class="banner bad">{{ error }}</div>

      <div class="card">
        <div class="card-head"><span class="card-title">History</span></div>
        <table>
          <thead><tr><th>Version</th><th>By</th><th>Note</th><th>When</th></tr></thead>
          <tbody>
            <tr v-for="v in history" :key="v.version" :class="{ current: v.version === project.edl_version }">
              <td class="mono">v{{ v.version }}</td>
              <td><span class="mono">{{ v.authored_kind }}</span> {{ v.authored_by }}</td>
              <td>{{ v.note }}</td>
              <td class="mono">{{ fmtDate(v.authored_at) }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </section>
</template>

<style scoped>
.agent-workspace {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.head {
  padding: 20px 28px 16px;
  border-bottom: 1px solid var(--line-3);
  display: flex;
  align-items: flex-start;
  gap: 18px;
  flex-wrap: wrap;
}
.head-text {
  flex: 1;
  min-width: 280px;
}
.head-text h1 {
  margin: 0;
  font: 600 20px var(--font-ui);
  letter-spacing: -0.02em;
}
.head-text p {
  margin: 5px 0 0;
  font: 400 12.5px/1.5 var(--font-ui);
  color: var(--fg-muted-2);
  max-width: 76ch;
}
.project-pick {
  font: inherit;
  padding: 0.5rem 0.6rem;
  border: 1px solid var(--line-5);
  border-radius: 6px;
  background: var(--bg-field);
  color: inherit;
  min-width: 220px;
}
.state {
  padding: 40px 28px;
}
.scroll {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 28px 36px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}

.card {
  border-radius: var(--radius-xl);
  background: var(--bg-panel);
  border: 1px solid var(--line-3);
  overflow: hidden;
}
.card-head {
  padding: 12px 16px;
  border-bottom: 1px solid var(--line-3);
  display: flex;
  align-items: center;
  gap: 9px;
}
.card-title {
  font: 600 12.5px var(--font-ui);
  flex: 1;
}
.card-body {
  padding: 14px 16px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.status-line {
  margin: 0;
  font-size: 12.5px;
  line-height: 1.6;
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}

.version-pickers {
  display: flex;
  gap: 10px;
  align-items: flex-end;
  flex-wrap: wrap;
}
.version-pickers label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font: 400 10.5px var(--font-ui);
  color: var(--fg-muted-2);
}
.version-pickers select {
  font: inherit;
  padding: 0.45rem 0.55rem;
  border: 1px solid var(--line-5);
  border-radius: 6px;
  background: var(--bg-field);
  color: inherit;
  min-width: 220px;
}
.arrow {
  padding-bottom: 0.5rem;
  color: var(--fg-muted-3);
}

.diff-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.diff-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 0;
  border-bottom: 1px solid var(--line-1);
  font-size: 12.5px;
}
.diff-item:last-child {
  border-bottom: none;
}
.diff-item .pill {
  flex: none;
}

.actions {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}
.btn {
  all: unset;
  cursor: pointer;
  padding: 0.5rem 1rem;
  border: 1px solid var(--line-5);
  border-radius: 6px;
  background: var(--bg-chip);
  color: var(--fg-dim);
  font: 500 12.5px var(--font-ui);
}
.btn.danger {
  border-color: var(--red-border);
  color: var(--red-soft);
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.pill {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 999px;
  font: 500 10.5px var(--font-mono);
  white-space: nowrap;
}
.tone-grey { background: var(--grey-wash); color: var(--grey-fg); }
.tone-green { background: var(--green-wash); color: var(--green); }
.tone-amber { background: var(--amber-wash); color: var(--amber); }
.tone-red { background: var(--red-wash); color: var(--red-soft); }
.tone-blue { background: var(--blue-wash); color: var(--blue); }

.banner {
  border-left: 4px solid var(--amber);
  background: var(--bg-panel);
  padding: 0.7rem 0.9rem;
  border-radius: 0 6px 6px 0;
  font-size: 12.5px;
}
.banner.bad {
  border-left-color: var(--red);
}
.banner.note {
  border-left-color: var(--green);
  margin: 0;
}

table {
  border-collapse: collapse;
  width: 100%;
}
th, td {
  text-align: left;
  padding: 0.5rem 0.6rem;
  border-bottom: 1px solid var(--line-2);
  font-size: 12px;
}
th {
  font: 500 10px var(--font-mono);
  color: var(--fg-muted-3);
}
tr.current td {
  background: var(--bg-card);
}
.small {
  font-size: 11px;
}
</style>
