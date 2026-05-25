import { DETECTOR_META } from "@/lib/detectors";
import { cn } from "@/lib/utils";
import { CELL_WIDTH, formatCell } from "./format";
import type { AnomalyType, Detection, SuggestedFix } from "@/api/types";
import type { StagedSelection } from "@/state/selections";

export function Cell({
  rowIdx,
  column,
  originalValue,
  detection,
  afterValues,
  activeFilters,
  mode,
  selections,
  onClick,
}: {
  rowIdx: number;
  column: string;
  originalValue: number;
  detection: Detection | undefined;
  afterValues: Map<string, number>;
  activeFilters: Set<AnomalyType>;
  mode: "before" | "after";
  selections: Map<string, StagedSelection>;
  onClick: (d: Detection) => void;
}) {
  const cellKey = `${rowIdx}::${column}`;
  const afterVal = afterValues.get(cellKey);
  const isAfter = mode === "after";
  const displayed = isAfter && afterVal !== undefined ? afterVal : originalValue;

  const dimmedByFilter = detection && !detection.flagged_by.some((t) => activeFilters.has(t));

  const isStaged = detection ? selections.has(detection.detection_id) : false;
  // Background — color by primary detector (highest priority in flagged_by).
  const primaryDetector = detection?.flagged_by[0];
  const detectorBg = primaryDetector ? DETECTOR_META[primaryDetector].bg : undefined;

  // True when this cell's value will change due to a staged action — either
  // directly (the staged detection) or as a side effect (the prior period
  // of a staged refund; the partner of a staged split_evenly).
  const willChange = afterVal !== undefined && afterVal !== originalValue;
  const isInStagedAction = isStaged || willChange;
  const stagedFix: SuggestedFix | undefined = isStaged
    ? selections.get(detection!.detection_id)?.fix
    : undefined;
  const showAsChanged = isAfter && willChange;

  // Tooltip text — surfaces detector names + current/proposed value on hover.
  const tooltip = detection
    ? [
        `Flagged by: ${detection.flagged_by.map((t) => DETECTOR_META[t].label).join(", ")}`,
        `Original: ${originalValue}`,
        isStaged ? `Staged fix: ${stagedFix}` : "Click to stage default fix",
      ].join("\n")
    : willChange
      ? "Will be zeroed as part of a staged refund fix"
      : undefined;

  return (
    <button
      onClick={() => detection && onClick(detection)}
      disabled={!detection}
      title={tooltip}
      className={cn(
        "px-2 flex items-center justify-end font-mono tabular-nums border-r relative",
        detection && "cursor-pointer hover:ring-2 hover:ring-ring/40 hover:ring-inset",
        isStaged && "ring-2 ring-emerald-500 ring-inset bg-emerald-50/60",
        !isStaged && willChange && "ring-2 ring-emerald-400/70 ring-inset ring-dashed bg-emerald-50/60",
        dimmedByFilter && "opacity-30",
        !detection && "cursor-default",
      )}
      style={{
        width: CELL_WIDTH,
        minWidth: CELL_WIDTH,
        background: detection && !isInStagedAction ? detectorBg : undefined,
      }}
    >
      <span
        className={cn(
          showAsChanged && "font-semibold",
          !isAfter && willChange && "line-through decoration-2 decoration-emerald-600/70",
        )}
      >
        {formatCell(displayed)}
      </span>
      {detection && (
        <div className="absolute top-0.5 right-0.5 flex gap-0.5">
          {detection.flagged_by.map((t) => (
            <span
              key={t}
              className="size-1.5 rounded-full"
              style={{ backgroundColor: DETECTOR_META[t].color }}
              title={DETECTOR_META[t].label}
            />
          ))}
        </div>
      )}
      {isInStagedAction && (
        <span
          className="absolute bottom-0.5 left-1 text-[11px] font-semibold text-emerald-700"
          title={
            isStaged
              ? stagedFix === "keep_as_is" ? "Kept as is" : "Fix staged"
              : "Will be zeroed by a staged refund"
          }
        >
          {isStaged && stagedFix === "keep_as_is" ? "⊙" : "✓"}
        </span>
      )}
    </button>
  );
}

