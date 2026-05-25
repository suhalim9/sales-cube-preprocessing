// In-memory store of staged fixes per (slug, fileId, detectionId). Lives
// for the session — matches "selections live in process memory" from
// ASSUMPTIONS.md. Subscribers are lightweight: a useSelections() hook
// returns the map plus stable mutators.
//
// Each entry stores both the chosen ``fix`` and an optional ``attribution``
// — which detector context the user staged from. The active left-rail tab
// supplies the attribution; "All" leaves it undefined so apply falls back
// to its priority order. Refund-specific behavior (FIFO absorb) only
// kicks in when attribution is "refund", regardless of what other
// detectors flagged the same cell.

import { useSyncExternalStore } from "react";
import type { AnomalyType, SuggestedFix } from "@/api/types";

interface SelectionKey {
  slug: string;
  fileId: string;
}

export interface StagedSelection {
  fix: SuggestedFix;
  attribution?: AnomalyType;
  // Detectors that flagged this cell, captured at stage time. Used by the
  // Apply-page summary so it doesn't have to re-fetch detections to compute
  // the per-detector count when the user lands on that page.
  flagged_by: AnomalyType[];
}

type SelectionMap = Map<string, StagedSelection>;

const stores = new Map<string, SelectionMap>();
const listeners = new Map<string, Set<() => void>>();

function keyOf({ slug, fileId }: SelectionKey): string {
  return `${slug}::${fileId}`;
}

function getMap(key: string): SelectionMap {
  let m = stores.get(key);
  if (!m) {
    m = new Map();
    stores.set(key, m);
  }
  return m;
}

function notify(key: string) {
  listeners.get(key)?.forEach((fn) => fn());
}

export function useSelections({ slug, fileId }: SelectionKey) {
  const key = keyOf({ slug, fileId });
  const map = useSyncExternalStore(
    (cb) => {
      let set = listeners.get(key);
      if (!set) {
        set = new Set();
        listeners.set(key, set);
      }
      set.add(cb);
      return () => set!.delete(cb);
    },
    () => getMap(key),
    () => getMap(key),
  );

  // Mutations clone the Map so the snapshot returned by useSyncExternalStore
  // gets a new reference each time. Mutating in place would notify subscribers
  // but React's bail-out would skip the re-render — same reference, "no change".
  function replace(next: SelectionMap) {
    stores.set(key, next);
    notify(key);
  }

  return {
    selections: map,
    stage(
      detectionId: string,
      fix: SuggestedFix,
      flagged_by: AnomalyType[],
      attribution?: AnomalyType,
    ) {
      const next = new Map(getMap(key));
      next.set(detectionId, { fix, attribution, flagged_by });
      replace(next);
    },
    unstage(detectionId: string) {
      const next = new Map(getMap(key));
      next.delete(detectionId);
      replace(next);
    },
    toggle(
      detectionId: string,
      fix: SuggestedFix,
      flagged_by: AnomalyType[],
      attribution?: AnomalyType,
    ) {
      const cur = getMap(key);
      const next = new Map(cur);
      const existing = cur.get(detectionId);
      if (existing?.fix === fix && existing.attribution === attribution) {
        next.delete(detectionId);
      } else {
        next.set(detectionId, { fix, attribution, flagged_by });
      }
      replace(next);
    },
    clear() {
      replace(new Map());
    },
    stageMany(
      entries: Array<[string, SuggestedFix, AnomalyType[], AnomalyType | undefined]>,
    ) {
      const next = new Map(getMap(key));
      for (const [id, fix, flagged_by, attribution] of entries) {
        next.set(id, { fix, attribution, flagged_by });
      }
      replace(next);
    },
  };
}
