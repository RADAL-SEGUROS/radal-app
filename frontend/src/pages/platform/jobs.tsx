/**
 * Mantenedor → Tareas programadas (`/platform/jobs`, v12 02 §10).
 *
 * Read-only: the scheduler (EventBridge → `POST /events`, or
 * `POST /internal/jobs/{name}`) runs the jobs; this page shows what happened.
 *
 * - "Última ejecución": one card per REGISTERED job (the server lists every
 *   name, so a job that never ran still shows — as "Sin ejecuciones").
 * - History: every `job_run` row, filterable by job and status, newest first;
 *   a row opens its stats and error.
 *
 * Platform users only — nothing here touches a broker endpoint.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { motion } from "framer-motion";
import { ListChecks, Loader2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { FadeUp, Stagger, fadeUpItem } from "@/components/common/motion";
import { EmptyState, ErrorBanner, LoadingRows } from "@/components/common/kit";
import {
  JOB_STATUSES,
  useJobRuns,
  useLatestJobRuns,
  type JobParams,
  type JobRun,
  type JobStatus,
} from "@/api/platform";
import { formatDateTime } from "@/lib/format";
import {
  JOB_LABEL,
  JOB_STATUS_LABEL,
  JOB_STATUS_TONE,
  JsonBlock,
  Pager,
  PlatformHeader,
  durationSeconds,
  labelFrom,
} from "./components/shared";

type TFn = (key: string, options?: Record<string, unknown>) => string;

const ALL = "__all__";
const LIMIT = 50;

export default function PlatformJobsPage() {
  const { t: rawT } = useTranslation("platform");
  const t = rawT as unknown as TFn;
  const [job, setJob] = React.useState<string>(ALL);
  const [status, setStatus] = React.useState<JobStatus | typeof ALL>(ALL);
  const [offset, setOffset] = React.useState(0);
  const [open, setOpen] = React.useState<JobRun | null>(null);

  const params: JobParams = {
    job: job === ALL ? undefined : job,
    status: status === ALL ? undefined : status,
    limit: LIMIT,
    offset,
  };
  const history = useJobRuns(params);
  const registered = React.useMemo(
    () => [...(history.data?.registered ?? [])].sort(),
    [history.data?.registered],
  );
  const latest = useLatestJobRuns(registered);
  const rows = history.data?.items ?? [];

  return (
    <>
      <PlatformHeader title={t("jobs.title")} subtitle={t("jobs.description")} />

      <ErrorBanner error={history.error} className="mb-4" />

      {/* Latest run per registered job */}
      <section className="mb-8 flex flex-col gap-3">
        <h2 className="text-h3 tracking-tight text-ink">{t("jobs.latest")}</h2>
        {history.isLoading ? (
          <Card className="p-5">
            <LoadingRows rows={3} />
          </Card>
        ) : registered.length === 0 ? (
          <Card>
            <EmptyState icon={<ListChecks />} title={t("jobs.noRegistered")} />
          </Card>
        ) : (
          <Stagger className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {registered.map((name, i) => {
              const q = latest[i];
              const run = q?.data ?? null;
              return (
                <motion.div key={name} variants={fadeUpItem}>
                  <Card className="flex h-full flex-col gap-2 p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-body font-medium text-ink">
                          {labelFrom(t, JOB_LABEL, name)}
                        </p>
                        <p className="truncate font-mono text-[12px] text-ink-3">{name}</p>
                      </div>
                      {q?.isLoading ? (
                        <Loader2 className="h-4 w-4 animate-spin text-ink-3" aria-hidden />
                      ) : run ? (
                        <JobBadge status={run.status} t={t} />
                      ) : null}
                    </div>
                    {q?.isLoading ? null : run ? (
                      <button
                        type="button"
                        onClick={() => setOpen(run)}
                        className="-mx-1.5 mt-auto rounded-md px-1.5 py-1 text-left text-caption text-ink-3 transition-[background-color,color] duration-150 ease-out hover:bg-paper-2 hover:text-ink"
                      >
                        <span className="tabular-nums">{formatDateTime(run.scheduled_for)}</span>
                        {run.error ? (
                          <span className="mt-0.5 line-clamp-2 block text-neg-text">{run.error}</span>
                        ) : null}
                      </button>
                    ) : (
                      <p className="mt-auto text-caption text-ink-3">
                        {q?.error ? t("jobs.latestError") : t("jobs.never")}
                      </p>
                    )}
                  </Card>
                </motion.div>
              );
            })}
          </Stagger>
        )}
      </section>

      {/* History */}
      <FadeUp className="flex flex-col gap-4">
        <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center sm:justify-between">
          <h2 className="text-h3 tracking-tight text-ink">{t("jobs.history")}</h2>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <Select
              value={job}
              onValueChange={(v) => {
                setJob(v);
                setOffset(0);
              }}
            >
              <SelectTrigger className="w-full sm:w-60" aria-label={t("jobs.columns.job")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{t("jobs.allJobs")}</SelectItem>
                {registered.map((name) => (
                  <SelectItem key={name} value={name}>
                    {labelFrom(t, JOB_LABEL, name)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={status}
              onValueChange={(v) => {
                setStatus(v as JobStatus | typeof ALL);
                setOffset(0);
              }}
            >
              <SelectTrigger className="w-full sm:w-48" aria-label={t("jobs.columns.status")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{t("jobs.allStatuses")}</SelectItem>
                {JOB_STATUSES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {t(JOB_STATUS_LABEL[s])}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>

        <Card className="overflow-hidden">
          {history.isLoading ? (
            <div className="p-5">
              <LoadingRows rows={6} />
            </div>
          ) : rows.length === 0 ? (
            <EmptyState icon={<ListChecks />} title={t("jobs.empty")} />
          ) : (
            <>
              <div className="hidden md:block">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>{t("jobs.columns.job")}</TableHead>
                      <TableHead>{t("jobs.columns.scheduled")}</TableHead>
                      <TableHead>{t("jobs.columns.status")}</TableHead>
                      <TableHead className="text-right">{t("jobs.columns.duration")}</TableHead>
                      <TableHead>{t("jobs.columns.result")}</TableHead>
                      <TableHead className="w-0" />
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rows.map((run) => {
                      const secs = durationSeconds(run.started_at, run.finished_at);
                      return (
                        <TableRow key={run.id ?? `${run.job}-${run.scheduled_for}`}>
                          <TableCell>
                            <p className="text-body text-ink">{labelFrom(t, JOB_LABEL, run.job)}</p>
                            <p className="font-mono text-[12px] text-ink-3">{run.job}</p>
                          </TableCell>
                          <TableCell className="whitespace-nowrap text-caption tabular-nums">
                            {formatDateTime(run.scheduled_for)}
                          </TableCell>
                          <TableCell>
                            <JobBadge status={run.status} t={t} />
                          </TableCell>
                          <TableCell className="text-right text-caption tabular-nums">
                            {secs === null ? "—" : t("jobs.seconds", { value: secs })}
                          </TableCell>
                          <TableCell className="max-w-[20rem] truncate text-caption">
                            {run.error ? (
                              <span className="text-neg-text">{run.error}</span>
                            ) : (
                              <span className="text-ink-3">{statsSummary(run)}</span>
                            )}
                          </TableCell>
                          <TableCell>
                            <Button variant="ghost" size="sm" className="min-h-9" onClick={() => setOpen(run)}>
                              {t("jobs.viewDetail")}
                            </Button>
                          </TableCell>
                        </TableRow>
                      );
                    })}
                  </TableBody>
                </Table>
              </div>

              <ul className="flex flex-col divide-y divide-line md:hidden">
                {rows.map((run) => {
                  const secs = durationSeconds(run.started_at, run.finished_at);
                  return (
                    <li key={run.id ?? `${run.job}-${run.scheduled_for}`}>
                      <button
                        type="button"
                        onClick={() => setOpen(run)}
                        className="flex w-full flex-col gap-1.5 p-4 text-left transition-[background-color] duration-150 ease-out hover:bg-paper-2"
                      >
                        <span className="flex items-start justify-between gap-3">
                          <span className="min-w-0 truncate font-medium text-ink">
                            {labelFrom(t, JOB_LABEL, run.job)}
                          </span>
                          <JobBadge status={run.status} t={t} />
                        </span>
                        <span className="text-caption tabular-nums text-ink-3">
                          {formatDateTime(run.scheduled_for)}
                          {secs === null ? "" : ` · ${t("jobs.seconds", { value: secs })}`}
                        </span>
                        {run.error ? (
                          <span className="line-clamp-2 text-caption text-neg-text">{run.error}</span>
                        ) : null}
                      </button>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </Card>

        {history.data ? (
          <Pager
            total={history.data.total}
            limit={history.data.limit}
            offset={history.data.offset}
            onOffset={setOffset}
          />
        ) : null}
      </FadeUp>

      <RunDialog run={open} onClose={() => setOpen(null)} />
    </>
  );
}

/** "3 sent · 1 failed" from the job's own stats dict (numbers only, keys verbatim). */
function statsSummary(run: JobRun): string {
  const stats = run.stats ?? {};
  const parts = Object.entries(stats)
    .filter(([, v]) => typeof v === "number" || typeof v === "string" || typeof v === "boolean")
    .slice(0, 4)
    .map(([k, v]) => `${k}: ${String(v)}`);
  return parts.length ? parts.join(" · ") : "—";
}

function JobBadge({ status, t }: { status: JobStatus; t: TFn }) {
  return (
    <Badge variant={JOB_STATUS_TONE[status] ?? "neutral"} dot className="whitespace-nowrap">
      {labelFrom(t, JOB_STATUS_LABEL, status)}
    </Badge>
  );
}

function RunDialog({ run, onClose }: { run: JobRun | null; onClose: () => void }) {
  const { t: rawT } = useTranslation("platform");
  const t = rawT as unknown as TFn;
  const secs = run ? durationSeconds(run.started_at, run.finished_at) : null;
  return (
    <Dialog open={run !== null} onOpenChange={(next) => (!next ? onClose() : undefined)}>
      <DialogContent className="max-h-[92vh] max-w-xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{run ? labelFrom(t, JOB_LABEL, run.job) : ""}</DialogTitle>
          <DialogDescription>
            {run ? (
              <span className="tabular-nums">
                {formatDateTime(run.scheduled_for)}
                {secs === null ? "" : ` · ${t("jobs.seconds", { value: secs })}`}
              </span>
            ) : null}
          </DialogDescription>
        </DialogHeader>
        {run ? (
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-2">
              <JobBadge status={run.status} t={t} />
              <span className="font-mono text-[12px] text-ink-3">{run.job}</span>
            </div>
            <dl className="grid grid-cols-1 gap-3 text-caption sm:grid-cols-2">
              <div>
                <dt className="text-ink-3">{t("jobs.startedAt")}</dt>
                <dd className="mt-0.5 tabular-nums text-ink-2">
                  {run.started_at ? formatDateTime(run.started_at) : "—"}
                </dd>
              </div>
              <div>
                <dt className="text-ink-3">{t("jobs.finishedAt")}</dt>
                <dd className="mt-0.5 tabular-nums text-ink-2">
                  {run.finished_at ? formatDateTime(run.finished_at) : "—"}
                </dd>
              </div>
            </dl>
            {run.error ? (
              <p className="rounded-lg bg-neg-soft px-3 py-2 text-caption text-neg-text">{run.error}</p>
            ) : null}
            <section className="flex flex-col gap-2">
              <h3 className="text-body font-medium text-ink">{t("jobs.statsTitle")}</h3>
              <JsonBlock value={run.stats} empty={t("jobs.noStats")} />
            </section>
          </div>
        ) : null}
        <DialogFooter>
          <Button variant="secondary" onClick={onClose}>
            {t("common.close")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
