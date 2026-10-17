/**
 * `/groups/:groupId/accounts/new` — open one or more accounts in a vigencia.
 *
 * An **account** (the team's *colocación*) is one ramo × one validity period ×
 * N RUTs. v12 closed the ramo catalogue (team rule R4): ramos and subtipos are
 * one global Radal list (`GET /catalog/lines`) and no corredora creates, edits
 * or deletes them — the old "Crear ramo" drawer is gone and the server answers
 * 403 on every ramo write. Each row of this form is a **ramo → subtipo**
 * cascade plus its **vía** (Tradicional · Digital · Manual): the subtipo
 * proposes the vía and never obliges it (R37).
 *
 * Every row posts one `case_file(kind=account)` with `insurance_subtype_id`
 * (the server derives the ramo and, without an explicit `route`, the subtipo's
 * suggested vía). The antecedentes template resolves from the ramo on the
 * server, so there is no second "assign line" call any more.
 *
 * Dates are required by the server for `kind=account` (422 otherwise) and are
 * the folder's authoritative period; the label is only a grouping string and is
 * derived from the years unless the broker overrides it.
 *
 * One open folder per (group, ramo, vigencia): a ramo already open for these
 * dates comes back as 422 `folder_exists` and is reported with a link to that
 * account, while the other rows still land — the calls are independent.
 */
import * as React from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AlertTriangle, ArrowLeft, Check, Lock, Plus, Trash2 } from "lucide-react";

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
import { RamoSubtypeSelect, RoutePicker } from "@/components/catalog/RamoSubtypeSelect";
import { cn } from "@/lib/utils";
import { DisabledHint, ErrorBanner } from "@/pages/proposals/shared";
import {
  GroupCrumbs,
  accountErrorMessage,
  addYears,
  derivePeriodLabel,
  errorCode,
  todayIso,
  useGroupId,
} from "@/pages/groups/shared";
import { useAccountGroup, useGroupTree } from "@/api/accountGroups";
import { useCreateCaseFile } from "@/api/caseFiles";
import { findSubtype, useCatalogLines, type PlacementRoute } from "@/api/catalog";
import { useCan } from "@/lib/permissions";

interface RamoRow {
  key: number;
  subtypeId: number | null;
  /** `null` = follow the subtipo's suggested vía. */
  route: PlacementRoute | null;
}

interface RowResult {
  key: number;
  label: string;
  caseFileId?: number;
  error?: string;
  /** `folder_exists`: the open account that already holds this ramo. */
  existingCaseId?: number;
}

/** The `case_file_id` a structured `folder_exists` 422 points at. */
function existingFolderId(error: unknown): number | undefined {
  const detail = (
    error as { response?: { data?: { detail?: unknown } } } | undefined
  )?.response?.data?.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const id = (detail as { case_file_id?: unknown }).case_file_id;
    if (typeof id === "number") return id;
  }
  return undefined;
}

let rowSeq = 0;
const newRow = (): RamoRow => ({ key: ++rowSeq, subtypeId: null, route: null });

