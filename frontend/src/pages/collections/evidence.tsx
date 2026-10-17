/**
 * Cobranza — "Respuestas por confirmar" (`GET /collections/evidence`).
 *
 * Radal cannot read transfer comprobantes (the money lands in the insurer's
 * account), so it ASKS the insurer and reads the answer. Every answer — a mail
 * reply, an insurer report through a share link, a statement, a payer upload —
 * lands here as a suggestion. Nothing is booked until a human confirms.
 *
 * Default view = the open ones (`suggested` + `needs_review`, the server's
 * default without `status`); the other two tabs are the history.
 */
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowLeft, ChevronLeft, ChevronRight, Inbox } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { EmptyState, ErrorBanner, LoadingRows } from "@/components/common/kit";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useSetUrlParams, useUrlParam } from "@/lib/urlParams";
import { usePaymentEvidence, type PaymentEvidenceStatus } from "@/api/collections";
import { EvidenceCard } from "@/components/collections/EvidenceCard";

const PAGE = 30;

const TABS = {
  open: { label: "collections:evidence.tabs.open", status: undefined },
  confirmed: { label: "collections:evidence.tabs.confirmed", status: ["confirmed"] },
  rejected: { label: "collections:evidence.tabs.rejected", status: ["rejected", "superseded"] },
} as const satisfies Record<string, { label: string; status: readonly PaymentEvidenceStatus[] | undefined }>;
type TabKey = keyof typeof TABS;

function isTab(v: string | null): v is TabKey {
  return !!v && Object.prototype.hasOwnProperty.call(TABS, v);
}

export default function CollectionsEvidencePage() {
  const { t } = useTranslation(["collections", "common"]);
  const setParams = useSetUrlParams();
  const [tabRaw] = useUrlParam("tab");
  const [pageRaw] = useUrlParam("page");
  const tab: TabKey = isTab(tabRaw) ? tabRaw : "open";
  const pageIndex = pageRaw && Number(pageRaw) > 1 ? Number(pageRaw) - 1 : 0;
  const status = TABS[tab].status;

  const evidence = usePaymentEvidence({
    status: status ? [...status] : undefined,
    limit: PAGE,
    offset: pageIndex * PAGE,
  });
  const items = evidence.data?.items ?? [];
  const total = evidence.data?.total ?? 0;
  const pageCount = Math.max(1, Math.ceil(total / PAGE));

  return (
    <>
      <PageHeader
        eyebrow={
          <Link to="/collections" className="inline-flex items-center gap-1.5 no-underline">
            <ArrowLeft className="h-3.5 w-3.5" />
            {t("collections:title")}
          </Link>
        }
        title={t("collections:evidence.title")}
        subtitle={t("collections:evidence.description")}
      />

      <Tabs value={tab} onValueChange={(v) => setParams({ tab: v === "open" ? null : v, page: null })}>
        <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
          <TabsList variant="underline" className="min-w-max">
            {(Object.keys(TABS) as TabKey[]).map((key) => (
              <TabsTrigger key={key} value={key}>
                {t(TABS[key].label)}
                {key === tab && evidence.data ? (
                  <span className="ml-1.5 rounded-full bg-paper-2 px-1.5 text-[11px] tabular-nums text-ink-3">
                    {total}
                  </span>
                ) : null}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
      </Tabs>

      <ErrorBanner error={evidence.error} />

      {evidence.isLoading ? (
        <Card className="p-5">
          <LoadingRows rows={4} />
        </Card>
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            icon={<Inbox />}
            title={tab === "open" ? t("collections:evidence.emptyOpen") : t("collections:evidence.emptyHistory")}
            hint={tab === "open" ? t("collections:evidence.emptyOpenHint") : undefined}
            action={
              tab === "open" ? (
                <Button asChild variant="secondary" size="sm">
                  <Link to="/collections/inquiries">{t("collections:inquiries.title")}</Link>
                </Button>
              ) : undefined
            }
          />
        </Card>
      ) : (
        <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
          {items.map((ev) => (
            <EvidenceCard key={ev.id} evidence={ev} />
          ))}
        </div>
      )}

      {pageCount > 1 ? (
        <div className="flex items-center justify-end gap-2">
          <span className="text-caption tabular-nums text-ink-3">
            {t("collections:board.page", { page: pageIndex + 1, pages: pageCount })}
          </span>
          <Button
            variant="secondary"
            size="icon"
            aria-label={t("common:actions.previous")}
            disabled={pageIndex === 0}
            onClick={() => setParams({ page: pageIndex <= 1 ? null : String(pageIndex) })}
          >
            <ChevronLeft className="h-4 w-4" />
          </Button>
          <Button
            variant="secondary"
            size="icon"
            aria-label={t("common:actions.next")}
            disabled={pageIndex + 1 >= pageCount}
            onClick={() => setParams({ page: String(pageIndex + 2) })}
          >
            <ChevronRight className="h-4 w-4" />
          </Button>
        </div>
      ) : null}
    </>
  );
}
