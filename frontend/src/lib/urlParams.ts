/**
 * URL search params as state.
 *
 * These used to live in the analytics page. They moved here when Analítica
 * split into two destinations — **Datos** (the tables) and **Analítica** (the
 * visuals) — because both now speak the same filter vocabulary and neither
 * should import the other's internals.
 *
 * The rule they encode: every piece of view state lives in the URL, so a
 * filtered view is a shareable link.
 */
import * as React from "react";
import { useSearchParams } from "react-router-dom";

/**
 * One URL search param as state. Setting `null` removes the key; every other
 * param in the URL survives, so tab + filters + view compose freely.
 */
export function useUrlParam(
  key: string,
): [string | null, (value: string | null) => void] {
  const [params, setParams] = useSearchParams();
  const value = params.get(key);
  const set = React.useCallback(
    (next: string | null) => {
      setParams(
        (prev) => {
          const out = new URLSearchParams(prev);
          if (next === null || next === "") out.delete(key);
          else out.set(key, next);
          return out;
        },
        { replace: true },
      );
    },
    [key, setParams],
  );
  return [value, set];
}

/**
 * Batched form of {@link useUrlParam}: apply several key changes in ONE
 * `setSearchParams` call. Two consecutive single-key setters in the same tick
 * both read the same stale `prev` (react-router resolves the functional
 * updater against the params captured at render), so the second call silently
 * drops the first one's change — clearing three filters used to clear one.
 * `null`/`""` removes the key.
 */
export function useSetUrlParams(): (updates: Record<string, string | null>) => void {
  const [, setParams] = useSearchParams();
  return React.useCallback(
    (updates: Record<string, string | null>) => {
      setParams(
        (prev) => {
          const out = new URLSearchParams(prev);
          for (const [key, next] of Object.entries(updates)) {
            if (next === null || next === "") out.delete(key);
            else out.set(key, next);
          }
          return out;
        },
        { replace: true },
      );
    },
    [setParams],
  );
}

/** A numeric URL param, or `undefined` when absent/garbage. */
export function useNumericUrlParam(key: string): number | undefined {
  const [raw] = useUrlParam(key);
  if (!raw) return undefined;
  const parsed = Number(raw);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : undefined;
}
