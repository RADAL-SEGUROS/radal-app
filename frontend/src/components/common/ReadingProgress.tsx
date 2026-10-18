/**
 * "Mientras la IA lee: barra, nunca pantalla quieta."
 *
 * An HONEST progress block for a long request the server cannot report on:
 * named steps (the real phases the client knows about — uploading, reading,
 * done), a bar that creeps toward the end of the current step on a time curve
 * (it never claims 100 % before the answer arrives) and a live seconds counter.
 * The bar is time-based by design: no AI read streams its progress today.
 *
 * `active` is the index of the running step; `active >= steps.length - 1`
 * renders the final step as done and fills the bar.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Check, Loader2 } from "lucide-react";

import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

export interface ReadingStep {
  label: string;
  /** Typical seconds this step takes — shapes the creep, never shown. */
  expectedSeconds?: number;
}

export function ReadingProgress({
  title,
  hint,
  steps,
  active,
  className,
}: {
  title: React.ReactNode;
  hint?: React.ReactNode;
  steps: ReadingStep[];
  active: number;
  className?: string;
}) {
  const { t } = useTranslation("common");
  const last = steps.length - 1;
  const done = active >= last;

  const startedAt = React.useRef(Date.now());
  const stepStartedAt = React.useRef(Date.now());
  const stepIndex = React.useRef(active);
  const [now, setNow] = React.useState(() => Date.now());

  // Restart the step clock the render the step changes (not an effect later,
  // which would paint one frame with the previous step's creep).
  if (stepIndex.current !== active) {
    stepIndex.current = active;
    stepStartedAt.current = Date.now();
  }

  React.useEffect(() => {
    if (done) return;
    const id = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(id);
  }, [done]);

  const elapsed = Math.max(0, Math.round((now - startedAt.current) / 1000));

  let value = 100;
  if (!done && last > 0) {
    const span = 100 / last;
    const expected = Math.max(1, steps[active]?.expectedSeconds ?? 10);
    const inStep = Math.max(0, (now - stepStartedAt.current) / 1000);
    // Asymptotic: ~63 % of the step at its expected time, never past 92 %.
    const creep = Math.min(0.92, 1 - Math.exp(-inStep / expected));
    value = Math.round(span * active + span * creep);
  }

  return (
    <div
      role="status"
      aria-live="polite"
      className={cn("flex flex-col gap-3 rounded-[12px] bg-paper-2/60 p-4", className)}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="text-body font-medium text-ink">{title}</div>
          {hint ? <p className="mt-0.5 text-caption text-ink-3">{hint}</p> : null}
        </div>
        {!done ? (
          <span className="shrink-0 font-mono text-caption tabular-nums text-ink-3">
            {t("reading.elapsed", { seconds: elapsed })}
          </span>
        ) : null}
      </div>

      <Progress value={value} aria-label={typeof title === "string" ? title : undefined} />

      <ol className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
        {steps.map((step, i) => {
          const state = i < active || done ? "done" : i === active ? "active" : "todo";
          return (
            <li
              key={step.label}
              className={cn(
                "flex items-center gap-1.5 text-caption",
                state === "done" && "text-ink-2",
                state === "active" && "font-medium text-ink",
                state === "todo" && "text-ink-3",
              )}
            >
              <span
                className={cn(
                  "flex h-4 w-4 shrink-0 items-center justify-center rounded-full",
                  state === "done" && "bg-pos-soft text-pos-text",
                  state === "active" && "bg-brand-soft text-brand-deep",
                  state === "todo" && "bg-paper-2 text-ink-3",
                )}
              >
                {state === "done" ? (
                  <Check className="h-3 w-3" />
                ) : state === "active" ? (
                  <Loader2 className="h-3 w-3 animate-spin motion-reduce:animate-none" />
                ) : (
                  <span className="h-1 w-1 rounded-full bg-current" />
                )}
              </span>
              {step.label}
            </li>
          );
        })}
      </ol>
    </div>
  );
}
