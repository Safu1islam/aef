<script setup lang="ts">
export interface PanelRow {
  k: string;
  v: string;
  sub?: string;
  tone?: "green" | "amber" | "red" | "blue" | "grey";
}
export interface PanelCard {
  title: string;
  badge?: string;
  badgeTone?: "green" | "amber" | "red";
  rows: PanelRow[];
}
export interface PanelAction {
  label: string;
  primary?: boolean;
  onClick?: () => void;
  href?: string;
}

defineProps<{
  title: string;
  blurb: string;
  actions?: PanelAction[];
  cards: PanelCard[];
}>();
</script>

<template>
  <section class="panel-screen">
    <div class="panel-head">
      <div class="panel-head-text">
        <h1>{{ title }}</h1>
        <p>{{ blurb }}</p>
      </div>
      <div class="panel-actions" v-if="actions?.length">
        <component
          :is="a.href ? 'a' : 'button'"
          v-for="a in actions"
          :key="a.label"
          :href="a.href"
          class="panel-action"
          :class="{ primary: a.primary }"
          @click="a.onClick"
        >
          {{ a.label }}
        </component>
      </div>
    </div>

    <div class="panel-body">
      <div class="cards">
        <div v-for="c in cards" :key="c.title" class="card">
          <div class="card-head">
            <span class="card-title">{{ c.title }}</span>
            <span v-if="c.badge" class="pill" :class="`tone-${c.badgeTone ?? 'grey'}`">{{ c.badge }}</span>
          </div>
          <div v-for="(r, i) in c.rows" :key="i" class="card-row">
            <div class="card-row-k">
              <div>{{ r.k }}</div>
              <div v-if="r.sub" class="card-row-sub">{{ r.sub }}</div>
            </div>
            <span class="pill" :class="`tone-${r.tone ?? 'grey'}`">{{ r.v }}</span>
          </div>
        </div>
      </div>
      <slot />
    </div>
  </section>
</template>

<style scoped>
.panel-screen {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
}
.panel-head {
  padding: 20px 28px 16px;
  border-bottom: 1px solid var(--line-3);
  display: flex;
  align-items: flex-start;
  gap: 18px;
  flex-wrap: wrap;
}
.panel-head-text {
  flex: 1;
  min-width: 280px;
}
.panel-head-text h1 {
  margin: 0;
  font: 600 20px var(--font-ui);
  letter-spacing: -0.02em;
}
.panel-head-text p {
  margin: 5px 0 0;
  font: 400 12.5px/1.5 var(--font-ui);
  color: var(--fg-muted-2);
  max-width: 76ch;
}
.panel-actions {
  display: flex;
  gap: 7px;
}
.panel-action {
  all: unset;
  cursor: pointer;
  padding: 8px 14px;
  border-radius: 8px;
  background: var(--bg-chip);
  border: 1px solid var(--line-5);
  color: var(--fg-dim);
  font: 600 12.5px var(--font-ui);
}
.panel-action.primary {
  background: var(--green);
  color: var(--green-ink);
  border-color: var(--green);
}
.panel-action:focus-visible {
  outline: var(--focus-ring);
  outline-offset: 2px;
}

.panel-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 20px 28px 36px;
}
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(304px, 1fr));
  gap: 14px;
  align-content: start;
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
.card-row {
  padding: 10px 16px;
  border-bottom: 1px solid var(--line-1);
  display: flex;
  gap: 12px;
  align-items: center;
}
.card-row:last-child {
  border-bottom: none;
}
.card-row-k {
  flex: 1;
  min-width: 0;
}
.card-row-k > div:first-child {
  font: 500 12px var(--font-ui);
}
.card-row-sub {
  font: 400 10.5px/1.45 var(--font-ui);
  color: var(--fg-muted-3);
  margin-top: 2px;
}

.pill {
  flex: none;
}
.tone-grey {
  background: var(--grey-wash);
  color: var(--grey-fg);
}
.tone-green {
  background: var(--green-wash);
  color: var(--green);
}
.tone-amber {
  background: var(--amber-wash);
  color: var(--amber);
}
.tone-red {
  background: var(--red-wash);
  color: var(--red-soft);
}
.tone-blue {
  background: var(--blue-wash);
  color: var(--blue);
}
</style>
