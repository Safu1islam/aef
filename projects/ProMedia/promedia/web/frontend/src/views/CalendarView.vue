<script setup lang="ts">
// Content calendar (DR-022). A hand-built month grid, no calendar library —
// see the decision record for why. This screen invents no data and no
// backend surface: it fetches list-posts (every status) and publications
// (what actually went out) and arranges what already exists onto a grid.
// Clicking a post opens the existing /posts/{id} approval screen — this
// screen is navigation and overview only, never a second place a decision
// gets made (T-035's rule, restated in DR-022's own decision text).
import { computed, onMounted, ref } from "vue";
import { useRouter } from "vue-router";
import { api, ApiError } from "../api";

const router = useRouter();
const loading = ref(true);
const error = ref<string | null>(null);
const posts = ref<any[]>([]);
const publications = ref<any[]>([]);

// The visible month, always normalised to day 1 so month arithmetic never
// drifts against a short month (e.g. adding a month to the 31st).
const cursor = ref(startOfMonth(new Date()));

async function load() {
  loading.value = true;
  error.value = null;
  try {
    const [postsResult, pubsResult] = await Promise.all([api.listPosts(), api.publications()]);
    posts.value = postsResult.posts;
    publications.value = pubsResult.publications;
  } catch (err) {
    error.value = err instanceof ApiError ? err.message : "could not reach the server";
  } finally {
    loading.value = false;
  }
}
onMounted(load);

function startOfMonth(d: Date): Date {
  return new Date(d.getFullYear(), d.getMonth(), 1);
}
function addMonths(d: Date, delta: number): Date {
  return new Date(d.getFullYear(), d.getMonth() + delta, 1);
}
// Local YYYY-MM-DD — matches the first 10 characters of the ISO timestamps
// posts/publications already carry, so a grid cell key and a data key are
// always the same string with no timezone conversion in between.
function isoDate(d: Date): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

const monthLabel = computed(() =>
  cursor.value.toLocaleDateString(undefined, { month: "long", year: "numeric" }),
);

function prevMonth() {
  cursor.value = addMonths(cursor.value, -1);
}
function nextMonth() {
  cursor.value = addMonths(cursor.value, 1);
}
function goToday() {
  cursor.value = startOfMonth(new Date());
}

const WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

interface DayCell {
  date: Date;
  key: string;
  inMonth: boolean;
  isToday: boolean;
}

// Always 42 cells (6 full weeks) so the grid height never jumps between a
// 4-week and a 6-week month. Monday-first, matching the ISO week the rest
// of this project's timestamps already use.
const weeks = computed<DayCell[][]>(() => {
  const first = cursor.value;
  const firstWeekday = (first.getDay() + 6) % 7; // Mon=0..Sun=6
  const gridStart = new Date(first);
  gridStart.setDate(first.getDate() - firstWeekday);
  const todayKey = isoDate(new Date());

  const cells: DayCell[] = [];
  for (let i = 0; i < 42; i++) {
    const date = new Date(gridStart);
    date.setDate(gridStart.getDate() + i);
    const key = isoDate(date);
    cells.push({
      date,
      key,
      inMonth: date.getMonth() === first.getMonth() && date.getFullYear() === first.getFullYear(),
      isToday: key === todayKey,
    });
  }
  const out: DayCell[][] = [];
  for (let i = 0; i < 42; i += 7) out.push(cells.slice(i, i + 7));
  return out;
});

interface CalEntry {
  id: string;
  status: string;
  simulated: boolean | null;
  time: string;
}

const STATUS_TONE: Record<string, string> = {
  queued: "tone-grey",
  approved: "tone-blue",
  publishing: "tone-amber",
  published: "tone-green",
  missed: "tone-red",
  rejected: "tone-grey",
};

const publicationByPostId = computed<Record<string, any>>(() => {
  const map: Record<string, any> = {};
  for (const pub of publications.value) map[pub.post_id] = pub;
  return map;
});

// AC-1: every entry traces back to a real posts/publications row — nothing
// here is invented. A post with neither a scheduled_at nor (once published)
// a recorded publish time has no calendar day of its own; it is surfaced
// separately below rather than silently dropped (see `unscheduled`).
const postsByDate = computed<Record<string, CalEntry[]>>(() => {
  const map: Record<string, CalEntry[]> = {};
  for (const post of posts.value) {
    let when: string | null;
    if (post.status === "published") {
      when = publicationByPostId.value[post.id]?.published_at ?? post.scheduled_at ?? null;
    } else {
      when = post.scheduled_at ?? null;
    }
    if (!when) continue;
    const key = when.slice(0, 10);
    (map[key] ??= []).push({
      id: post.id,
      status: post.status,
      simulated: post.simulated,
      time: when.slice(11, 16),
    });
  }
  for (const key of Object.keys(map)) map[key].sort((a, b) => a.time.localeCompare(b.time));
  return map;
});

const unscheduled = computed(() =>
  posts.value.filter((p) => p.status !== "published" && !p.scheduled_at),
);

function entriesFor(cell: DayCell): CalEntry[] {
  return postsByDate.value[cell.key] ?? [];
}

function openPost(id: string) {
  router.push(`/posts/${id}`);
}
</script>

