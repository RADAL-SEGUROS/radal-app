/**
 * Líneas manager — the 4th settings tab (spec v7 §D).
 *
 * A *line* is the ramo plus the antecedentes data it requires. This tab lists
 * the broker's own lines AND the global Radal **templates** (a Plantilla badge),
 * each with its **usage** ("N cuentas · M grupos"). A broker creates a line from
 * a template (adopt → editable) or from scratch, edits it in the nested
 * section/field authoring form (mandatory toggle + field-type select incl. the
 * `list` matrix with a totals row and `money_uf`), and deletes it when nothing
 * uses it (disabled with the count otherwise). Gated `Settings.Manage`; the
 * global templates are read-only — a broker adopts, never edits, them.
 *
 * Keys are English identifiers (rule 1); labels/descriptions/options are
 * broker-authored data and may be Spanish.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import type { ColumnDef } from "@tanstack/react-table";
import { Copy, FilePlus2, Layers, Pencil, Plus, Sparkles, Trash2 } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
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
import { DataTable } from "@/components/common/DataTable";
import { apiError } from "@/components/common/kit";
import {
  useCreateRamoSchema,
  useDeleteRamoSchema,
  useRamoSchemas,
  useUpdateRamoSchema,
} from "@/api/antecedentes";
import { useInsuranceLines } from "@/pages/placements/useInsuranceLines";
import { useCan } from "@/lib/permissions";
import { Field, GuardedButton, SectionCard, type Guard } from "./shared";
import type {
  LineRecordSchema,
  LineRecordSchemaCreate,
  LineRecordSchemaUpdate,
  RamoField,
  RamoFieldType,
} from "@/api/types";

const FIELD_TYPES: RamoFieldType[] = [
  "text",
  "number",
  "integer",
  "boolean",
  "date",
  "money_uf",
  "percent",
  "select",
  "list",
  "group",
];

/** Total usage across accounts + groups — the delete gate. */
function usageCount(schema: LineRecordSchema): number {
  return (schema.usage?.accounts ?? 0) + (schema.usage?.groups ?? 0);
}

// =============================================================================
// Draft model (local editor state) ↔ wire definition
// =============================================================================

interface DraftField {
  key: string;
  label: string;
  type: RamoFieldType;
  required: boolean;
  description: string;
  options: string; // comma-separated in the editor
  repeatable: boolean;
  totals: boolean;
  fields: DraftField[];
}

interface DraftSection {
  key: string;
  label: string;
  fields: DraftField[];
}

interface Draft {
  id: number | null;
  name: string;
  insuranceLineId: number | null;
  isActive: boolean;
  sections: DraftSection[];
}

const KEY_RE = /^[A-Za-z0-9_]+$/;

function newField(): DraftField {
  return {
    key: "",
    label: "",
    type: "text",
    required: false,
    description: "",
    options: "",
    repeatable: false,
    totals: false,
    fields: [],
  };
}

function newSection(): DraftSection {
  return { key: "", label: "", fields: [newField()] };
}

function emptyDraft(): Draft {
  return { id: null, name: "", insuranceLineId: null, isActive: true, sections: [newSection()] };
}

function draftFieldFrom(field: RamoField): DraftField {
  return {
    key: field.key,
    label: field.label,
    type: field.type,
    required: !!field.required,
    description: field.description ?? "",
    options: (field.options ?? []).join(", "),
    repeatable: !!field.repeatable,
    totals: !!field.totals,
    fields: (field.fields ?? []).map(draftFieldFrom),
  };
}

function draftFrom(schema: LineRecordSchema): Draft {
  return {
    id: schema.id,
    name: schema.name,
    insuranceLineId: schema.insurance_line_id,
    isActive: schema.is_active,
    sections: (schema.definition?.sections ?? []).map((s) => ({
      key: s.key,
      label: s.label,
      fields: (s.fields ?? []).map(draftFieldFrom),
    })),
  };
}

/** Adopt a template into a NEW broker line: same shape, no id (so save = create). */
function draftFromTemplate(template: LineRecordSchema): Draft {
  return { ...draftFrom(template), id: null };
}

