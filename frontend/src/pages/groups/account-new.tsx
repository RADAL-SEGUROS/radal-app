/**
 * `/groups/:groupId/accounts/new` — open one or more accounts in a vigencia.
 *
 * An **account** is one insurance line × one validity period × N RUTs, so the
 * ramo picker is a REQUIRED multi-select: choosing three ramos posts three
 * `case_file(kind=account)` folders, one per ramo, all sharing the dates and
 * the members. There is no "multi-ramo folder" — that is the whole point of
 * the model.
 *
 * Dates are required by the server for `kind=account` (422 otherwise) and are
 * the folder's authoritative period; the label is only a grouping string and is
 * derived from the years unless the broker overrides it.
 *
 * A ramo that already has an open folder for these exact dates comes back as
 * 422 `folder_exists`; that line is reported by name and the others still land,
 * because the calls are independent folders, not one transaction.
 */
import * as React from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ArrowLeft, Check, Plus } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { DisabledHint, ErrorBanner, MonoChip } from "@/pages/proposals/shared";
import {
  GroupCrumbs,
  accountErrorMessage,
  addYears,
  derivePeriodLabel,
  todayIso,
  useGroupId,
} from "@/pages/groups/shared";
import { useInsuranceLines } from "@/pages/placements/useInsuranceLines";
import { useAccountGroup } from "@/api/accountGroups";
import { useCreateCaseFile } from "@/api/caseFiles";
import { useCan } from "@/lib/permissions";

interface LineResult {
  lineId: number;
  lineName: string;
  caseFileId?: number;
  error?: string;
}

