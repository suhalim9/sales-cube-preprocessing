import { useRef, useState } from "react";
import { useNavigate, useParams, Link } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertCircle, CheckCircle2, Circle, CircleDot, FileQuestion, FileUp, Trash2, Upload, X } from "lucide-react";
import {
  UploadError,
  type UploadProgress,
  deleteFile,
  getProject,
  uploadFile,
} from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { FileStatus, Manifest, ProjectFile } from "@/api/types";

const STATUS_META: Record<
  FileStatus,
  { label: string; icon: typeof Circle; tone: string }
> = {
  uploaded: { label: "upload pending", icon: Circle, tone: "text-muted-foreground" },
  schema_pending: { label: "schema needs confirm", icon: FileQuestion, tone: "text-amber-600" },
  detected: { label: "ready to review", icon: CircleDot, tone: "text-blue-600" },
  cleaning: { label: "review in progress", icon: CircleDot, tone: "text-blue-600" },
  cleaned: { label: "cleaned", icon: CheckCircle2, tone: "text-emerald-600" },
};

export function Project() {
  const { slug = "" } = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const { data: manifest, isLoading } = useQuery({
    queryKey: ["project", slug],
    queryFn: () => getProject(slug),
  });

  const [uploadErr, setUploadErr] = useState<string | null>(null);
  const [progress, setProgress] = useState<UploadProgress | null>(null);

  const upload = useMutation({
    mutationFn: (file: File) =>
      uploadFile(slug, file, { onProgress: setProgress }),
    onMutate: () => {
      setUploadErr(null);
      setProgress({ phase: "validating", percent: 0 });
    },
    onSuccess: (m) => {
      queryClient.setQueryData(["project", slug], m);
      const newest = m.files[m.files.length - 1];
      navigate(`/workspace/${slug}/${newest.file_id}`);
    },
    onError: (err: Error) => {
      setUploadErr(err instanceof UploadError ? err.message : err.message);
      setProgress(null);
    },
    onSettled: () => {
      // Clear progress slightly later so the bar can settle at 100% briefly.
      setTimeout(() => setProgress(null), 400);
    },
  });

  if (isLoading) {
    return <div className="p-8 text-sm text-muted-foreground">Loading…</div>;
  }
  if (!manifest) {
    return (
      <div className="p-8 space-y-3">
        <p className="text-sm">Project not found.</p>
        <Link to="/" className="text-sm underline">
          Back to start
        </Link>
      </div>
    );
  }

  return (
    <div className="min-h-svh keye-bg">
      <header className="border-b border-border/60 bg-background/60 backdrop-blur">
        <div className="max-w-5xl mx-auto px-6 h-14 flex items-center justify-between">
          <div>
            <h1 className="text-lg font-semibold">{manifest.project_name}</h1>
            <p className="text-xs text-muted-foreground">{manifest.project_slug}</p>
          </div>
          <Link to="/">
            <Button variant="ghost" size="sm">Switch project</Button>
          </Link>
        </div>
      </header>

      <main className="max-w-5xl mx-auto p-6 space-y-4">
        <Dropzone
          isUploading={upload.isPending}
          progress={progress}
          onFile={(f) => upload.mutate(f)}
        />
        {uploadErr && (
          <div className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive flex items-start gap-2">
            <AlertCircle className="size-4 shrink-0 mt-0.5" />
            <span>{uploadErr}</span>
            <button
              onClick={() => setUploadErr(null)}
              className="ml-auto opacity-60 hover:opacity-100"
              title="Dismiss"
              aria-label="Dismiss error"
            >
              <X className="size-4" />
            </button>
          </div>
        )}

        <Card>
          <CardHeader className="pb-3 flex flex-row items-baseline justify-between space-y-0">
            <CardTitle>Files ({manifest.files.length})</CardTitle>
            {manifest.files.length > 0 && <StatusLegend />}
          </CardHeader>
          <CardContent className="p-0">
            {manifest.files.length === 0 ? (
              <div className="p-12 text-center text-sm text-muted-foreground">
                No files yet. Drop a sales cube above to get started.
              </div>
            ) : (
              <div className="divide-y">
                {withDisplayNames(manifest.files).map(({ file, displayName }) => (
                  <FileRow key={file.file_id} slug={slug} file={file} displayName={displayName} />
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </main>
    </div>
  );
}

function Dropzone({
  isUploading,
  progress,
  onFile,
}: {
  isUploading: boolean;
  progress: UploadProgress | null;
  onFile: (file: File) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [hover, setHover] = useState(false);

  function handleFiles(list: FileList | null) {
    if (!list || list.length === 0) return;
    onFile(list[0]);
    if (inputRef.current) inputRef.current.value = "";
  }

  const phaseLabel = progress
    ? {
        validating: "Checking file…",
        uploading: `Uploading — ${progress.percent}%`,
        parsing: "Parsing…",
        done: "Done",
      }[progress.phase]
    : "Drop a sales cube here";

  // Indeterminate during parsing (no determinate progress); else use real %.
  const barPercent =
    progress?.phase === "parsing" || progress?.phase === "done"
      ? 100
      : progress?.percent ?? 0;

  return (
    <div
      onDragOver={(e) => {
        e.preventDefault();
        setHover(true);
      }}
      onDragLeave={() => setHover(false)}
      onDrop={(e) => {
        e.preventDefault();
        setHover(false);
        handleFiles(e.dataTransfer.files);
      }}
      className={cn(
        "rounded-lg border-2 border-dashed transition-colors p-6 flex items-center gap-4 relative overflow-hidden",
        hover ? "border-primary bg-primary/5" : "border-border bg-background",
        isUploading && "pointer-events-none",
      )}
    >
      <div className={cn("size-12 rounded-full flex items-center justify-center shrink-0 relative", hover ? "bg-primary/10" : "bg-muted")}>
        <FileUp className="size-6 text-muted-foreground" />
        <span className="absolute -bottom-1 -right-2 px-1.5 py-0.5 rounded text-[10px] font-bold tracking-wider bg-primary text-primary-foreground shadow-sm">
          .parquet
        </span>
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-sm font-medium">{phaseLabel}</div>
        <div className="text-xs text-muted-foreground tabular-nums">
          {progress?.phase === "uploading" && progress.bytesTotal
            ? `${formatBytes(progress.bytesSent ?? 0)} of ${formatBytes(progress.bytesTotal)}`
            : "customer × product × period · Parquet only · up to 2 GB"}
        </div>
      </div>
      <Button onClick={() => inputRef.current?.click()} disabled={isUploading}>
        <Upload /> Browse files
      </Button>
      <input
        ref={inputRef}
        type="file"
        accept=".parquet"
        className="hidden"
        onChange={(e) => handleFiles(e.target.files)}
      />
      {progress && (
        <div className="absolute left-0 right-0 bottom-0 h-1 bg-muted">
          <div
            className={cn(
              "h-full bg-primary transition-[width] duration-150 ease-out",
              progress.phase === "parsing" && "animate-pulse",
            )}
            style={{ width: `${barPercent}%` }}
          />
        </div>
      )}
    </div>
  );
}

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function StatusLegend() {
  // Inline key so a quick glance teaches the icons.
  const items: FileStatus[] = ["schema_pending", "detected", "cleaned"];
  return (
    <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
      {items.map((s) => {
        const meta = STATUS_META[s];
        const Icon = meta.icon;
        return (
          <span key={s} className="inline-flex items-center gap-1">
            <Icon className={`size-3 ${meta.tone}`} />
            {meta.label}
          </span>
        );
      })}
    </div>
  );
}

/** Suffix duplicate filenames in display: second `happy.parquet` shows as
 * `happy (2).parquet`. The underlying filename in the manifest stays
 * untouched — this is purely a render concern. */
function withDisplayNames(
  files: ProjectFile[],
): Array<{ file: ProjectFile; displayName: string }> {
  const counts = new Map<string, number>();
  return files.map((file) => {
    const seen = (counts.get(file.original_filename) ?? 0) + 1;
    counts.set(file.original_filename, seen);
    const displayName =
      seen === 1
        ? file.original_filename
        : suffixName(file.original_filename, seen);
    return { file, displayName };
  });
}

function suffixName(name: string, n: number): string {
  const dot = name.lastIndexOf(".");
  if (dot <= 0) return `${name} (${n})`;
  return `${name.slice(0, dot)} (${n})${name.slice(dot)}`;
}

function FileRow({
  slug,
  file,
  displayName,
}: {
  slug: string;
  file: ProjectFile;
  displayName: string;
}) {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const meta = STATUS_META[file.status];
  const Icon = meta.icon;
  const totalAnomalies = file.anomaly_counts
    ? Object.values(file.anomaly_counts).reduce((a, b) => a + b, 0)
    : null;

  const del = useMutation({
    mutationFn: () => deleteFile(slug, file.file_id),
    // Optimistic update: drop the row from the cached manifest immediately
    // so the UI reflects the delete without waiting for a refetch. The
    // background invalidate keeps the cache honest if the server disagrees.
    onMutate: () => {
      const prev = queryClient.getQueryData<Manifest>(["project", slug]);
      if (prev) {
        queryClient.setQueryData<Manifest>(["project", slug], {
          ...prev,
          files: prev.files.filter((f) => f.file_id !== file.file_id),
        });
      }
      return { prev };
    },
    onError: (_err, _vars, ctx) => {
      // Roll back the optimistic removal on failure.
      if (ctx?.prev) queryClient.setQueryData(["project", slug], ctx.prev);
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["project", slug] });
    },
  });

  function handleDelete(e: React.MouseEvent) {
    e.stopPropagation();
    if (window.confirm(`Delete "${displayName}"? This removes the file and any cleaned output.`)) {
      del.mutate();
    }
  }

  return (
    <div
      onClick={() => navigate(`/workspace/${slug}/${file.file_id}`)}
      className="w-full px-6 py-4 hover:bg-accent text-left flex items-center gap-4 transition-colors cursor-pointer group"
    >
      <Icon className={`size-5 ${meta.tone}`} />
      <div className="flex-1 min-w-0">
        <div className="font-medium text-sm truncate" title={file.original_filename}>
          {displayName}
        </div>
        <div className="text-xs text-muted-foreground tabular-nums">
          ~{file.row_count.toLocaleString()} rows · {meta.label}
          {totalAnomalies != null && ` · ${totalAnomalies} anomalies`}
        </div>
      </div>
      {file.status === "cleaned" && (
        <Badge variant="secondary" className="text-emerald-700 bg-emerald-50 border border-emerald-200">
          ✓ cleaned
        </Badge>
      )}
      <button
        onClick={handleDelete}
        disabled={del.isPending}
        className="opacity-0 group-hover:opacity-100 transition-opacity p-1.5 rounded hover:bg-destructive/10 hover:text-destructive disabled:opacity-40"
        title="Delete file"
      >
        <Trash2 className="size-4" />
      </button>
    </div>
  );
}