function toWireField(field: DraftField): RamoField {
  const out: RamoField = {
    key: field.key.trim(),
    label: field.label.trim() || field.key.trim(),
    type: field.type,
    required: field.required,
  };
  if (field.description.trim()) out.description = field.description.trim();
  if (field.type === "select") {
    out.options = field.options
      .split(",")
      .map((o) => o.trim())
      .filter(Boolean);
  }
  if (field.type === "list" || field.repeatable) out.repeatable = true;
  if (field.type === "list" && field.totals) out.totals = true;
  if ((field.type === "group" || field.type === "list") && field.fields.length) {
    out.fields = field.fields.map(toWireField);
  }
  return out;
}

function toDefinition(draft: Draft) {
  return {
    sections: draft.sections.map((s) => ({
      key: s.key.trim(),
      label: s.label.trim() || s.key.trim(),
      fields: s.fields.map(toWireField),
    })),
  };
}

function toCreate(draft: Draft): LineRecordSchemaCreate {
  return {
    name: draft.name.trim(),
    insurance_line_id: draft.insuranceLineId as number,
    is_active: draft.isActive,
    definition: toDefinition(draft),
  };
}

function toUpdate(draft: Draft): LineRecordSchemaUpdate {
  return {
    name: draft.name.trim(),
    is_active: draft.isActive,
    definition: toDefinition(draft),
  };
}

/** Reasons the draft cannot be saved, or `null`. */
function validate(draft: Draft, t: (k: string) => string): string | null {
  if (!draft.name.trim()) return t("schemas.form.needName");
  if (!draft.insuranceLineId) return t("schemas.form.needLine");
  const hasField = draft.sections.some((s) => s.fields.length > 0);
  if (draft.sections.length === 0 || !hasField) return t("schemas.form.needSection");
  return null;
}

// =============================================================================
// Tab
// =============================================================================

