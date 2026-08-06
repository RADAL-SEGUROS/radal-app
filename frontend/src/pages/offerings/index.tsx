/**
 * Offerings — the shareable package sent to the insured.
 *
 * Delivery is deliberately MANUAL: Radal does not send the WhatsApp or the
 * email, the broker does, and `POST /offerings/{id}/send` records which channel
 * was used. That is why nothing here promises to "send" anything.
 */
import * as React from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Eye, Plus, Send, Share2 } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { KpiCard } from "@/components/common/KpiCard";
import { FadeUp, Stagger } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
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
import { formatDateTime } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { useCreateOffering, useOfferings } from "@/api/offerings";
import { useQuotes } from "@/api/quotes";
import { useProposals } from "@/api/proposals";
import {
  DisabledHint,
  EmptyState,
  ErrorBanner,
  MonoChip,
  StatusBadge,
  apiError,
  uf,
} from "@/pages/proposals/shared";

export default function OfferingsListPage() {
  const { t } = useTranslation("offerings");
  const navigate = useNavigate();
  const offerings = useOfferings({ limit: 100 });
  const canCreate = useCan("Offerings", "Create");
  const [createOpen, setCreateOpen] = React.useState(false);

  const items = offerings.data?.items ?? [];
  const sent = items.filter((o) => o.status === "sent" || o.sent_at).length;
  const viewed = items.filter((o) => o.viewed_at).length;

  return (
    <>
      <PageHeader
        title={t("title")}
        subtitle={t("subtitle")}
        actions={
          <DisabledHint hint={canCreate.allowed ? null : t("create.noPermission")}>
            <Button onClick={() => setCreateOpen(true)} disabled={!canCreate.allowed}>
              <Plus className="h-4 w-4" />
              {t("create.action")}
            </Button>
          </DisabledHint>
        }
      />

      <Stagger className="grid grid-cols-1 gap-[18px] sm:grid-cols-3">
        <KpiCard
          label={t("kpi.total")}
          countTo={offerings.data?.total ?? items.length}
          icon={<Share2 />}
          tone="brand"
        />
        <KpiCard label={t("kpi.sent")} countTo={sent} icon={<Send />} tone="action" />
        <KpiCard label={t("kpi.viewed")} countTo={viewed} icon={<Eye />} tone="success" />
      </Stagger>

      {offerings.isError ? <ErrorBanner error={offerings.error} /> : null}

      <FadeUp delay={0.1}>
        <Card className="overflow-hidden">
          {offerings.isLoading ? (
            <div className="p-5">
              <Skeleton className="h-40 w-full" />
            </div>
          ) : items.length === 0 ? (
            <EmptyState
              title={t("empty.title")}
              hint={t("empty.hint")}
              icon={<Share2 className="h-6 w-6" />}
              action={
                canCreate.allowed ? (
                  <Button size="sm" onClick={() => setCreateOpen(true)}>
                    <Plus className="h-4 w-4" />
                    {t("create.action")}
                  </Button>
                ) : undefined
              }
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>{t("table.id")}</TableHead>
                  <TableHead>{t("table.quote")}</TableHead>
                  <TableHead>{t("table.selected")}</TableHead>
                  <TableHead>{t("table.status")}</TableHead>
                  <TableHead>{t("table.channel")}</TableHead>
                  <TableHead>{t("table.sentAt")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.map((offering) => (
                  <TableRow
                    key={offering.id}
                    className="cursor-pointer"
                    onClick={() => navigate(`/offerings/${offering.id}`)}
                  >
                    <TableCell>
                      <MonoChip>OFR-{String(offering.id).padStart(4, "0")}</MonoChip>
                    </TableCell>
                    <TableCell>
                      <Link
                        to={`/quotes/${offering.quote_request_id}`}
                        onClick={(e) => e.stopPropagation()}
                        className="font-mono text-mono-sm"
                      >
                        COT-{String(offering.quote_request_id).padStart(4, "0")}
                      </Link>
                    </TableCell>
                    <TableCell>
                      {offering.selected_proposal_id ? (
                        <MonoChip>
                          PROP-{String(offering.selected_proposal_id).padStart(4, "0")}
                        </MonoChip>
                      ) : (
                        <Badge variant="warn">{t("table.noSelection")}</Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      <StatusBadge
                        value={offering.status}
                        label={t(`status.${offering.status}`)}
                      />
                    </TableCell>
                    <TableCell>
                      {offering.sent_via ? (
                        <Badge variant="action">{t(`channels.${offering.sent_via}`)}</Badge>
                      ) : (
                        <span className="text-text-muted">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-caption text-text-muted">
                      {offering.sent_at ? formatDateTime(offering.sent_at) : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Card>
      </FadeUp>

      <CreateOfferingDialog open={createOpen} onOpenChange={setCreateOpen} />
    </>
  );
}

// =============================================================================
// Create
// =============================================================================

function CreateOfferingDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("offerings");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const [quoteId, setQuoteId] = React.useState("");
  const [proposalId, setProposalId] = React.useState("");

  const quotes = useQuotes({ limit: 100 }, open);
  const proposals = useProposals(
    { quote_request_id: Number(quoteId), limit: 50 },
    open && !!quoteId,
  );
  const create = useCreateOffering();

  React.useEffect(() => {
    if (!open) {
      setQuoteId("");
      setProposalId("");
    }
  }, [open]);

  React.useEffect(() => {
    setProposalId("");
  }, [quoteId]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("create.title")}</DialogTitle>
          <DialogDescription>{t("create.description")}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="offering-quote">{t("create.quote")}</Label>
            <Select value={quoteId} onValueChange={setQuoteId}>
              <SelectTrigger id="offering-quote">
                <SelectValue
                  placeholder={quotes.isLoading ? tc("actions.loading") : t("create.quotePlaceholder")}
                />
              </SelectTrigger>
              <SelectContent>
                {(quotes.data?.items ?? []).map((q) => (
                  <SelectItem key={q.id} value={String(q.id)}>
                    COT-{String(q.id).padStart(4, "0")} · {q.insured_object ?? ""} ·{" "}
                    {t("create.proposalCount", { count: q.proposal_count })}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="offering-proposal">{t("create.proposal")}</Label>
            <Select value={proposalId} onValueChange={setProposalId} disabled={!quoteId}>
              <SelectTrigger id="offering-proposal">
                <SelectValue
                  placeholder={
                    !quoteId
                      ? t("create.pickQuoteFirst")
                      : proposals.isLoading
                        ? tc("actions.loading")
                        : t("create.proposalPlaceholder")
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {(proposals.data?.items ?? []).map((p) => (
                  <SelectItem key={p.id} value={String(p.id)}>
                    {p.insurer?.trade_name || p.insurer?.legal_name || `#${p.insurer_id}`} ·{" "}
                    {uf(p.total_premium_uf)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <p className="text-caption text-text-muted">{t("create.proposalHint")}</p>
          </div>

          {create.isError ? <ErrorBanner error={create.error} /> : null}
        </div>

        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            {tc("actions.cancel")}
          </Button>
          <DisabledHint hint={quoteId ? null : t("create.pickQuote")}>
            <Button
              disabled={!quoteId || create.isPending}
              onClick={() =>
                create.mutate(
                  {
                    quote_request_id: Number(quoteId),
                    selected_proposal_id: proposalId ? Number(proposalId) : null,
                  },
                  {
                    onSuccess: (offering) => {
                      toast.success(t("create.created"));
                      onOpenChange(false);
                      navigate(`/offerings/${offering.id}`);
                    },
                    onError: (error) => toast.error(apiError(error, tc("toast.error"))),
                  },
                )
              }
            >
              {create.isPending ? tc("actions.loading") : tc("actions.create")}
            </Button>
          </DisabledHint>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
