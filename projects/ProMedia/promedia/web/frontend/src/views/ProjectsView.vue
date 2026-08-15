<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, ApiError } from "../api";

const router = useRouter();
const loading = ref(true);
const error = ref<string | null>(null);
const view = ref<"list" | "board">("list");
const projects = ref<any[]>([]);
const capabilities = ref<any>(null);
const newTitle = ref("");
const creating = ref(false);

// Renders keyed by project, latest first — used ONLY to derive an honest
// two-state board grouping (rendered vs not, and whether the latest render
// substituted something). ProMedia's project model has no 'pipeline stage'
// field, unlike the mockup's fabricated Intake/Editing/Review/Live columns;
// inventing one here would be exactly what Constitution section 6 forbids.
// This groups by facts the system actually recorded.
const rendersByProject = ref<Record<string, any[]>>({});

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const [list, caps, outputs] = await Promise.all([
      api.listProjects(),
      api.mediaCapabilities(),
      api.renders(),
    ]);
    projects.value = list.projects;
    capabilities.value = caps;
    const grouped: Record<string, any[]> = {};
    for (const r of outputs.renders) {
      (grouped[r.project_id] ??= []).push(r);
    }
    rendersByProject.value = grouped;
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "could not reach the server";
  } finally {
    loading.value = false;
  }
}
onMounted(load);

async function createProject() {
  if (!newTitle.value.trim()) return;
  creating.value = true;
  try {
    const created = await api.createProject(newTitle.value.trim());
    router.push(`/editor/${created.project_id}`);
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "could not create the project";
  } finally {
    creating.value = false;
  }
}

type Column = "no-render" | "clean" | "substituted";
const columns: { id: Column; label: string; dot: string }[] = [
  { id: "no-render", label: "NOT RENDERED", dot: "var(--fg-muted-4)" },
  { id: "substituted", label: "RENDERED · WITH SUBSTITUTIONS", dot: "var(--amber)" },
  { id: "clean", label: "RENDERED", dot: "var(--green)" },
];
function columnFor(projectId: string): Column {
  const renders = rendersByProject.value[projectId];
  if (!renders || !renders.length) return "no-render";
  return renders[0].substitutions?.length ? "substituted" : "clean";
}
const board = computed(() =>
  columns.map((col) => ({ ...col, items: projects.value.filter((p) => columnFor(p.id) === col.id) })),
);

function fmtDate(iso: string): string {
  return iso.slice(0, 16).replace("T", " ");
}
</script>

