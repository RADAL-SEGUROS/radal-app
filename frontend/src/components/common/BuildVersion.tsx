/**
 * The build stamp — `dev · 08d0293 · 10 sep 2026 03:41`.
 *
 * It exists for one reason: the team files issues against a deployed app, and
 * until now nothing on screen said WHICH deploy. A report that cannot be tied
 * to a build is a report you re-investigate from scratch, and on a day with
 * three deploys the timestamp does not narrow it either.
 *
 * So this is quiet but **copyable in one click**, and the GitHub issue forms
 * (`.github/ISSUE_TEMPLATE/`) ask the reporter to paste exactly this string.
 * Anything that asks a non-developer to find a commit hash any other way will
 * come back blank.
 *
 * The values are injected at build time by `vite.config.ts`; see `buildVersion()`
 * there for why the commit has to arrive as a build arg rather than be read
 * from git.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Check, Copy } from "lucide-react";

import { cn } from "@/lib/utils";

/** `dev · 08d0293 · 10 sep 2026 03:41` — one line, safe to paste anywhere. */
export function buildStamp(): string {
  const built = new Date(__APP_BUILD_TIME__);
  const when = Number.isNaN(built.getTime())
    ? __APP_BUILD_TIME__
    : new Intl.DateTimeFormat("es-CL", {
        day: "numeric",
        month: "short",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      }).format(built);
  return `${__APP_ENV__} · ${__APP_VERSION__} · ${when}`;
}

export function BuildVersion({ className }: { className?: string }) {
  const { t } = useTranslation("settings");
  const [copied, setCopied] = React.useState(false);
  const stamp = buildStamp();

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(`Radal · ${stamp}`);
      setCopied(true);
      // Long enough to read, short enough that the button is ready again if the
      // reporter pastes into the wrong field the first time.
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Clipboard is permission-gated and blocked outside a secure context;
      // the stamp is still selectable text, so there is nothing to recover.
      setCopied(false);
    }
  };

  return (
    <button
      type="button"
      onClick={() => void copy()}
      title={t("version.copyHint")}
      className={cn(
        "group inline-flex items-center gap-2 rounded-md px-2 py-1 text-caption text-ink-3 transition-colors duration-150 hover:bg-paper-2 hover:text-ink-2",
        className,
      )}
    >
      <span className="tabular-nums">Radal · {stamp}</span>
      {copied ? (
        <span className="inline-flex items-center gap-1 text-pos-text">
          <Check className="h-3 w-3" />
          {t("version.copied")}
        </span>
      ) : (
        <Copy className="h-3 w-3 opacity-0 transition-opacity duration-150 group-hover:opacity-70" />
      )}
    </button>
  );
}
