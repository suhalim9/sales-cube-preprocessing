import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Check, X } from "lucide-react";
import { getProject } from "@/api/client";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { SchemaStage } from "@/pages/stages/SchemaStage";
import { ReviewStage } from "@/pages/stages/ReviewStage";
import { ApplyStage } from "@/pages/stages/ApplyStage";
import { CompletionView } from "@/pages/Completion";

type StageId = "schema" | "review" | "apply";

const STAGES: Array<{ id: StageId; label: string }> = [
  { id: "schema", label: "Schema" },
  { id: "review", label: "Detect & review" },
  { id: "apply", label: "Apply" },
];

export function Workspace() {
  const { slug = "", fileId = "" } = useParams();
  const { data: manifest } = useQuery({
    queryKey: ["project", slug],
    queryFn: () => getProject(slug),
  });
  const file = useMemo(
    () => manifest?.files.find((f) => f.file_id === fileId),
    [manifest, fileId],
  );

  // The stepper allows revisiting completed stages — track furthest reached
  // and active separately.
  const [active, setActive] = useState<StageId>("schema");
  const [furthest, setFurthest] = useState<StageId>("schema");

  function advance(next: StageId) {
    setActive(next);
    const order = STAGES.findIndex((s) => s.id === next);
    const furthestOrder = STAGES.findIndex((s) => s.id === furthest);
    if (order > furthestOrder) setFurthest(next);
  }

  if (!manifest || !file) {
    return <div className="p-8 text-sm text-muted-foreground">Loading…</div>;
  }

  // Cleaned files skip the stepper entirely — the only useful actions are
  // downloads and going back. Apply has already happened; re-running the
  // stages would 409.
  const isCleaned = file.status === "cleaned";

  return (
    <div className="h-svh flex flex-col bg-background overflow-hidden">
      <header className="border-b">
        <div className="px-6 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3 min-w-0">
            <Link to={`/project/${slug}`} className="text-sm text-muted-foreground hover:underline truncate">
              {manifest.project_name}
            </Link>
            <span className="text-muted-foreground">›</span>
            <span className="text-sm font-medium truncate">{file.original_filename}</span>
          </div>
          <Link to={`/project/${slug}`}>
            <Button variant="ghost" size="icon" aria-label="Close workspace" title="Back to project">
              <X />
            </Button>
          </Link>
        </div>
        {!isCleaned && (
          <Stepper
            active={active}
            furthest={furthest}
            onSelect={setActive}
          />
        )}
      </header>

      <main className="flex-1 min-h-0 flex flex-col">
        {isCleaned ? (
          <CompletionView slug={slug} file={file} />
        ) : (
          <>
            {active === "schema" && <SchemaStage slug={slug} fileId={fileId} onAdvance={() => advance("review")} />}
            {active === "review" && <ReviewStage slug={slug} fileId={fileId} onAdvance={() => advance("apply")} />}
            {active === "apply" && <ApplyStage slug={slug} fileId={fileId} />}
          </>
        )}
      </main>
    </div>
  );
}

function Stepper({
  active,
  furthest,
  onSelect,
}: {
  active: StageId;
  furthest: StageId;
  onSelect: (id: StageId) => void;
}) {
  const furthestOrder = STAGES.findIndex((s) => s.id === furthest);
  return (
    <div className="px-6 pb-3 flex items-center gap-2">
      {STAGES.map((stage, i) => {
        const isActive = stage.id === active;
        const isReachable = i <= furthestOrder;
        const isComplete = i < furthestOrder;
        return (
          <div key={stage.id} className="flex items-center gap-2">
            <button
              onClick={() => isReachable && onSelect(stage.id)}
              disabled={!isReachable}
              className={cn(
                "flex items-center gap-2 px-3 py-1.5 rounded-md text-sm transition-colors",
                isActive && "bg-primary text-primary-foreground",
                !isActive && isReachable && "hover:bg-accent",
                !isReachable && "opacity-40 cursor-not-allowed",
              )}
            >
              <span
                className={cn(
                  "size-5 rounded-full inline-flex items-center justify-center text-xs font-medium",
                  isActive && "bg-primary-foreground/20",
                  !isActive && isComplete && "bg-emerald-100 text-emerald-700",
                  !isActive && !isComplete && "bg-muted",
                )}
              >
                {isComplete && !isActive ? <Check className="size-3" /> : i + 1}
              </span>
              {stage.label}
            </button>
            {i < STAGES.length - 1 && <div className="w-6 h-px bg-border" />}
          </div>
        );
      })}
    </div>
  );
}
