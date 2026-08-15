<script setup lang="ts">
import { onMounted, onUnmounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";
import { MENUS } from "./menu";
import { api, ApiError } from "./api";

const route = useRoute();
const router = useRouter();

const openMenu = ref<string | null>(null);
const headerEl = ref<HTMLElement | null>(null);

function toggleMenu(label: string) {
  openMenu.value = openMenu.value === label ? null : label;
}
function hoverMenu(label: string) {
  if (openMenu.value && openMenu.value !== label) openMenu.value = label;
}
function closeMenu() {
  openMenu.value = null;
}
function go(path: string) {
  closeMenu();
  router.push(path);
}

function onDocClick(e: MouseEvent) {
  if (openMenu.value && headerEl.value && !headerEl.value.contains(e.target as Node)) {
    closeMenu();
  }
}
function onDocKeydown(e: KeyboardEvent) {
  if (e.key === "Escape" && openMenu.value) {
    closeMenu();
  }
}
function onMenuKeydown(e: KeyboardEvent, label: string) {
  const focusable = () =>
    Array.from(headerEl.value?.querySelectorAll<HTMLElement>(".menu-panel .menu-item") ?? []);
  if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    if (openMenu.value !== label) {
      openMenu.value = label;
      return;
    }
    const els = focusable();
    if (!els.length) return;
    const current = els.indexOf(document.activeElement as HTMLElement);
    const next = e.key === "ArrowDown" ? (current + 1) % els.length : (current - 1 + els.length) % els.length;
    els[next]?.focus();
  } else if (e.key === "Enter" || e.key === " ") {
    if (openMenu.value !== label) {
      e.preventDefault();
      openMenu.value = label;
    }
  }
}

// Real data, not decoration: storage pressure (the dashboard's original
// headline stat, DR-004/DR-017 both keep numbers honest) and pending posts
// (the same set /approvals shows). Presence avatars come from the C-19
// entity lock table — an agent actually holding a lock right now, not a
// fabricated "who's online" feed this system has no way to know.
const storagePct = ref<number | null>(null);
const pendingCount = ref(0);
const presence = ref<{ initials: string; agent: string }[]>([]);
const loadError = ref<string | null>(null);

function initialsFor(agent: string): string {
  const cleaned = agent.replace(/^claude-code-session-?/, "");
  return (cleaned.slice(0, 2) || "AI").toUpperCase();
}

async function loadHeaderData() {
  try {
    const [status, posts, locks] = await Promise.all([api.status(), api.listPosts(), api.locks()]);
    loadError.value = null;
    storagePct.value = Math.round((status.storage?.fraction_used ?? 0) * 100);
    pendingCount.value = posts.posts.filter((p: any) =>
      ["queued", "approved", "publishing"].includes(p.status),
    ).length;
    const seen = new Set<string>();
    presence.value = (locks.locks ?? [])
      .filter((l: any) => {
        if (seen.has(l.agent)) return false;
        seen.add(l.agent);
        return true;
      })
      .slice(0, 4)
      .map((l: any) => ({ initials: initialsFor(l.agent), agent: l.agent }));
  } catch (err) {
    loadError.value = err instanceof ApiError ? err.message : "could not reach the server";
  }
}

let refreshTimer: ReturnType<typeof setInterval> | undefined;

onMounted(() => {
  document.addEventListener("click", onDocClick, true);
  document.addEventListener("keydown", onDocKeydown);
  loadHeaderData();
  // Locks and the decision queue both change while this tab sits open —
  // an agent can start a render or queue a post at any moment. 20s matches
  // C-1's "this is a localhost approval surface", not a live-collab cadence.
  refreshTimer = setInterval(loadHeaderData, 20000);
});
onUnmounted(() => {
  document.removeEventListener("click", onDocClick, true);
  document.removeEventListener("keydown", onDocKeydown);
  if (refreshTimer) clearInterval(refreshTimer);
});
</script>

<template>
  <div class="shell">
    <header ref="headerEl">
      <div class="brand">
        <span class="brand-mark">P</span>
        <span class="brand-name">Pro Media</span>
      </div>

      <nav class="menubar">
        <div v-for="m in MENUS" :key="m.label" class="menu-wrap">
          <button
            class="menu-btn"
            :class="{ active: openMenu === m.label }"
            :aria-expanded="openMenu === m.label"
            aria-haspopup="menu"
            @click="toggleMenu(m.label)"
            @mouseenter="hoverMenu(m.label)"
            @keydown="onMenuKeydown($event, m.label)"
          >
            {{ m.label }}
          </button>
          <div v-if="openMenu === m.label" class="menu-panel" role="menu">
            <template v-for="(item, i) in m.items" :key="i">
              <div v-if="'rule' in item" class="menu-rule" role="separator" />
              <button
                v-else
                class="menu-item"
                :class="{ current: route.path === item.route }"
                role="menuitem"
                @click="go(item.route)"
              >
                <span class="menu-item-bar" />
                <span class="menu-item-body">
                  <span class="menu-item-label">{{ item.label }}</span>
                  <span v-if="item.note" class="menu-item-note">{{ item.note }}</span>
                </span>
                <span v-if="item.key" class="menu-item-key mono">{{ item.key }}</span>
              </button>
            </template>
          </div>
        </div>
      </nav>

      <div class="spacer" />

      <div v-if="loadError" class="badge badge-bad" :title="loadError">offline</div>
      <div v-else-if="storagePct !== null" class="badge mono">storage {{ storagePct }}%</div>

      <div class="presence" v-if="presence.length">
        <span v-for="p in presence" :key="p.agent" class="avatar" :title="p.agent">{{ p.initials }}</span>
      </div>

      <button class="inbox-btn" @click="go('/approvals')">
        Inbox <span v-if="pendingCount" class="mono inbox-count">{{ pendingCount }}</span>
      </button>
    </header>

    <main>
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.shell {
  height: 100vh;
  display: flex;
  flex-direction: column;
  overflow: hidden;
  background: var(--bg);
}

