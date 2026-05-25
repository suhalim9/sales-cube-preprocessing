import { useMutation } from "@tanstack/react-query";
import { CheckCircle2, Download, FileText } from "lucide-react";
import { Link } from "react-router";
import { getAuditUrl, getCleanedUrl } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { ProjectFile } from "@/api/types";

export function CompletionView({
  slug,
  file,
}: {
  slug: string;
  file: ProjectFile;
}) {
  // Lazy presigned URL fetches — only when the button is clicked. Each
  // click opens a fresh, short-lived URL so links never go stale.
  const cleanedUrl = useMutation({
    mutationFn: () => getCleanedUrl(slug, file.file_id),
    onSuccess: (url) => window.open(url, "_blank"),
  });
  const auditUrl = useMutation({
    mutationFn: () => getAuditUrl(slug, file.file_id),
    onSuccess: (url) => window.open(url, "_blank"),
  });

  const totalChanges = file.applied_changes ?? 0;
  const summary = file.anomaly_counts
    ? Object.entries(file.anomaly_counts).filter(([, n]) => (n ?? 0) > 0)
    : [];

  return (
    <div className="flex-1 min-h-0 overflow-auto bg-muted/10">
      <div className="max-w-3xl mx-auto p-8 space-y-6 w-full">
        <div className="text-center space-y-3">
          <div className="size-14 rounded-full bg-emerald-100 mx-auto flex items-center justify-center">
            <CheckCircle2 className="size-8 text-emerald-600" />
          </div>
          <h2 className="text-xl font-semibold">This file has been cleaned</h2>
          <p className="text-sm text-muted-foreground">
            {totalChanges === 0
              ? "Finalized with no changes. The source file was already clean."
              : `${totalChanges} change${totalChanges === 1 ? "" : "s"} applied`}
            {file.cleaned_at && ` · ${new Date(file.cleaned_at).toLocaleString()}`}
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <FileText className="size-4" /> Cleaned file
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Button
                variant="outline"
                className="w-full"
                onClick={() => cleanedUrl.mutate()}
                disabled={cleanedUrl.isPending}
              >
                <Download /> {cleanedUrl.isPending ? "Preparing…" : "Download .parquet"}
              </Button>
            </CardContent>
          </Card>
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm flex items-center gap-2">
                <FileText className="size-4" /> Audit log
              </CardTitle>
            </CardHeader>
            <CardContent>
              <Button
                variant="outline"
                className="w-full"
                onClick={() => auditUrl.mutate()}
                disabled={auditUrl.isPending}
              >
                <Download /> {auditUrl.isPending ? "Preparing…" : "Download .json"}
              </Button>
            </CardContent>
          </Card>
        </div>

        {summary.length > 0 && (
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-sm">What was detected</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 text-sm">
              {summary.map(([type, count]) => (
                <div key={type} className="flex items-center justify-between">
                  <span className="text-muted-foreground capitalize">
                    {type.replace("_", " ")}
                  </span>
                  <span className="tabular-nums">{count}</span>
                </div>
              ))}
            </CardContent>
          </Card>
        )}

        <div className="border-t pt-4 text-xs text-muted-foreground space-y-2">
          <p>
            To start over from the original, delete this file from the project list and
            re-upload it.
          </p>
          <Link to={`/project/${slug}`}>
            <Button variant="ghost" size="sm">← Back to project</Button>
          </Link>
        </div>
      </div>
    </div>
  );
}
