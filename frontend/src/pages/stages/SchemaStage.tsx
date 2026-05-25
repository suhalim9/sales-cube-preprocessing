import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Wand2 } from "lucide-react";
import { coerceColumns, getSchema } from "@/api/client";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { OverrideModal } from "./schema/OverrideModal";

export function SchemaStage({
  slug,
  fileId,
  onAdvance,
}: {
  slug: string;
  fileId: string;
  onAdvance: () => void;
}) {
  const queryClient = useQueryClient();
  const { data: schema, isLoading } = useQuery({
    queryKey: ["schema", slug, fileId],
    queryFn: () => getSchema(slug, fileId),
  });
  const [editing, setEditing] = useState(false);

  const coerce = useMutation({
    mutationFn: (cols: string[]) => coerceColumns(slug, fileId, cols),
    onSuccess: (result) => {
      queryClient.setQueryData(["schema", slug, fileId], result);
    },
  });

  if (isLoading || !schema) {
    return <div className="p-8 text-sm text-muted-foreground">Analyzing schema…</div>;
  }

  const ok = schema.hard_errors.length === 0;

  return (
    <div className="max-w-3xl mx-auto p-6 space-y-4 w-full">
      <div className="space-y-1">
        <h2 className="text-xl font-semibold">Confirm the schema</h2>
        <p className="text-sm text-muted-foreground">
          We've auto-detected your column roles. Review and confirm before running detection.
        </p>
      </div>

      {ok ? (
        <div className="flex items-center gap-2 text-sm text-emerald-700">
          <CheckCircle2 className="size-4" />
          File looks good — all required column types detected.
        </div>
      ) : (
        <div className="rounded-md border border-destructive/50 bg-destructive/5 p-4 space-y-1">
          <div className="flex items-center gap-2 text-sm font-medium text-destructive">
            <AlertTriangle className="size-4" />
            Hard errors — detection blocked
          </div>
          <ul className="text-sm list-disc pl-5">
            {schema.hard_errors.map((e, i) => <li key={i}>{e}</li>)}
          </ul>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
        <RoleCard
          title="Identifiers"
          subtitle="String columns used as row keys"
          columns={schema.id_columns}
        />
        <RoleCard
          title="Time periods"
          subtitle={schema.time_format ? `Format: ${schema.time_format}` : "—"}
          columns={schema.time_columns}
          truncate={6}
        />
        <RoleCard
          title="Measures"
          subtitle="Numeric data the detectors run on"
          columns={schema.measure_columns}
          truncate={6}
        />
      </div>

      {schema.soft_warnings.length > 0 && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-base flex items-center gap-2">
              <AlertTriangle className="size-4 text-amber-600" />
              Schema observations ({schema.soft_warnings.length})
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <ul className="text-sm space-y-1 list-disc pl-5">
              {schema.soft_warnings.map((w, i) => <li key={i}>{w}</li>)}
            </ul>
            {(schema.coercible_columns?.length ?? 0) > 0 && (
              <div className="rounded-md border border-sky-200 bg-sky-50 p-3 text-xs text-sky-900 flex items-start gap-3">
                <Wand2 className="size-4 mt-0.5 shrink-0" />
                <div className="flex-1 space-y-0.5">
                  <p>
                    <span className="font-medium">Fix <code className="font-mono">{schema.coercible_columns!.join(", ")}</code>:</span> keep the numbers, clear the text cells, include in analysis.
                  </p>
                  <p className="text-sky-900/70">Skip and that period stays out of anomaly checks.</p>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  className="bg-background"
                  onClick={() => coerce.mutate(schema.coercible_columns!)}
                  disabled={coerce.isPending}
                >
                  {coerce.isPending ? "Fixing…" : "Fix and include"}
                </Button>
              </div>
            )}
            <div className="rounded-md border border-amber-200 bg-amber-50 p-3 text-xs text-amber-900">
              Doesn't block detection. Fix in your source file and re-upload,
              or click <span className="font-medium">Run detection</span> to continue.
            </div>
          </CardContent>
        </Card>
      )}

      <div className="flex gap-2 justify-end pt-2">
        <Button variant="outline" onClick={() => setEditing(true)}>
          Adjust column roles
        </Button>
        <Button onClick={onAdvance} disabled={!ok}>
          Run detection →
        </Button>
      </div>

      {editing && (
        <OverrideModal
          slug={slug}
          fileId={fileId}
          schema={schema}
          onClose={() => setEditing(false)}
        />
      )}
    </div>
  );
}

function RoleCard({
  title,
  subtitle,
  columns,
  truncate,
}: {
  title: string;
  subtitle: string;
  columns: string[];
  truncate?: number;
}) {
  const shown = truncate ? columns.slice(0, truncate) : columns;
  const hidden = truncate ? columns.length - shown.length : 0;
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm">{title}</CardTitle>
        <p className="text-xs text-muted-foreground">{subtitle}</p>
      </CardHeader>
      <CardContent className="space-y-1.5">
        <div className="text-2xl font-semibold tabular-nums">{columns.length}</div>
        <div className="flex flex-wrap gap-1">
          {shown.map((c) => (
            <Badge key={c} variant="secondary" className="font-mono">{c}</Badge>
          ))}
          {hidden > 0 && <Badge variant="outline">+{hidden}</Badge>}
        </div>
      </CardContent>
    </Card>
  );
}
