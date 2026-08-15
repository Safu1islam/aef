<script setup lang="ts">
import { computed } from "vue";
import PanelScreen, { type PanelCard } from "../components/PanelScreen.vue";

// Titles and blurbs are descriptive copy, ported from the mockup as-is —
// they describe what the ROOM IS FOR, not a claim about data. The mockup's
// sample numbers (assets, tokens, view counts) are NOT ported: Constitution
// section 6 forbids presenting invented data as real, so each screen here
// says plainly what it cannot do yet instead. T-057/T-059/T-060 replace
// these with real operations as they land; nothing here is silently faked
// in the meantime.
const SCREENS: Record<string, { title: string; blurb: string; note: string; task: string }> = {
  import: {
    title: "Import & download",
    blurb: "Every route into the platform, with origin recorded on each file.",
    note: "Local file upload with a rights declaration already works — see Media library. Import from a URL needs T-046 (blocked on that task's own backend, not this screen).",
    task: "T-046",
  },
  templates: {
    title: "Templates",
    blurb: "Parametric templates with named slots, filled from a brief and a brand kit.",
    note: "No template data model exists yet. Needs its own planning pass before a task is written (T-060).",
    task: "T-060",
  },
  brand: {
    title: "Brand kits",
    blurb: "Colours, type, motion assets, caption styles and an approver, per brand.",
    note: "No brand-kit data model exists yet. Needs its own planning pass before a task is written (T-060).",
    task: "T-060",
  },
  generation: {
    title: "Capabilities & models",
    blurb: "What the system can do today, what needs an integration, and what has no path yet.",
    note: "The AI capability provider seam (T-048) is designed but not built. Nothing here is wired to a real model yet, so nothing is claimed.",
    task: "T-048",
  },
  export: {
    title: "Export & rendering",
    blurb: "Batch one timeline to many targets in a single job.",
    note: "Rendering exists and is real (one target at a time, from a project) — see a project's Editor room. Batch multi-target export is not built.",
    task: "T-060",
  },
  settings: {
    title: "Settings & integrations",
    blurb: "Accounts, storage, backup and what this installation can do.",
    note: "This is a placeholder in the new client. Accounts now have a real home at Distribute > Social; storage, backup and capabilities are still real and working today only at the classic settings page. No task currently plans a rich-client settings screen.",
    task: "not planned",
  },
};

const props = defineProps<{ screenId: string }>();
const screen = computed(() => SCREENS[props.screenId] ?? SCREENS.import);
const cards = computed<PanelCard[]>(() => [
  {
    title: "Status",
    badge: screen.value.task,
    badgeTone: "amber",
    rows: [{ k: "Not available in this client yet", sub: screen.value.note, v: "planned", tone: "amber" }],
  },
]);
</script>

<template>
  <PanelScreen :title="screen.title" :blurb="screen.blurb" :cards="cards" />
</template>