header {
  height: 42px;
  flex: none;
  display: flex;
  align-items: center;
  gap: 2px;
  padding: 0 10px;
  background: var(--bg-header);
  border-bottom: 1px solid var(--line-4);
  position: relative;
  z-index: 60;
}

.brand {
  display: flex;
  align-items: center;
  gap: 8px;
  padding-right: 10px;
  margin-right: 4px;
  border-right: 1px solid var(--line-3);
}
.brand-mark {
  width: 21px;
  height: 21px;
  border-radius: 6px;
  background: var(--green);
  display: flex;
  align-items: center;
  justify-content: center;
  font: 700 11px var(--font-ui);
  color: var(--green-ink);
}
.brand-name {
  font: 600 13px var(--font-ui);
  letter-spacing: -0.01em;
  white-space: nowrap;
}

.menubar {
  display: flex;
}
.menu-wrap {
  position: relative;
}
.menu-btn {
  all: unset;
  cursor: pointer;
  padding: 5px 11px;
  border-radius: 6px;
  font: 500 12.5px var(--font-ui);
  white-space: nowrap;
  color: var(--fg-muted-1);
}
.menu-btn.active {
  background: #22262c;
  color: var(--fg-bright);
}
.menu-btn:focus-visible {
  outline: var(--focus-ring);
  outline-offset: 2px;
}

.menu-panel {
  position: absolute;
  top: 34px;
  left: 0;
  min-width: 268px;
  padding: 6px;
  border-radius: 10px;
  background: #16191e;
  border: 1px solid var(--line-6);
  box-shadow: 0 18px 44px rgba(0, 0, 0, 0.6);
  z-index: 80;
}
.menu-rule {
  height: 1px;
  background: var(--line-3);
  margin: 5px 4px;
}
.menu-item {
  all: unset;
  cursor: pointer;
  width: 100%;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 7px 9px;
  border-radius: 7px;
  box-sizing: border-box;
}
.menu-item:hover,
.menu-item:focus-visible {
  background: #1c2026;
}
.menu-item.current {
  background: #22262c;
}
.menu-item-bar {
  width: 3px;
  height: 14px;
  border-radius: 2px;
  flex: none;
  background: transparent;
}
.menu-item.current .menu-item-bar {
  background: var(--green);
}
.menu-item-body {
  flex: 1;
  min-width: 0;
}
.menu-item-label {
  font: 500 12.5px var(--font-ui);
  color: var(--fg-dim);
}
.menu-item.current .menu-item-label {
  color: var(--fg-bright);
}
.menu-item-note {
  font: 400 11px var(--font-ui);
  color: var(--fg-muted-3);
  margin-top: 1px;
}
.menu-item-key {
  font-size: 10.5px;
  color: var(--fg-muted-4);
  flex: none;
}

.spacer {
  flex: 1;
  min-width: 12px;
}

.badge {
  font: 500 10.5px var(--font-mono);
  padding: 3px 9px;
  border-radius: 7px;
  background: var(--bg-chip);
  border: 1px solid var(--line-4);
  white-space: nowrap;
  margin-right: 7px;
}
.badge-bad {
  background: var(--red-wash);
  border-color: var(--red-border);
  color: var(--red-soft);
}

.presence {
  display: flex;
  align-items: center;
  margin-right: 6px;
}
.avatar {
  width: 23px;
  height: 23px;
  border-radius: 50%;
  margin-left: -6px;
  border: 2px solid var(--bg-header);
  display: flex;
  align-items: center;
  justify-content: center;
  font: 600 9px var(--font-mono);
  background: var(--amber-wash);
  color: var(--amber);
}

.inbox-btn {
  all: unset;
  cursor: pointer;
  padding: 5px 10px;
  border-radius: 7px;
  background: var(--bg-chip);
  border: 1px solid var(--line-4);
  font: 500 11.5px var(--font-ui);
  color: var(--fg-dim);
  white-space: nowrap;
}
.inbox-count {
  color: var(--green);
  font-size: 10.5px;
}

main {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}
</style>
