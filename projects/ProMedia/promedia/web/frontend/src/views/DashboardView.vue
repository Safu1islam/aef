<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, ApiError } from "../api";

const router = useRouter();
const loading = ref(true);
const error = ref<string | null>(null);

const simulationEnabled = ref(false);
const storage = ref<{ fraction_used: number; state: string } | null>(null);
const pendingPosts = ref<any[]>([]);
const recentRenders = ref<any[]>([]);
const projects = ref<any[]>([]);
const projectCount = ref(0);

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const [status, posts, projectList, outputs] = await Promise.all([
      api.status(),
      api.listPosts(),
      api.listProjects(),
      api.renders(),
    ]);
    simulationEnabled.value = !!status.simulation_enabled;
    storage.value = status.storage;
    pendingPosts.value = posts.posts.filter((p: any) =>
      ["queued", "approved", "publishing"].includes(p.status),
    );
    projectCount.value = projectList.count;
    projects.value = projectList.projects.slice(0, 5);
    recentRenders.value = outputs.renders.slice(0, 6);
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "could not reach the server";
  } finally {
    loading.value = false;
  }
}
onMounted(load);

function fmtMB(bytes: number): string {
  return (bytes / 1048576).toFixed(1);
}
function fmtDate(iso: string): string {
  return iso.slice(0, 16).replace("T", " ");
}
</script>

<template>
  <section class="dash">
    <div v-if="loading" class="state muted">Loading…</div>
    <div v-else-if="error" class="state banner bad">{{ error }}</div>
    <template v-else>
      <div class="head">
        <div>
          <div class="eyebrow mono">STUDIO WORKSPACE</div>
          <h1>Production floor</h1>
          <p>What needs a decision, what just rendered, and where every project stands.</p>
        </div>
        <div class="head-actions">
          <button class="btn primary" @click="router.push('/projects')">Open projects</button>
          <button class="btn" @click="router.push('/media')">Media library</button>
        </div>
      </div>

      <div v-if="simulationEnabled" class="banner bad">
        <strong>Simulation is enabled.</strong> Publishing uses the stub publisher (fabrication
        F-001). Nothing reaches any platform; every publication recorded while this is on is
        marked simulated.
      </div>
      <div v-if="storage && storage.state !== 'ok'" class="banner">
        <strong>Storage at {{ Math.round(storage.fraction_used * 100) }}%</strong>
        ({{ storage.state }}) — new ingest may be refused or queued.
        <router-link to="/settings">Details</router-link>
      </div>

      <div class="body">
        <div class="col col-needs">
          <div class="block">
            <div class="block-head">
              <h2>Needs you</h2>
              <router-link to="/approvals">All approvals &rarr;</router-link>
            </div>
            <div v-if="!pendingPosts.length" class="muted empty">Nothing waiting on you.</div>
            <div v-else class="table-wrap">
            <table>
              <thead>
                <tr><th>Post</th><th>Status</th><th>Body</th><th>Queued by</th></tr>
              </thead>
              <tbody>
                <tr v-for="p in pendingPosts" :key="p.id" class="row" tabindex="0"
                    :aria-label="`Open post ${p.id}`"
                    @click="router.push(`/posts/${p.id}`)"
                    @keydown.enter="router.push(`/posts/${p.id}`)"
                    @keydown.space.prevent="router.push(`/posts/${p.id}`)">
                  <td class="mono">{{ p.id }}</td>
                  <td>
                    <span v-if="p.status === 'publishing'" class="pill tone-red">stuck mid-publish</span>
                    <span v-else>{{ p.status }}</span>
                  </td>
                  <td class="truncate">{{ p.body.slice(0, 80) }}</td>
                  <td class="muted">{{ p.created_by }}</td>
                </tr>
              </tbody>
            </table>
            </div>
          </div>
        </div>

        <div class="col col-side">
          <div class="block">
            <div class="block-head">
              <h2>Recent renders</h2>
            </div>
            <div v-if="!recentRenders.length" class="muted empty">Nothing rendered yet.</div>
            <div v-else class="table-wrap">
            <table>
              <thead>
                <tr><th>Project</th><th>Version</th><th>Size</th><th>Rendered</th><th></th></tr>
              </thead>
              <tbody>
                <tr v-for="r in recentRenders" :key="r.id">
                  <td><router-link :to="`/editor/${r.project_id}`">{{ r.project_id }}</router-link></td>
                  <td class="mono">v{{ r.edl_version }}</td>
                  <td class="mono">{{ fmtMB(r.byte_size) }} MB</td>
                  <td class="mono">{{ fmtDate(r.rendered_at) }}</td>
                  <td>
                    <span v-if="!r.output_exists" class="muted">file gone</span>
                    <span v-else-if="r.substitutions && r.substitutions.length" class="pill tone-amber"
                          title="did not render exactly as asked">substituted</span>
                    <span v-else class="pill tone-green">ok</span>
                  </td>
                </tr>
              </tbody>
            </table>
            </div>
          </div>

          <div class="block">
            <div class="block-head">
              <h2>Projects</h2>
              <router-link to="/projects">{{ projectCount }} total &rarr;</router-link>
            </div>
            <ul v-if="projects.length" class="plist">
              <li v-for="p in projects" :key="p.id">
                <router-link :to="`/editor/${p.id}`">{{ p.title }}</router-link>
                <span class="muted mono">v{{ p.edl_version }} · {{ p.updated_at.slice(0, 10) }}</span>
              </li>
            </ul>
            <div v-else class="muted empty">No projects yet.</div>
          </div>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.dash {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 24px 28px 36px;
}
.state {
  padding: 40px 0;
}
.head {
  display: flex;
  align-items: flex-end;
  gap: 22px;
  flex-wrap: wrap;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--line-3);
  margin-bottom: 18px;
}
.eyebrow {
  font: 500 10.5px var(--font-mono);
  letter-spacing: 0.1em;
  color: var(--fg-muted-3);
  margin-bottom: 7px;
}
h1 {
  margin: 0;
  font: 600 25px/1.15 var(--font-ui);
  letter-spacing: -0.02em;
}
.head p {
  margin: 7px 0 0;
  font: 400 13.5px/1.5 var(--font-ui);
  color: var(--fg-muted-2);
  max-width: 60ch;
}
.head-actions {
  display: flex;
  gap: 7px;
}
.btn {
  all: unset;
  cursor: pointer;
  padding: 9px 14px;
  border-radius: 8px;
  background: var(--bg-chip);
  border: 1px solid var(--line-5);
  color: var(--fg-dim);
  font: 500 12.5px var(--font-ui);
}
.btn.primary {
  background: var(--green);
  color: var(--green-ink);
  font-weight: 600;
  border-color: var(--green);
}

