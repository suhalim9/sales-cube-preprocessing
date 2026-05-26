import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Download, FileText } from "lucide-react";
import { ApiError, applyFixes } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { DETECTOR_META } from "@/lib/detectors";
import { useSelections } from "@/state/selections";
import type { AnomalyType, ApplyResponse } from "@/api/types";

export function ApplyStage({ slug, fileId }: { slug: string; fileId: string }) {
  const queryClient = useQueryClient();
  const sel = useSelections({ slug, fileId });

  // Summary by detector, computed directly from the staged selections so we
  // don't depend on detectionsPage being loaded / fresh. Each selection
  // carries its own attribution + flagged_by, captured at stage time.
  const summary = useMemo(() => {
    const out: Record<AnomalyType, number> = {
      negative: 0, refund: 0, double_booking: 0, outlier: 0,
    };
    for (const [, s] of sel.selections) {
      const target = s.attribution ?? s.flagged_by[0];
      if (target) out[target] += 1;
    }
    return out;
  }, [sel.selections]);

  const apply = useMutation({
    mutationFn: () => {
      const selections = Array.from(sel.selections.entries()).map(
        ([detection_id, { fix, attribution }]) => ({
          detection_id,
          fix,
          attribution,
        }),
      );
      return applyFixes(slug, fileId, selections);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["project", slug] });
    },
    onError: (err) => {
      // 409 = file already cleaned. Stale manifest cache let the user land
      // here; refreshing it routes them to CompletionView automatically.
      if (err instanceof ApiError && err.status === 409) {
        queryClient.invalidateQueries({ queryKey: ["project", slug] });
      }
    },
  });

  const [confirmed, setConfirmed] = useState(false);
  const staged = sel.selections.size;
  const result: ApplyResponse | undefined = apply.data;

  if (result) {
    return (
      <div className="max-w-3xl mx-auto p-6 space-y-6 w-full">
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-6 space-y-2 text-center">
          <CheckCircle2 className="size-10 text-emerald-600 mx-auto" />
          <h2 className="text-xl font-semibold text-emerald-900">Applied successfully</h2>
          <p className="text-sm text-emerald-800">
            {result.total_changes} change{result.total_changes === 1 ? "" : "s"} committed at{" "}
            {new Date(result.applied_at).toLocaleString()}
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <FileText className="size-4" /> Cleaned file
              </CardTitle>
            </CardHeader>
            <CardContent>
              <a
                href={result.cleaned_url}
                className="inline-flex w-full items-center justify-center gap-2 h-9 px-4 rounded-md border border-border bg-background text-sm font-medium hover:bg-accent"
              >
                <Download className="size-4" /> Download .parquet
              </a>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <FileText className="size-4" /> Audit log
              </CardTitle>
            </CardHeader>
            <CardContent>
              <a
                href={result.audit_url}
                className="inline-flex w-full items-center justify-center gap-2 h-9 px-4 rounded-md border border-border bg-background text-sm font-medium hover:bg-accent"
              >
                <Download className="size-4" /> Download .json
              </a>
            </CardContent>
          </Card>
        </div>

        <div className="flex justify-center pt-2">
          <Link to={`/project/${slug}`}>
            <Button variant="ghost">← Back to project</Button>
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-3xl mx-auto p-6 space-y-4 w-full">
      <div className="space-y-1">
        <h2 className="text-xl font-semibold">Review &amp; apply</h2>
        <p className="text-sm text-muted-foreground">
          {staged === 0
            ? "Nothing to clean. Finalize the file as-is. An empty audit log will still be written so the file is marked cleaned."
            : `One last look before committing. ${staged} change${staged === 1 ? "" : "s"} will be written to the cleaned file and audit log.`}
        </p>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Changes by detector</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2">
          {(["negative", "refund", "double_booking", "outlier"] as AnomalyType[]).map((t) => {
            const meta = DETECTOR_META[t];
            const count = summary[t];
            return (
              <div key={t} className="flex items-center justify-between text-sm">
                <span className="flex items-center gap-2">
                  <span className="size-2.5 rounded-sm" style={{ backgroundColor: meta.color }} />
                  {meta.label}
                </span>
                <span className="tabular-nums font-medium">{count}</span>
              </div>
            );
          })}
          <div className="border-t pt-2 mt-2 flex items-center justify-between text-sm">
            <span className="font-medium">Total staged</span>
            <span className="tabular-nums font-semibold">{staged}</span>
          </div>
        </CardContent>
      </Card>

      <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
        Once applied, the cleaned file and audit log are written together (atomic). This
        action cannot be undone for the demo.
      </div>

      {apply.isError && !(apply.error instanceof ApiError && apply.error.status === 409) && (
        <ErrorBanner error={apply.error} onRetry={() => apply.mutate()} />
      )}

      {apply.isPending && <InFlightProgress staged={staged} />}

      <div className="flex items-center justify-between gap-3 pt-2">
        <label className="flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(e) => setConfirmed(e.target.checked)}
          />
          {staged === 0 ? "I've reviewed and want to finalize as-is." : "I've reviewed the staged changes."}
        </label>
        <Button
          onClick={() => apply.mutate()}
          disabled={!confirmed || apply.isPending}
        >
          {apply.isPending
            ? "Applying…"
            : staged === 0
              ? "Finalize as-is →"
              : `Apply ${staged} change${staged === 1 ? "" : "s"}`}
        </Button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// InFlightProgress: shown while apply is running. Just an elapsed timer plus
// a hint about why this might take a while on large staged sets — keeps the
// user from thinking the request is stuck. Real progress reporting would
// need backend job streaming; this is the "set expectations" version.
// ---------------------------------------------------------------------------

function InFlightProgress({ staged }: { staged: number }) {
  const [elapsed, setElapsed] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setElapsed((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, []);
  const mm = String(Math.floor(elapsed / 60)).padStart(1, "0");
  const ss = String(elapsed % 60).padStart(2, "0");
  // Loose buckets — actual time varies with the slowest of: backend compute,
  // S3 upload of cleaned.parquet, S3 upload of audit.json.
  const expectation =
    staged > 100_000
      ? "Large staged sets (100k+) take ~30-60s — most of that is writing the audit log to S3."
      : staged > 10_000
        ? "This usually takes 5-15s for staged sets at this size."
        : "This should finish in a few seconds.";
  return (
    <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900 space-y-1">
      <div className="flex items-center gap-2">
        <div className="size-2 rounded-full bg-blue-500 animate-pulse" />
        <span className="font-medium">Applying…</span>
        <span className="ml-auto font-mono tabular-nums text-xs">{mm}:{ss}</span>
      </div>
      <p className="text-xs text-blue-800/80">{expectation}</p>
    </div>
  );
}


// ---------------------------------------------------------------------------
// ErrorBanner: surfaces apply failures with a friendly message.
//
// Three flavors of failure we want to distinguish:
// - Network / CORS / backend died (ApiError status=0, code="network"): the
//   backend stopped responding mid-request — usually OOM or restart. Tell
//   the user explicitly so they don't think it's their internet.
// - 400/500 with detail string from the server: surface the detail verbatim.
// - Anything else: show the message as-is.
// ---------------------------------------------------------------------------

function ErrorBanner({ error, onRetry }: { error: Error; onRetry: () => void }) {
  const isApi = error instanceof ApiError;
  const isNetwork = isApi && (error as ApiError).status === 0;
  const isServerError = isApi && (error as ApiError).status >= 500;
  const title = isNetwork
    ? "Server unavailable"
    : isServerError
      ? "Server error"
      : "Apply failed";
  const hint = isNetwork || isServerError
    ? "This usually happens when the backend runs out of memory on very large staged sets. Try staging fewer changes (e.g., one detector tab at a time) and retry."
    : null;
  return (
    <div className="rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-900 space-y-2">
      <div className="flex items-start gap-2">
        <AlertCircle className="size-4 mt-0.5 shrink-0" />
        <div className="flex-1 space-y-1 min-w-0">
          <div className="font-medium">{title}</div>
          <div className="text-xs break-words">{error.message}</div>
          {hint && <div className="text-xs text-red-800/80">{hint}</div>}
        </div>
        <Button variant="outline" size="sm" onClick={onRetry}>
          Retry
        </Button>
      </div>
    </div>
  );
}
