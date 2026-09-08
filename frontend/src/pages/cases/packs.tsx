/**
 * Pack generation for one expediente.
 *
 * Three packs, one button each, all gated by `CaseFiles.Submit` — the pack is
 * the headline broker deliverable, so the executives and technicians who run
 * the expediente (both hold Submit) generate it; downloads stay on
 * `Documents.View`. Next to them sits the resolved recipient
 * list (native insurers for the case's line, with the contact the
 * broker+line → broker → line → global precedence found) so the broker can see
 * exactly who the submission is aimed at before generating anything.
 *
 * "Enviar carpeta a compañías" is NOT wired this pass: email delivery is out of
 * scope, so the control renders disabled inside `<DisabledHint>` with a
 * "pronto" chip rather than as a button that quietly does nothing.
 */
import * as React from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowLeft, Download, FileArchive, FileText, Mail, Package, Users } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DisabledHint,
  EmptyState,
  ErrorBanner,
  KeyValue,
  MonoChip,
  Section,
  SoonButton,
  StatusBadge,
  resolveFileUrl,
} from "@/pages/proposals/shared";
import { useCaseFile } from "@/api/caseFiles";
import {
  fetchPackDownload,
  useCasePacks,
  useCaseRecipients,
  useGeneratePack,
} from "@/api/packs";
import { useCan } from "@/lib/permissions";
import { formatDateTime } from "@/lib/format";
import { PACK_KINDS, type CasePack, type PackKind, type PackPart } from "@/api/types";

