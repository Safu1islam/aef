<script setup lang="ts">
import { computed, onMounted, onUnmounted, ref } from "vue";
import { onBeforeRouteLeave } from "vue-router";
import { api, ApiError } from "../api";

const props = defineProps<{ projectId: string }>();

const loading = ref(true);
const error = ref<string | null>(null);
const saving = ref(false);
const rendering = ref(false);
const note = ref<string>("");

const project = ref<any>(null);
const versions = ref<any[]>([]);
const renders = ref<any[]>([]);
const assets = ref<any[]>([]);
const capabilities = ref<any>(null);

// Local, editable copy of the clip list. Nothing here is persisted until
// "Save version" runs set-edl — matching T-051's rule (every change goes
// through set-edl and creates a new version; nothing mutates in place),
// just with instant local reordering/editing instead of a full page
// round-trip per change, which a real client can afford and a form cannot.
const clips = ref<any[]>([]);
const selectedClipIndex = ref<number | null>(null);
const activeRoom = ref("Edit");
const roomNote = ref<string | null>(null);

function assetName(assetId: string): string {
  return assets.value.find((a) => a.id === assetId)?.original_filename ?? assetId;
}
function assetState(assetId: string): string {
  return assets.value.find((a) => a.id === assetId)?.state ?? "?";
}
// The timeline's flex-basis is a duration, not decoration — an invented
// number here would misrepresent clip length on the one screen whose
// acceptance criterion is "real durations, not sample data" (T-055 AC-1).
// A clip with no explicit out point (c.end) runs to the asset's own real
// duration_seconds (schema.sql, populated by ffprobe at ingest, A-15).
// Only when NEITHER is known do we fall back to an unweighted share.
function clipDuration(c: any): number {
  if (c.end != null) return c.end - c.start;
  const asset = assets.value.find((a) => a.id === c.asset_id);
  if (asset?.duration_seconds != null) return asset.duration_seconds - c.start;
  return 1;
}
function substitutionFor(transition: string): any {
  return capabilities.value?.known_substitutions?.find((s: any) => s.requested === transition) ?? null;
}

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const [proj, hist, out, assetList, caps] = await Promise.all([
      api.project(props.projectId),
      api.projectVersions(props.projectId),
      api.renders(props.projectId),
      api.listAssets(),
      api.mediaCapabilities(),
    ]);
    project.value = proj;
    versions.value = hist.versions;
    renders.value = out.renders;
    assets.value = assetList.assets;
    capabilities.value = caps;
    clips.value = proj.edl.clips.map((c: any) => ({ ...c }));
    selectedClipIndex.value = clips.value.length ? 0 : null;
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "could not reach the server";
  } finally {
    loading.value = false;
  }
}
onMounted(load);

const dirty = computed(() => JSON.stringify(clips.value) !== JSON.stringify(project.value?.edl.clips ?? []));

// Unsaved clip edits are held only in this component's local `clips` ref
// (see the comment above it) until "Save as new version" runs — so leaving
// the room, whether by an in-app navigation or closing/reloading the tab,
// silently discarded them. Neither is hypothetical: the timeline, the room
// tabs and the top nav all route away from here without passing through
// saveVersion.
function onBeforeUnload(e: BeforeUnloadEvent) {
  if (!dirty.value) return;
  e.preventDefault();
  e.returnValue = "";
}
onBeforeRouteLeave(() => {
  if (!dirty.value) return true;
  return window.confirm("You have unsaved clip changes. Leave without saving as a new version?");
});
onMounted(() => window.addEventListener("beforeunload", onBeforeUnload));
onUnmounted(() => window.removeEventListener("beforeunload", onBeforeUnload));

