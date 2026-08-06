/**
 * Insurers — the canonical catalogue.
 *
 * One identity per company (`rut` + `cmf_code`), flagged **native** (a Radal
 * commercial partner, with a profile and contacts) or **external** (tracked
 * because a broker uploaded its proposal). A broker may register an external
 * company; only the platform can turn one native, so that control is not
 * offered here at all rather than offered and rejected.
 */
import * as React from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Building2, Mail, Plus, Search, Star } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp, Stagger } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useCan } from "@/lib/permissions";
import { useCreateInsurer, useInsurerRecommendations, useInsurers } from "@/api/insurers";
import type { Insurer } from "@/api/types";
import {
  DisabledHint,
  EmptyState,
  ErrorBanner,
  MonoChip,
  StatusBadge,
  apiError,
} from "@/pages/proposals/shared";

type Scope = "all" | "native" | "external";

export default function InsurersListPage() {
  const { t } = useTranslation("insurers");
  const navigate = useNavigate();

  const [scope, setScope] = React.useState<Scope>("all");
  const [search, setSearch] = React.useState("");
  const [debounced, setDebounced] = React.useState("");
  const [createOpen, setCreateOpen] = React.useState(false);

  React.useEffect(() => {
    const id = window.setTimeout(() => setDebounced(search.trim()), 300);
    return () => window.clearTimeout(id);
  }, [search]);

  const insurers = useInsurers({
    q: debounced || undefined,
    is_native: scope === "all" ? undefined : scope === "native",
    limit: 200,
  });

  const recommendations = useInsurerRecommendations({ limit: 4 });
  const canCreate = useCan("Insurers", "Create");

  const items = insurers.data?.items ?? [];

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

      {/* Native partners recommended for the workspace */}
      {(recommendations.data?.items.length ?? 0) > 0 ? (
        <FadeUp delay={0.04}>
          <Card className="p-5">
            <div className="mb-4 flex items-center gap-2">
              <Star className="h-4 w-4 text-teal" />
              <h2 className="font-display text-h3 text-text-primary">
                {t("recommendations.title")}
              </h2>
              <span className="text-caption text-text-muted">
                {t("recommendations.subtitle")}
              </span>
            </div>
            <div className="grid gap-3 md:grid-cols-2 lg:grid-cols-4">
              {(recommendations.data?.items ?? []).map((rec) => (
                <Link
                  key={rec.insurer.id}
                  to={`/insurers/${rec.insurer.id}`}
                  className="flex flex-col gap-2 rounded-[12px] border border-line bg-bg-recessed p-3.5 no-underline transition-colors hover:border-teal"
                >
                  <span className="truncate text-body font-medium text-text-primary">
                    {rec.insurer.trade_name || rec.insurer.legal_name}
                  </span>
                  <span className="flex flex-wrap items-center gap-1.5">
                    <Badge variant="brand">{t("origin.native")}</Badge>
                    {rec.has_line_contact ? (
                      <Badge variant="success">{t("recommendations.hasLineContact")}</Badge>
                    ) : null}
                  </span>
                  <span className="text-caption text-text-muted">
                    {t(`recommendations.reasons.${rec.reason}`, { defaultValue: rec.reason })}
                  </span>
                  {rec.contact?.contact ? (
                    <span className="flex flex-col gap-0.5 text-caption text-text-tertiary">
                      <span className="truncate">{rec.contact.contact.name}</span>
                      {rec.contact.contact.email ? (
                        <span className="flex items-center gap-1 truncate">
                          <Mail className="h-3 w-3" />
                          {rec.contact.contact.email}
                        </span>
                      ) : null}
                    </span>
                  ) : null}
                </Link>
              ))}
            </div>
          </Card>
        </FadeUp>
      ) : null}

      <FadeUp delay={0.08}>
        <Card className="flex flex-wrap items-center gap-3 p-4">
          <div className="relative max-w-sm flex-1">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-text-muted" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={t("filters.search")}
              className="h-9 pl-9"
            />
          </div>
          <Tabs value={scope} onValueChange={(v) => setScope(v as Scope)}>
            <TabsList>
              <TabsTrigger value="all">{t("filters.all")}</TabsTrigger>
              <TabsTrigger value="native">{t("origin.native")}</TabsTrigger>
              <TabsTrigger value="external">{t("origin.external")}</TabsTrigger>
            </TabsList>
          </Tabs>
          <span className="ml-auto text-caption text-text-muted">
            {t("filters.showing", { shown: items.length, total: insurers.data?.total ?? 0 })}
          </span>
        </Card>
      </FadeUp>

      {insurers.isError ? <ErrorBanner error={insurers.error} /> : null}

      {insurers.isLoading ? (
        <div className="grid gap-[18px] md:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => (
            <Skeleton key={i} className="h-[150px] rounded-card" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            title={t("empty.title")}
            hint={t("empty.hint")}
            icon={<Building2 className="h-6 w-6" />}
            action={
              canCreate.allowed ? (
                <Button size="sm" onClick={() => setCreateOpen(true)}>
                  <Plus className="h-4 w-4" />
                  {t("create.action")}
                </Button>
              ) : undefined
            }
          />
        </Card>
      ) : (
        <Stagger className="grid gap-[18px] md:grid-cols-2 lg:grid-cols-3">
          {items.map((insurer) => (
            <InsurerCard
              key={insurer.id}
              insurer={insurer}
              onOpen={() => navigate(`/insurers/${insurer.id}`)}
            />
          ))}
        </Stagger>
      )}

      <CreateInsurerDialog open={createOpen} onOpenChange={setCreateOpen} />
    </>
  );
}

