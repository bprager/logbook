import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Clock3,
  Moon,
  RefreshCw,
  Sun,
  XCircle,
} from "lucide-react"
import { useEffect, useMemo, useState } from "react"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Progress } from "@/components/ui/progress"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { cn } from "@/lib/utils"

type Health = {
  api: string
  sqlite: string
  odin: string
  memgraph: string
}

type JobOutcome = {
  job_id: number
  status: string
  classification: string | null
  recorded_at: string | null
  finished_at: string
  duration_seconds: number | null
  vault_synced: boolean
}

type Failure = {
  job_id: number
  status: string
  classification: string | null
  occurred_at: string
  safe_detail: string
}

type Stats = {
  window: string
  jobs_seen: number
  succeeded: number
  failed: number
  dead_letters: number
  p50_duration_seconds: number
  p90_duration_seconds: number
}

type Snapshot = {
  generated_at: string
  latest_finished_at: string | null
  health: Health
  current_run: Record<string, unknown> | null
  active_stage: Record<string, unknown> | null
  recent_finished: JobOutcome[]
  recent_failures: Failure[]
  stats: Stats
}

type Theme = "day" | "night"

const REFRESH_MS = 2500

export default function App() {
  const [snapshot, setSnapshot] = useState<Snapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [updatedAt, setUpdatedAt] = useState<Date | null>(null)
  const [theme, setTheme] = useState<Theme>(() => automaticTheme())

  async function refresh() {
    try {
      const response = await fetch("/observer/snapshot", { cache: "no-store" })
      if (!response.ok) {
        throw new Error(`snapshot request failed: ${response.status}`)
      }
      setSnapshot((await response.json()) as Snapshot)
      setUpdatedAt(new Date())
      setError(null)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "snapshot request failed")
    }
  }

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "night")
  }, [theme])

  useEffect(() => {
    const themeTimer = window.setInterval(() => setTheme(automaticTheme()), 60_000)
    return () => window.clearInterval(themeTimer)
  }, [])

  useEffect(() => {
    void refresh()
    const timer = window.setInterval(() => void refresh(), REFRESH_MS)
    return () => window.clearInterval(timer)
  }, [])

  const stage = snapshot?.active_stage
  const progressPercent = numeric(stage?.progress_percent)
  const running = snapshot?.current_run !== null && snapshot?.current_run !== undefined
  const failureCount = snapshot?.recent_failures.length ?? 0

  return (
    <main className="min-h-screen bg-background text-foreground">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-4 px-4 py-4 sm:px-6">
        <header className="flex flex-col gap-3 border-b pb-3 lg:flex-row lg:items-center lg:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-semibold tracking-normal">Logbook Watch</h1>
              <Badge variant={running ? "success" : "secondary"}>
                {running ? "running" : "idle"}
              </Badge>
              {failureCount > 0 ? (
                <Badge variant="destructive">{failureCount} failures</Badge>
              ) : (
                <Badge variant="outline">no recent failures</Badge>
              )}
            </div>
            <p className="mt-1 text-sm text-muted-foreground">
              {formatTimestamp(snapshot?.generated_at, "waiting for first snapshot")}
            </p>
            <p className="mt-0.5 text-sm text-muted-foreground">
              Latest finished job: {formatTimestamp(snapshot?.latest_finished_at)}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Badge variant="outline" className="gap-1">
              {theme === "night" ? <Moon className="h-3.5 w-3.5" /> : <Sun className="h-3.5 w-3.5" />}
              {theme}
            </Badge>
            <span className="text-xs text-muted-foreground">
              {updatedAt ? `updated ${updatedAt.toLocaleTimeString()}` : "not updated yet"}
            </span>
            <Button variant="outline" size="sm" onClick={() => void refresh()}>
              <RefreshCw className="h-4 w-4" />
              Refresh
            </Button>
          </div>
        </header>

        {error ? (
          <Card className="border-destructive">
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-destructive">
                <AlertTriangle className="h-4 w-4" />
                Snapshot unavailable
              </CardTitle>
              <CardDescription>{error}</CardDescription>
            </CardHeader>
          </Card>
        ) : null}

        <section className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <HealthCard label="API" value={snapshot?.health.api} />
          <HealthCard label="SQLite" value={snapshot?.health.sqlite} />
          <HealthCard label="Odin" value={snapshot?.health.odin} />
          <HealthCard label="Graph" value={snapshot?.health.memgraph} />
        </section>

        <section className="grid gap-4 lg:grid-cols-[minmax(0,1.2fr)_minmax(320px,0.8fr)]">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Activity className="h-4 w-4" />
                Active work
              </CardTitle>
              <CardDescription>{runSummary(snapshot)}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid gap-3 sm:grid-cols-3">
                <Metric label="Stage" value={text(stage?.stage) ?? "none" } />
                <Metric label="Job" value={text(stage?.job_id) ?? "-" } />
                <Metric label="Elapsed" value={duration(numeric(stage?.elapsed_seconds))} />
              </div>
              <div className="space-y-2">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="text-sm font-medium">Progress</span>
                  <span className="text-xs text-muted-foreground">
                    {progressPercent.toFixed(0)}% {text(stage?.progress_kind) ?? "unknown"}
                  </span>
                </div>
                <Progress value={progressPercent} />
                <p className="text-xs text-muted-foreground">{etaSummary(stage)}</p>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Clock3 className="h-4 w-4" />
                Window statistics
              </CardTitle>
              <CardDescription>{snapshot?.stats.window ?? "24h"} rolling window</CardDescription>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-3">
              <Metric label="Jobs" value={snapshot?.stats.jobs_seen ?? 0} />
              <Metric label="Success" value={snapshot?.stats.succeeded ?? 0} />
              <Metric label="Failed" value={snapshot?.stats.failed ?? 0} />
              <Metric label="Dead letters" value={snapshot?.stats.dead_letters ?? 0} />
              <Metric label="p50" value={duration(snapshot?.stats.p50_duration_seconds ?? 0)} />
              <Metric label="p90" value={duration(snapshot?.stats.p90_duration_seconds ?? 0)} />
            </CardContent>
          </Card>
        </section>

        <Tabs defaultValue="finished">
          <TabsList>
            <TabsTrigger value="finished">Finished</TabsTrigger>
            <TabsTrigger value="failures">Failures</TabsTrigger>
          </TabsList>
          <TabsContent value="finished">
            <JobTable items={snapshot?.recent_finished ?? []} />
          </TabsContent>
          <TabsContent value="failures">
            <FailureTable items={snapshot?.recent_failures ?? []} />
          </TabsContent>
        </Tabs>
      </div>
    </main>
  )
}

