<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, ApiError } from "../api";

const router = useRouter();
const loading = ref(true);
const error = ref<string | null>(null);
const assets = ref<any[]>([]);
const queued = ref<any[]>([]);
const q = ref("");
const verdictFilter = ref("");

const authorship = ref("");
const thirdParty = ref("");
const file = ref<File | null>(null);
const uploading = ref(false);
const uploadError = ref<string | null>(null);

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const [listing, queue] = await Promise.all([api.listAssets(), api.ingestQueue()]);
    assets.value = listing.assets;
    queued.value = queue.queued;
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "could not reach the server";
  } finally {
    loading.value = false;
  }
}
onMounted(load);

const filtered = computed(() =>
  assets.value.filter((a) => {
    if (q.value && !a.original_filename?.toLowerCase().includes(q.value.toLowerCase())) return false;
    if (verdictFilter.value === "none" && a.latest_verdict) return false;
    if (verdictFilter.value && verdictFilter.value !== "none" && a.latest_verdict !== verdictFilter.value) return false;
    return true;
  }),
);

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement;
  file.value = input.files?.[0] ?? null;
}

// The /media route renders its refusal as an HTML page (error.html), not
// JSON — there is no fetch-friendly error body. Parsing the actual DOM
// (error.error in <h1>, error.message in .banner.bad) surfaces the SAME
// reason the CLI and the Jinja2 workspace show, instead of a single
// hardcoded guess that collapsed every VALIDATION refusal into "a rights
// declaration is required" and silently lost every other refusal's message
// — including the F-7 storage-ceiling refusal, a first-class expected
// outcome the operator needs to be able to act on.
function parseErrorPage(html: string): string | null {
  try {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const code = doc.querySelector("h1")?.textContent?.trim();
    const message = doc.querySelector(".banner.bad")?.textContent?.trim();
    if (!message) return null;
    return code ? `${code}: ${message}` : message;
  } catch {
    return null;
  }
}

// Reuses T-050's exact /media upload route rather than a second
// implementation: this is adapter-level staging (write bytes to a temp
// path, then call the `ingest` operation), not a capability of its own, and
// it already refuses without a declaration exactly as the CLI does.
async function upload() {
  if (!file.value) return;
  uploading.value = true;
  uploadError.value = null;
  try {
    const form = new FormData();
    form.append("file", file.value);
    form.append("authorship", authorship.value);
    form.append("third_party_material", thirdParty.value);
    const response = await fetch("/media", { method: "POST", body: form, credentials: "same-origin" });
    if (!response.ok) {
      const text = await response.text();
      throw new Error(parseErrorPage(text) ?? `upload failed (HTTP ${response.status})`);
    }
    const assetId = new URL(response.url).pathname.split("/").filter(Boolean).pop();
    if (assetId) {
      router.push(`/media/${assetId}`);
    } else {
      // Redirect landed somewhere unparseable — surface that rather than
      // doing nothing at all and leaving the operator staring at a spinner
      // that quietly stopped.
      uploadError.value = "upload finished, but the asset id could not be read from the redirect — reloading the list.";
      await load();
    }
  } catch (err) {
    uploadError.value = err instanceof Error ? err.message : "upload failed";
  } finally {
    uploading.value = false;
  }
}

function fmtMB(bytes: number): string {
  return (bytes / 1048576).toFixed(1);
}
</script>