export default function CasePacksPage() {
  const { caseId: caseIdParam } = useParams();
  const caseId = Number(caseIdParam);
  const { t } = useTranslation("packs");
  const { t: tc } = useTranslation("common");
  const { t: tcase } = useTranslation("cases");

  const detail = useCaseFile(caseId);
  const packs = useCasePacks(caseId);
  const generate = useGeneratePack(caseId);
  const manage = useCan("CaseFiles", "Submit");
  const insurersView = useCan("Insurers", "View");
  const recipients = useCaseRecipients(caseId, insurersView.allowed);

  const [pending, setPending] = React.useState<PackKind | null>(null);

  const byKind = React.useMemo(() => {
    const map = new Map<PackKind, CasePack>();
    for (const pack of packs.data?.items ?? []) map.set(pack.kind, pack);
    return map;
  }, [packs.data]);

  const run = (kind: PackKind) => {
    setPending(kind);
    generate.mutate(kind, { onSettled: () => setPending(null) });
  };

  const gateHint = manage.isLoading
    ? tc("actions.loading")
    : manage.allowed
      ? null
      : t("noPermission");

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={
          <Link to={`/cases/${caseId}`} className="flex items-center gap-1.5 hover:text-brand-deep">
            <ArrowLeft className="h-3.5 w-3.5" />
            {detail.data?.reference ?? tcase("list.title")}
          </Link>
        }
        title={t("title")}
        subtitle={t("subtitle")}
        actions={
          <SoonButton reason={t("send.soonReason")}>
            <Mail className="h-4 w-4" />
            {t("send.label")}
          </SoonButton>
        }
      />

      {generate.isError ? <ErrorBanner error={generate.error} /> : null}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <FadeUp className="flex flex-col gap-4">
          {PACK_KINDS.map((kind) => {
            const pack = byKind.get(kind);
            return (
              <Section
                key={kind}
                title={t(`kinds.${kind}.title`)}
                description={t(`kinds.${kind}.description`)}
                actions={
                  <DisabledHint hint={gateHint}>
                    <Button
                      size="sm"
                      disabled={!manage.allowed || generate.isPending}
                      onClick={() => run(kind)}
                    >
                      <Package className="h-4 w-4" />
                      {pending === kind
                        ? tc("actions.loading")
                        : pack
                          ? t("actions.regenerate")
                          : t("actions.generate")}
                    </Button>
                  </DisabledHint>
                }
              >
                {packs.isLoading ? (
                  <Skeleton className="h-16 w-full" />
                ) : !pack ? (
                  <EmptyState title={t("empty.title")} hint={t("empty.hint")} />
                ) : (
                  <div className="flex flex-col gap-4">
                    <div className="grid gap-5 sm:grid-cols-3">
                      <KeyValue
                        label={t("fields.status")}
                        value={
                          <StatusBadge
                            value={pack.status}
                            label={t(`statuses.${pack.status}`, { defaultValue: pack.status })}
                          />
                        }
                      />
                      <KeyValue
                        label={t("fields.generatedAt")}
                        value={formatDateTime(pack.generated_at)}
                      />
                      <KeyValue
                        label={t("fields.recipients")}
                        value={String(pack.recipients.length)}
                      />
                    </div>

                    {pack.error ? <ErrorBanner error={pack.error} /> : null}

                    <div className="flex flex-wrap gap-2">
                      <PackDownload pack={pack} part="pdf" />
                      <PackDownload pack={pack} part="zip" />
                    </div>

                    {pack.summary ? (
                      <div className="rounded-card border border-line bg-bg-recessed p-3.5">
                        <div className="mb-1.5 flex flex-wrap items-center gap-2">
                          <Badge variant={pack.is_summary_confirmed ? "success" : "action"}>
                            {pack.is_summary_confirmed
                              ? t("summary.confirmed")
                              : t("summary.unconfirmed")}
                          </Badge>
                          {pack.summary_model ? <MonoChip>{pack.summary_model}</MonoChip> : null}
                          {pack.summary_prompt_version ? (
                            <MonoChip>{pack.summary_prompt_version}</MonoChip>
                          ) : null}
                        </div>
                        <p className="whitespace-pre-wrap text-caption text-text-secondary">
                          {pack.summary}
                        </p>
                      </div>
                    ) : null}
                  </div>
                )}
              </Section>
            );
          })}
        </FadeUp>

        <FadeUp delay={0.06}>
          <Section title={t("recipients.title")} description={t("recipients.description")}>
            {!insurersView.allowed ? (
              <EmptyState title={t("recipients.noPermission")} />
            ) : recipients.isLoading ? (
              <Skeleton className="h-32 w-full" />
            ) : (recipients.data?.items.length ?? 0) === 0 ? (
              <EmptyState
                title={t("recipients.empty")}
                hint={t("recipients.emptyHint")}
                icon={<Users className="h-6 w-6" />}
              />
            ) : (
              <ul className="flex flex-col divide-y divide-line">
                {recipients.data?.items.map((r) => (
                  <li key={r.insurer_id} className="flex flex-col gap-1 py-2.5">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="min-w-0 flex-1 truncate font-medium text-text-primary">
                        {r.legal_name}
                      </span>
                      <Badge variant={r.is_native ? "brand" : "neutral"}>
                        {r.is_native ? t("recipients.native") : t("recipients.external")}
                      </Badge>
                    </div>
                    <p className="truncate text-caption text-text-muted">
                      {r.contact_name ? `${r.contact_name} · ` : ""}
                      {r.contact_email ?? t("recipients.noContact")}
                    </p>
                    {r.resolution_level ? (
                      <Badge variant="muted" className="self-start">
                        {t(`recipients.levels.${r.resolution_level}`, {
                          defaultValue: r.resolution_level,
                        })}
                      </Badge>
                    ) : null}
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </FadeUp>
      </div>
    </div>
  );
}

/**
 * The download URL is short-lived, so it is fetched on click rather than
 * rendered as a stale href. When the pack has no document of that part the
 * button is disabled with the reason on the tooltip.
 */
function PackDownload({ pack, part }: { pack: CasePack; part: PackPart }) {
  const { t } = useTranslation("packs");
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState<unknown>(null);

  const inline = part === "pdf" ? pack.pdf : pack.zip;
  const documentId = part === "pdf" ? pack.pdf_document_id : pack.zip_document_id;
  const Icon = part === "pdf" ? FileText : FileArchive;

  const open = async () => {
    setBusy(true);
    setError(null);
    try {
      const direct = resolveFileUrl(inline?.url);
      const url = direct ?? resolveFileUrl((await fetchPackDownload(pack.id, part)).url);
      if (url) window.open(url, "_blank", "noreferrer");
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex flex-col gap-1">
      <DisabledHint hint={documentId ? null : t(`download.missing.${part}`)}>
        <Button
          size="sm"
          variant="secondary"
          disabled={!documentId || busy}
          onClick={() => void open()}
        >
          <Icon className="h-4 w-4" />
          {t(`download.${part}`)}
          <Download className="h-3.5 w-3.5" />
        </Button>
      </DisabledHint>
      {error ? <ErrorBanner error={error} /> : null}
    </div>
  );
}
