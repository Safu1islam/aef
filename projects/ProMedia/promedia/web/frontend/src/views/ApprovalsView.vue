<script setup lang="ts">
import { onMounted, ref, watch } from "vue";
import { useRouter } from "vue-router";
import { api, ApiError } from "../api";

const router = useRouter();
const loading = ref(true);
const error = ref<string | null>(null);
const posts = ref<any[]>([]);
const statusFilter = ref("");

const STATUSES = ["queued", "approved", "publishing", "published", "rejected", "missed"];

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const result = await api.listPosts(statusFilter.value || undefined);
    posts.value = result.posts;
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "could not reach the server";
  } finally {
    loading.value = false;
  }
}
onMounted(load);
watch(statusFilter, load);
</script>

<template>
  <section class="approvals">
    <div class="head">
      <div>
        <h1>Approvals &amp; history</h1>
        <p class="muted">Agents queue; only the operator approves and publishes (F-2).</p>
      </div>
      <select v-model="statusFilter">
        <option value="">Any status</option>
        <option v-for="s in STATUSES" :key="s" :value="s">{{ s }}</option>
      </select>
    </div>

    <div v-if="loading" class="state muted">Loading…</div>
    <div v-else-if="error" class="state banner bad">{{ error }}</div>
    <div v-else-if="!posts.length" class="state muted">
      No posts{{ statusFilter ? ` with status '${statusFilter}'` : "" }} yet.
    </div>
    <div v-else class="scroll">
      <table>
        <thead><tr><th>Post</th><th>Status</th><th>Body</th><th>Queued by</th><th>When</th></tr></thead>
        <tbody>
          <tr v-for="p in posts" :key="p.id" class="row" tabindex="0"
              :aria-label="`Open post ${p.id}`"
              @click="router.push(`/posts/${p.id}`)"
              @keydown.enter="router.push(`/posts/${p.id}`)"
              @keydown.space.prevent="router.push(`/posts/${p.id}`)">
            <td class="mono">{{ p.id }}</td>
            <td>
              {{ p.status }}
              <div v-if="p.simulated" class="pill tone-red">SIMULATED — never published</div>
              <div v-if="p.status === 'publishing'" class="pill tone-red">stuck mid-publish</div>
              <div v-if="p.status === 'missed'" class="pill tone-red">window missed, escalated</div>
            </td>
            <td class="truncate">{{ p.body.slice(0, 80) }}</td>
            <td class="muted">{{ p.created_by }}</td>
            <td class="mono">{{ p.created_at.slice(0, 16).replace("T", " ") }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<style scoped>
.approvals {
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
select {
  font: inherit;
  padding: 0.45rem 0.55rem;
  border: 1px solid var(--line-5);
  border-radius: 6px;
  background: var(--bg-field);
  color: inherit;
}
.state {
  padding: 30px 0;
}
.scroll {
  flex: 1;
  min-height: 0;
  overflow: auto;
  padding: 14px 0 30px;
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
  vertical-align: top;
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
  max-width: 36ch;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.pill {
  margin-top: 4px;
}
</style>