.banner {
  border-left: 4px solid var(--amber);
  background: var(--bg-panel);
  padding: 0.75rem 1rem;
  margin-bottom: 1rem;
  border-radius: 0 6px 6px 0;
}
.banner.bad {
  border-left-color: var(--red);
}

.body {
  /* Flexbox with an explicit wrapper per column, not CSS Grid with fr
     tracks: measured this browser distributing minmax(0,1.45fr)/minmax(0,1fr)
     wildly wrong (69px/550px, then 1.6px/618px after other changes) even
     with every documented automatic-minimum-size escape hatch applied
     (overflow:hidden, explicit min-width:0, table-layout:fixed). Flexbox's
     flex-basis + min-width:0 on an actual DOM wrapper per column is a far
     more reliably supported pattern for the same visual result. */
  display: flex;
  gap: 18px;
  flex-wrap: wrap;
  align-items: flex-start;
}
.col {
  min-width: 0;
}
.col-needs {
  flex: 1.45 1 320px;
}
.col-side {
  flex: 1 1 260px;
  display: flex;
  flex-direction: column;
  gap: 18px;
}
.col-side .block {
  margin-bottom: 0;
}

.block {
  /* Grid blowout fix: a grid item's automatic min-width defaults to its
     content's min-content size (here, a <table> with a long unbroken mono
     project id), which silently overrides minmax(0, Nfr) on the TRACK and
     starves the sibling column — measured: a 1.45fr/1fr split rendering as
     69px/550px. min-width: 0 lets the item actually shrink to its track's
     share; .table-wrap below gives the table somewhere to scroll if it still
     does not fit. */
  min-width: 0;
  background: var(--bg-panel);
  border: 1px solid var(--line-3);
  border-radius: var(--radius-xl);
  overflow: hidden;
  margin-bottom: 18px;
}
.table-wrap {
  overflow-x: auto;
}
.block-head {
  padding: 13px 16px;
  display: flex;
  align-items: center;
  gap: 12px;
  border-bottom: 1px solid var(--line-3);
}
.block-head h2 {
  margin: 0;
  flex: 1;
  font: 600 13.5px var(--font-ui);
}
.empty {
  padding: 16px;
}
table {
  /* table-layout: fixed sidesteps a grid-track-sizing edge case measured in
     this browser: even with overflow:hidden + min-width:0 on the grid item
     (.block), an auto-layout table's own min-content width (driven by the
     unbroken mono project id) still dictated the GRID TRACK's size — a
     1.45fr/1fr split rendered as 69px/550px. Fixed layout makes column
     widths a function of the table's own width, never its content, which is
     what actually stops the outer grid track from being pulled along. */
  table-layout: fixed;
  border-collapse: collapse;
  width: 100%;
}
th,
td {
  text-align: left;
  padding: 0.5rem 1rem;
  border-bottom: 1px solid var(--line-2);
  vertical-align: top;
  font-size: 12.5px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
th {
  font: 500 10px var(--font-mono);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--fg-muted-3);
}
tr.row {
  cursor: pointer;
}
tr.row:hover td {
  background: var(--bg-card);
}
tr.row:focus-visible {
  outline: var(--focus-ring);
  outline-offset: -2px;
}
.truncate {
  max-width: 30ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.plist {
  list-style: none;
  margin: 0;
  padding: 8px 16px 14px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.plist li {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-size: 13px;
}
</style>
