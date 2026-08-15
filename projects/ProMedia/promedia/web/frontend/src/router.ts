import { createRouter, createWebHistory, type RouteRecordRaw } from "vue-router";

const routes: RouteRecordRaw[] = [
  { path: "/", redirect: "/dashboard" },
  { path: "/dashboard", name: "dashboard", component: () => import("./views/DashboardView.vue") },
  { path: "/projects", name: "projects", component: () => import("./views/ProjectsView.vue") },
  {
    path: "/editor/:projectId",
    name: "editor",
    component: () => import("./views/EditorView.vue"),
    props: true,
  },
  { path: "/media", name: "media", component: () => import("./views/MediaView.vue") },
  { path: "/media/:assetId", name: "asset", component: () => import("./views/AssetView.vue"), props: true },
  { path: "/approvals", name: "approvals", component: () => import("./views/ApprovalsView.vue") },
  { path: "/posts/:postId", name: "post", component: () => import("./views/PostView.vue"), props: true },
  { path: "/social", name: "social", component: () => import("./views/SocialView.vue") },
  {
    path: "/settings",
    name: "settings",
    component: () => import("./views/PanelPlaceholderView.vue"),
    props: { screenId: "settings" },
  },
  {
    path: "/import",
    name: "import",
    component: () => import("./views/PanelPlaceholderView.vue"),
    props: { screenId: "import" },
  },
  {
    path: "/templates",
    name: "templates",
    component: () => import("./views/PanelPlaceholderView.vue"),
    props: { screenId: "templates" },
  },
  {
    path: "/brand",
    name: "brand",
    component: () => import("./views/PanelPlaceholderView.vue"),
    props: { screenId: "brand" },
  },
  {
    path: "/agent",
    name: "agent",
    component: () => import("./views/AgentWorkspaceView.vue"),
  },
  {
    path: "/generation",
    name: "generation",
    component: () => import("./views/PanelPlaceholderView.vue"),
    props: { screenId: "generation" },
  },
  {
    path: "/export",
    name: "export",
    component: () => import("./views/PanelPlaceholderView.vue"),
    props: { screenId: "export" },
  },
  { path: "/calendar", name: "calendar", component: () => import("./views/CalendarView.vue") },
  // Anything unknown resolves to the dashboard rather than a dead route — the
  // menu is the exhaustive list of destinations, so an unmatched path can
  // only be a stale link, never a screen the menu itself would offer.
  { path: "/:pathMatch(.*)*", redirect: "/dashboard" },
];

export const router = createRouter({
  history: createWebHistory("/studio/"),
  routes,
});
