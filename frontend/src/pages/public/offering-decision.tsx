/**
 * THE PUBLIC INSURED-DECISION SURFACE — `/o/:token`.
 *
 * Unauthenticated: the share token IS the credential (see `api/public.ts`, which
 * never attaches the broker's app token). The insured reads every alternative in
 * plain language, picks one and confirms. No jargon, no commission, no internal
 * ids. A draft/unknown token is 404, an expired link is 410 — both get a clear,
 * calm screen rather than a broken app.
 */
import * as React from "react";
import { useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Check, Clock, ShieldCheck, Star } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Label } from "@/components/ui/label";
import { cn } from "@/lib/utils";
import { formatDate } from "@/lib/format";
import { uf, apiError, resolveFileUrl } from "@/components/common/kit";
import { usePublicOffering, useRecordDecision } from "@/api/public";
import type { OfferingPublicProposal, OfferingPublicRead } from "@/api/types";

function statusOf(error: unknown): number | null {
  const err = error as { response?: { status?: number } } | null;
  return err?.response?.status ?? null;
}

export default function OfferingDecisionPage() {
  const { token } = useParams<{ token: string }>();
  const { t } = useTranslation("publicOffering");
  const query = usePublicOffering(token);

  return (
    <div className="min-h-screen bg-bg-app">
      <header className="border-b border-line bg-bg-surface">
        <div className="mx-auto flex max-w-3xl items-center justify-between px-5 py-4">
          <span className="font-wordmark text-h3 tracking-tight text-ink">Radal.</span>
          {query.data?.broker_name ? (
            <span className="text-caption text-ink-3">
              {t("via", { broker: query.data.broker_name })}
            </span>
          ) : null}
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-5 py-8">
        {query.isLoading ? (
          <div className="flex flex-col gap-4">
            <Skeleton className="h-24 w-full rounded-card" />
            <Skeleton className="h-40 w-full rounded-card" />
            <Skeleton className="h-40 w-full rounded-card" />
          </div>
        ) : query.isError ? (
          <StateScreen
            tone="warn"
            title={
              statusOf(query.error) === 410 ? t("expired.title") : t("notFound.title")
            }
            hint={
              statusOf(query.error) === 410 ? t("expired.hint") : t("notFound.hint")
            }
          />
        ) : query.data ? (
          <DecisionBody token={token!} data={query.data} />
        ) : null}
      </main>

      <footer className="mx-auto max-w-3xl px-5 pb-10 text-center text-caption text-ink-3">
        {t("footer")}
      </footer>
    </div>
  );
}

function StateScreen({
  tone,
  title,
  hint,
}: {
  tone: "warn" | "success";
  title: string;
  hint: string;
}) {
  return (
    <Card className="p-8">
      <div className="flex flex-col items-center gap-3 text-center">
        <span
          className={cn(
            "flex h-12 w-12 items-center justify-center rounded-xl",
            tone === "warn" ? "bg-warn-soft text-warn-text" : "bg-pos-soft text-pos-text",
          )}
        >
          {tone === "warn" ? (
            <AlertTriangle className="h-6 w-6" />
          ) : (
            <ShieldCheck className="h-6 w-6" />
          )}
        </span>
        <h1 className="text-h2 tracking-tight text-ink">{title}</h1>
        <p className="max-w-md text-pretty text-body text-ink-2">{hint}</p>
      </div>
    </Card>
  );
}