export default function NewAccountPage() {
  const { t } = useTranslation("accounts");
  const { t: tc } = useTranslation("common");
  const { t: tCat } = useTranslation("catalog");
  const groupId = useGroupId();
  const navigate = useNavigate();
  const [params] = useSearchParams();

  const group = useAccountGroup(groupId);
  const catalog = useCatalogLines();
  const createCase = useCreateCaseFile();
  const canCreate = useCan("CaseFiles", "Create");

  const [start, setStart] = React.useState(todayIso());
  const [end, setEnd] = React.useState(() => addYears(todayIso(), 1));
  // `?period=` (the vigencia page's "Nueva cuenta") pins the label, so it must
  // not be re-derived from the dates; the dates follow from that vigencia below.
  const periodParam = params.get("period");
  const [labelTouched, setLabelTouched] = React.useState(() => params.has("period"));
  const [label, setLabel] = React.useState(() =>
    params.get("period") ?? derivePeriodLabel(todayIso(), addYears(todayIso(), 1)),
  );
  const [holder, setHolder] = React.useState<string>("");
  const [members, setMembers] = React.useState<number[]>([]);
  const [rows, setRows] = React.useState<RamoRow[]>(() => [newRow()]);
  const [results, setResults] = React.useState<RowResult[] | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  const clients = group.data?.clients ?? [];

  // Accounts opened from a vigencia land IN that vigencia: once the tree has
  // loaded, copy the full dates of one of its accounts (the server keys a
  // folder by its exact period_start/period_end, not by the label), falling
  // back to the vigencia's extremes. Runs once; the broker may still edit.
  const tree = useGroupTree(periodParam ? groupId : undefined);
  const periodPrefilled = React.useRef(false);
  React.useEffect(() => {
    if (!periodParam || periodPrefilled.current || !tree.data || tree.isPlaceholderData) return;
    periodPrefilled.current = true;
    const period = tree.data.periods.find((item) => item.label === periodParam);
    if (!period) return;
    const dated = period.lines.find(
      (line) => line.account.period_start && line.account.period_end,
    );
    const nextStart = dated?.account.period_start ?? period.start;
    const nextEnd = dated?.account.period_end ?? period.end;
    if (nextStart && nextEnd) {
      setStart(nextStart);
      setEnd(nextEnd);
    }
  }, [periodParam, tree.data, tree.isPlaceholderData]);

  // The contratante defaults to the group's first RUT; it is a real choice, so
  // it stays editable and is never silently assumed.
  React.useEffect(() => {
    if (!holder && clients.length > 0) setHolder(String(clients[0].id));
  }, [clients, holder]);

  React.useEffect(() => {
    if (!labelTouched) setLabel(derivePeriodLabel(start, end));
  }, [start, end, labelTouched]);

  const toggleMember = (id: number) =>
    setMembers((current) =>
      current.includes(id) ? current.filter((item) => item !== id) : [...current, id],
    );

  const patchRow = (key: number, patch: Partial<RamoRow>) =>
    setRows((current) => current.map((row) => (row.key === key ? { ...row, ...patch } : row)));

  // One account per ramo and vigencia: a second row of the same ramo would be
  // refused by the server (`folder_exists`), so it is flagged before posting.
  const lineOfRow = (row: RamoRow) => findSubtype(catalog.data, row.subtypeId)?.line.id ?? null;
  const duplicateKeys = React.useMemo(() => {
    const seen = new Map<number, number>();
    const dupes = new Set<number>();
    for (const row of rows) {
      const lineId = findSubtype(catalog.data, row.subtypeId)?.line.id;
      if (lineId == null) continue;
      if (seen.has(lineId)) dupes.add(row.key);
      else seen.set(lineId, row.key);
    }
    return dupes;
  }, [rows, catalog.data]);

  const datesValid = !!start && !!end && start < end;
  const rowsValid = rows.every((row) => row.subtypeId != null) && duplicateKeys.size === 0;
  const canSubmit = canCreate.allowed && datesValid && rowsValid && !!holder && !submitting;

  const submit = async () => {
    if (!canSubmit) return;
    setSubmitting(true);
    setResults(null);

    const holderId = Number(holder);
    const holderName = clients.find((client) => client.id === holderId)?.legal_name ?? "";
    const collected: RowResult[] = [];

    // Sequential on purpose: each folder takes the next EXP reference.
    for (const row of rows) {
      const found = findSubtype(catalog.data, row.subtypeId);
      if (!found) continue;
      const ramoLabel =
        found.line.name === found.subtype.name
          ? found.line.name
          : `${found.line.name} · ${found.subtype.name}`;
      try {
        const created = await createCase.mutateAsync({
          kind: "account",
          client_id: holderId,
          insurance_subtype_id: found.subtype.id,
          route: row.route,
          account_group_id: groupId,
          period_start: start,
          period_end: end,
          period_label: label || null,
          client_ids: members.length ? members : null,
          title: holderName ? `${holderName} · ${ramoLabel}` : ramoLabel,
        });
        collected.push({ key: row.key, label: ramoLabel, caseFileId: created.id });
      } catch (error) {
        collected.push({
          key: row.key,
          label: ramoLabel,
          error: accountErrorMessage(error, (key, options) => t(key, options)),
          existingCaseId:
            errorCode(error) === "folder_exists" ? existingFolderId(error) : undefined,
        });
      }
    }

    setResults(collected);
    setSubmitting(false);
    // One account opened → straight into it; several → the results card links each.
    const landed = collected.filter((item) => item.caseFileId);
    if (collected.length === 1 && landed.length === 1) {
      navigate(`/groups/${groupId}/accounts/${landed[0].caseFileId}`);
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
          <Card className="flex max-w-3xl flex-col gap-2 p-5">
            <h2 className="text-h3 text-ink">{t("account.create.results")}</h2>
            <ul className="flex flex-col gap-2">
              {results.map((item) => (
                <li key={item.key} className="flex flex-wrap items-center gap-2">
                  {item.caseFileId ? (
                    <>
                      <Check className="h-4 w-4 shrink-0 text-pos-text" aria-hidden />
                      <Link
                        to={`/groups/${groupId}/accounts/${item.caseFileId}`}
                        className="text-body text-ink no-underline hover:text-brand-deep"
                      >
                        {item.label}
                      </Link>
                    </>
                  ) : (
                    <>
                      <AlertTriangle className="h-4 w-4 shrink-0 text-neg-text" aria-hidden />
                      <span className="text-body text-ink">{item.label}</span>
                      <span className="text-caption text-neg-text">{item.error}</span>
                      {item.existingCaseId ? (
                        <Link
                          to={`/groups/${groupId}/accounts/${item.existingCaseId}`}
                          className="text-caption text-brand-deep"
                        >
                          {t("account.create.openExisting")}
                        </Link>
                      ) : null}
                    </>
                  )}
                </li>
              ))}
            </ul>
          </Card>
        </FadeUp>
      ) : null}

      <FadeUp>
        <Card className="flex max-w-3xl flex-col gap-5 p-4 sm:p-5">
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
            <p className="text-caption text-neg-text">{t("account.create.datesInvalid")}</p>
          ) : (
            <p className="text-caption text-ink-3">{t("renew.periodLabelHint")}</p>
          )}

          {/* --- Contratante + miembros ----------------------------------- */}
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="holder">{t("account.contratante")}</Label>
              {group.isLoading ? (
                <Skeleton className="mt-1.5 h-9 w-full" />
              ) : clients.length === 0 ? (
                <p className="mt-1.5 text-caption text-ink-3">{t("empty.clients")}</p>
              ) : (
                <Select value={holder} onValueChange={setHolder}>
                  <SelectTrigger id="holder" className="mt-1.5 h-10 sm:h-9">
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
                        aria-pressed={selected}
                        onClick={() => toggleMember(client.id)}
                        className={cn(
                          "min-h-9 rounded-full border px-3 py-1 text-caption transition-[background-color,border-color,color,scale] duration-150 ease-out active:scale-[0.96]",
                          selected
                            ? "border-brand-line bg-brand-soft text-brand-deep"
                            : "border-line text-ink-2 hover:bg-paper-2",
                        )}
                      >
                        {client.legal_name}
                      </button>
                    );
                  })}
                {clients.length <= 1 ? (
                  <span className="text-caption text-ink-3">
                    {t("account.create.noExtraMembers")}
                  </span>
                ) : null}
              </div>
            </div>
          </div>

          {/* --- Ramos: one row per account, closed catalogue ------------ */}
          <div className="flex flex-col gap-3">
            <div>
              <Label htmlFor={`ramo-${rows[0]?.key}`}>{t("account.create.ramo")}</Label>
              <p className="mt-1 text-caption text-ink-3">{t("account.create.ramoHint")}</p>
            </div>

            {catalog.isError ? <ErrorBanner error={catalog.error} /> : null}

            <ul className="flex flex-col gap-3">
              {rows.map((row, index) => {
                const found = findSubtype(catalog.data, row.subtypeId);
                const duplicate = duplicateKeys.has(row.key);
                return (
                  <li
                    key={row.key}
                    className="flex flex-col gap-3 rounded-well border border-line bg-paper-2/40 p-3 sm:p-3.5"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-label font-medium text-ink">
                        {t("account.create.rowTitle", { n: index + 1 })}
                      </span>
                      {rows.length > 1 ? (
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-10 w-10 sm:h-8 sm:w-8"
                          aria-label={t("account.create.removeRamo")}
                          disabled={submitting}
                          onClick={() =>
                            setRows((current) => current.filter((item) => item.key !== row.key))
                          }
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      ) : null}
                    </div>

                    <RamoSubtypeSelect
                      id={`ramo-${row.key}`}
                      value={row.subtypeId}
                      // A new subtipo brings its own suggestion: drop the override.
                      onChange={(subtypeId) => patchRow(row.key, { subtypeId, route: null })}
                      disabled={submitting}
                    />

                    {found ? (
                      <div className="flex flex-col gap-1.5">
                        <span className="text-caption font-medium text-ink-2">
                          {tCat("route.label")}
                        </span>
                        <RoutePicker
                          value={row.route}
                          suggested={found.subtype.suggested_route}
                          onChange={(route) => patchRow(row.key, { route })}
                          disabled={submitting}
                        />
                        <p className="text-caption text-ink-3">{tCat("route.hint")}</p>
                      </div>
                    ) : null}

                    {duplicate ? (
                      <p className="text-caption text-neg-text">
                        {t("account.create.ramoDuplicate")}
                      </p>
                    ) : null}
                  </li>
                );
              })}
            </ul>

            <div className="flex flex-wrap items-center justify-between gap-2">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                className="h-10 sm:h-8"
                disabled={submitting || rows.some((row) => lineOfRow(row) == null)}
                onClick={() => setRows((current) => [...current, newRow()])}
              >
                <Plus className="h-4 w-4" />
                {t("account.create.addRamo")}
              </Button>
              {rows.some((row) => row.subtypeId == null) ? (
                <span className="text-caption text-ink-3">{t("account.create.ramoRequired")}</span>
              ) : null}
            </div>

            {/* Why there is no "Crear ramo": the catalogue is closed (R4). */}
            <p className="flex items-start gap-1.5 text-caption text-ink-3">
              <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
              <span>{tCat("closedHint")}</span>
            </p>
          </div>

          <div className="flex flex-col-reverse gap-2 border-t border-line pt-4 sm:flex-row sm:items-center sm:justify-end">
            <Button
              variant="secondary"
              className="h-10 sm:h-8"
              size="sm"
              disabled={submitting}
              onClick={() => navigate(`/groups/${groupId}`)}
            >
              {tc("actions.cancel")}
            </Button>
            <DisabledHint hint={canCreate.allowed ? null : t("account.noCreatePermission")}>
              <Button
                size="sm"
                className="h-10 w-full sm:h-8 sm:w-auto"
                onClick={submit}
                disabled={!canSubmit}
              >
                <Plus className="h-4 w-4" />
                {submitting
                  ? tc("actions.loading")
                  : t("account.create.submit", { count: rows.length })}
              </Button>
            </DisabledHint>
          </div>
        </Card>
      </FadeUp>
    </div>
  );
}
