/**
 * Cotizar en portales — the COMING-SOON page, rendered while
 * `GET /meta/features` reports `portal_search.enabled: false`.
 *
 * No forms on purpose: the RPA needs each insurer's own portal user and
 * password before it can apply filters, so a search form would be a control
 * that cannot work. The page says what the feature will do, why it is pending
 * (the SERVER's reason, verbatim, with a local fallback only when the server
 * sent none) and the alternative that works today — upload the insurer's PDF
 * cotización in the account's Comparación.
 *
 * `?case=ID` (the old account deep link) turns the alternative into a direct
 * link to that account's comparison worktable.
 */
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { motion, useReducedMotion } from "framer-motion";
import { ArrowRight, Clock, FileUp, Globe, KeyRound, ListChecks, Sparkles } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { useCan } from "@/lib/permissions";
import { useNumericUrlParam } from "@/lib/urlParams";

const EASE_STAGE = [0.2, 0, 0, 1] as const;

const WILL_DO = [
  { key: "fill", icon: ListChecks, labelKey: "comingSoon.will.fill" },
  { key: "compare", icon: Sparkles, labelKey: "comingSoon.will.compare" },
  { key: "file", icon: FileUp, labelKey: "comingSoon.will.file" },
] as const;

export function PortalSearchComingSoon({ reason }: { reason: string | null }) {
  const { t } = useTranslation("rpa");
  const { t: tc } = useTranslation("common");
  const reduce = useReducedMotion();
  const caseFileId = useNumericUrlParam("case") ?? null;
  const canGroups = useCan("Groups", "View");
  const canAgent = useCan("Dashboard", "View");

  const enter = (i: number) =>
    reduce
      ? {}
      : {
          initial: { opacity: 0, y: 10 },
          animate: { opacity: 1, y: 0 },
          transition: { duration: 0.45, ease: EASE_STAGE, delay: 0.1 * i },
        };

  return (
    <div className="flex min-w-0 flex-col gap-5">
      <PageHeader
        title={t("title")}
        subtitle={t("comingSoon.subtitle")}
        actions={<Badge variant="muted">{tc("nav.soon")}</Badge>}
      />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
        <motion.div {...enter(0)}>
          <Card className="flex h-full flex-col gap-4 p-5">
            <div className="flex items-center gap-3">
              <span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-brand-soft text-brand-deep">
                <Globe className="h-5 w-5" strokeWidth={1.5} aria-hidden />
              </span>
              <h2 className="text-h3 text-ink">{t("comingSoon.whatTitle")}</h2>
            </div>
            <p className="text-body text-ink-2 [text-wrap:pretty]">{t("comingSoon.what")}</p>
            <ul className="flex flex-col gap-2.5">
              {WILL_DO.map((item) => {
                const Icon = item.icon;
                return (
                  <li key={item.key} className="flex items-start gap-2.5 text-body text-ink-2">
                    <Icon className="mt-0.5 h-4 w-4 shrink-0 text-ink-3" strokeWidth={1.5} aria-hidden />
                    <span className="[text-wrap:pretty]">{t(item.labelKey)}</span>
                  </li>
                );
              })}
            </ul>
          </Card>
        </motion.div>

        <div className="flex flex-col gap-4">
          <motion.div {...enter(1)}>
            <Card className="flex flex-col gap-3 p-5">
              <div className="flex items-center gap-2">
                <Clock className="h-4 w-4 text-warn-text" strokeWidth={1.75} aria-hidden />
                <h2 className="text-h3 text-ink">{t("comingSoon.whyTitle")}</h2>
              </div>
              <p className="text-body text-ink-2 [text-wrap:pretty]">
                {reason ?? t("comingSoon.whyFallback")}
              </p>
              <p className="flex items-start gap-2 text-caption text-ink-3">
                <KeyRound className="mt-0.5 h-3.5 w-3.5 shrink-0" strokeWidth={1.5} aria-hidden />
                <span className="[text-wrap:pretty]">{t("comingSoon.credentialsNote")}</span>
              </p>
            </Card>
          </motion.div>

          <motion.div {...enter(2)}>
            <Card className="flex flex-col gap-3 p-5">
              <h2 className="text-h3 text-ink">{t("comingSoon.altTitle")}</h2>
              <p className="text-body text-ink-2 [text-wrap:pretty]">
                {caseFileId ? t("comingSoon.altCase") : t("comingSoon.alt")}
              </p>
              <div className="flex flex-wrap gap-2">
                {caseFileId ? (
                  <Button asChild className="min-h-10 sm:min-h-9">
                    <Link to={`/comparisons/${caseFileId}`}>
                      <FileUp className="h-4 w-4" strokeWidth={1.5} aria-hidden />
                      {t("comingSoon.altCaseAction")}
                    </Link>
                  </Button>
                ) : canGroups.allowed ? (
                  <Button asChild className="min-h-10 sm:min-h-9">
                    <Link to="/groups">
                      {t("comingSoon.altGroupsAction")}
                      <ArrowRight className="h-4 w-4" aria-hidden />
                    </Link>
                  </Button>
                ) : null}
                {canAgent.allowed ? (
                  <Button asChild variant="ghost" className="min-h-10 sm:min-h-9">
                    <Link to="/agent">{t("comingSoon.askAgent")}</Link>
                  </Button>
                ) : null}
              </div>
            </Card>
          </motion.div>
        </div>
      </div>
    </div>
  );
}
