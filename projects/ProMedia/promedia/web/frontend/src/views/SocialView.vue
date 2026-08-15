<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, ApiError } from "../api";

const router = useRouter();
const loading = ref(true);
const error = ref<string | null>(null);

const TABS = ["Accounts", "Queue", "Publications", "Composer", "Insights"] as const;
type Tab = (typeof TABS)[number];
const tab = ref<Tab>("Accounts");
const tabRefs = ref<Record<string, HTMLButtonElement | null>>({});

function selectTab(t: Tab) {
  tab.value = t;
}

// Standard ARIA tablist keyboard model (WAI-ARIA APG): Left/Right/Home/End
// move focus AND selection between tabs, matching the top menu bar's own
// Escape/Arrow support (App.vue) rather than inventing a second convention.
function onTabKey(event: KeyboardEvent, index: number) {
  let next = index;
  if (event.key === "ArrowRight") next = (index + 1) % TABS.length;
  else if (event.key === "ArrowLeft") next = (index - 1 + TABS.length) % TABS.length;
  else if (event.key === "Home") next = 0;
  else if (event.key === "End") next = TABS.length - 1;
  else return;
  event.preventDefault();
  const t = TABS[next];
  tab.value = t;
  tabRefs.value[t]?.focus();
}

const accounts = ref<any[]>([]);
const posts = ref<any[]>([]);
const publications = ref<any[]>([]);

// Posts and publications carry only account_id / are already joined to
// platform respectively (publications.platform is a real column — see
// promedia/core/schema.sql). accountsById lets the Queue table show a
// platform/handle the list-posts response does not itself carry, without a
// second operation: it is a client-side join of two already-loaded real
// responses, not an invented field (frontend-brief.md rule 2 — a screen
// needing something no operation provides gets a new FIELD, not a query; this
// needs no new field at all, both pieces already exist).
const accountsById = computed<Record<string, any>>(() =>
  Object.fromEntries(accounts.value.map((a) => [a.id, a])),
);
function accountLabel(accountId: string): string {
  const a = accountsById.value[accountId];
  return a ? `${a.platform}/${a.handle}` : accountId;
}

const platform = ref("x");
const handle = ref("");
const secret = ref("");
const connecting = ref(false);
const connectError = ref<string | null>(null);
const connectNote = ref<string | null>(null);

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const [acc, postList, pubs] = await Promise.all([api.listAccounts(), api.listPosts(), api.publications()]);
    accounts.value = acc.accounts;
    posts.value = postList.posts;
    publications.value = pubs.publications;
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "could not reach the server";
  } finally {
    loading.value = false;
  }
}
onMounted(load);

function accountTone(status: string): string {
  if (status === "connected") return "green";
  if (status === "error") return "red";
  return "amber"; // 'disconnected' — recorded but not currently reachable, not a fault
}

async function connect() {
  if (!handle.value.trim()) return;
  connecting.value = true;
  connectError.value = null;
  connectNote.value = null;
  try {
    const result = await api.connectAccount(platform.value, handle.value.trim(), secret.value || undefined);
    connectNote.value = result.note ?? null;
    handle.value = "";
    secret.value = "";
    await load();
  } catch (err) {
    connectError.value = err instanceof ApiError ? err.message : "could not connect";
  } finally {
    connecting.value = false;
  }
}
</script>

