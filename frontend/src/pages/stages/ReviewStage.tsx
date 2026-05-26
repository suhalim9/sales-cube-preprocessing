import { useCallback, useMemo, useRef, useState } from "react";
import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";
import { getPreview, runDetection } from "@/api/client";
import { Button } from "@/components/ui/button";
import { DETECTOR_META } from "@/lib/detectors";
import { cn } from "@/lib/utils";
import { useSelections } from "@/state/selections";
import { CubePane } from "./review/CubePane";
import { StagedDetail } from "./review/StagedDetail";
import type { AnomalyType, Detection, SuggestedFix } from "@/api/types";

const ALL_DETECTORS: AnomalyType[] = ["negative", "refund", "double_booking", "outlier"];
const PREVIEW_PAGE_SIZE = 100;

type ActiveTab = "all" | AnomalyType;

export function ReviewStage({
  slug,
  fileId,
  onAdvance,
}: {
  slug: string;
  fileId: string;
  onAdvance: () => void;
}) {
  const { data: detectionsPage, isLoading: detecting } = useQuery({
    queryKey: ["detection-run", slug, fileId],
    queryFn: () => runDetection(slug, fileId),
  });
  const sel = useSelections({ slug, fileId });
  // Single-select tab. "all" shows every anomaly; a specific detector
  // dims/hides cells that aren't flagged by it.
  const [activeTab, setActiveTab] = useState<ActiveTab>("all");

  // Infinite-scroll preview. Backend returns ``cursor`` = next offset as a
  // string (or null when exhausted). Each page is PREVIEW_PAGE_SIZE rows;
  // pages are appended and rendered through the virtualizer in CubePane,
  // so memory stays bounded by the user's scroll depth.
  //
  // When a specific detector tab is active, the query keys on it and asks
  // the backend to return only rows containing detections of that type
  // (``?detected=<type>``). The "All" tab pages through the full cube as
  // before.
  const detectedParam = activeTab === "all" ? undefined : activeTab;
  const previewQuery = useInfiniteQuery({
    queryKey: ["preview", slug, fileId, activeTab],
    initialPageParam: 0,
    queryFn: ({ pageParam }) => getPreview(slug, fileId, pageParam, PREVIEW_PAGE_SIZE, detectedParam),
    getNextPageParam: (lastPage) =>
      lastPage.cursor != null ? Number(lastPage.cursor) : undefined,
  });
  // Flatten loaded pages into a single rows array + matching row indices.
  // ``row_indices[i]`` is the original cube row index for ``rows[i]``,
  // critical when the server returns a sparse subset (anomaly-only view).
  const preview = useMemo(() => {
    if (!previewQuery.data) return undefined;
    const pages = previewQuery.data.pages;
    return {
      rows: pages.flatMap((p) => p.rows),
      rowIndices: pages.flatMap((p) => p.row_indices),
      columns: pages[0]?.columns ?? [],
      total: pages[0]?.total ?? 0,
    };
  }, [previewQuery.data]);
  const loadNextPage = useCallback(() => {
    if (previewQuery.hasNextPage && !previewQuery.isFetchingNextPage) {
      previewQuery.fetchNextPage();
    }
  }, [previewQuery]);

  // Shared scroll refs so the two cube panes can sync lockstep. Declared
  // up here (above any conditional return) so hook order is stable.
  const beforeScrollRef = useRef<HTMLDivElement>(null);
  const afterScrollRef = useRef<HTMLDivElement>(null);

  const activeFilters = useMemo<Set<AnomalyType>>(
    () => (activeTab === "all" ? new Set(ALL_DETECTORS) : new Set([activeTab])),
    [activeTab],
  );

  // Detection lookup by (row_idx, column). Memoized so cell renders stay fast.
  const detectionsByCell = useMemo(() => {
    const map = new Map<string, Detection>();
    if (!detectionsPage) return map;
    for (const d of detectionsPage.detections) {
      if (d.row_idx == null) continue;
      map.set(`${d.row_idx}::${d.column}`, d);
    }
    return map;
  }, [detectionsPage]);

  const filteredDetections = useMemo(() => {
    if (!detectionsPage) return [];
    return detectionsPage.detections.filter((d) =>
      d.flagged_by.some((t) => activeFilters.has(t)),
    );
  }, [detectionsPage, activeFilters]);

  // Rows to render in both panes. The server already filtered to anomaly
  // rows when the user picked a detector tab, so we just zip rows with
  // their original cube indices. ``originalIdx`` is the cube row index —
  // used to look up detections and after-values by ``${row}::${col}``.
  const visibleRows = useMemo(() => {
    if (!preview) return [];
    return preview.rows.map((row, i) => ({
      row,
      originalIdx: preview.rowIndices[i],
    }));
  }, [preview]);

  // Attribution captured at staging time — the left-rail tab the user was
  // looking at. "all" leaves it undefined so apply can pick a default.
  const stagingAttribution: AnomalyType | undefined =
    activeTab === "all" ? undefined : activeTab;

  // Compute the "after" value for every cell that has a staged selection.
  const afterValues = useMemo(() => {
    const after = new Map<string, number>();
    if (!detectionsPage || !preview) return after;
    const timeCols = preview.columns.slice(2); // skip id cols
    for (const d of detectionsPage.detections) {
      const staged = sel.selections.get(d.detection_id);
      if (!staged || d.row_idx == null) continue;
      const { fix, attribution } = staged;
      if (fix === "set_to_zero") {
        after.set(`${d.row_idx}::${d.column}`, 0);
        // Refund cascade fires when:
        // - the user explicitly staged from the Refunds tab, OR
        // - no tab was specified (staged from "All") and refund is one of the
        //   detectors that flagged this cell — mirroring the backend priority
        //   order that resolves the same situation.
        // Staging from a tab *other* than Refunds (e.g., Negatives) on a
        // refund-flagged cell does NOT cascade — the user's context wins.
        const refundCascade =
          attribution === "refund" ||
          (attribution === undefined && d.flagged_by.includes("refund"));
        if (fix === "set_to_zero" && refundCascade) {
          const refundIdx = timeCols.indexOf(d.column);
          let remaining = -d.value;
          for (let i = refundIdx - 1; i >= 0 && remaining > 0; i--) {
            const priorCol = timeCols[i];
            const priorKey = `${d.row_idx}::${priorCol}`;
            const current = after.get(priorKey) ?? preview.rows[d.row_idx]?.[priorCol];
            if (typeof current !== "number" || current <= 0) continue;
            const absorbed = Math.min(current, remaining);
            after.set(priorKey, current - absorbed);
            remaining -= absorbed;
          }
        }
      } else if (fix === "split_evenly") {
        const idx = timeCols.indexOf(d.column);
        const cellAt = (col: string): number => {
          const v = preview.rows[d.row_idx!]?.[col];
          return typeof v === "number" ? v : Number(v ?? 0);
        };
        let partnerCol: string | null = null;
        if (idx >= 0 && idx + 1 < timeCols.length && cellAt(timeCols[idx + 1]) === 0) {
          partnerCol = timeCols[idx + 1];
        } else if (idx > 0 && cellAt(timeCols[idx - 1]) === 0) {
          partnerCol = timeCols[idx - 1];
        }
        if (partnerCol === null) continue;
        const [earlier, later] = splitEvenly(d.value);
        after.set(`${d.row_idx}::${d.column}`, earlier);
        after.set(`${d.row_idx}::${partnerCol}`, later);
      }
      // keep_as_is: no value change
    }
    return after;
  }, [detectionsPage, preview, sel.selections]);

  function onCellClick(d: Detection) {
    sel.toggle(d.detection_id, d.suggested_fix, d.flagged_by, stagingAttribution);
  }

  function pickFix(
    detectionId: string,
    fix: SuggestedFix,
    attribution?: AnomalyType,
  ) {
    const d = detectionsPage?.detections.find(
      (dd) => dd.detection_id === detectionId,
    );
    sel.stage(detectionId, fix, d?.flagged_by ?? [], attribution);
  }

  function stageAllVisible() {
    sel.stageMany(
      filteredDetections.map(
        (d) => [d.detection_id, d.suggested_fix, d.flagged_by, stagingAttribution],
      ),
    );
  }
  function unstageAllVisible() {
    for (const d of filteredDetections) sel.unstage(d.detection_id);
  }

  if (detecting || !detectionsPage || !preview) {
    return (
      <div className="max-w-md mx-auto p-12 text-center space-y-3 w-full">
        <div className="text-sm text-muted-foreground">Running 4 detectors…</div>
        <div className="h-1 bg-muted overflow-hidden rounded">
          <div className="h-full bg-primary animate-pulse" style={{ width: "60%" }} />
        </div>
      </div>
    );
  }

  const total = detectionsPage.detections.length;
  const staged = sel.selections.size;
  const stagedInView = filteredDetections.filter((d) =>
    sel.selections.has(d.detection_id),
  ).length;
  const totalMagnitude = detectionsPage.detections.reduce(
    (sum, d) => sum + Math.abs(d.value),
    0,
  );

  const activeTitle = activeTab === "all" ? "All anomalies" : DETECTOR_META[activeTab].label;
  const activeSubtitle = activeTab === "all"
    ? `${total} potential anomalies · ~$${formatMagnitude(totalMagnitude)} total magnitude`
    : DETECTOR_META[activeTab].short;

  return (
    <div className="flex flex-1 min-h-0">
      {/* Left rail: detector tabs (single-select) */}
      <aside className="w-60 shrink-0 border-r bg-muted/30 flex flex-col">
        <div className="px-4 py-3 border-b">
          <div className="text-[10px] tracking-[0.18em] uppercase text-muted-foreground">Anomalies</div>
          <div className="text-2xl font-semibold tabular-nums">{total}</div>
          <div className="text-xs text-muted-foreground">
            ~${formatMagnitude(totalMagnitude)} magnitude
          </div>
        </div>
        <nav className="flex-1 p-2 space-y-1 overflow-auto">
          <TabButton
            label="All"
            count={total}
            active={activeTab === "all"}
            onClick={() => setActiveTab("all")}
          />
          {ALL_DETECTORS.map((t) => (
            <TabButton
              key={t}
              label={DETECTOR_META[t].label}
              count={detectionsPage.counts[t] ?? 0}
              color={DETECTOR_META[t].color}
              active={activeTab === t}
              onClick={() => setActiveTab(t)}
            />
          ))}
        </nav>
        <div className="border-t p-3 space-y-2 text-xs">
          <div className="flex items-center justify-between">
            <span className="text-muted-foreground">Staged</span>
            <span className="font-semibold tabular-nums">{staged} / {total}</span>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={sel.clear}
            disabled={staged === 0}
            className="w-full"
          >
            Clear staged
          </Button>
        </div>
      </aside>

      {/* Right content: title + dual pane + bottom bar */}
      <div className="flex-1 flex flex-col min-h-0">
        <div className="px-6 py-3 border-b bg-background flex items-baseline gap-3">
          <h2 className="text-base font-semibold flex items-center gap-2">
            {activeTab !== "all" && (
              <span
                className="size-2.5 rounded-sm"
                style={{ backgroundColor: DETECTOR_META[activeTab].color }}
              />
            )}
            {activeTitle}
          </h2>
          <p className="text-sm text-muted-foreground">{activeSubtitle}</p>
          <div className="flex-1" />
          <span className="text-xs text-muted-foreground tabular-nums mr-1">
            {stagedInView} of {filteredDetections.length} staged
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={stageAllVisible}
            disabled={filteredDetections.length === 0 || stagedInView === filteredDetections.length}
          >
            Stage all
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={unstageAllVisible}
            disabled={stagedInView === 0}
          >
            Stage none
          </Button>
        </div>

        {total === 0 ? (
          <EmptyState onAdvance={onAdvance} />
        ) : (
          <>
            <div className="flex-1 min-h-0 grid grid-cols-[1fr_auto_1fr] bg-muted/20">
              <CubePane
                title="BEFORE"
                subtitle="Original cube"
                tone="before"
                rows={visibleRows}
                columns={preview.columns}
                detectionsByCell={detectionsByCell}
                activeFilters={activeFilters}
                mode="before"
                selections={sel.selections}
                afterValues={afterValues}
                onCellClick={onCellClick}
                scrollRef={beforeScrollRef}
                siblingScrollRef={afterScrollRef}
                onNearBottom={loadNextPage}
                isLoadingMore={previewQuery.isFetchingNextPage}
              />
              <PaneDivider hasStaged={staged > 0} />
              <CubePane
                title="AFTER"
                subtitle={staged === 0 ? "No fixes staged yet. Click a colored cell on the left." : `${staged} fix${staged === 1 ? "" : "es"} staged`}
                tone="after"
                rows={visibleRows}
                columns={preview.columns}
                detectionsByCell={detectionsByCell}
                activeFilters={activeFilters}
                mode="after"
                selections={sel.selections}
                afterValues={afterValues}
                onCellClick={onCellClick}
                scrollRef={afterScrollRef}
                siblingScrollRef={beforeScrollRef}
                onNearBottom={loadNextPage}
                isLoadingMore={previewQuery.isFetchingNextPage}
              />
            </div>

            <StagedDetail
              detections={detectionsPage.detections}
              selections={sel.selections}
              onPickFix={pickFix}
              onUnstage={sel.unstage}
            />

            <div className="border-t px-6 py-3 flex items-center justify-end gap-2 bg-background">
              <Button onClick={onAdvance} disabled={staged === 0}>
                Continue to Apply ({staged}) →
              </Button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}

function TabButton({
  label,
  count,
  color,
  active,
  onClick,
}: {
  label: string;
  count: number;
  color?: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={cn(
        "w-full flex items-center gap-2.5 px-2.5 py-2 rounded-md text-sm transition-colors text-left",
        active
          ? "bg-background border border-border shadow-sm"
          : "hover:bg-background/60 text-muted-foreground hover:text-foreground",
      )}
    >
      {color ? (
        <span className="size-2 rounded-sm shrink-0" style={{ backgroundColor: color }} />
      ) : (
        <span className="size-2 rounded-sm shrink-0 border border-border" />
      )}
      <span className={cn("flex-1 truncate", active && "font-medium text-foreground")}>
        {label}
      </span>
      <span className={cn("text-xs tabular-nums", active ? "text-foreground" : "text-muted-foreground")}>
        {count}
      </span>
    </button>
  );
}

// ---------------------------------------------------------------------------
// EmptyState: shown when total detections == 0. Replaces the dual-pane
// preview with a celebration + Continue button.
// ---------------------------------------------------------------------------

function EmptyState({ onAdvance }: { onAdvance: () => void }) {
  return (
    <div className="flex-1 min-h-0 flex flex-col items-center justify-center bg-muted/10 px-6 py-12 text-center space-y-4">
      <div className="size-14 rounded-full bg-emerald-100 flex items-center justify-center">
        <CheckCircle2 className="size-8 text-emerald-600" />
      </div>
      <div className="space-y-1 max-w-md">
        <h3 className="text-lg font-semibold">No anomalies detected</h3>
        <p className="text-sm text-muted-foreground">
          All four detectors ran and found nothing to clean. You can finalize the file
          as-is — it'll be written to the cleaned output with an empty audit log.
        </p>
      </div>
      <Button onClick={onAdvance}>Finalize as-is →</Button>
    </div>
  );
}

// ---------------------------------------------------------------------------
// PaneDivider: vertical separator between Before and After. Carries an
// "→ transforms to" affordance so the two-pane structure reads at a glance.
// ---------------------------------------------------------------------------

function PaneDivider({ hasStaged }: { hasStaged: boolean }) {
  return (
    <div className="w-12 flex flex-col items-center justify-center bg-muted/30 border-x border-border relative">
      <div
        className={cn(
          "size-8 rounded-full flex items-center justify-center text-base transition-colors",
          hasStaged ? "bg-emerald-500 text-white" : "bg-muted text-muted-foreground",
        )}
        title={hasStaged ? "Fixes will be applied" : "Click anomaly cells to stage fixes"}
      >
        →
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Pure helpers (used by the afterValues memo + headline)
// ---------------------------------------------------------------------------

function splitEvenly(x: number): [number, number] {
  if (Number.isInteger(x)) {
    return [Math.ceil(x / 2), Math.floor(x / 2)];
  }
  const half = x / 2;
  return [half, half];
}

function formatMagnitude(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return Math.round(n).toLocaleString();
}
