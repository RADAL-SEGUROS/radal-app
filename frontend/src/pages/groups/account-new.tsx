/**
 * `/groups/:groupId/accounts/new` — open one or more accounts in a vigencia.
 *
 * An **account** is one insurance line × one validity period × N RUTs. The
 * broker picks one or more **ramos** (the `line_record_schema` rows the Ramos
 * settings tab authors, plus the global Radal templates): each selected ramo
 * posts one `case_file(kind=account)` folder — with the ramo's own
 * `insurance_line_id` — and then assigns that ramo to the fresh folder with
 * `POST /case-files/{id}/line`, so the account arrives with its Bases-Técnicas
 * template already resolved. Choosing three ramos posts three folders, all
 * sharing the dates and the members. There is no "multi-ramo folder".
 *
 * Selecting a ramo is the primary path; a broker with `Settings.Manage` can
 * author one inline (create-then-select). Without that grant the create door is
 * disabled with the server's reason — never a dead button, adopt-only.
 *
 * Dates are required by the server for `kind=account` (422 otherwise) and are
 * the folder's authoritative period; the label is only a grouping string and is
 * derived from the years unless the broker overrides it.
 *
 * A ramo whose insurance line already has an open folder for these exact dates
 * comes back as 422 `folder_exists`; that ramo is reported by name and the
 * others still land, because the calls are independent folders.
 */
import * as React from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { AlertTriangle, ArrowLeft, Check, FileText, Plus, Sparkles, Trash2 } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { apiError } from "@/components/common/kit";
import { DisabledHint, ErrorBanner } from "@/pages/proposals/shared";
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
import {
  useAssignCaseLine,
  useCreateRamoSchema,
  useRamoSchemas,
} from "@/api/antecedentes";
import { useCan } from "@/lib/permissions";
import type { LineRecordSchema, RamoRecommendedFile } from "@/api/types";

/** Expected upload formats a recommended file may declare. */
const RECOMMENDED_FORMATS = ["pdf", "word", "pdf/word", "image"] as const;
/** Sentinel value for the "Crear ramo" entry inside the ramo dropdown. */
const CREATE_RAMO = "__create__";

interface RamoResult {
  ramoId: number;
  ramoName: string;
  caseFileId?: number;
  error?: string;
  /** The folder landed but the ramo could not be assigned to it. */
  assignWarning?: string;
}

