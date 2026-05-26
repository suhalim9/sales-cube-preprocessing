// Real backend client — talks to FastAPI at VITE_API_BASE (default localhost:8000/api).
//
// Upload is two-step: POST /files → returns a presigned PUT URL → browser PUTs
// the bytes directly to S3. Backend never sees the file body.
//
// Recent projects live in localStorage (no auth → per-browser memory).

import type {
  AnomalyType,
  ApplyResponse,
  DetectionsPage,
  Manifest,
  PreviewPage,
  SchemaResult,
  SuggestedFix,
} from "./types";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8000/api";
const RECENT_KEY = "recent_projects";

export class UploadError extends Error {}
export class ApiError extends Error {
  readonly status: number;
  readonly code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

interface RecentProject {
  name: string;
  slug: string;
  last_active: string;
  file_count: number;
}

// ---------------------------------------------------------------------------
// Recent projects (localStorage)
// ---------------------------------------------------------------------------

export function listRecent(): RecentProject[] {
  const raw = localStorage.getItem(RECENT_KEY);
  if (!raw) return [];
  try {
    return JSON.parse(raw) as RecentProject[];
  } catch {
    return [];
  }
}

function touchRecent(m: Manifest) {
  const list = listRecent().filter((p) => p.slug !== m.project_slug);
  list.unshift({
    name: m.project_name,
    slug: m.project_slug,
    last_active: m.updated_at,
    file_count: m.files.length,
  });
  localStorage.setItem(RECENT_KEY, JSON.stringify(list.slice(0, 8)));
}

// ---------------------------------------------------------------------------
// HTTP helper
// ---------------------------------------------------------------------------

async function api<T>(
  path: string,
  init?: RequestInit & { json?: unknown },
): Promise<T> {
  const headers: Record<string, string> = {
    Accept: "application/json",
    ...((init?.headers ?? {}) as Record<string, string>),
  };
  let body: BodyInit | undefined = init?.body as BodyInit | undefined;
  if (init?.json !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(init.json);
  }
  let resp: Response;
  try {
    resp = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
      body,
    });
  } catch (err) {
    // fetch() rejects only on network failure — CORS blocked, DNS, connection
    // reset, the backend crashed mid-request and Fly's proxy hung up, etc.
    // Wrap these in ApiError with status 0 so callers don't have to
    // distinguish ApiError from raw TypeError in their error handling.
    // The browser console will still show the real CORS/network error.
    throw new ApiError(
      "Server unavailable. The backend isn't responding — it may have crashed or be restarting. Try again in a moment.",
      0,
      "network",
    );
  }
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      const json = await resp.json();
      detail = json.detail ?? detail;
    } catch {
      /* body wasn't JSON */
    }
    throw new ApiError(detail, resp.status);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

function slugify(name: string): string {
  return (
    name
      .toLowerCase()
      .trim()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-|-$/g, "")
      .slice(0, 64) || "untitled"
  );
}

export async function getOrCreateProject(name: string): Promise<Manifest> {
  const slug = slugify(name);
  const m = await api<Manifest>(`/projects/${slug}`);
  // Backend returns slug-only project name on GET-without-create; backfill the
  // user-typed display name so the UI shows what they typed.
  if (m.project_name === m.project_slug && name !== slug) {
    m.project_name = name;
  }
  touchRecent(m);
  return m;
}

export async function getProject(slug: string): Promise<Manifest | null> {
  try {
    const m = await api<Manifest>(`/projects/${slug}`);
    touchRecent(m);
    return m;
  } catch (e) {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  }
}

export type UploadPhase = "validating" | "uploading" | "parsing" | "done";

export interface UploadProgress {
  phase: UploadPhase;
  percent: number; // 0..100 during uploading, 100 otherwise
  bytesSent?: number;
  bytesTotal?: number;
}

export interface UploadOptions {
  onProgress?: (p: UploadProgress) => void;
}

