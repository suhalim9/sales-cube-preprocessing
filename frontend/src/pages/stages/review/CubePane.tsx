import { useVirtualizer } from "@tanstack/react-virtual";
import { cn } from "@/lib/utils";
import { Cell } from "./Cell";
import { CELL_WIDTH, ID_COL_WIDTH, ROW_H } from "./format";
import type { AnomalyType, Detection } from "@/api/types";
import type { StagedSelection } from "@/state/selections";

export interface CubePaneProps {
  title: string;
  subtitle: string;
  tone: "before" | "after";
  rows: Array<{ row: Record<string, string | number>; originalIdx: number }>;
  columns: string[];
  detectionsByCell: Map<string, Detection>;
  activeFilters: Set<AnomalyType>;
  mode: "before" | "after";
  selections: Map<string, StagedSelection>;
  afterValues: Map<string, number>;
  onCellClick: (d: Detection) => void;
  scrollRef: React.RefObject<HTMLDivElement | null>;
  siblingScrollRef: React.RefObject<HTMLDivElement | null>;
  // Called when scroll reaches near the bottom of the loaded rows. ReviewStage
  // wires this to ``fetchNextPage`` on the infinite preview query. Safe to
  // call on every scroll tick — the query layer dedupes inflight fetches.
  onNearBottom?: () => void;
  isLoadingMore?: boolean;
}

const NEAR_BOTTOM_PX = 240;

export function CubePane({
  title,
  subtitle,
  tone,
  rows,
  columns,
  detectionsByCell,
  activeFilters,
  mode,
  selections,
  afterValues,
  onCellClick,
  scrollRef,
  siblingScrollRef,
  onNearBottom,
  isLoadingMore,
}: CubePaneProps) {
  const idCols = columns.slice(0, 2);
  const timeCols = columns.slice(2);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_H,
    overscan: 8,
  });

  // Lockstep scroll: mirror our scroll position into the sibling pane. The
  // value check prevents the sibling's own scroll event from looping back.
  // Also triggers infinite-scroll pagination when we get close to the bottom
  // of the currently-loaded rows.
  function onScroll() {
    const me = scrollRef.current;
    const sibling = siblingScrollRef.current;
    if (!me || !sibling) return;
    if (sibling.scrollTop !== me.scrollTop) sibling.scrollTop = me.scrollTop;
    if (sibling.scrollLeft !== me.scrollLeft) sibling.scrollLeft = me.scrollLeft;
    if (
      onNearBottom
      && me.scrollTop + me.clientHeight >= me.scrollHeight - NEAR_BOTTOM_PX
    ) {
      onNearBottom();
    }
  }

  const toneClasses =
    tone === "before"
      ? "bg-slate-50 border-slate-200"
      : "bg-emerald-50/50 border-emerald-200";
  const labelClasses =
    tone === "before"
      ? "bg-slate-200 text-slate-700"
      : "bg-emerald-200 text-emerald-800";
  const minRowWidth = ID_COL_WIDTH * idCols.length + CELL_WIDTH * timeCols.length;

  return (
    <div className={cn("flex flex-col min-w-0 min-h-0 border-y", toneClasses)}>
      <div className={cn("px-4 py-2.5 border-b flex items-center gap-3", tone === "before" ? "border-slate-200" : "border-emerald-200")}>
        <span className={cn("text-[10px] font-bold tracking-wider px-2 py-0.5 rounded", labelClasses)}>
          {title}
        </span>
        <span className="text-xs text-muted-foreground truncate">{subtitle}</span>
      </div>
      <div
        ref={scrollRef}
        onScroll={onScroll}
        className="flex-1 min-h-0 overflow-auto bg-background"
      >
        {/* Header row */}
        <div
          className="sticky top-0 z-10 bg-background border-b flex text-xs font-medium"
          style={{ minWidth: minRowWidth }}
        >
          {idCols.map((c, i) => (
            <div
              key={c}
              className="sticky z-20 bg-background border-r px-2 h-9 flex items-center"
              style={{
                left: i * ID_COL_WIDTH,
                width: ID_COL_WIDTH,
                minWidth: ID_COL_WIDTH,
              }}
            >
              {c}
            </div>
          ))}
          {timeCols.map((c) => (
            <div
              key={c}
              className="px-2 h-9 flex items-center justify-end font-mono"
              style={{ width: CELL_WIDTH, minWidth: CELL_WIDTH }}
            >
              {c}
            </div>
          ))}
        </div>

        {/* Virtualized rows */}
        <div style={{ height: virtualizer.getTotalSize() + (isLoadingMore ? ROW_H : 0), position: "relative" }}>
          {virtualizer.getVirtualItems().map((vrow) => {
            const { row, originalIdx } = rows[vrow.index];
            return (
              <div
                key={vrow.key}
                className="absolute left-0 right-0 flex border-b text-xs"
                style={{
                  top: 0,
                  transform: `translateY(${vrow.start}px)`,
                  height: ROW_H,
                  minWidth: minRowWidth,
                }}
              >
                {idCols.map((c, i) => (
                  <div
                    key={c}
                    className="sticky bg-background border-r px-2 flex items-center font-medium"
                    style={{
                      left: i * ID_COL_WIDTH,
                      width: ID_COL_WIDTH,
                      minWidth: ID_COL_WIDTH,
                      zIndex: 1,
                    }}
                  >
                    {row[c]}
                  </div>
                ))}
                {timeCols.map((c) => (
                  <Cell
                    key={c}
                    rowIdx={originalIdx}
                    column={c}
                    originalValue={row[c] as number}
                    detection={detectionsByCell.get(`${originalIdx}::${c}`)}
                    afterValues={afterValues}
                    activeFilters={activeFilters}
                    mode={mode}
                    selections={selections}
                    onClick={onCellClick}
                  />
                ))}
              </div>
            );
          })}
          {isLoadingMore && (
            <div
              className="absolute left-0 right-0 flex items-center justify-center text-xs text-muted-foreground"
              style={{
                top: virtualizer.getTotalSize(),
                height: ROW_H,
                minWidth: minRowWidth,
              }}
            >
              Loading more rows…
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