export default function NewAccountPage() {
  const { t } = useTranslation("accounts");
  const { t: tc } = useTranslation("common");
  const groupId = useGroupId();
  const navigate = useNavigate();
  const [params] = useSearchParams();

  const group = useAccountGroup(groupId);
  const schemas = useRamoSchemas();
  const createCase = useCreateCaseFile();
  const assignLine = useAssignCaseLine();
  const canCreate = useCan("CaseFiles", "Create");
  const canManageSettings = useCan("Settings", "Manage");

  const [start, setStart] = React.useState(todayIso());
  const [end, setEnd] = React.useState(() => addYears(todayIso(), 1));
  const [labelTouched, setLabelTouched] = React.useState(false);
  const [label, setLabel] = React.useState(() =>
    params.get("period") ?? derivePeriodLabel(todayIso(), addYears(todayIso(), 1)),
  );
  const [holder, setHolder] = React.useState<string>("");
  const [members, setMembers] = React.useState<number[]>([]);
  const [selectedRamo, setSelectedRamo] = React.useState<number | null>(null);
  const [creatingRamo, setCreatingRamo] = React.useState(false);
  const [results, setResults] = React.useState<RamoResult[] | null>(null);
  const [submitting, setSubmitting] = React.useState(false);

  const clients = group.data?.clients ?? [];
  const ramos = React.useMemo(
    () => (schemas.data?.items ?? []).filter((s) => s.is_active),
    [schemas.data],
  );

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

  const datesValid = !!start && !!end && start < end;
  const canSubmit =
    canCreate.allowed &&
    datesValid &&
    selectedRamo != null &&
    !!holder &&
    !submitting;

  const submit = async () => {
    if (!canSubmit || selectedRamo == null) return;
    const ramo = ramos.find((item) => item.id === selectedRamo);
    if (!ramo) return;
    setSubmitting(true);
    setResults(null);

    const holderId = Number(holder);
    const holderName =
      clients.find((client) => client.id === holderId)?.legal_name ?? "";
    const ramoName = ramo.name;
    let result: RamoResult;

    try {
      const created = await createCase.mutateAsync({
        kind: "account",
        client_id: holderId,
        insurance_line_id: ramo.insurance_line_id,
        account_group_id: groupId,
        period_start: start,
        period_end: end,
        period_label: label || null,
        client_ids: members.length ? members : null,
        title: holderName ? `${holderName} · ${ramoName}` : ramoName,
      });
      // The folder exists; now resolve its Bases-Técnicas template. A failed
      // assignment is a soft warning — the account still opened.
      let assignWarning: string | undefined;
      try {
        await assignLine.mutateAsync({
          caseId: created.id,
          line_record_schema_id: ramo.id,
        });
      } catch (assignError) {
        assignWarning = accountErrorMessage(assignError, (key, options) =>
          t(key, options),
        );
      }
      result = { ramoId: ramo.id, ramoName, caseFileId: created.id, assignWarning };
    } catch (error) {
      result = {
        ramoId: ramo.id,
        ramoName,
        error: accountErrorMessage(error, (key, options) => t(key, options)),
      };
    }

    setResults([result]);
    setSubmitting(false);
    if (result.caseFileId) {
      navigate(`/groups/${groupId}/accounts/${result.caseFileId}`);
    }
  };

  const createHint = canManageSettings.allowed
    ? null
    : t("account.create.ramoNoCreatePermission");

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
                <li key={item.ramoId} className="flex flex-wrap items-center gap-2">
                  {item.caseFileId ? (
                    <>
                      <Check className="h-4 w-4 text-pos-text" />
                      <Link
                        to={`/groups/${groupId}/accounts/${item.caseFileId}`}
                        className="text-body text-text-primary no-underline hover:text-brand-deep"
                      >
                        {item.ramoName}
                      </Link>
                      {item.assignWarning ? (
                        <span className="text-caption text-warn-text">
                          {t("account.create.ramoAssignFailed")}
                        </span>
                      ) : null}
                    </>
                  ) : (
                    <>
                      <AlertTriangle className="h-4 w-4 text-signal-danger" />
                      <span className="text-body text-text-primary">{item.ramoName}</span>
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

          {/* --- Ramo (required, single-select dropdown) ------------------ */}
          <div>
            <Label htmlFor="ramo">{t("account.create.ramo")}</Label>
            <p className="mt-1 text-caption text-text-muted">
              {t("account.create.ramoHint")}
            </p>

            {schemas.isLoading ? (
              <Skeleton className="mt-2 h-9 w-full" />
            ) : (
              <Select
                value={selectedRamo != null ? String(selectedRamo) : ""}
                onValueChange={(value) => {
                  if (value === CREATE_RAMO) {
                    setCreatingRamo(true);
                    return;
                  }
                  setSelectedRamo(Number(value));
                }}
              >
                <SelectTrigger id="ramo" className="mt-2">
                  <SelectValue placeholder={t("account.create.ramoPlaceholder")} />
                </SelectTrigger>
                <SelectContent>
                  {ramos.map((ramo) => (
                    <SelectItem key={ramo.id} value={String(ramo.id)}>
                      <span className="flex items-center gap-2">
                        {ramo.is_template ? (
                          <Sparkles className="h-3.5 w-3.5 shrink-0 text-brand" />
                        ) : null}
                        <span>{ramo.name}</span>
                        <span className="text-caption text-ink-3">
                          {t("account.create.ramoFileCount", {
                            count: ramo.recommended_files?.length ?? 0,
                          })}
                        </span>
                      </span>
                    </SelectItem>
                  ))}
                  {canManageSettings.allowed ? (
                    <SelectItem value={CREATE_RAMO}>
                      <span className="flex items-center gap-2 text-brand-deep">
                        <Plus className="h-3.5 w-3.5" />
                        {t("account.create.ramoCreate")}
                      </span>
                    </SelectItem>
                  ) : null}
                </SelectContent>
              </Select>
            )}

            {!canManageSettings.allowed ? (
              <p className="mt-1.5 text-caption text-text-muted">{createHint}</p>
            ) : null}

            {schemas.isError ? <ErrorBanner error={schemas.error} className="mt-2" /> : null}

            {selectedRamo == null && !schemas.isLoading && ramos.length > 0 ? (
              <p className="mt-2 text-caption text-signal-danger">
                {t("account.create.ramoRequired")}
              </p>
            ) : null}
            {ramos.length === 0 && !schemas.isLoading ? (
              <p className="mt-2 rounded-lg border border-line bg-bg-recessed px-3 py-3 text-caption text-text-muted">
                {t("account.create.noRamos")}
              </p>
            ) : null}
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

      {creatingRamo ? (
        <CreateRamoSheet
          knownLines={ramos}
          onClose={() => setCreatingRamo(false)}
          onCreated={(id) => {
            setCreatingRamo(false);
            setSelectedRamo(id);
          }}
        />
      ) : null}
    </div>
  );
}

// =============================================================================
// Crear ramo — a Sheet with ONLY: name, an optional insurance line, and an
// editable list of RECOMMENDED FILES (label · doc_type · format · required ·
// description). A ramo is now its recommended antecedentes, not a field builder
// (v9). Posts `POST /line-record-schemas {name, insurance_line_id?,
// recommended_files[]}`; the account then adopts it. Gated Settings.Manage.
// =============================================================================

interface RecommendedRow {
  label: string;
  doc_type: string;
  format: string;
  required: boolean;
  description: string;
}

function emptyRow(): RecommendedRow {
  return { label: "", doc_type: "", format: "pdf/word", required: false, description: "" };
}

function CreateRamoSheet({
  knownLines,
  onClose,
  onCreated,
}: {
  knownLines: LineRecordSchema[];
  onClose: () => void;
  onCreated: (ramoId: number) => void;
}) {
  const { t } = useTranslation("accounts");
  const { t: tc } = useTranslation("common");
  const create = useCreateRamoSchema();
  const insuranceLines = useInsuranceLines();

  const [name, setName] = React.useState("");
  const [lineId, setLineId] = React.useState<string>("");
  const [rows, setRows] = React.useState<RecommendedRow[]>([emptyRow()]);
  const [error, setError] = React.useState<string | null>(null);

  // Insurance-line options: the placements-derived lines unioned with the lines
  // already carried by the visible ramos, so a broker without placements still
  // has the seeded template lines to pick from.
  const lineOptions = React.useMemo(() => {
    const byId = new Map<number, string>();
    for (const line of insuranceLines.lines) byId.set(line.id, line.name);
    for (const ramo of knownLines) {
      if (!byId.has(ramo.insurance_line_id)) {
        byId.set(
          ramo.insurance_line_id,
          ramo.insurance_line_name ?? `#${ramo.insurance_line_id}`,
        );
      }
    }
    return [...byId.entries()]
      .map(([id, label]) => ({ id, label }))
      .sort((a, b) => a.label.localeCompare(b.label, "es"));
  }, [insuranceLines.lines, knownLines]);

  const patchRow = (index: number, patch: Partial<RecommendedRow>) =>
    setRows((current) => current.map((row, i) => (i === index ? { ...row, ...patch } : row)));

  const submit = () => {
    if (!name.trim()) {
      setError(t("account.create.ramoNeedName"));
      return;
    }
    setError(null);
    const recommended_files: RamoRecommendedFile[] = rows
      .filter((row) => row.label.trim() || row.doc_type.trim())
      .map((row) => ({
        label: row.label.trim() || row.doc_type.trim(),
        doc_type: row.doc_type.trim() || row.label.trim(),
        format: row.format,
        required: row.required,
        description: row.description.trim() || null,
      }));
    create.mutate(
      {
        name: name.trim(),
        insurance_line_id: lineId ? Number(lineId) : null,
        recommended_files,
      },
      {
        onSuccess: (ramo) => {
          toast.success(t("account.create.ramoCreated"));
          onCreated(ramo.id);
        },
        onError: (err) => setError(apiError(err, t("account.create.error"))),
      },
    );
  };

  return (
    <Sheet open onOpenChange={(open) => (open ? undefined : onClose())}>
      <SheetContent side="right" className="flex w-full flex-col gap-0 overflow-y-auto sm:max-w-xl">
        <SheetHeader>
          <SheetTitle>{t("account.create.ramoCreateTitle")}</SheetTitle>
          <SheetDescription>{t("account.create.ramoCreateHint")}</SheetDescription>
        </SheetHeader>

        <div className="flex flex-1 flex-col gap-5 py-5">
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <Label htmlFor="ramo-name">{t("account.create.ramoName")}</Label>
              <Input
                id="ramo-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                className="mt-1.5"
                placeholder={t("account.create.ramoNamePlaceholder")}
              />
            </div>
            <div>
              <Label htmlFor="ramo-line">{t("account.create.ramoLineOptional")}</Label>
              <Select value={lineId} onValueChange={setLineId}>
                <SelectTrigger id="ramo-line" className="mt-1.5">
                  <SelectValue placeholder={t("account.create.ramoLinePlaceholder")} />
                </SelectTrigger>
                <SelectContent>
                  {lineOptions.map((line) => (
                    <SelectItem key={line.id} value={String(line.id)}>
                      {line.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="flex flex-col gap-3">
            <div className="flex items-center justify-between">
              <div>
                <Label>{t("account.create.ramoFiles")}</Label>
                <p className="mt-0.5 text-caption text-text-muted">
                  {t("account.create.ramoFilesHint")}
                </p>
              </div>
              <Button
                type="button"
                size="sm"
                variant="secondary"
                onClick={() => setRows((current) => [...current, emptyRow()])}
              >
                <Plus className="h-3.5 w-3.5" />
                {t("account.create.ramoAddFile")}
              </Button>
            </div>

            {rows.map((row, index) => (
              <div
                key={index}
                className="flex flex-col gap-2.5 rounded-well border border-line bg-bone/40 p-3.5"
              >
                <div className="flex items-center gap-2">
                  <FileText className="h-4 w-4 shrink-0 text-ink-3" aria-hidden />
                  <Input
                    value={row.label}
                    onChange={(e) => patchRow(index, { label: e.target.value })}
                    placeholder={t("account.create.ramoFileLabel")}
                  />
                  {rows.length > 1 ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="h-9 w-9 shrink-0"
                      aria-label={tc("actions.delete")}
                      onClick={() =>
                        setRows((current) => current.filter((_, i) => i !== index))
                      }
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </Button>
                  ) : null}
                </div>
                <div className="grid gap-2.5 sm:grid-cols-[1fr_auto]">
                  <Input
                    value={row.doc_type}
                    onChange={(e) => patchRow(index, { doc_type: e.target.value })}
                    placeholder={t("account.create.ramoFileType")}
                  />
                  <div className="flex items-center gap-3">
                    <Select
                      value={row.format}
                      onValueChange={(value) => patchRow(index, { format: value })}
                    >
                      <SelectTrigger className="w-[130px]">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {RECOMMENDED_FORMATS.map((fmt) => (
                          <SelectItem key={fmt} value={fmt}>
                            {t(`account.create.ramoFormat.${fmt}`, { defaultValue: fmt })}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <label className="flex items-center gap-2">
                      <Switch
                        checked={row.required}
                        onCheckedChange={(v) => patchRow(index, { required: v })}
                      />
                      <span className="text-caption text-ink-2">
                        {t("account.create.ramoFileRequired")}
                      </span>
                    </label>
                  </div>
                </div>
                <Textarea
                  value={row.description}
                  onChange={(e) => patchRow(index, { description: e.target.value })}
                  rows={2}
                  placeholder={t("account.create.ramoFileDescription")}
                />
              </div>
            ))}
          </div>

          {error ? <p className="text-caption text-signal-danger">{error}</p> : null}
        </div>

        <SheetFooter className="border-t border-line pt-4">
          <Button variant="secondary" onClick={onClose} disabled={create.isPending}>
            {tc("actions.cancel")}
          </Button>
          <Button onClick={submit} disabled={create.isPending || !name.trim()}>
            {create.isPending ? tc("actions.loading") : t("account.create.ramoCreate")}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