<template>
  <section class="media">
    <div class="head">
      <h1>Media library</h1>
      <p class="muted">{{ assets.length }} asset{{ assets.length === 1 ? "" : "s" }}</p>
    </div>

    <form class="upload" @submit.prevent="upload">
      <input type="file" @change="onFileChange" required />
      <div class="decl">
        <label><input type="radio" value="operator_original" v-model="authorship" required /> My own recording</label>
        <label><input type="radio" value="third_party" v-model="authorship" /> Contains third-party material</label>
        <label><input type="radio" value="unknown" v-model="authorship" /> Unknown</label>
      </div>
      <input
        v-if="authorship === 'third_party'"
        v-model="thirdParty"
        type="text"
        placeholder="What third-party material (one line each, separated by ;)"
      />
      <button class="btn primary" type="submit" :disabled="uploading || !file || !authorship">
        {{ uploading ? "Uploading…" : "Add media" }}
      </button>
      <span v-if="uploadError" class="banner bad">{{ uploadError }}</span>
    </form>

    <div class="filters">
      <input v-model="q" type="text" placeholder="Search filename…" />
      <select v-model="verdictFilter">
        <option value="">Any verdict</option>
        <option value="none">No verdict yet</option>
        <option value="PERMITTED">PERMITTED</option>
        <option value="BLOCKED">BLOCKED</option>
        <option value="ESCALATE">ESCALATE</option>
      </select>
    </div>

    <div v-if="queued.length" class="banner">
      <strong>{{ queued.length }} ingest{{ queued.length === 1 ? "" : "s" }} queued</strong> — refused by the
      storage ceiling, will resume automatically as space frees up.
      <ul><li v-for="qi in queued" :key="qi.id" class="mono small">{{ qi.source_path }}</li></ul>
    </div>

    <div v-if="loading" class="state muted">Loading…</div>
    <div v-else-if="error" class="state banner bad">{{ error }}</div>
    <div v-else-if="!filtered.length" class="state muted">No media matches.</div>
    <div v-else class="scroll">
      <table>
        <thead><tr><th>Asset</th><th>Rights</th><th>Media</th><th>Size</th></tr></thead>
        <tbody>
          <tr v-for="a in filtered" :key="a.id" class="row" tabindex="0"
              :aria-label="`Open asset ${a.original_filename}`"
              @click="router.push(`/media/${a.id}`)"
              @keydown.enter="router.push(`/media/${a.id}`)"
              @keydown.space.prevent="router.push(`/media/${a.id}`)">
            <td>{{ a.original_filename }}<div class="muted mono small">{{ a.id }}</div></td>
            <td><span class="pill" :class="`tone-${a.latest_verdict === 'PERMITTED' ? 'green' : a.latest_verdict === 'BLOCKED' ? 'red' : a.latest_verdict === 'ESCALATE' ? 'amber' : 'grey'}`">
              {{ a.latest_verdict ?? "NO VERDICT" }}
            </span></td>
            <td class="mono">{{ a.state }}</td>
            <td class="mono">{{ fmtMB(a.byte_size) }} MB</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.media {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 20px 28px 0;
}
.head h1 {
  margin: 0;
  font: 600 20px var(--font-ui);
}
.upload {
  display: flex;
  gap: 10px;
  align-items: center;
  flex-wrap: wrap;
  background: var(--bg-panel);
  border: 1px solid var(--line-3);
  border-radius: var(--radius-lg);
  padding: 12px;
  margin: 14px 0;
}
.decl {
  display: flex;
  gap: 10px;
  font-size: 12px;
  flex-wrap: wrap;
}
.decl label {
  display: flex;
  align-items: center;
  gap: 4px;
}
.filters {
  display: flex;
  gap: 8px;
  margin-bottom: 14px;
}
input[type="text"],
select {
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
  padding: 0.55rem 1rem;
  border-radius: 7px;
  font: 600 12px var(--font-ui);
}
.btn.primary {
  background: var(--green);
  color: var(--green-ink);
}
.btn.primary:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}
.banner {
  border-left: 4px solid var(--amber);
  background: var(--bg-panel);
  padding: 0.7rem 0.9rem;
  border-radius: 0 6px 6px 0;
  margin-bottom: 12px;
  font-size: 12.5px;
}
.banner.bad {
  border-left-color: var(--red);
}
.state {
  padding: 30px 0;
}
.scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding-bottom: 24px;
}
table {
  border-collapse: collapse;
  width: 100%;
}
th,
td {
  text-align: left;
  padding: 0.55rem 0.6rem;
  border-bottom: 1px solid var(--line-2);
  font-size: 12.5px;
}
th {
  font: 500 10px var(--font-mono);
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
</style>