function DecisionBody({ token, data }: { token: string; data: OfferingPublicRead }) {
  const { t } = useTranslation("publicOffering");
  const decision = useRecordDecision(token);

  const decidedId = data.decided_proposal_id;
  const proposals = data.proposals.length
    ? data.proposals
    : data.proposal
      ? [data.proposal]
      : [];

  const [selected, setSelected] = React.useState<number | null>(decidedId ?? null);
  const [note, setNote] = React.useState<string>(data.decided_note ?? "");

  React.useEffect(() => {
    if (decidedId != null) setSelected(decidedId);
  }, [decidedId]);

  const chosen = proposals.find((p) => p.id === decidedId) ?? null;
  const pdfUrl = resolveFileUrl(data.pdf_url);

  const dirty =
    selected != null &&
    (selected !== decidedId || note !== (data.decided_note ?? ""));
  const canSubmit = dirty && !decision.isPending;

  const confirm = () => {
    if (selected == null) return;
    decision.mutate({ proposal_id: selected, note: note.trim() || null });
  };

  return (
    <div className="flex flex-col gap-6">
      {/* Intro */}
      <div className="flex flex-col gap-1.5">
        <h1 className="text-h1 tracking-tight text-ink">{t("heading")}</h1>
        <p className="text-body text-ink-2">
          {data.insured_object
            ? t("leadWithObject", { object: data.insured_object })
            : t("lead")}
        </p>
        {data.declared_value_uf ? (
          <p className="text-caption text-ink-3">
            {t("declaredValue")} {uf(data.declared_value_uf)}
          </p>
        ) : null}
      </div>

      {/* Decided confirmation */}
      {chosen ? (
        <Card className="border-pos-line bg-pos-soft/40 p-5">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-pos-soft text-pos-text">
              <Check className="h-5 w-5" />
            </span>
            <div>
              <div className="text-body font-medium text-ink">{t("decided.title")}</div>
              <p className="mt-0.5 text-caption text-ink-2">
                {t("decided.body", {
                  insurer: chosen.insurer_name ?? t("card.insurerFallback"),
                })}
              </p>
              {data.decided_at ? (
                <p className="mt-0.5 text-caption text-ink-3">
                  {t("decided.at")} {formatDate(data.decided_at)}
                </p>
              ) : null}
            </div>
          </div>
        </Card>
      ) : null}

      {/* Alternatives */}
      {proposals.length === 0 ? (
        <StateScreen tone="warn" title={t("noneTitle")} hint={t("noneHint")} />
      ) : (
        <div className="flex flex-col gap-3">
          <h2 className="text-h3 tracking-tight text-ink">
            {t("chooseTitle", { count: proposals.length })}
          </h2>
          {proposals.map((p) => (
            <ProposalCard
              key={p.id ?? p.insurer_name ?? Math.random()}
              proposal={p}
              selected={selected != null && selected === p.id}
              decided={decidedId != null && decidedId === p.id}
              onSelect={() => p.id != null && setSelected(p.id)}
            />
          ))}
        </div>
      )}

      {/* Note + confirm */}
      {proposals.length > 0 ? (
        <Card className="p-5">
          <div className="flex flex-col gap-3">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="decision-note">{t("note.label")}</Label>
              <textarea
                id="decision-note"
                value={note}
                onChange={(e) => setNote(e.target.value)}
                rows={3}
                maxLength={2000}
                placeholder={t("note.placeholder")}
                className="w-full resize-y rounded-sm border border-line bg-bg-surface px-3 py-2 text-body text-ink outline-none transition-colors placeholder:text-ink-3 focus:border-brand-line"
              />
            </div>

            {decision.isError ? (
              <div
                role="alert"
                className="flex items-start gap-2.5 rounded-lg bg-neg-soft px-3.5 py-2.5"
              >
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-neg" />
                <p className="text-caption text-ink-2">
                  {apiError(decision.error, t("errorFallback"))}
                </p>
              </div>
            ) : null}

            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-caption text-ink-3">{t("confirmNote")}</p>
              <Button size="lg" disabled={!canSubmit} onClick={confirm}>
                <Check className="h-4 w-4" />
                {decidedId != null ? t("updateChoice") : t("confirmChoice")}
              </Button>
            </div>
          </div>
        </Card>
      ) : null}

      {pdfUrl ? (
        <div className="text-center">
          <Button variant="secondary" size="sm" asChild>
            <a href={pdfUrl} target="_blank" rel="noreferrer">
              {t("openPdf")}
            </a>
          </Button>
        </div>
      ) : null}
    </div>
  );
}

function ProposalCard({
  proposal,
  selected,
  decided,
  onSelect,
}: {
  proposal: OfferingPublicProposal;
  selected: boolean;
  decided: boolean;
  onSelect: () => void;
}) {
  const { t } = useTranslation("publicOffering");
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className={cn(
        "flex flex-col gap-3 rounded-card border bg-bg-surface p-5 text-left transition-[border-color,box-shadow] duration-150",
        selected
          ? "border-brand-line shadow-elev ring-2 ring-brand-line"
          : "border-line hover:border-line-strong",
      )}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-h3 tracking-tight text-ink">
              {proposal.insurer_name ?? t("card.insurerFallback")}
            </span>
            {proposal.is_recommended ? (
              <Badge variant="brand" className="gap-1">
                <Star className="h-3 w-3 fill-current" />
                {t("card.recommended")}
              </Badge>
            ) : null}
            {decided ? <Badge variant="success">{t("card.yourChoice")}</Badge> : null}
          </div>
          {proposal.modality ? (
            <p className="mt-0.5 text-caption text-ink-3">{proposal.modality}</p>
          ) : null}
        </div>
        <span
          className={cn(
            "mt-1 flex h-5 w-5 shrink-0 items-center justify-center rounded-full border",
            selected ? "border-brand bg-brand text-white" : "border-line-strong",
          )}
          aria-hidden
        >
          {selected ? <Check className="h-3.5 w-3.5" /> : null}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        <Figure label={t("card.total")} value={uf(proposal.total_premium_uf)} emphasis />
        {proposal.net_premium_uf ? (
          <Figure label={t("card.net")} value={uf(proposal.net_premium_uf)} />
        ) : null}
        {proposal.validity_business_days != null ? (
          <Figure
            label={t("card.validity")}
            value={t("card.businessDays", { count: proposal.validity_business_days })}
          />
        ) : null}
      </div>

      {proposal.coverage_start || proposal.coverage_end ? (
        <div className="flex items-center gap-1.5 text-caption text-ink-3">
          <Clock className="h-3.5 w-3.5" />
          {formatDate(proposal.coverage_start)} → {formatDate(proposal.coverage_end)}
        </div>
      ) : null}
    </button>
  );
}

function Figure({
  label,
  value,
  emphasis,
}: {
  label: string;
  value: React.ReactNode;
  emphasis?: boolean;
}) {
  return (
    <div className="min-w-0">
      <div className="text-caption text-ink-3">{label}</div>
      <div
        className={cn(
          "mt-0.5 tabular-nums text-ink",
          emphasis ? "text-h3 font-medium" : "text-body font-medium",
        )}
      >
        {value}
      </div>
    </div>
  );
}