<template>
  <section class="social">
    <div class="head">
      <div>
        <h1>Social integration</h1>
        <p class="muted">Accounts, the publish queue, and what actually went out.</p>
      </div>
      <div class="tabs" role="tablist" aria-label="Social integration sections">
        <button
          v-for="(t, i) in TABS"
          :key="t"
          :id="`social-tab-${t}`"
          :ref="(el) => (tabRefs[t] = el as HTMLButtonElement | null)"
          role="tab"
          :aria-selected="tab === t"
          :aria-controls="`social-panel-${t}`"
          :tabindex="tab === t ? 0 : -1"
          :class="{ active: tab === t }"
          @click="selectTab(t)"
          @keydown="onTabKey($event, i)"
        >{{ t }}</button>
      </div>
    </div>

    <div v-if="loading" class="state muted">Loading…</div>
    <div v-else-if="error" class="state banner bad">{{ error }}</div>
    <div
      v-else
      class="body"
      role="tabpanel"
      :id="`social-panel-${tab}`"
      :aria-labelledby="`social-tab-${tab}`"
      tabindex="0"
    >
      <template v-if="tab === 'Accounts'">
        <div class="cards">
          <div v-for="a in accounts" :key="a.id" class="card">
            <div class="card-head">
              <strong>{{ a.platform }}</strong>
              <span class="pill" :class="`tone-${accountTone(a.status)}`">{{ a.status }}</span>
            </div>
            <div class="muted mono small">{{ a.handle }}</div>
            <div class="muted mono small">{{ a.credential_ref }}</div>
          </div>
          <div v-if="!accounts.length" class="muted">No accounts connected.</div>
        </div>
        <form class="connect" @submit.prevent="connect">
          <label class="sr-only" for="social-connect-platform">Platform</label>
          <select id="social-connect-platform" v-model="platform">
            <option value="x">x</option>
            <option value="linkedin">linkedin</option>
          </select>
          <label class="sr-only" for="social-connect-handle">Handle</label>
          <input id="social-connect-handle" v-model="handle" type="text" placeholder="handle" required />
          <label class="sr-only" for="social-connect-secret">Credential (optional to reconnect)</label>
          <input
            id="social-connect-secret"
            v-model="secret"
            type="password"
            placeholder="credential (optional to reconnect)"
            autocomplete="new-password"
          />
          <button class="btn primary" type="submit" :disabled="connecting">Connect</button>
        </form>
        <div v-if="connectError" class="banner bad">{{ connectError }}</div>
        <div v-if="connectNote" class="banner">{{ connectNote }}</div>
      </template>

      <template v-else-if="tab === 'Queue'">
        <table>
          <thead><tr><th>Post</th><th>Account</th><th>Status</th><th>Body</th><th>Scheduled</th></tr></thead>
          <tbody>
            <tr v-for="p in posts" :key="p.id" class="row" tabindex="0"
                :aria-label="`Open post ${p.id}`"
                @click="router.push(`/posts/${p.id}`)"
                @keydown.enter="router.push(`/posts/${p.id}`)"
                @keydown.space.prevent="router.push(`/posts/${p.id}`)">
              <td class="mono">{{ p.id }}</td>
              <td class="mono">{{ accountLabel(p.account_id) }}</td>
              <td>{{ p.status }}</td>
              <td class="truncate">{{ p.body.slice(0, 80) }}</td>
              <td class="mono">{{ p.scheduled_at ? p.scheduled_at.slice(0, 16).replace("T", " ") : "—" }}</td>
            </tr>
          </tbody>
        </table>
        <div v-if="!posts.length" class="muted">Nothing queued.</div>
      </template>

      <template v-else-if="tab === 'Publications'">
        <table>
          <thead><tr><th>Post</th><th>Platform</th><th>Platform post id</th><th>Published</th><th></th></tr></thead>
          <tbody>
            <tr v-for="pub in publications" :key="pub.id">
              <td class="mono"><router-link :to="`/posts/${pub.post_id}`">{{ pub.post_id }}</router-link></td>
              <td class="mono">{{ pub.platform }}</td>
              <td class="mono">{{ pub.platform_post_id }}</td>
              <td class="mono">{{ pub.published_at.slice(0, 16).replace("T", " ") }}</td>
              <td><span v-if="pub.simulated" class="pill tone-red">SIMULATED — never published</span></td>
            </tr>
          </tbody>
        </table>
        <div v-if="!publications.length" class="muted">Nothing published yet.</div>
      </template>

      <template v-else-if="tab === 'Composer'">
        <div class="card gap-card">
          <div class="card-head"><strong>Not available in this client</strong> <span class="pill tone-amber">no backing operation</span></div>
          <p class="muted">
            This system does not yet prepare cross-platform variants — drafting or adapting one
            piece of media/text per target platform from a single brief. No operation in the
            registry does this today, so this tab shows nothing rather than a form that would
            silently do less than it implies. Drafting and editing a post's body happens through
            <code>queue-post</code> today (agent-callable, no UI form yet); per-platform variant
            generation would need its own registered operation before any screen can honestly
            offer it (frontend-brief.md rule 2).
          </p>
        </div>
      </template>

      <template v-else>
        <div class="card gap-card">
          <div class="card-head"><strong>Not available in this client</strong> <span class="pill tone-amber">no backing operation</span></div>
          <p class="muted">
            No analytics beyond what informs the operator's own scheduling decisions exist in this
            system — project.md section 4 names that as explicitly out of scope, and section 3's
            only publishing-related success measure (S6) is about missed scheduled windows, not
            engagement or reach. There is no impressions/engagement/follower data anywhere in this
            database. Nothing is estimated to fill this tab; it stays empty rather than showing a
            plausible-looking number nobody measured.
          </p>
        </div>
      </template>
    </div>
  </section>
</template>

<style scoped>
.social {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 20px 28px 0;
}
.head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
  flex-wrap: wrap;
}
h1 {
  margin: 0;
  font: 600 20px var(--font-ui);
}
.tabs {
  display: flex;
  gap: 2px;
}
.tabs button {
  all: unset;
  cursor: pointer;
  padding: 8px 13px;
  font: 500 12.5px var(--font-ui);
  color: var(--fg-muted-2);
  border-bottom: 2px solid transparent;
}
.tabs button.active {
  color: var(--fg-bright);
  border-bottom-color: var(--green);
}
.tabs button:focus-visible {
  outline: var(--focus-ring);
  outline-offset: 2px;
}
.body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 16px 0 32px;
}
.body:focus-visible {
  outline: none;
}
.state {
  padding: 30px 0;
}
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
  gap: 12px;
  margin-bottom: 16px;
}
.card {
  border-radius: var(--radius-lg);
  background: var(--bg-panel);
  border: 1px solid var(--line-3);
  padding: 12px 14px;
}
.gap-card {
  max-width: 68ch;
}
.gap-card p {
  line-height: 1.55;
}
.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 6px;
}
.small {
  font-size: 11px;
}
.connect {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.connect input,
.connect select {
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
  border-radius: 7px;
  font: 600 12px var(--font-ui);
}
.btn.primary {
  background: var(--green);
  color: var(--green-ink);
}
table {
  border-collapse: collapse;
  width: 100%;
}
th,
td {
  text-align: left;
  padding: 0.5rem 0.6rem;
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
.truncate {
  max-width: 40ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.banner {
  border-left: 4px solid var(--amber);
  background: var(--bg-panel);
  padding: 0.7rem 0.9rem;
  border-radius: 0 6px 6px 0;
  margin-top: 10px;
}
.banner.bad {
  border-left-color: var(--red);
}
</style>
