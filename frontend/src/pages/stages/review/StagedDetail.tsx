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
  const stagedList = detections.filter((d) => selections.has(d.detection_id));
  if (stagedList.length === 0) return null;
  return (
    <div className="border-t bg-muted/30 px-6 py-2 text-xs space-y-1.5 max-h-32 overflow-auto">
      <div className="text-muted-foreground">
        Staged changes ({stagedList.length}):
      </div>
      {stagedList.map((d) => {
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
        // Highlight the button matching the current staged (fix, attribution).
        // If attribution wasn't captured (staged from "All"), match by fix
        // against whichever option has the same fix.
        const activeKey = (opt: (typeof options)[number]) =>
          staged.attribution
            ? opt.fix === staged.fix && opt.detector === staged.attribution
            : opt.fix === staged.fix;
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
    </div>
  );
}
