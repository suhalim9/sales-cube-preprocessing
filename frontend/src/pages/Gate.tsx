import { useState } from "react";
import { useNavigate } from "react-router";
import { getOrCreateProject, listRecent } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";

export function Gate() {
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const recent = listRecent();

  async function onContinue() {
    if (!name.trim() || submitting) return;
    setSubmitting(true);
    try {
      const m = await getOrCreateProject(name.trim());
      navigate(`/project/${m.project_slug}`);
    } finally {
      setSubmitting(false);
    }
  }

  function openRecent(slug: string) {
    navigate(`/project/${slug}`);
  }

  return (
    <div className="min-h-svh flex items-center justify-center keye-hero p-6">
      <div className="w-full max-w-md space-y-6">
        <div className="text-center space-y-3">
          <div className="inline-flex items-center gap-2 text-xs tracking-[0.2em] uppercase text-white/60">
            <span className="size-1.5 rounded-full bg-primary" />
            Keye · Diligence Platform
          </div>
          <h1 className="text-4xl font-semibold tracking-tight text-white">Sales Cube Cleaner</h1>
          <p className="text-sm text-white/70">
            Detect anomalies in customer × product × period sales cubes.
          </p>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>What project are you working on?</CardTitle>
            <CardDescription>
              A project groups together the files for one diligence target.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <Input
              autoFocus
              placeholder="e.g. Acme diligence Q1"
              value={name}
              onChange={(e) => setName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onContinue()}
            />
            <Button
              className="w-full"
              disabled={!name.trim() || submitting}
              onClick={onContinue}
            >
              Continue
            </Button>
          </CardContent>
        </Card>

        {recent.length > 0 && (
          <Card>
            <CardHeader className="pb-3">
              <CardTitle className="text-base">Recent projects</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 p-3 pt-0">
              {recent.map((p) => (
                <button
                  key={p.slug}
                  onClick={() => openRecent(p.slug)}
                  className="w-full text-left px-3 py-2 rounded-md hover:bg-accent transition-colors flex items-center justify-between gap-3"
                >
                  <span className="text-sm font-medium">{p.name}</span>
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {p.file_count} {p.file_count === 1 ? "file" : "files"} ·{" "}
                    {formatRelative(p.last_active)}
                  </span>
                </button>
              ))}
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  );
}

function formatRelative(iso: string): string {
  const then = new Date(iso).getTime();
  const diff = (Date.now() - then) / 1000;
  if (diff < 60) return "just now";
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}