export default function NewAccountPage() {
  const { t } = useTranslation("accounts");
  const { t: tc } = useTranslation("common");
  const groupId = useGroupId();
  const navigate = useNavigate();
  const [params] = useSearchParams();

  const group = useAccountGroup(groupId);
  const lines = useInsuranceLines();
  const createCase = useCreateCaseFile();
  const canCreate = useCan("CaseFiles", "Create");

  const [start, setStart] = React.useState(todayIso());
  const [end, setEnd] = React.useState(() => addYears(todayIso(), 1));
  const [labelTouched, setLabelTouched] = React.useState(false);
  const [label, setLabel] = React.useState(() =>
    params.get("period") ?? derivePeriodLabel(todayIso(), addYears(todayIso(), 1)),
  );
  const [holder, setHolder] = React.useState<string>("");
  const [members, setMembers] = React.useState<number[]>([]);
  const [selectedLines, setSelectedLines] = React.useState<number[]>(() => {
    const raw = params.get("lines");
    if (!raw) return [];
    return raw
      .split(",")
      .map((value) => Number(value))
      .filter((value) => Number.isFinite(value) && value > 0);
  });
  const [results, setResults] = React.useState<LineResult[] | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  const clients = group.data?.clients ?? [];

  // The contratante defaults to the group's first RUT; it is a real choice, so
  // it stays editable and is never silently assumed.
  React.useEffect(() => {
    if (!holder && clients.length > 0) setHolder(String(clients[0].id));
  }, [clients, holder]);

  React.useEffect(() => {
    if (!labelTouched) setLabel(derivePeriodLabel(start, end));
  }, [start, end, labelTouched]);

  const toggleLine = (id: number) =>
    setSelectedLines((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );

  const toggleMember = (id: number) =>
    setMembers((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );

  const datesValid = !!start && !!end && start < end;
  const canSubmit =
    canCreate.allowed &&
    datesValid &&
    selectedLines.length > 0 &&
    !!holder &&
    !submitting;

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setResults(null);

    const holderId = Number(holder);
    const holderName =
      clients.find((client) => client.id === holderId)?.legal_name ?? "";
    const collected: LineResult[] = [];

    for (const lineId of selectedLines) {
      const lineName = lines.lines.find((item) => item.id === lineId)?.name ?? `#${lineId}`;
      try {
        const created = await createCase.mutateAsync({
          kind: "account",
          client_id: holderId,
          insurance_line_id: lineId,
          account_group_id: groupId,
          period_start: start,
          period_end: end,
          period_label: label || null,
          client_ids: members.length ? members : null,
          title: holderName ? `${holderName} · ${lineName}` : lineName,
        });
        collected.push({ lineId, lineName, caseFileId: created.id });
      } catch (error) {
        collected.push({
          lineId,
          lineName,
          error: accountErrorMessage(error, (key, options) => t(key, options)),
        });
      }
    }

    setResults(collected);
    setSubmitting(false);

    const created = collected.filter((item) => item.caseFileId);
    if (created.length === collected.length && created.length === 1) {
      navigate(`/groups/${groupId}/accounts/${created[0].caseFileId}`);
    }
  };

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={
          <GroupCrumbs
            items={[
              { label: t("group.title"), to: "/groups" },
              { label: group.data?.name ?? t("group.one"), to: `/groups/${groupId}` },
              { label: t("account.create.title") },
            ]}
          />
        }
        title={t("account.create.title")}
        subtitle={t("account.create.subtitle")}
        actions={
          <Button variant="secondary" size="sm" onClick={() => navigate(`/groups/${groupId}`)}>
            <ArrowLeft className="h-4 w-4" />
            {tc("actions.back")}
          </Button>
        }
      />

      {results ? (
        <FadeUp>
          <Card className="flex flex-col gap-2 p-5">
            <h2 className="text-h3 text-text-primary">
              {t("account.create.results")}
            </h2>
            <ul className="flex flex-col gap-1.5">
              {results.map((item) => (
                <li key={item.lineId} className="flex flex-wrap items-center gap-2">
                  {item.caseFileId ? (
                    <>
                      <Check className="h-4 w-4 text-pos-text" />
                      <Link
                        to={`/groups/${groupId}/accounts/${item.caseFileId}`}
                        className="text-body text-text-primary no-underline hover:text-brand-deep"
                      >
                        {item.lineName}
                      </Link>
                    </>
                  ) : (
                    <>
                      <AlertTriangle className="h-4 w-4 text-signal-danger" />
                      <span className="text-body text-text-primary">{item.lineName}</span>
                      <span className="text-caption text-signal-danger">{item.error}</span>
                    </>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        </FadeUp>
      ) : null}

      <FadeUp>
        <Card className="flex max-w-3xl flex-col gap-5 p-5">
          {/* --- Vigencia ------------------------------------------------- */}
          <div className="grid gap-4 sm:grid-cols-3">
            <div>
              <Label htmlFor="period-start">{t("account.create.periodStart")}</Label>
              <Input
                id="period-start"
                type="date"
                value={start}
                onChange={(e) => setStart(e.target.value)}
                className="mt-1.5"
              />
            </div>
            <div>
              <Label htmlFor="period-end">{t("account.create.periodEnd")}</Label>
              <Input
                id="period-end"
                type="date"
                value={end}
                onChange={(e) => setEnd(e.target.value)}
                className="mt-1.5"
              />
            </div>
            <div>
              <Label htmlFor="period-label">{t("renew.periodLabel")}</Label>
              <Input
                id="period-label"
                value={label}
                onChange={(e) => {
                  setLabelTouched(true);
                  setLabel(e.target.value);
                }}
                className="mt-1.5"
              />
            </div>
          </div>
          {!datesValid ? (
            <p className="text-caption text-signal-danger">{t("account.create.datesInvalid")}</p>
          ) : (
            <p className="text-caption text-text-muted">{t("renew.periodLabelHint")}</p>
          )}

          {/* --- Contratante + miembros ----------------------------------- */}
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="holder">{t("account.contratante")}</Label>
              {group.isLoading ? (
                <Skeleton className="mt-1.5 h-9 w-full" />
              ) : clients.length === 0 ? (
                <p className="mt-1.5 text-caption text-text-muted">{t("empty.clients")}</p>
              ) : (
                <Select value={holder} onValueChange={setHolder}>
                  <SelectTrigger id="holder" className="mt-1.5">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {clients.map((client) => (
                      <SelectItem key={client.id} value={String(client.id)}>
                        {client.legal_name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            </div>

            <div>
              <Label>{t("account.members")}</Label>
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {clients
                  .filter((client) => String(client.id) !== holder)
                  .map((client) => {
                    const selected = members.includes(client.id);
                    return (
                      <button
                        key={client.id}
                        type="button"
                        onClick={() => toggleMember(client.id)}
                        className={cn(
                          "rounded-full border px-2.5 py-1 text-caption transition-colors",
                          selected
                            ? "border-brand-line bg-brand-soft text-brand-deep"
                            : "border-line text-text-secondary hover:bg-bg-recessed",
                        )}
                      >
                        {client.legal_name}
                      </button>
                    );
                  })}
                {clients.length <= 1 ? (
                  <span className="text-caption text-text-muted">
                    {t("account.create.noExtraMembers")}
                  </span>
                ) : null}
              </div>
            </div>
          </div>

          {/* --- Ramos (required multi-select) ---------------------------- */}
          <div>
            <Label>{t("account.create.lines")}</Label>
            <p className="mt-1 text-caption text-text-muted">
              {t("account.create.linesHint")}
            </p>

            {lines.isLoading ? (
              <Skeleton className="mt-2 h-24 w-full" />
            ) : lines.lines.length === 0 ? (
              <p className="mt-2 rounded-lg border border-line bg-bg-recessed px-3 py-3 text-caption text-text-muted">
                {t("account.create.noLines")}
              </p>
            ) : (
              <div className="mt-2 flex flex-wrap gap-2">
                {lines.lines.map((line) => {
                  const selected = selectedLines.includes(line.id);
                  return (
                    <button
                      key={line.id}
                      type="button"
                      onClick={() => toggleLine(line.id)}
                      className={cn(
                        "flex items-center gap-1.5 rounded-lg border px-3 py-2 text-body transition-colors",
                        selected
                          ? "border-brand-line bg-brand-soft text-brand-deep"
                          : "border-line text-text-secondary hover:bg-bg-recessed",
                      )}
                    >
                      {selected ? <Check className="h-3.5 w-3.5" /> : null}
                      {line.name}
                    </button>
                  );
                })}
              </div>
            )}

            {selectedLines.length > 0 ? (
              <p className="mt-2 flex items-center gap-2 text-caption text-text-muted">
                <MonoChip>{selectedLines.length}</MonoChip>
                {t("account.create.willCreate", { count: selectedLines.length })}
              </p>
            ) : (
              <p className="mt-2 text-caption text-signal-danger">
                {t("account.create.linesRequired")}
              </p>
            )}
          </div>

          {createCase.isError && !results ? <ErrorBanner error={createCase.error} /> : null}

          <div className="flex items-center justify-end gap-2 border-t border-line pt-4">
            <Button
              variant="secondary"
              size="sm"
              disabled={submitting}
              onClick={() => navigate(`/groups/${groupId}`)}
            >
              {tc("actions.cancel")}
            </Button>
            <DisabledHint
              hint={canCreate.allowed ? null : t("account.noCreatePermission")}
            >
              <Button size="sm" onClick={submit} disabled={!canSubmit}>
                <Plus className="h-4 w-4" />
                {t("account.create.submit")}
              </Button>
            </DisabledHint>
          </div>
        </Card>
      </FadeUp>
    </div>
  );
}