function InsurerCard({ insurer, onOpen }: { insurer: Insurer; onOpen: () => void }) {
  const { t } = useTranslation("insurers");
  return (
    <FadeUp className="h-full">
      <Card
        interactive
        onClick={onOpen}
        className="flex h-full cursor-pointer flex-col gap-3 p-[18px]"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <h3 className="truncate font-display text-h3 text-text-primary">
              {insurer.trade_name || insurer.legal_name}
            </h3>
            {insurer.trade_name ? (
              <p className="truncate text-caption text-text-muted">{insurer.legal_name}</p>
            ) : null}
          </div>
          <Badge variant={insurer.is_native ? "brand" : "neutral"}>
            {insurer.is_native ? t("origin.native") : t("origin.external")}
          </Badge>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <MonoChip>{insurer.rut}</MonoChip>
          <MonoChip>CMF {insurer.cmf_code}</MonoChip>
          <StatusBadge value={insurer.status} label={t(`status.${insurer.status}`)} />
        </div>

        {insurer.native_profile ? (
          <div className="mt-auto flex flex-wrap items-center gap-3 text-caption text-text-muted">
            <span>
              {t("profile.priority")}: {insurer.native_profile.priority}
            </span>
            {insurer.native_profile.sla_hours ? (
              <span>
                {t("profile.sla")}: {insurer.native_profile.sla_hours} h
              </span>
            ) : null}
          </div>
        ) : (
          <div className="mt-auto text-caption text-text-muted">{t("card.externalHint")}</div>
        )}
      </Card>
    </FadeUp>
  );
}

// =============================================================================
// Create (external only — brokers cannot mint native partners)
// =============================================================================

function CreateInsurerDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("insurers");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();
  const create = useCreateInsurer();

  const [rut, setRut] = React.useState("");
  const [cmf, setCmf] = React.useState("");
  const [legalName, setLegalName] = React.useState("");
  const [tradeName, setTradeName] = React.useState("");
  const [paymentUrl, setPaymentUrl] = React.useState("");

  React.useEffect(() => {
    if (!open) {
      setRut("");
      setCmf("");
      setLegalName("");
      setTradeName("");
      setPaymentUrl("");
    }
  }, [open]);

  const missing = !rut.trim() || !cmf.trim() || !legalName.trim();

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("create.title")}</DialogTitle>
          <DialogDescription>{t("create.description")}</DialogDescription>
        </DialogHeader>

        <div className="grid gap-4 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="rut">{t("fields.rut")}</Label>
            <Input
              id="rut"
              value={rut}
              onChange={(e) => setRut(e.target.value)}
              placeholder="99999999-9"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="cmf">{t("fields.cmfCode")}</Label>
            <Input id="cmf" value={cmf} onChange={(e) => setCmf(e.target.value)} placeholder="0000" />
          </div>
          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <Label htmlFor="legal">{t("fields.legalName")}</Label>
            <Input id="legal" value={legalName} onChange={(e) => setLegalName(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <Label htmlFor="trade">{t("fields.tradeName")}</Label>
            <Input id="trade" value={tradeName} onChange={(e) => setTradeName(e.target.value)} />
          </div>
          <div className="flex flex-col gap-1.5 sm:col-span-2">
            <Label htmlFor="payment">{t("fields.paymentUrl")}</Label>
            <Input
              id="payment"
              value={paymentUrl}
              onChange={(e) => setPaymentUrl(e.target.value)}
              placeholder="https://"
            />
          </div>
        </div>

        <p className="text-caption text-text-muted">{t("create.externalOnly")}</p>
        {create.isError ? <ErrorBanner error={create.error} /> : null}

        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            {tc("actions.cancel")}
          </Button>
          <DisabledHint hint={missing ? t("create.required") : null}>
            <Button
              disabled={missing || create.isPending}
              onClick={() =>
                create.mutate(
                  {
                    rut: rut.trim(),
                    cmf_code: cmf.trim(),
                    legal_name: legalName.trim(),
                    trade_name: tradeName.trim() || null,
                    payment_url: paymentUrl.trim() || null,
                  },
                  {
                    onSuccess: (insurer) => {
                      toast.success(t("create.created"));
                      onOpenChange(false);
                      navigate(`/insurers/${insurer.id}`);
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

