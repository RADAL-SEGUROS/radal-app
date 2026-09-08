/**
 * Persisted expand/collapse state for the two navigation rails (spec v3 §5.2, §5.3).
 *
 * Both rails are trees the broker leaves half-open for days at a time: the main
 * sidebar remembers which groups and which sections are unfolded
 * (`radal.nav.expanded`), and the contextual group rail remembers the path down
 * to a policy (`radal.tree.expanded`). Losing that on every reload is what makes
 * a tree feel disposable, so the map lives in `localStorage`.
 *
 * Design notes, all load-bearing:
 *
 * - **Collapsed is the default.** The map stores only the keys the user actually
 *   opened, so a node that has never been touched reads as `false` and a brand
 *   new group does not spring open. `openByDefault` exists for the one rail that
 *   needs an exception (the latest vigencia).
 * - **Parents never auto-expand on navigation.** Nothing here reacts to the
 *   route; a caller that wants "auto-expand the active path on first mount"
 *   calls {@link ExpandedState.expandMany} once from an effect. That keeps the
 *   surprise-free behaviour the spec asks for: the tree moves only when the user
 *   moves it, or exactly once when a page first opens.
 * - **One state per storage key.** Two components reading the same key share a
 *   module-level cache and a subscriber set, so a chevron clicked in one place
 *   is reflected everywhere at once instead of drifting until the next reload.
 * - **Storage may throw**, not just return null (Safari private mode, a browser
 *   with site data disabled). Every read and write is guarded; when it fails the
 *   state degrades to in-memory for the session, which is strictly better than a
 *   sidebar that crashes.
 */
import * as React from "react";

/** Main rail (`Sidebar.tsx`): groups and the collapsible MÁS section. */
export const NAV_EXPANDED_STORAGE_KEY = "radal.nav.expanded";

/** Contextual rail (`components/groups/GroupSidebar.tsx`): the group tree. */
export const TREE_EXPANDED_STORAGE_KEY = "radal.tree.expanded";

export type ExpandedMap = Record<string, boolean>;

/**
 * Node-key builders. Both rails must agree on the shape of a key or the same
 * node would be stored twice, so they are built here and nowhere else.
 */
export const navKeys = {
  /** A collapsible sidebar section, e.g. `section:more`. */
  section: (name: string) => `section:${name}`,
  /** A group row in the main rail, e.g. `group:6`. */
  group: (groupId: number | string) => `group:${groupId}`,
  /** A period row under a group, e.g. `group:6/period:2026-2027`. */
  period: (groupId: number | string, label: string) => `group:${groupId}/period:${label}`,
};

/** Contextual-tree keys: `g6`, `g6/p2025-2026`, `g6/p2025-2026/l1`, `…/policies`, `…/pol1`. */
export const treeKeys = {
  group: (groupId: number | string) => `g${groupId}`,
  period: (groupId: number | string, label: string) => `g${groupId}/p${label}`,
  line: (groupId: number | string, label: string, lineId: number | string) =>
    `g${groupId}/p${label}/l${lineId}`,
  node: (parentKey: string, name: string) => `${parentKey}/${name}`,
  policy: (parentKey: string, policyId: number | string) => `${parentKey}/pol${policyId}`,
};

export interface ExpandedState {
  /** The raw map — only keys the user has touched appear in it. */
  expanded: ExpandedMap;
  isExpanded: (key: string, openByDefault?: boolean) => boolean;
  toggle: (key: string, openByDefault?: boolean) => void;
  setExpanded: (key: string, value: boolean) => void;
  /** Open several nodes at once — the "auto-expand the active path" door. */
  expandMany: (keys: string[]) => void;
  /** Forget everything stored under this storage key. */
  reset: () => void;
}

// --- module-level store, one per storage key --------------------------------

type Store = {
  map: ExpandedMap;
  listeners: Set<() => void>;
};

const stores = new Map<string, Store>();

function read(storageKey: string): ExpandedMap {
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return {};
    const parsed: unknown = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const out: ExpandedMap = {};
    for (const [key, value] of Object.entries(parsed as Record<string, unknown>)) {
      // Only keep booleans; a corrupted or hand-edited entry is dropped rather
      // than crashing the rail that reads it.
      if (typeof value === "boolean") out[key] = value;
    }
    return out;
  } catch {
    return {};
  }
}

function write(storageKey: string, map: ExpandedMap) {
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(map));
  } catch {
    /* storage unavailable — the map stays in memory for this session */
  }
}

function getStore(storageKey: string): Store {
  let store = stores.get(storageKey);
  if (!store) {
    store = { map: read(storageKey), listeners: new Set() };
    stores.set(storageKey, store);
  }
  return store;
}

function commit(storageKey: string, next: ExpandedMap) {
  const store = getStore(storageKey);
  store.map = next;
  write(storageKey, next);
  for (const listener of store.listeners) listener();
}

/**
 * Subscribe a component to the expanded map stored under `storageKey`.
 *
 * ```tsx
 * const nav = useExpanded(NAV_EXPANDED_STORAGE_KEY);
 * nav.isExpanded(navKeys.group(6));
 * nav.toggle(navKeys.group(6));
 * ```
 */
export function useExpanded(storageKey: string): ExpandedState {
  const subscribe = React.useCallback(
    (listener: () => void) => {
      const store = getStore(storageKey);
      store.listeners.add(listener);
      return () => {
        store.listeners.delete(listener);
      };
    },
    [storageKey],
  );

  const getSnapshot = React.useCallback(() => getStore(storageKey).map, [storageKey]);

  const expanded = React.useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  return React.useMemo<ExpandedState>(() => {
    const isExpanded = (key: string, openByDefault = false) =>
      getStore(storageKey).map[key] ?? openByDefault;

    const setExpanded = (key: string, value: boolean) => {
      const current = getStore(storageKey).map;
      if (current[key] === value) return;
      commit(storageKey, { ...current, [key]: value });
    };

    return {
      expanded,
      isExpanded,
      setExpanded,
      toggle: (key: string, openByDefault = false) =>
        setExpanded(key, !isExpanded(key, openByDefault)),
      expandMany: (keys: string[]) => {
        if (keys.length === 0) return;
        const current = getStore(storageKey).map;
        if (keys.every((key) => current[key] === true)) return;
        const next = { ...current };
        for (const key of keys) next[key] = true;
        commit(storageKey, next);
      },
      reset: () => commit(storageKey, {}),
    };
  }, [expanded, storageKey]);
}
