/**
 * Persisted geometry for the app rail — width and collapsed, modeled exactly
 * on `src/lib/treeState.ts`: a module-level store behind
 * `useSyncExternalStore`, guarded `localStorage`, one subscriber set so every
 * consumer (the desktop aside, the resize handle, the collapse chevron) sees
 * the same value the instant it changes.
 *
 * Design notes, all load-bearing:
 *
 * - **Two storage keys, one store.** `radal.sidebar.width` and
 *   `radal.sidebar.collapsed` persist independently (collapsing must not
 *   forget the width the user dragged to), but the in-memory state is one
 *   immutable snapshot object, so a component re-renders once per change and
 *   `useSyncExternalStore` never sees a torn read.
 * - **Width is clamped on every path in** — read, set, drag — so a
 *   hand-edited or corrupted stored value can never render a 4px or a 4000px
 *   rail. Out-of-range reads degrade to the default.
 * - **Storage may throw**, not just return null (Safari private mode, site
 *   data disabled). Every read and write is guarded; on failure the state
 *   stays in memory for the session, which beats a rail that crashes.
 * - **The mobile sheet ignores all of this on purpose.** Width and collapse
 *   are desktop affordances; the sheet is always full-width and expanded, so
 *   it simply never reads this store.
 */
import * as React from "react";

export const SIDEBAR_WIDTH_STORAGE_KEY = "radal.sidebar.width";
export const SIDEBAR_COLLAPSED_STORAGE_KEY = "radal.sidebar.collapsed";

/** Drag clamp (spec: ~200–360px, default 264). */
export const SIDEBAR_MIN_WIDTH = 200;
export const SIDEBAR_MAX_WIDTH = 360;
export const SIDEBAR_DEFAULT_WIDTH = 264;

/** The collapsed icon rail — icons + tooltips, brand mark only. */
export const SIDEBAR_COLLAPSED_WIDTH = 56;

export interface SidebarState {
  /** Expanded width in px, already clamped. Applies only when not collapsed. */
  width: number;
  collapsed: boolean;
  /** Set the expanded width (clamped); used live during the edge drag. */
  setWidth: (width: number) => void;
  /** Snap back to {@link SIDEBAR_DEFAULT_WIDTH} (double-click on the handle). */
  resetWidth: () => void;
  setCollapsed: (collapsed: boolean) => void;
  toggleCollapsed: () => void;
}

export function clampSidebarWidth(width: number): number {
  if (!Number.isFinite(width)) return SIDEBAR_DEFAULT_WIDTH;
  return Math.min(SIDEBAR_MAX_WIDTH, Math.max(SIDEBAR_MIN_WIDTH, Math.round(width)));
}

// --- module-level store ------------------------------------------------------

type Snapshot = { width: number; collapsed: boolean };

function readWidth(): number {
  try {
    const raw = window.localStorage.getItem(SIDEBAR_WIDTH_STORAGE_KEY);
    if (!raw) return SIDEBAR_DEFAULT_WIDTH;
    const parsed = Number(raw);
    // A stored value outside the clamp is stale config, not a preference.
    if (!Number.isFinite(parsed)) return SIDEBAR_DEFAULT_WIDTH;
    return clampSidebarWidth(parsed);
  } catch {
    return SIDEBAR_DEFAULT_WIDTH;
  }
}

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(SIDEBAR_COLLAPSED_STORAGE_KEY) === "true";
  } catch {
    return false;
  }
}

function writeStorage(key: string, value: string) {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    /* storage unavailable — the value stays in memory for this session */
  }
}

const store: { snapshot: Snapshot; listeners: Set<() => void> } = {
  snapshot: { width: readWidth(), collapsed: readCollapsed() },
  listeners: new Set(),
};

function commit(next: Snapshot) {
  store.snapshot = next;
  for (const listener of store.listeners) listener();
}

function setWidth(width: number) {
  const clamped = clampSidebarWidth(width);
  if (store.snapshot.width === clamped) return;
  writeStorage(SIDEBAR_WIDTH_STORAGE_KEY, String(clamped));
  commit({ ...store.snapshot, width: clamped });
}

function setCollapsed(collapsed: boolean) {
  if (store.snapshot.collapsed === collapsed) return;
  writeStorage(SIDEBAR_COLLAPSED_STORAGE_KEY, String(collapsed));
  commit({ ...store.snapshot, collapsed });
}

function subscribe(listener: () => void) {
  store.listeners.add(listener);
  return () => {
    store.listeners.delete(listener);
  };
}

function getSnapshot(): Snapshot {
  return store.snapshot;
}

/**
 * Subscribe a component to the rail's geometry.
 *
 * ```tsx
 * const rail = useSidebarState();
 * <aside style={{ width: rail.collapsed ? SIDEBAR_COLLAPSED_WIDTH : rail.width }} />
 * ```
 */
export function useSidebarState(): SidebarState {
  const snapshot = React.useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  return React.useMemo<SidebarState>(
    () => ({
      width: snapshot.width,
      collapsed: snapshot.collapsed,
      setWidth,
      resetWidth: () => setWidth(SIDEBAR_DEFAULT_WIDTH),
      setCollapsed,
      toggleCollapsed: () => setCollapsed(!getSnapshot().collapsed),
    }),
    [snapshot],
  );
}