<template>
  <section class="projects">
    <div class="head">
      <div class="head-text">
        <h1>Projects</h1>
        <p class="muted">{{ projects.length }} project{{ projects.length === 1 ? "" : "s" }}</p>
      </div>
      <div class="view-toggle">
        <button :class="{ active: view === 'list' }" @click="view = 'list'">List</button>
        <button :class="{ active: view === 'board' }" @click="view = 'board'">Board</button>
      </div>
      <form class="create" @submit.prevent="createProject">
        <input v-model="newTitle" type="text" placeholder="New project title" required />
        <button type="submit" class="btn primary" :disabled="creating">Create</button>
      </form>
    </div>

    <div v-if="capabilities && !capabilities.ffmpeg_available" class="banner bad">
      <strong>ffmpeg is not installed.</strong> Editing and rendering cannot run until it is.
      {{ capabilities.note }}
    </div>

    <div v-if="loading" class="state muted">Loading…</div>
    <div v-else-if="error" class="state banner bad">{{ error }}</div>
    <div v-else-if="!projects.length" class="state muted">No projects yet. Create one above.</div>

    <div v-else-if="view === 'list'" class="scroll">
      <table>
        <thead>
          <tr><th>Project</th><th>Version</th><th>Created by</th><th>Last changed</th></tr>
        </thead>
        <tbody>
          <tr v-for="p in projects" :key="p.id" class="row" tabindex="0"
              :aria-label="`Open project ${p.title}`"
              @click="router.push(`/editor/${p.id}`)"
              @keydown.enter="router.push(`/editor/${p.id}`)"
              @keydown.space.prevent="router.push(`/editor/${p.id}`)">
            <td>
              <strong>{{ p.title }}</strong>
              <div class="muted mono small">{{ p.id }}</div>
            </td>
            <td class="mono">v{{ p.edl_version }}</td>
            <td class="mono">{{ p.created_by }}</td>
            <td class="mono">{{ fmtDate(p.updated_at) }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-else class="scroll board">
      <div class="board-row">
        <div v-for="col in board" :key="col.id" class="board-col">
          <div class="board-col-head">
            <span class="dot" :style="{ background: col.dot }" />
            <span class="board-col-title">{{ col.label }}</span>
            <span class="mono muted">{{ col.items.length }}</span>
          </div>
          <div class="board-col-body">
            <button
              v-for="p in col.items"
              :key="p.id"
              class="board-card"
              @click="router.push(`/editor/${p.id}`)"
            >
              <div class="board-card-title">{{ p.title }}</div>
              <div class="board-card-meta mono muted">v{{ p.edl_version }} · {{ p.created_by }}</div>
            </button>
            <div v-if="!col.items.length" class="muted small board-empty">—</div>
          </div>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
.projects {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.head {
  padding: 20px 28px 14px;
  border-bottom: 1px solid var(--line-3);
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.head-text {
  flex: 1;
  min-width: 220px;
}
.head-text h1 {
  margin: 0;
  font: 600 20px var(--font-ui);
  letter-spacing: -0.02em;
}
.view-toggle {
  display: flex;
  gap: 3px;
  padding: 3px;
  border-radius: 8px;
  background: var(--bg-panel);
  border: 1px solid var(--line-4);
}
.view-toggle button {
  all: unset;
  cursor: pointer;
  padding: 5px 12px;
  border-radius: 6px;
  font: 500 12px var(--font-ui);
  color: var(--fg-muted-2);
}
.view-toggle button.active {
  background: #2e343c;
  color: var(--fg-bright);
}
.create {
  display: flex;
  gap: 6px;
}
.create input {
  font: inherit;
  padding: 0.5rem 0.6rem;
  border: 1px solid var(--line-5);
  border-radius: 6px;
  background: var(--bg-field);
  color: inherit;
  min-width: 220px;
}
.btn {
  all: unset;
  cursor: pointer;
  padding: 9px 15px;
  border-radius: 8px;
  font: 600 12.5px var(--font-ui);
}
.btn.primary {
  background: var(--green);
  color: var(--green-ink);
}
.btn.primary:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.banner {
  margin: 14px 28px 0;
  border-left: 4px solid var(--red);
  background: var(--bg-panel);
  padding: 0.75rem 1rem;
  border-radius: 0 6px 6px 0;
}
.state {
  padding: 40px 28px;
}
.scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 0 28px 30px;
}

table {
  border-collapse: collapse;
  width: 100%;
  margin-top: 14px;
}
th,
td {
  text-align: left;
  padding: 0.6rem;
  border-bottom: 1px solid var(--line-2);
}
th {
  font: 500 10px var(--font-mono);
  letter-spacing: 0.09em;
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
.small {
  font-size: 11px;
}

.board-row {
  display: flex;
  gap: 12px;
  align-items: flex-start;
  min-width: 720px;
  padding-top: 18px;
}
.board-col {
  flex: 1;
  min-width: 200px;
  background: var(--bg-column);
  border: 1px solid var(--line-3);
  border-radius: var(--radius-lg);
  overflow: hidden;
}
.board-col-head {
  padding: 10px 12px;
  border-bottom: 1px solid var(--line-2);
  display: flex;
  align-items: center;
  gap: 7px;
}
.board-col-title {
  flex: 1;
  font: 500 10px var(--font-mono);
  letter-spacing: 0.06em;
  color: var(--fg-muted-2);
}
.dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  flex: none;
}
.board-col-body {
  padding: 9px;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.board-card {
  all: unset;
  cursor: pointer;
  padding: 10px;
  border-radius: 8px;
  background: var(--bg-card);
  border: 1px solid var(--line-3);
  display: flex;
  flex-direction: column;
  gap: 6px;
  box-sizing: border-box;
}
.board-card-title {
  font: 500 12px/1.35 var(--font-ui);
}
.board-empty {
  padding: 4px 2px;
}
</style>