function moveClip(i: number, dir: -1 | 1) {
  const j = i + dir;
  if (j < 0 || j >= clips.value.length) return;
  const arr = clips.value;
  [arr[i], arr[j]] = [arr[j], arr[i]];
  if (selectedClipIndex.value === i) selectedClipIndex.value = j;
  else if (selectedClipIndex.value === j) selectedClipIndex.value = i;
}
function removeClip(i: number) {
  clips.value.splice(i, 1);
  if (selectedClipIndex.value === i) selectedClipIndex.value = clips.value.length ? 0 : null;
}
function addClip() {
  if (!assets.value.length) return;
  clips.value.push({
    asset_id: assets.value[0].id,
    start: 0,
    end: null,
    speed: 1,
    effect: "none",
    transition_in: "cut",
    transition_duration: 0.5,
    volume: 1,
    mute: false,
  });
  selectedClipIndex.value = clips.value.length - 1;
}

async function saveVersion() {
  if (!clips.value.length) {
    error.value = "an EDL needs at least one clip";
    return;
  }
  saving.value = true;
  error.value = null;
  try {
    const edl = { ...project.value.edl, clips: clips.value };
    // expected_version pins this write to the version the local `clips`
    // copy was actually loaded from (R-010): without it, an agent and this
    // tab editing the same project's EDL in the minutes this room can stay
    // open silently overwrite each other with no signal to either side.
    await api.setEdl(props.projectId, edl, note.value, project.value.edl_version);
    note.value = "";
    await load();
  } catch (err) {
    if (err instanceof ApiError && err.code === "VALIDATION" && "current_version" in err.detail) {
      error.value =
        `Someone saved v${err.detail.current_version} while you were editing v${err.detail.expected_version}` +
        " — your changes were NOT saved. Reload to see the newer version (it's in History below either" +
        " way), then reapply your edit.";
    } else {
      error.value = err instanceof ApiError ? err.message : "could not save";
    }
  } finally {
    saving.value = false;
  }
}

const quality = ref("");
async function render() {
  rendering.value = true;
  error.value = null;
  try {
    await api.renderProject(props.projectId, quality.value || undefined);
    await load();
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "render failed";
  } finally {
    rendering.value = false;
  }
}

const latestRender = computed(() => renders.value[0] ?? null);
const sourceAssetId = computed(() =>
  selectedClipIndex.value !== null ? clips.value[selectedClipIndex.value]?.asset_id : null,
);

function pickRoom(name: string) {
  if (name === "Edit") {
    activeRoom.value = name;
    roomNote.value = null;
    return;
  }
  roomNote.value = `${name} room is not built yet — see task T-060.`;
}
</script>

