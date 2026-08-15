// The top menu bar's contents, ported from Pro Media v2.dc.html's menuDef.
// Static structure only — no business logic, just where each item routes.

export interface MenuItem {
  route: string;
  label: string;
  note?: string;
  key?: string;
}

export interface MenuGroup {
  label: string;
  items: (MenuItem | { rule: true })[];
}

export const MENUS: MenuGroup[] = [
  {
    label: "Work",
    items: [
      { route: "/dashboard", label: "Dashboard", note: "Production floor", key: "⌘1" },
      { route: "/projects", label: "Projects", note: "List and pipeline board", key: "⌘2" },
      { route: "/approvals", label: "Approvals & history", key: "⌘3" },
      { rule: true },
      { route: "/settings", label: "Team & permissions" },
    ],
  },
  {
    label: "Media",
    items: [
      { route: "/media", label: "Media library", note: "Rights, evidence, provenance", key: "⌘L" },
      { route: "/import", label: "Import & download" },
      { rule: true },
      { route: "/templates", label: "Templates" },
      { route: "/brand", label: "Brand kits" },
    ],
  },
  {
    label: "Studio",
    items: [{ route: "/projects", label: "Open editor", note: "Pick a project to edit", key: "E" }],
  },
  {
    label: "Intelligence",
    items: [
      { route: "/agent", label: "Agent workspace" },
      { route: "/generation", label: "Capabilities", note: "What needs an API" },
      { rule: true },
      { route: "/approvals", label: "Decision queue", note: "Accept or reject" },
    ],
  },
  {
    label: "Distribute",
    items: [
      { route: "/export", label: "Export & render" },
      { route: "/social", label: "Social integration", note: "Accounts, queue", key: "⌘P" },
      { route: "/calendar", label: "Content calendar" },
    ],
  },
  {
    label: "System",
    items: [
      { route: "/settings", label: "Settings" },
      { route: "/generation", label: "Models & integrations" },
    ],
  },
];