export function RamoSchemasTab() {
  const { t } = useTranslation("settings");
  const { t: tc } = useTranslation("common");
  const canManage = useCan("Settings", "Manage");
  const schemas = useRamoSchemas();
  const remove = useDeleteRamoSchema();

  const [picking, setPicking] = React.useState(false);
  const [editing, setEditing] = React.useState<Draft | null>(null);
  const [deleting, setDeleting] = React.useState<LineRecordSchema | null>(null);

  const writeGuard: Guard = canManage.allowed
    ? { allowed: true, reason: "" }
    : { allowed: false, reason: t("guard.noPermission") };

  const templates = React.useMemo(
    () => (schemas.data?.items ?? []).filter((s) => s.is_template),
    [schemas.data],
  );

  const columns = React.useMemo<ColumnDef<LineRecordSchema>[]>(
    () => [
      {
        accessorKey: "name",
        header: t("lines.columns.name"),
        cell: ({ row }) => (
          <span className="font-medium text-text-primary">{row.original.name}</span>
        ),
      },
      {
        accessorKey: "insurance_line_id",
        header: t("lines.columns.line"),
        cell: ({ row }) => (
          <span className="text-text-secondary">
            {row.original.insurance_line_name ?? `#${row.original.insurance_line_id}`}
          </span>
        ),
      },
      {
        id: "scope",
        header: t("lines.columns.scope"),
        cell: ({ row }) =>
          row.original.is_template ? (
            <Badge variant="brand" className="gap-1">
              <Sparkles className="h-3 w-3" />
              {t("lines.plantilla")}
            </Badge>
          ) : (
            <Badge variant="muted">{t("lines.ownScope")}</Badge>
          ),
      },
      {
        id: "usage",
        header: t("lines.columns.usage"),
        cell: ({ row }) => (
          <span className="text-caption tabular-nums text-text-secondary">
            {t("lines.usageLabel", {
              accounts: row.original.usage?.accounts ?? 0,
              groups: row.original.usage?.groups ?? 0,
            })}
          </span>
        ),
      },
      {
        id: "status",
        header: t("lines.columns.status"),
        cell: ({ row }) => (
          <Badge variant={row.original.is_active ? "success" : "muted"}>
            {row.original.is_active ? t("lines.active") : t("lines.inactive")}
          </Badge>
        ),
      },
      {
        id: "actions",
        header: t("lines.columns.actions"),
        enableSorting: false,
        cell: ({ row }) => {
          const schema = row.original;
          if (schema.is_template) {
            // A global template is read-only; the broker adopts it into a line.
            return (
              <div className="flex items-center justify-end">
                <GuardedButton
                  guard={writeGuard}
                  variant="ghost"
                  size="sm"
                  onClick={() => setEditing(draftFromTemplate(schema))}
                >
                  <Copy className="h-3.5 w-3.5" />
                  {t("lines.adopt")}
                </GuardedButton>
              </div>
            );
          }
          const inUse = usageCount(schema);
          const deleteGuard: Guard = !writeGuard.allowed
            ? writeGuard
            : inUse > 0
              ? { allowed: false, reason: t("lines.deleteInUse", { count: inUse }) }
              : writeGuard;
          return (
            <div className="flex items-center justify-end gap-1">
              <GuardedButton
                guard={writeGuard}
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                aria-label={t("lines.edit")}
                onClick={() => setEditing(draftFrom(schema))}
              >
                <Pencil />
              </GuardedButton>
              <GuardedButton
                guard={deleteGuard}
                variant="ghost"
                size="icon"
                className="h-8 w-8"
                aria-label={t("lines.delete")}
                onClick={() => setDeleting(schema)}
              >
                <Trash2 />
              </GuardedButton>
            </div>
          );
        },
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [t, writeGuard.allowed],
  );

  return (
    <SectionCard
      title={t("lines.title")}
      icon={<Layers />}
      description={t("lines.description")}
      actions={
        <GuardedButton guard={writeGuard} size="sm" onClick={() => setPicking(true)}>
          <Plus /> {t("lines.create")}
        </GuardedButton>
      }
      bodyClassName="p-5 pt-4"
    >
      <DataTable
        columns={columns}
        data={schemas.data?.items ?? []}
        isLoading={schemas.isLoading}
        emptyMessage={t("lines.empty")}
      />

      {picking ? (
        <CreatePickerDialog
          templates={templates}
          onScratch={() => {
            setPicking(false);
            setEditing(emptyDraft());
          }}
          onTemplate={(template) => {
            setPicking(false);
            setEditing(draftFromTemplate(template));
          }}
          onClose={() => setPicking(false)}
        />
      ) : null}

      {editing ? <SchemaEditorDialog initial={editing} onDone={() => setEditing(null)} /> : null}

      <Dialog open={!!deleting} onOpenChange={(open) => (open ? undefined : setDeleting(null))}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle>{t("lines.deleteTitle")}</DialogTitle>
            <DialogDescription>{t("lines.deleteDescription")}</DialogDescription>
          </DialogHeader>
          <p className="truncate text-body text-text-secondary">{deleting?.name}</p>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setDeleting(null)} disabled={remove.isPending}>
              {tc("actions.cancel")}
            </Button>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              onClick={() => {
                if (!deleting) return;
                remove.mutate(deleting.id, {
                  onSuccess: () => {
                    toast.success(t("lines.deleted"));
                    setDeleting(null);
                  },
                  onError: (error) => toast.error(apiError(error, t("error.generic"))),
                });
              }}
            >
              {remove.isPending ? tc("actions.loading") : tc("actions.delete")}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </SectionCard>
  );
}

// =============================================================================
// Create picker — a template gallery, or from scratch
// =============================================================================

function CreatePickerDialog({
  templates,
  onScratch,
  onTemplate,
  onClose,
}: {
  templates: LineRecordSchema[];
  onScratch: () => void;
  onTemplate: (template: LineRecordSchema) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation("settings");
  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onClose())}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{t("lines.picker.title")}</DialogTitle>
          <DialogDescription>{t("lines.picker.description")}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-3">
          {templates.length > 0 ? (
            <div className="flex flex-col gap-2">
              <span className="text-caption font-medium text-ink-3">
                {t("lines.picker.templatesTitle")}
              </span>
              {templates.map((template) => (
                <button
                  key={template.id}
                  type="button"
                  onClick={() => onTemplate(template)}
                  className="flex items-center justify-between gap-3 rounded-lg border border-line bg-bone/40 px-4 py-3 text-left transition-colors duration-150 hover:border-line-strong hover:bg-paper-2/60"
                >
                  <span className="min-w-0">
                    <span className="flex items-center gap-2">
                      <Sparkles className="h-3.5 w-3.5 text-brand" />
                      <span className="truncate text-label font-medium text-ink">
                        {template.name}
                      </span>
                    </span>
                    <span className="mt-0.5 block text-caption text-ink-3">
                      {template.insurance_line_name ?? `#${template.insurance_line_id}`} ·{" "}
                      {t("lines.picker.sectionsCount", {
                        count: template.definition?.sections?.length ?? 0,
                      })}
                    </span>
                  </span>
                  <Badge variant="brand">{t("lines.picker.useTemplate")}</Badge>
                </button>
              ))}
            </div>
          ) : (
            <p className="text-caption text-ink-3">{t("lines.picker.noTemplates")}</p>
          )}

          <button
            type="button"
            onClick={onScratch}
            className="flex items-center gap-3 rounded-lg border border-dashed border-line px-4 py-3 text-left transition-colors duration-150 hover:border-line-strong hover:bg-paper-2/60"
          >
            <FilePlus2 className="h-4 w-4 text-ink-3" />
            <span>
              <span className="block text-label font-medium text-ink">
                {t("lines.picker.fromScratch")}
              </span>
              <span className="block text-caption text-ink-3">
                {t("lines.picker.fromScratchHint")}
              </span>
            </span>
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

// =============================================================================
// Editor dialog
// =============================================================================

function SchemaEditorDialog({ initial, onDone }: { initial: Draft; onDone: () => void }) {
  const { t } = useTranslation("settings");
  const { lines } = useInsuranceLines();
  const create = useCreateRamoSchema();
  const update = useUpdateRamoSchema(initial.id ?? 0);

  const [draft, setDraft] = React.useState<Draft>(initial);
  const [error, setError] = React.useState<string | null>(null);

  const isEdit = initial.id !== null;
  const pending = create.isPending || update.isPending;

  // The schema's own line may not be in the derived options — keep it visible.
  const lineOptions = React.useMemo(() => {
    const opts = [...lines];
    if (draft.insuranceLineId && !opts.some((l) => l.id === draft.insuranceLineId)) {
      opts.unshift({ id: draft.insuranceLineId, name: `#${draft.insuranceLineId}` });
    }
    return opts;
  }, [lines, draft.insuranceLineId]);

  const patchSection = (i: number, patch: Partial<DraftSection>) =>
    setDraft((d) => ({
      ...d,
      sections: d.sections.map((s, idx) => (idx === i ? { ...s, ...patch } : s)),
    }));

  const patchField = (si: number, fi: number, patch: Partial<DraftField>) =>
    setDraft((d) => ({
      ...d,
      sections: d.sections.map((s, idx) =>
        idx === si
          ? { ...s, fields: s.fields.map((f, j) => (j === fi ? { ...f, ...patch } : f)) }
          : s,
      ),
    }));

  const patchSubField = (si: number, fi: number, gi: number, patch: Partial<DraftField>) =>
    setDraft((d) => ({
      ...d,
      sections: d.sections.map((s, idx) =>
        idx === si
          ? {
              ...s,
              fields: s.fields.map((f, j) =>
                j === fi ? { ...f, fields: f.fields.map((g, k) => (k === gi ? { ...g, ...patch } : g)) } : f,
              ),
            }
          : s,
      ),
    }));

  const submit = () => {
    const reason = validate(draft, (k) => t(k));
    if (reason) {
      setError(reason);
      return;
    }
    setError(null);
    const opts = {
      onSuccess: () => {
        toast.success(isEdit ? t("lines.updated") : t("lines.created"));
        onDone();
      },
      onError: (err: unknown) => toast.error(apiError(err, t("error.generic"))),
    };
    if (isEdit) update.mutate(toUpdate(draft), opts);
    else create.mutate(toCreate(draft), opts);
  };

  return (
    <Dialog open onOpenChange={(open) => (open ? undefined : onDone())}>
      <DialogContent className="max-h-[88vh] max-w-3xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{isEdit ? t("lines.form.editTitle") : t("lines.form.createTitle")}</DialogTitle>
          <DialogDescription>{t("schemas.form.description")}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-5">
          <div className="grid gap-4 md:grid-cols-2">
            <Field label={t("schemas.form.name")} htmlFor="schema-name">
              <Input
                id="schema-name"
                value={draft.name}
                placeholder={t("schemas.form.namePlaceholder")}
                onChange={(e) => setDraft((d) => ({ ...d, name: e.target.value }))}
              />
            </Field>
            <Field label={t("schemas.form.line")}>
              <Select
                value={draft.insuranceLineId ? String(draft.insuranceLineId) : ""}
                onValueChange={(v) => setDraft((d) => ({ ...d, insuranceLineId: Number(v) }))}
              >
                <SelectTrigger>
                  <SelectValue placeholder={t("schemas.form.linePlaceholder")} />
                </SelectTrigger>
                <SelectContent>
                  {lineOptions.map((line) => (
                    <SelectItem key={line.id} value={String(line.id)}>
                      {line.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {lineOptions.length === 0 ? (
                <p className="text-caption text-text-muted">{t("schemas.form.noLines")}</p>
              ) : null}
            </Field>
          </div>

          <label className="flex items-center gap-2.5">
            <Switch
              checked={draft.isActive}
              onCheckedChange={(v) => setDraft((d) => ({ ...d, isActive: v }))}
            />
            <span className="text-label text-ink-2">{t("schemas.form.isActive")}</span>
          </label>

          {/* Sections */}
          <div className="flex flex-col gap-4">
            {draft.sections.map((section, si) => (
              <div key={si} className="flex flex-col gap-3 rounded-lg border border-line bg-bone/40 p-4">
                <div className="flex flex-wrap items-end gap-3">
                  <Field label={t("schemas.form.sectionKey")} className="min-w-[140px] flex-1">
                    <Input
                      value={section.key}
                      onChange={(e) => patchSection(si, { key: e.target.value })}
                    />
                  </Field>
                  <Field label={t("schemas.form.sectionLabel")} className="min-w-[180px] flex-[2]">
                    <Input
                      value={section.label}
                      onChange={(e) => patchSection(si, { label: e.target.value })}
                    />
                  </Field>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() =>
                      setDraft((d) => ({ ...d, sections: d.sections.filter((_, idx) => idx !== si) }))
                    }
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                    {t("schemas.form.removeSection")}
                  </Button>
                </div>

                <div className="flex flex-col gap-2 border-t border-line pt-3">
                  <span className="text-caption font-medium text-ink-3">{t("schemas.form.fields")}</span>
                  {section.fields.map((field, fi) => (
                    <FieldEditor
                      key={fi}
                      field={field}
                      onChange={(patch) => patchField(si, fi, patch)}
                      onRemove={() =>
                        patchSection(si, { fields: section.fields.filter((_, idx) => idx !== fi) })
                      }
                      onSubChange={(gi, patch) => patchSubField(si, fi, gi, patch)}
                      onAddSub={() =>
                        patchField(si, fi, { fields: [...field.fields, newField()] })
                      }
                      onRemoveSub={(gi) =>
                        patchField(si, fi, { fields: field.fields.filter((_, idx) => idx !== gi) })
                      }
                    />
                  ))}
                  <div>
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={() => patchSection(si, { fields: [...section.fields, newField()] })}
                    >
                      <Plus className="h-3.5 w-3.5" />
                      {t("schemas.form.addField")}
                    </Button>
                  </div>
                </div>
              </div>
            ))}
            <div>
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => setDraft((d) => ({ ...d, sections: [...d.sections, newSection()] }))}
              >
                <Plus className="h-3.5 w-3.5" />
                {t("schemas.form.addSection")}
              </Button>
            </div>
          </div>

          {error ? <p className="text-caption text-neg-text">{error}</p> : null}
        </div>

        <DialogFooter>
          <Button type="button" variant="secondary" onClick={onDone} disabled={pending}>
            {t("schemas.form.cancel")}
          </Button>
          <Button type="button" onClick={submit} disabled={pending}>
            {pending ? t("schemas.form.saving") : t("schemas.form.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function FieldEditor({
  field,
  onChange,
  onRemove,
  onSubChange,
  onAddSub,
  onRemoveSub,
}: {
  field: DraftField;
  onChange: (patch: Partial<DraftField>) => void;
  onRemove: () => void;
  onSubChange: (index: number, patch: Partial<DraftField>) => void;
  onAddSub: () => void;
  onRemoveSub: (index: number) => void;
}) {
  const { t } = useTranslation("settings");
  const nested = field.type === "group" || field.type === "list";
  const invalidKey = field.key.trim() !== "" && !KEY_RE.test(field.key.trim());

  return (
    <div className="flex flex-col gap-2 rounded-md border border-line bg-paper px-3 py-2.5">
      <div className="grid gap-2 md:grid-cols-[1fr_1.5fr_1fr]">
        <div className="flex flex-col gap-1">
          <Input
            value={field.key}
            placeholder={t("schemas.form.fieldKey")}
            onChange={(e) => onChange({ key: e.target.value })}
            className={invalidKey ? "border-neg" : undefined}
          />
          {invalidKey ? <span className="text-[11px] text-neg-text">{t("schemas.form.keyHint")}</span> : null}
        </div>
        <Input
          value={field.label}
          placeholder={t("schemas.form.fieldLabel")}
          onChange={(e) => onChange({ label: e.target.value })}
        />
        <Select value={field.type} onValueChange={(v) => onChange({ type: v as RamoFieldType })}>
          <SelectTrigger>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {FIELD_TYPES.map((ft) => (
              <SelectItem key={ft} value={ft}>
                {t(`schemas.fieldTypes.${ft}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className="flex flex-wrap items-center gap-4">
        <label className="flex items-center gap-2">
          <Checkbox checked={field.required} onCheckedChange={(v) => onChange({ required: v === true })} />
          <span className="text-caption text-ink-2">{t("schemas.form.required")}</span>
        </label>
        {field.type !== "list" && field.type !== "group" ? (
          <label className="flex items-center gap-2">
            <Checkbox
              checked={field.repeatable}
              onCheckedChange={(v) => onChange({ repeatable: v === true })}
            />
            <span className="text-caption text-ink-2">{t("schemas.form.repeatable")}</span>
          </label>
        ) : null}
        {field.type === "list" ? (
          <label className="flex items-center gap-2">
            <Checkbox checked={field.totals} onCheckedChange={(v) => onChange({ totals: v === true })} />
            <span className="text-caption text-ink-2">{t("schemas.form.totals")}</span>
          </label>
        ) : null}
        <Button type="button" variant="ghost" size="sm" className="ml-auto" onClick={onRemove}>
          <Trash2 className="h-3.5 w-3.5" />
          {t("schemas.form.removeField")}
        </Button>
      </div>

      {field.type === "list" && field.totals ? (
        <p className="text-[11px] text-ink-3">{t("schemas.form.totalsHint")}</p>
      ) : null}

      {field.type === "select" ? (
        <Field label={t("schemas.form.options")} hint={t("schemas.form.optionsHint")}>
          <Input value={field.options} onChange={(e) => onChange({ options: e.target.value })} />
        </Field>
      ) : null}

      <Textarea
        value={field.description}
        placeholder={t("schemas.form.descriptionPlaceholder")}
        rows={2}
        onChange={(e) => onChange({ description: e.target.value })}
      />

      {nested ? (
        <div className="mt-1 flex flex-col gap-2 border-l-2 border-line pl-3">
          <span className="text-caption font-medium text-ink-3">{t("schemas.form.subfields")}</span>
          {field.fields.map((sub, gi) => (
            <div key={gi} className="grid gap-2 md:grid-cols-[1fr_1.5fr_1fr_auto]">
              <Input
                value={sub.key}
                placeholder={t("schemas.form.fieldKey")}
                onChange={(e) => onSubChange(gi, { key: e.target.value })}
              />
              <Input
                value={sub.label}
                placeholder={t("schemas.form.fieldLabel")}
                onChange={(e) => onSubChange(gi, { label: e.target.value })}
              />
              <Select value={sub.type} onValueChange={(v) => onSubChange(gi, { type: v as RamoFieldType })}>
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {FIELD_TYPES.filter((ft) => ft !== "group" && ft !== "list").map((ft) => (
                    <SelectItem key={ft} value={ft}>
                      {t(`schemas.fieldTypes.${ft}`)}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <div className="flex items-center gap-2">
                <label className="flex items-center gap-1.5">
                  <Checkbox
                    checked={sub.required}
                    onCheckedChange={(v) => onSubChange(gi, { required: v === true })}
                  />
                  <span className="text-[11px] text-ink-3">{t("schemas.form.required")}</span>
                </label>
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="h-9 w-9"
                  aria-label={t("schemas.form.removeField")}
                  onClick={() => onRemoveSub(gi)}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                </Button>
              </div>
            </div>
          ))}
          <div>
            <Button type="button" variant="secondary" size="sm" onClick={onAddSub}>
              <Plus className="h-3.5 w-3.5" />
              {t("schemas.form.addSubfield")}
            </Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