// Two-step upload: server issues presigned PUT, browser uploads directly.
// Uses XHR (not fetch) so we can wire upload.onprogress.
export async function uploadFile(
  slug: string,
  file: File,
  options: UploadOptions = {},
): Promise<Manifest> {
  const emit = (p: UploadProgress) => options.onProgress?.(p);

  emit({ phase: "validating", percent: 0 });
  if (file.size === 0) throw new UploadError("File is empty.");
  if (!file.name.toLowerCase().endsWith(".parquet")) {
    throw new UploadError("Only Parquet files are supported in this demo.");
  }
  // Magic-byte sniff before we even ask for a URL.
  const head = await file.slice(0, 4).text();
  if (head !== "PAR1") {
    throw new UploadError("File doesn't look like a Parquet (header bytes don't match).");
  }

  const contentType = file.type || "application/vnd.apache.parquet";
  const created = await api<{
    file_id: string;
    upload_url: string;
    upload_headers: Record<string, string>;
  }>(`/projects/${slug}/files`, {
    method: "POST",
    json: { filename: file.name, content_type: contentType },
  });

  // PUT to S3 via presigned URL. XHR gives us upload progress events.
  emit({ phase: "uploading", percent: 0, bytesSent: 0, bytesTotal: file.size });
  await new Promise<void>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", created.upload_url, true);
    for (const [k, v] of Object.entries(created.upload_headers)) {
      xhr.setRequestHeader(k, v);
    }
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) {
        emit({
          phase: "uploading",
          percent: Math.min(99, Math.round((e.loaded / e.total) * 100)),
          bytesSent: e.loaded,
          bytesTotal: e.total,
        });
      }
    };
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        emit({ phase: "uploading", percent: 100, bytesSent: file.size, bytesTotal: file.size });
        resolve();
      } else {
        reject(new UploadError(`Upload failed (HTTP ${xhr.status}).`));
      }
    };
    xhr.onerror = () => reject(new UploadError("Network error during upload."));
    xhr.onabort = () => reject(new UploadError("Upload aborted."));
    xhr.send(file);
  });

  // Trigger parse + schema inference; this is what actually adds the file
  // to the manifest. If parse fails, no orphan is left behind.
  emit({ phase: "parsing", percent: 100 });
  await api(`/projects/${slug}/files/${created.file_id}/parse`, {
    method: "POST",
    json: { filename: file.name },
  });
  emit({ phase: "done", percent: 100 });
  return (await getProject(slug))!;
}

export async function getSchema(slug: string, fileId: string): Promise<SchemaResult> {
  return api<SchemaResult>(`/projects/${slug}/files/${fileId}/schema`);
}

export interface SchemaOverride {
  id_columns: string[];
  time_columns: string[];
  measure_columns: string[];
}

export async function overrideSchema(
  slug: string,
  fileId: string,
  body: SchemaOverride,
): Promise<SchemaResult> {
  return api<SchemaResult>(`/projects/${slug}/files/${fileId}/schema`, {
    method: "PATCH",
    json: body,
  });
}

export async function coerceColumns(
  slug: string,
  fileId: string,
  columns: string[],
): Promise<SchemaResult> {
  return api<SchemaResult>(`/projects/${slug}/files/${fileId}/coerce`, {
    method: "POST",
    json: { columns },
  });
}

export async function runDetection(slug: string, fileId: string): Promise<DetectionsPage> {
  return api<DetectionsPage>(`/projects/${slug}/files/${fileId}/detect`, { method: "POST" });
}

export async function getDetections(slug: string, fileId: string): Promise<DetectionsPage> {
  return api<DetectionsPage>(`/projects/${slug}/files/${fileId}/detections`);
}

export async function getPreview(
  slug: string,
  fileId: string,
  offset = 0,
  limit = 100,
  detected?: string,
): Promise<PreviewPage> {
  const q = new URLSearchParams({ offset: String(offset), limit: String(limit) });
  if (detected) q.set("detected", detected);
  return api<PreviewPage>(`/projects/${slug}/files/${fileId}/preview?${q}`);
}

export async function deleteFile(slug: string, fileId: string): Promise<void> {
  await api<void>(`/projects/${slug}/files/${fileId}`, { method: "DELETE" });
}

export async function getCleanedUrl(slug: string, fileId: string): Promise<string> {
  const r = await api<{ url: string; expires_in: number }>(
    `/projects/${slug}/files/${fileId}/cleaned-url`,
  );
  return r.url;
}

export async function getAuditUrl(slug: string, fileId: string): Promise<string> {
  const r = await api<{ url: string; expires_in: number }>(
    `/projects/${slug}/files/${fileId}/audit-url`,
  );
  return r.url;
}

export async function applyFixes(
  slug: string,
  fileId: string,
  selections: Array<{
    detection_id: string;
    fix: SuggestedFix;
    attribution?: AnomalyType;
  }>,
): Promise<ApplyResponse> {
  const result = await api<{
    file_id: string;
    applied_at: string;
    summary: Record<string, number>;
    total_changes: number;
    cleaned_url: string;
    audit_url: string;
  }>(`/projects/${slug}/files/${fileId}/apply`, {
    method: "POST",
    json: { selections },
  });
  // The backend returns the *path* to the presigned URL endpoint, not the URL
  // itself. Resolve those here so the UI can hand them straight to an <a href>.
  const [cleanedUrl, auditUrl] = await Promise.all([
    resolvePresigned(result.cleaned_url),
    resolvePresigned(result.audit_url),
  ]);
  return { ...result, cleaned_url: cleanedUrl, audit_url: auditUrl };
}

async function resolvePresigned(path: string): Promise<string> {
  // path looks like "/api/projects/{slug}/files/{file_id}/cleaned-url"
  const stripped = path.replace(/^\/api/, "");
  const r = await api<{ url: string; expires_in: number }>(stripped);
  return r.url;
}
