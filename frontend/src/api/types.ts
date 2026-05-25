// Wire types that mirror DATA_MODEL.md. Kept identical to the backend's
// JSON shapes so swapping the mock client for a real fetch is a one-line
// change.

export type AnomalyType = "negative" | "refund" | "double_booking" | "outlier";

export type SuggestedFix =
  | "set_to_zero"
  | "split_evenly"
  | "keep_as_is";

export type FileStatus =
  | "uploaded"
  | "schema_pending"
  | "detected"
  | "cleaning"
  | "cleaned";

export interface ProjectFile {
  file_id: string;
  original_filename: string;
  uploaded_at: string;
  row_count: number;
  status: FileStatus;
  anomaly_counts?: Record<AnomalyType, number>;
  applied_changes?: number;
  cleaned_at?: string;
}

export interface Manifest {
  project_name: string;
  project_slug: string;
  created_at: string;
  updated_at: string;
  files: ProjectFile[];
}

export interface SchemaResult {
  id_columns: string[];
  time_columns: string[];
  measure_columns: string[];
  // Every column in the source file, in original order. Used by the
  // override modal so excluded columns can be re-assigned.
  all_columns?: string[];
  time_format: string | null;
  hard_errors: string[];
  soft_warnings: string[];
  // Time-named columns where a tiny fraction of values won't coerce.
  // The UI offers a "Coerce" button against these.
  coercible_columns?: string[];
}

export interface Detection {
  detection_id: string;
  // Row position within the preview window. The backend always returns it
  // alongside ``row_key`` so the UI can cross-reference cells without
  // re-resolving identifiers against the loaded preview range.
  row_idx?: number;
  row_key: Record<string, string | number>;
  column: string;
  value: number;
  flagged_by: AnomalyType[];
  suggested_fix: SuggestedFix;
  confidence: number;
  alternative_fixes: SuggestedFix[];
}

export interface DetectionsPage {
  detections: Detection[];
  cursor: string | null;
  total: number;
  counts: Record<AnomalyType, number>;
}

export interface PreviewPage {
  rows: Array<Record<string, string | number>>;
  cursor: string | null;
  total: number;
  columns: string[];
}

export interface ApplyResponse {
  file_id: string;
  applied_at: string;
  summary: Record<string, number>;
  total_changes: number;
  cleaned_url: string;
  audit_url: string;
}