<template>
  <section class="calendar">
    <div class="head">
      <div>
        <h1>Content calendar</h1>
        <p class="muted">
          Every queued, approved, published and missed post, arranged by its scheduled or
          published date. This is a view — approvals and publishing still happen on each post's
          own review screen.
        </p>
      </div>
      <div class="nav" role="group" aria-label="Change month">
        <button class="btn" @click="prevMonth" aria-label="Previous month">&larr;</button>
        <button class="btn" @click="goToday">Today</button>
        <span class="month mono" aria-live="polite">{{ monthLabel }}</span>
        <button class="btn" @click="nextMonth" aria-label="Next month">&rarr;</button>
      </div>
    </div>

    <div v-if="loading" class="state muted">Loading…</div>
    <div v-else-if="error" class="state banner bad">{{ error }}</div>
    <template v-else>
      <div class="legend">
        <span v-for="(tone, status) in STATUS_TONE" :key="status" class="pill" :class="tone">{{
          status
        }}</span>
      </div>

      <div class="grid" role="table" aria-label="Month grid">
        <div class="weekday-row" role="row">
          <div v-for="label in WEEKDAY_LABELS" :key="label" class="weekday" role="columnheader">
            {{ label }}
          </div>
        </div>
        <div v-for="(week, wi) in weeks" :key="wi" class="week-row" role="row">
          <div
            v-for="cell in week"
            :key="cell.key"
            class="cell"
            :class="{ 'out-of-month': !cell.inMonth, today: cell.isToday }"
            role="cell"
          >
            <div class="cell-head">
              <span class="day-num">{{ cell.date.getDate() }}</span>
              <span v-if="cell.isToday" class="pill tone-blue">today</span>
            </div>
            <div v-if="entriesFor(cell).length" class="entries">
              <button
                v-for="e in entriesFor(cell)"
                :key="e.id"
                class="entry"
                :aria-label="`Open post ${e.id}, ${e.status}, at ${e.time}`"
                @click="openPost(e.id)"
              >
                <span class="pill" :class="STATUS_TONE[e.status] ?? 'tone-grey'">{{
                  e.status
                }}</span>
                <span class="entry-time mono">{{ e.time }}</span>
                <span v-if="e.simulated" class="pill tone-red">sim</span>
              </button>
            </div>
          </div>
        </div>
      </div>

      <div v-if="unscheduled.length" class="unscheduled">
        <h2>Unscheduled ({{ unscheduled.length }})</h2>
        <p class="muted small">
          Queued with no target time yet — nothing to place on the grid until one is set.
        </p>
        <div class="entries">
          <button
            v-for="p in unscheduled"
            :key="p.id"
            class="entry"
            :aria-label="`Open post ${p.id}, ${p.status}`"
            @click="openPost(p.id)"
          >
            <span class="pill" :class="STATUS_TONE[p.status] ?? 'tone-grey'">{{ p.status }}</span>
            <span class="mono muted">{{ p.id }}</span>
          </button>
        </div>
      </div>
    </template>
  </section>
</template>

<style scoped>
.calendar {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  padding: 20px 28px 30px;
  overflow-y: auto;
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
p.muted {
  max-width: 52ch;
}
.nav {
  display: flex;
  align-items: center;
  gap: 6px;
}
.month {
  min-width: 12ch;
  text-align: center;
  font-size: 13px;
  color: var(--fg-muted-1);
}
.btn {
  all: unset;
  cursor: pointer;
  padding: 0.4rem 0.7rem;
  border-radius: 6px;
  background: var(--bg-chip);
  border: 1px solid var(--line-5);
  color: var(--fg-dim);
  font: 600 12px var(--font-ui);
}
.btn:hover {
  background: var(--bg-card);
}
.state {
  padding: 30px 0;
}
.legend {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin: 14px 0 4px;
}
.grid {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--line-3);
  border-radius: var(--radius-lg);
  overflow: hidden;
  margin: 10px 0 20px;
}
.weekday-row,
.week-row {
  display: grid;
  grid-template-columns: repeat(7, 1fr);
}
.weekday {
  padding: 6px 8px;
  font: 500 10px var(--font-mono);
  color: var(--fg-muted-3);
  border-bottom: 1px solid var(--line-3);
  background: var(--bg-panel);
}
.cell {
  min-height: 96px;
  padding: 6px 6px 8px;
  border-right: 1px solid var(--line-2);
  border-bottom: 1px solid var(--line-2);
  background: var(--bg-card-alt);
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.week-row .cell:last-child {
  border-right: none;
}
.cell.out-of-month {
  background: var(--bg);
  color: var(--fg-muted-4);
}
.cell.today {
  background: var(--blue-wash-soft, rgba(127, 176, 255, 0.06));
  box-shadow: inset 0 0 0 1px var(--blue-border);
}
.cell-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.day-num {
  font: 600 11.5px var(--font-mono);
  color: var(--fg-muted-2);
}
.cell.out-of-month .day-num {
  color: var(--fg-muted-4);
}
.entries {
  display: flex;
  flex-direction: column;
  gap: 3px;
  overflow-y: auto;
}
.entry {
  all: unset;
  cursor: pointer;
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 2px 3px;
  border-radius: 5px;
}
.entry:hover {
  background: var(--bg-card);
}
.entry-time {
  font-size: 10px;
  color: var(--fg-muted-3);
}
.unscheduled {
  margin-top: 4px;
}
.unscheduled h2 {
  margin: 0 0 4px;
  font: 600 14px var(--font-ui);
}
.unscheduled .entries {
  flex-direction: row;
  flex-wrap: wrap;
  gap: 8px;
}
.unscheduled .entry {
  border: 1px solid var(--line-3);
  padding: 4px 8px;
}
.small {
  font-size: 11.5px;
}
</style>