<template>
  <section class="editor">
    <div v-if="loading" class="state muted">Loading…</div>
    <div v-else-if="error && !project" class="state banner bad">{{ error }}</div>
    <template v-else>
      <div class="roombar">
        <button
          v-for="r in ['Edit', 'Color', 'Audio', 'Captions', 'Effects', 'Deliver']"
          :key="r"
          class="room-btn"
          :class="{ active: activeRoom === r }"
          @click="pickRoom(r)"
        >
          {{ r }}
        </button>
        <div class="spacer" />
        <span class="mono muted title-text">{{ project.title }} · v{{ project.edl_version }}</span>
      </div>
      <div v-if="roomNote" class="room-note">{{ roomNote }}</div>

      <div class="workarea">
        <div class="monitors">
          <div class="monitor">
            <div class="monitor-label mono">SOURCE</div>
            <div class="monitor-frame">
              <video
                v-if="sourceAssetId && assetState(sourceAssetId) === 'stored'"
                :src="`/media/${sourceAssetId}/file`"
                controls
                preload="metadata"
              />
              <div v-else class="monitor-empty muted">
                {{ sourceAssetId ? "media not available" : "select a clip" }}
              </div>
            </div>
          </div>
          <div class="monitor program">
            <div class="monitor-label mono program-label">PROGRAM · proxy preview</div>
            <div class="monitor-frame">
              <video v-if="latestRender?.output_exists" :src="`/renders/${latestRender.id}/file`" controls preload="metadata" />
              <div v-else class="monitor-empty muted">nothing rendered yet</div>
            </div>
            <div v-if="latestRender?.substitutions?.length" class="banner">
              <strong>This render did not do everything as asked.</strong>
              <ul>
                <li v-for="s in latestRender.substitutions" :key="s.requested">
                  asked for <span class="mono">{{ s.requested }}</span>; rendered
                  <strong>{{ s.rendered }}</strong> — {{ s.why }} ({{ s.fabrication }})
                </li>
              </ul>
            </div>
          </div>
        </div>

        <div class="render-row">
          <select v-model="quality">
            <option value="">default quality</option>
            <option v-for="q in capabilities?.qualities ?? []" :key="q" :value="q">{{ q }}</option>
          </select>
          <button class="btn primary" :disabled="rendering || !clips.length" @click="render">
            {{ rendering ? "Rendering…" : "Render" }}
          </button>
          <span v-if="!clips.length" class="muted">Add at least one clip below.</span>
          <span v-if="dirty" class="muted warn">Unsaved clip changes — save a version before rendering to include them.</span>
        </div>

        <div class="timeline">
          <div class="timeline-head mono muted">TIMELINE</div>
          <div class="timeline-track">
            <button
              v-for="(c, i) in clips"
              :key="i"
              class="tl-clip"
              :class="{ selected: selectedClipIndex === i }"
              :style="{ flexGrow: Math.max(clipDuration(c), 1) }"
              @click="selectedClipIndex = i"
            >
              <span class="tl-clip-name">{{ assetName(c.asset_id) }}</span>
              <span v-if="c.effect !== 'none'" class="tl-badge">{{ c.effect }}</span>
              <span v-if="c.transition_in !== 'cut'" class="tl-badge" :class="{ warn: substitutionFor(c.transition_in) }">
                {{ c.transition_in }}
              </span>
            </button>
            <div v-if="!clips.length" class="muted tl-empty">No clips yet — add one below.</div>
          </div>
        </div>

        <div class="clip-editor">
          <div class="clip-editor-head">
            <h2>Clips</h2>
            <button class="btn" :disabled="!assets.length" @click="addClip">+ Add clip</button>
          </div>
          <p v-if="!assets.length" class="muted">
            No media ingested yet. <router-link to="/media">Add some</router-link>.
          </p>
          <div v-for="(c, i) in clips" :key="i" class="clip-row" :class="{ selected: selectedClipIndex === i }"
               tabindex="0" role="button" :aria-label="`Preview clip ${i + 1} in the source monitor`"
               @click="selectedClipIndex = i"
               @keydown.enter="selectedClipIndex = i"
               @keydown.space.prevent="selectedClipIndex = i">
            <div class="clip-row-top">
              <select v-model="c.asset_id" @click.stop>
                <option v-for="a in assets" :key="a.id" :value="a.id">
                  {{ a.original_filename }}{{ a.state !== "stored" ? ` (${a.state})` : "" }}
                </option>
              </select>
              <div class="reorder">
                <button title="Move earlier" :disabled="i === 0" @click.stop="moveClip(i, -1)">&uarr;</button>
                <button title="Move later" :disabled="i === clips.length - 1" @click.stop="moveClip(i, 1)">&darr;</button>
              </div>
              <button class="danger" title="Remove clip" @click.stop="removeClip(i)">Remove</button>
            </div>
            <div class="clip-row-fields" @click.stop>
              <label>In (s)<input type="number" step="any" v-model.number="c.start" /></label>
              <label>Out (s)<input type="number" step="any" v-model.number="c.end" placeholder="end" /></label>
              <label>Speed<input type="number" step="any" min="0.1" max="10" v-model.number="c.speed" /></label>
              <label>Volume<input type="number" step="any" min="0" v-model.number="c.volume" /></label>
              <label>Effect
                <select v-model="c.effect">
                  <option v-for="e in capabilities?.effects ?? []" :key="e" :value="e">{{ e }}</option>
                </select>
              </label>
              <label>Transition in
                <select v-model="c.transition_in">
                  <option v-for="t in capabilities?.transitions ?? []" :key="t" :value="t">
                    {{ t }}{{ substitutionFor(t) ? ` — renders as ${substitutionFor(t).rendered}` : "" }}
                  </option>
                </select>
              </label>
              <label class="checkbox"><input type="checkbox" v-model="c.mute" /> Mute</label>
            </div>
          </div>

          <div class="save-row">
            <input v-model="note" type="text" placeholder="What changed (optional)" />
            <button class="btn primary" :disabled="saving || !clips.length" @click="saveVersion">
              {{ saving ? "Saving…" : "Save as new version" }}
            </button>
          </div>
          <div v-if="error" class="banner bad">{{ error }}</div>
        </div>

        <details class="json-escape">
          <summary>The edit, as JSON (escape hatch — the fastest way to read exactly what an agent changed)</summary>
          <pre class="mono">{{ JSON.stringify(project.edl, null, 2) }}</pre>
        </details>

        <div class="history">
          <h2>History</h2>
          <table>
            <thead><tr><th>Version</th><th>By</th><th>Note</th><th>When</th></tr></thead>
            <tbody>
              <tr v-for="v in versions" :key="v.version" :class="{ current: v.version === project.edl_version }">
                <td class="mono">v{{ v.version }}</td>
                <td><span class="mono">{{ v.authored_kind }}</span> {{ v.authored_by }}</td>
                <td>{{ v.note }}</td>
                <td class="mono">{{ v.authored_at.slice(0, 16).replace("T", " ") }}</td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.editor {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.state {
  padding: 40px 28px;
}

.roombar {
  height: 34px;
  flex: none;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 0 12px;
  background: var(--bg-toolbar);
  border-bottom: 1px solid var(--line-4);
}
.room-btn {
  all: unset;
  cursor: pointer;
  padding: 4px 11px;
  border-radius: 6px;
  font: 500 12px var(--font-ui);
  color: var(--fg-muted-2);
  border-bottom: 2px solid transparent;
}
.room-btn.active {
  background: #1b1f25;
  color: var(--fg-bright);
  border-bottom-color: var(--green);
}
.spacer {
  flex: 1;
}
.title-text {
  font-size: 11px;
}
.room-note {
  padding: 6px 12px;
  font: 400 11.5px var(--font-ui);
  color: var(--amber);
  background: var(--amber-wash-soft);
  border-bottom: 1px solid var(--amber-border);
}

.workarea {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 20px 32px;
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.monitors {
  display: grid;
  grid-template-columns: 1fr 1.3fr;
  gap: 10px;
}
.monitor-label {
  font-size: 10px;
  letter-spacing: 0.07em;
  color: var(--fg-muted-2);
  margin-bottom: 4px;
}
.program-label {
  color: var(--green);
}
.monitor-frame {
  aspect-ratio: 16 / 9;
  background: #000;
  border-radius: 6px;
  border: 1px solid var(--line-3);
  overflow: hidden;
  display: flex;
  align-items: center;
  justify-content: center;
}
.monitor-frame video {
  width: 100%;
  height: 100%;
}
.monitor-empty {
  font-size: 12px;
}

.render-row {
  display: flex;
  gap: 0.6rem;
  align-items: center;
  flex-wrap: wrap;
}
select,
input[type="text"],
input[type="number"] {
  font: inherit;
  padding: 0.45rem 0.55rem;
  border: 1px solid var(--line-5);
  border-radius: 6px;
  background: var(--bg-field);
  color: inherit;
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
.btn.primary {
  background: var(--green);
  color: var(--green-ink);
  border-color: var(--green);
  font-weight: 600;
}
.btn:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.warn {
  color: var(--amber);
}

.timeline-head {
  font-size: 10px;
  letter-spacing: 0.08em;
  margin-bottom: 4px;
}
.timeline-track {
  display: flex;
  gap: 2px;
  min-height: 46px;
  background: #0b0d10;
  border: 1px solid var(--line-3);
  border-radius: 6px;
  padding: 4px;
}
.tl-clip {
  all: unset;
  cursor: pointer;
  box-sizing: border-box;
  flex-basis: 0;
  min-width: 60px;
  background: rgba(159, 232, 112, 0.15);
  border: 1px solid rgba(159, 232, 112, 0.42);
  border-radius: 3px;
  padding: 4px 6px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  overflow: hidden;
}
.tl-clip.selected {
  outline: var(--focus-ring);
  outline-offset: -2px;
}
.tl-clip-name {
  font: 500 10px var(--font-ui);
  color: #d6f7bc;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.tl-badge {
  font: 500 8.5px var(--font-mono);
  color: #8b9199;
  background: rgba(0, 0, 0, 0.3);
  padding: 0 3px;
  border-radius: 2px;
  width: fit-content;
}
.tl-badge.warn {
  color: var(--amber);
}
.tl-empty {
  padding: 10px;
  font-size: 12px;
}

.clip-editor {
  background: var(--bg-panel);
  border: 1px solid var(--line-3);
  border-radius: var(--radius-xl);
  padding: 14px 16px;
}
.clip-editor-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 10px;
}
.clip-editor-head h2 {
  margin: 0;
  flex: 1;
  font: 600 13.5px var(--font-ui);
}
.clip-row {
  padding: 10px;
  border-radius: 8px;
  border: 1px solid var(--line-2);
  margin-bottom: 8px;
  cursor: pointer;
}
.clip-row.selected {
  border-color: rgba(159, 232, 112, 0.4);
  background: rgba(159, 232, 112, 0.04);
}
.clip-row-top {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
.clip-row-top select {
  flex: 1;
}
.reorder {
  display: flex;
  gap: 2px;
}
.reorder button {
  all: unset;
  cursor: pointer;
  padding: 0.3rem 0.5rem;
  border: 1px solid var(--line-5);
  border-radius: 5px;
  color: var(--fg-dim);
}
.reorder button:disabled {
  opacity: 0.35;
  cursor: not-allowed;
}
button.danger {
  all: unset;
  cursor: pointer;
  padding: 0.3rem 0.6rem;
  border: 1px solid var(--red-border);
  border-radius: 5px;
  color: var(--red-soft);
  font-size: 11.5px;
}
.clip-row-fields {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}
.clip-row-fields label {
  display: flex;
  flex-direction: column;
  gap: 3px;
  font: 400 10.5px var(--font-ui);
  color: var(--fg-muted-2);
}
.clip-row-fields input,
.clip-row-fields select {
  width: 7rem;
}
.clip-row-fields label.checkbox {
  flex-direction: row;
  align-items: center;
  gap: 5px;
  align-self: flex-end;
}

.save-row {
  display: flex;
  gap: 8px;
  margin-top: 10px;
}
.save-row input {
  flex: 1;
}

.banner {
  border-left: 4px solid var(--amber);
  background: var(--bg-panel);
  padding: 0.7rem 0.9rem;
  border-radius: 0 6px 6px 0;
  font-size: 12.5px;
}
.banner.bad {
  border-left-color: var(--red);
  margin-top: 10px;
}

.json-escape {
  background: var(--bg-panel);
  border: 1px solid var(--line-3);
  border-radius: var(--radius-lg);
  padding: 10px 14px;
}
.json-escape summary {
  cursor: pointer;
  font: 500 12px var(--font-ui);
  color: var(--fg-muted-2);
}
.json-escape pre {
  margin: 10px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-size: 11px;
  max-height: 300px;
  overflow: auto;
}

.history table {
  border-collapse: collapse;
  width: 100%;
}
.history th,
.history td {
  text-align: left;
  padding: 0.5rem 0.6rem;
  border-bottom: 1px solid var(--line-2);
  font-size: 12px;
}
.history th {
  font: 500 10px var(--font-mono);
  color: var(--fg-muted-3);
}
.history tr.current td {
  background: var(--bg-card);
}
</style>
