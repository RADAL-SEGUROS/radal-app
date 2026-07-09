import { useTranslation } from "react-i18next";
import { Check } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import { SINIESTRO_ESTADOS, type SiniestroEstado } from "./api";

/**
 * Horizontal estado timeline reportado → cerrado.
 * Completed steps use lime (affirmative), the current step uses teal (active),
 * pending steps stay neutral. Follows Aqua Spectrum accent ownership.
 */
export function EstadoTimeline({ estado }: { estado: SiniestroEstado }) {
  const { t } = useTranslation("siniestros");
  const currentIndex = SINIESTRO_ESTADOS.indexOf(estado);

  return (
    <Card className="p-5">
      <p className="mb-4 text-h3 font-display text-text-primary">
        {t("detail.timeline.title")}
      </p>
      <ol className="flex flex-col gap-4 sm:flex-row sm:items-start sm:gap-0">
        {SINIESTRO_ESTADOS.map((step, i) => {
          const isDone = i < currentIndex;
          const isCurrent = i === currentIndex;
          const isLast = i === SINIESTRO_ESTADOS.length - 1;
          return (
            <li
              key={step}
              className="flex items-start gap-3 sm:flex-1 sm:flex-col sm:items-center sm:gap-2 sm:text-center"
            >
              <div className="flex items-center sm:w-full sm:flex-col">
                <div className="flex items-center sm:w-full">
                  {/* Left connector (desktop) */}
                  <span
                    className={cn(
                      "hidden h-0.5 flex-1 sm:block",
                      i === 0
                        ? "opacity-0"
                        : isDone || isCurrent
                          ? "bg-lime"
                          : "bg-line",
                    )}
                    aria-hidden
                  />
                  {/* Node */}
                  <span
                    className={cn(
                      "flex h-8 w-8 shrink-0 items-center justify-center rounded-full border-2 font-mono text-mono-sm transition-colors",
                      isDone &&
                        "border-lime bg-lime/20 text-ink dark:text-lime",
                      isCurrent &&
                        "border-teal bg-teal-soft text-teal-deep",
                      !isDone &&
                        !isCurrent &&
                        "border-line bg-bg-recessed text-text-muted",
                    )}
                  >
                    {isDone ? <Check className="h-4 w-4" /> : i + 1}
                  </span>
                  {/* Right connector (desktop) */}
                  <span
                    className={cn(
                      "hidden h-0.5 flex-1 sm:block",
                      isLast
                        ? "opacity-0"
                        : isDone
                          ? "bg-lime"
                          : "bg-line",
                    )}
                    aria-hidden
                  />
                </div>
              </div>
              <span
                className={cn(
                  "text-caption sm:mt-1",
                  isCurrent
                    ? "font-medium text-teal-deep"
                    : isDone
                      ? "text-text-secondary"
                      : "text-text-muted",
                )}
              >
                {t(`estado.${step}`)}
              </span>
            </li>
          );
        })}
      </ol>
    </Card>
  );
}
