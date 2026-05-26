import { DETECTOR_ACTIONS, DETECTOR_META } from "@/lib/detectors";
import { cn } from "@/lib/utils";
import type { AnomalyType, Detection, SuggestedFix } from "@/api/types";
import type { StagedSelection } from "@/state/selections";

export function StagedDetail({
  detections,
  selections,
  onPickFix,
  onUnstage,
}: {
  detections: Detection[];
  selections: Map<string, StagedSelection>;
  onPickFix: (id: string, fix: SuggestedFix, attribution?: AnomalyType) => void;
  onUnstage: (id: string) => void;
}) {
  // Every staged cell appears here so the user can see what's queued and
  // unstage from one place. For cells flagged by more than one detector,
  // we render one button per detector — picking a button switches the
  // attribution (and therefore the action) the apply layer uses.
  //
  // Render cap: on stress.parquet (~hundreds of thousands of detections),
  // rendering a DOM row per staged item locks the browser. Cap to
  // ``MAX_VISIBLE`` and roll the rest into a one-line summary. The full
  // selection set still applies on submit — only the inline preview is
  // truncated.
  const MAX_VISIBLE = 50;
  const stagedList = detections.filter((d) => selections.has(d.detection_id));
  if (stagedList.length === 0) return null;
  const visibleStaged = stagedList.slice(0, MAX_VISIBLE);
  const hiddenCount = stagedList.length - visibleStaged.length;
  return (
    <div className="border-t bg-muted/30 px-6 py-2 text-xs space-y-1.5 max-h-32 overflow-auto">
      <div className="text-muted-foreground">
        Staged changes ({stagedList.length}):
      </div>
      {visibleStaged.map((d) => {
        const staged = selections.get(d.detection_id)!;
        // Union of each detector's actions, deduped by label so we don't show
        // two identical buttons when multiple detectors offer the same thing
        // (e.g., both negative and outlier expose "Set to 0"). The first
        // detector that offers a label keeps it — that's whose attribution
        // the audit log will record if the user picks that button.
        const seen = new Set<string>();
        const options: Array<{
          detector: AnomalyType;
          fix: SuggestedFix;
          label: string;
        }> = [];
        for (const detector of d.flagged_by) {
          for (const action of DETECTOR_ACTIONS[detector]) {
            if (seen.has(action.label)) continue;
            seen.add(action.label);
            options.push({ detector, ...action });
          }
        }
        // "Keep as is" always renders last — it's the conservative bail-out,
        // not the primary action, even for outliers where it's the default.
        options.sort((a, b) => {
          if (a.fix === "keep_as_is" && b.fix !== "keep_as_is") return 1;
          if (b.fix === "keep_as_is" && a.fix !== "keep_as_is") return -1;
          return 0;
        });
        // Pick exactly one option to highlight so the user can see what's
        // currently staged. Order of preference:
        //   1. Exact (fix, attribution) match — what the user explicitly
        //      picked or what was staged with attribution context.
        //   2. First option matching just the fix — covers the case where
        //      the staged attribution doesn't exist as a contributing
        //      detector (e.g. cached suggested_fix from a different
        //      detector). Without this fallback no button would highlight
        //      and the bar would look "broken."
        const exactOpt = staged.attribution
          ? options.find(
              (o) => o.fix === staged.fix && o.detector === staged.attribution,
            )
          : undefined;
        const fallbackOpt = options.find((o) => o.fix === staged.fix);
        const activeOpt = exactOpt ?? fallbackOpt;
        const activeKey = (opt: (typeof options)[number]) => opt === activeOpt;
        return (
          <div key={d.detection_id} className="flex items-center gap-2">
            <span className="font-mono text-muted-foreground">
              {Object.values(d.row_key).join(" / ")} @ {d.column}:
            </span>
            <div className="flex gap-1">
              {options.map((opt) => (
                <button
                  key={opt.label}
                  onClick={() => onPickFix(d.detection_id, opt.fix, opt.detector)}
                  className={cn(
                    "px-1.5 py-0.5 rounded text-[11px]",
                    activeKey(opt)
                      ? "bg-primary text-primary-foreground"
                      : "bg-background border hover:bg-accent",
                  )}
                  title={`Treat as ${DETECTOR_META[opt.detector].label}`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
            <button
              onClick={() => onUnstage(d.detection_id)}
              className="ml-1 text-muted-foreground hover:text-destructive"
              title="Unstage"
            >
              ×
            </button>
          </div>
        );
      })}
      {hiddenCount > 0 && (
        <div className="text-muted-foreground italic">
          …and {hiddenCount.toLocaleString()} more staged (all will apply).
        </div>
      )}
    </div>
  );
}
