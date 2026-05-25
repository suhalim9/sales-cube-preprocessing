import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, X } from "lucide-react";
import { overrideSchema } from "@/api/client";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { SchemaResult } from "@/api/types";

type Role = "id" | "time" | "measure" | "exclude";

export function OverrideModal({
  slug,
  fileId,
  schema,
  onClose,
}: {
  slug: string;
  fileId: string;
  schema: SchemaResult;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();

  // Build initial role map from the current schema. A column may be tagged
  // as both time and measure (typical for monthly cubes) — UI shows the
  // most-specific role: time > id > measure > exclude.
  const allColumns = useMemo(() => collectColumns(schema), [schema]);
  const initialRoles = useMemo(() => buildRoleMap(schema, allColumns), [schema, allColumns]);
  const [roles, setRoles] = useState<Record<string, Role>>(initialRoles);

  // Close on Escape — a tiny respect for keyboard users.
  useEffect(() => {
    const handler = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [onClose]);

  const save = useMutation({
    mutationFn: () => overrideSchema(slug, fileId, rolesToOverride(roles)),
    onSuccess: (result) => {
      // If backend accepted (no hard_errors), persist and close.
      if (result.hard_errors.length === 0) {
        queryClient.setQueryData(["schema", slug, fileId], result);
        onClose();
      }
      // Otherwise keep the modal open so user sees the errors.
    },
  });

  const changed = JSON.stringify(roles) !== JSON.stringify(initialRoles);
  const errors =
    save.data && save.data.hard_errors.length > 0 ? save.data.hard_errors : [];

  return (
    <div
      className="fixed inset-0 z-50 bg-black/40 flex items-center justify-center p-4"
      onClick={onClose}
    >
      <div
        className="bg-background rounded-lg shadow-xl w-full max-w-2xl max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="px-5 py-4 border-b flex items-start justify-between">
          <div>
            <h3 className="text-base font-semibold">Adjust column roles</h3>
            <p className="text-xs text-muted-foreground mt-0.5">
              Change a column's role if auto-detect got it wrong. Save re-validates
              against your file.
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            title="Close"
            className="p-1 -m-1 hover:bg-accent rounded"
          >
            <X className="size-4" />
          </button>
        </header>

        <div className="px-5 pt-4 pb-3 text-xs text-muted-foreground">
          <ul className="grid grid-cols-2 gap-x-4 gap-y-1.5">
            <li><span className="font-medium text-foreground">Identifier:</span> row-key column (e.g. <code className="font-mono">customer</code>). Never modified.</li>
            <li><span className="font-medium text-foreground">Time period:</span> date-named numeric column (e.g. <code className="font-mono">2022_5</code>). The cube's data.</li>
            <li><span className="font-medium text-foreground">Measure:</span> numeric column that isn't a time period. Rare in cubes.</li>
            <li><span className="font-medium text-foreground">Exclude from analysis:</span> skip in detection. File keeps the column.</li>
          </ul>
        </div>

        <div className="grid grid-cols-[1fr_1.2fr] text-xs text-muted-foreground bg-muted border-y">
          <div className="px-3 py-2 font-medium">Column</div>
          <div className="px-3 py-2 font-medium">Role</div>
        </div>

        <div className="flex-1 overflow-auto">
          {allColumns.map((col) => {
            const needsFix = schema.coercible_columns?.includes(col) ?? false;
            return (
              <div
                key={col}
                className={cn(
                  "grid grid-cols-[1fr_1.2fr] items-center border-b text-sm hover:bg-muted/30",
                  needsFix && "bg-amber-50/60",
                )}
              >
                <div className="px-3 py-2 font-mono text-xs flex items-center gap-2">
                  {col}
                  {needsFix && (
                    <span
                      className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-amber-100 text-amber-900 border border-amber-200"
                      title="This column has some text values mixed in. Close the modal and use 'Fix and include' to clean it up."
                    >
                      <AlertTriangle className="size-3" />
                      needs fix
                    </span>
                  )}
                </div>
                <div className="px-3 py-2">
                  <RoleSelect
                    value={roles[col]}
                    onChange={(role) => setRoles({ ...roles, [col]: role })}
                  />
                </div>
              </div>
            );
          })}
        </div>

        {errors.length > 0 && (
          <div className="mx-5 mb-3 rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive space-y-1">
            <div className="flex items-center gap-2 font-medium">
              <AlertTriangle className="size-4" />
              Override rejected
            </div>
            <ul className="text-xs list-disc pl-5">
              {errors.map((e, i) => <li key={i}>{e}</li>)}
            </ul>
          </div>
        )}

        <footer className="px-5 py-3 border-t flex items-center justify-end gap-2 bg-muted/20">
          <Button variant="ghost" onClick={onClose} disabled={save.isPending}>
            Cancel
          </Button>
          <Button
            onClick={() => save.mutate()}
            disabled={!changed || save.isPending}
          >
            {save.isPending ? "Validating…" : "Save"}
          </Button>
        </footer>
      </div>
    </div>
  );
}

function RoleSelect({
  value,
  onChange,
}: {
  value: Role;
  onChange: (r: Role) => void;
}) {
  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value as Role)}
      className={cn(
        "h-8 px-2 rounded-md border border-border bg-background text-xs",
        "focus:outline-none focus:ring-2 focus:ring-ring",
      )}
    >
      <option value="id">Identifier (row key)</option>
      <option value="time">Time period (date-named numeric)</option>
      <option value="measure">Measure (other numeric)</option>
      <option value="exclude">Exclude from analysis</option>
    </select>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function collectColumns(schema: SchemaResult): string[] {
  // Prefer backend's all_columns so excluded columns stay visible/editable.
  if (schema.all_columns && schema.all_columns.length > 0) {
    return [...schema.all_columns];
  }
  // Fallback: union of assigned roles (loses excluded columns).
  const set = new Set<string>();
  for (const c of schema.id_columns) set.add(c);
  for (const c of schema.time_columns) set.add(c);
  for (const c of schema.measure_columns) set.add(c);
  return Array.from(set);
}

function buildRoleMap(schema: SchemaResult, allColumns: string[]): Record<string, Role> {
  const m: Record<string, Role> = {};
  // Default every column to "exclude" — we'll overwrite below for those
  // currently assigned to a role.
  for (const c of allColumns) m[c] = "exclude";
  // Time wins over measure for display (typical cube has both on the same col).
  for (const c of schema.measure_columns) m[c] = "measure";
  for (const c of schema.id_columns) m[c] = "id";
  for (const c of schema.time_columns) m[c] = "time";
  return m;
}

function rolesToOverride(roles: Record<string, Role>) {
  const id_columns: string[] = [];
  const time_columns: string[] = [];
  const measure_columns: string[] = [];
  for (const [col, role] of Object.entries(roles)) {
    if (role === "id") id_columns.push(col);
    else if (role === "time") {
      time_columns.push(col);
      // Time columns in cubes are also measures (the numeric data lives there).
      // Including in both lists is what the backend expects.
      measure_columns.push(col);
    } else if (role === "measure") measure_columns.push(col);
    // "exclude" → omitted from all three lists
  }
  return { id_columns, time_columns, measure_columns };
}