function HealthCard({ label, value }: { label: string; value?: string }) {
  const variant = value === "ok" || value === "not_configured" ? "success" : value ? "warning" : "secondary"
  return (
    <Card>
      <CardContent className="flex items-center justify-between gap-3 p-3">
        <span className="text-sm font-medium">{label}</span>
        <Badge variant={variant}>{value ?? "unknown"}</Badge>
      </CardContent>
    </Card>
  )
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md border bg-muted/35 p-3">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 truncate text-sm font-semibold">{value}</div>
    </div>
  )
}

function JobTable({ items }: { items: JobOutcome[] }) {
  if (items.length === 0) {
    return <EmptyState icon={<CheckCircle2 className="h-4 w-4" />} text="No finished jobs in the window" />
  }
  return (
    <div className="overflow-hidden rounded-lg border">
      <div className="grid grid-cols-[80px_1fr_120px_120px] bg-muted px-3 py-2 text-xs font-medium text-muted-foreground">
        <span>Job</span>
        <span>Status</span>
        <span>Class</span>
        <span>Duration</span>
      </div>
      {items.map((item) => (
        <div
          className="grid grid-cols-[80px_1fr_120px_120px] items-center border-t px-3 py-2 text-sm"
          key={item.job_id}
        >
          <span>#{item.job_id}</span>
          <span className="truncate">{item.status}</span>
          <span className="truncate text-muted-foreground">{item.classification ?? "-"}</span>
          <span>{duration(item.duration_seconds)}</span>
        </div>
      ))}
    </div>
  )
}

function FailureTable({ items }: { items: Failure[] }) {
  if (items.length === 0) {
    return <EmptyState icon={<CheckCircle2 className="h-4 w-4" />} text="No failures or review items" />
  }
  return (
    <div className="overflow-hidden rounded-lg border">
      <div className="grid grid-cols-[80px_1fr_130px] bg-muted px-3 py-2 text-xs font-medium text-muted-foreground">
        <span>Job</span>
        <span>Status</span>
        <span>Detail</span>
      </div>
      {items.map((item) => (
        <div
          className="grid grid-cols-[80px_1fr_130px] items-center border-t px-3 py-2 text-sm"
          key={item.job_id}
        >
          <span className="flex items-center gap-2">
            <XCircle className="h-4 w-4 text-destructive" />
            #{item.job_id}
          </span>
          <span className="truncate">{item.status}</span>
          <span className="truncate text-muted-foreground">{item.safe_detail}</span>
        </div>
      ))}
    </div>
  )
}

function EmptyState({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <div className="flex items-center gap-2 rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
      {icon}
      {text}
    </div>
  )
}

function automaticTheme(): Theme {
  const hour = new Date().getHours()
  return hour >= 7 && hour < 19 ? "day" : "night"
}

function runSummary(snapshot: Snapshot | null) {
  if (!snapshot?.current_run) {
    return "No pipeline run is active"
  }
  const stale = snapshot.current_run.stale ? " stale" : ""
  return `${text(snapshot.current_run.command) ?? "pipeline"} on ${text(snapshot.current_run.host) ?? "local"}${stale}`
}

function etaSummary(stage: Record<string, unknown> | null | undefined) {
  if (!stage) {
    return "No active stage is reporting progress"
  }
  if (stage.eta_status === "collecting_baseline") {
    return `Collecting ETA baseline from ${text(stage.sample_count) ?? 0} comparable samples`
  }
  const eta = numeric(stage.eta_seconds)
  if (eta > 0) {
    return `ETA ${duration(eta)} from ${text(stage.sample_count) ?? 0} samples`
  }
  return "Measured progress is available for this stage"
}

function text(value: unknown): string | null {
  if (value === null || value === undefined || value === "") {
    return null
  }
  return String(value)
}

function numeric(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0
}

function duration(seconds: number | null | undefined): string {
  if (!seconds || seconds < 0) {
    return "00:00"
  }
  const total = Math.round(seconds)
  const mins = Math.floor(total / 60)
  const secs = total % 60
  const hours = Math.floor(mins / 60)
  const restMins = mins % 60
  if (hours > 0) {
    return `${hours}:${restMins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`
  }
  return `${restMins.toString().padStart(2, "0")}:${secs.toString().padStart(2, "0")}`
}

function formatTimestamp(value: string | null | undefined, fallback = "none"): string {
  if (!value) {
    return fallback
  }
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }
  return date.toLocaleString()
}
