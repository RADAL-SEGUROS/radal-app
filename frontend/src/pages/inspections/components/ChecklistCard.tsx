import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { ClipboardCheck, Pencil, Plus, Trash2, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { FadeUp, Stagger } from "@/components/common/motion";
import { useReplaceInspectionChecklist } from "@/api/inspections";
import type { Inspection } from "@/api/types";
import { cn } from "@/lib/utils";
import { EmptyState, GuardedButton, RESULT_VARIANT, SectionCard, type Guard } from "./shared";
import {
  RESULT_TOKENS,
  type ChecklistSection,
  type ResultToken,
  countResults,
  nextUid,
  parseChecklist,
  serializeChecklist,
} from "./checklist";

/**
 * Dynamic checklist renderer: sections and items come straight from the stored
 * JSON, so a new template version renders without a code change.
 *
 * Editing is wired to PUT /inspections/{id}/checklist, which replaces the JSON
 * wholesale — so the editor holds the whole tree in local state and sends it in
 * one call. Frozen (issued/archived) reports render read-only.
 */

function ResultBadge({ result }: { result: ResultToken }) {
  const { t } = useTranslation("inspections");
  return <Badge variant={RESULT_VARIANT[result]}>{t(`result.${result}`)}</Badge>;
}

export function ChecklistCard({
  inspection,
  editGuard,
}: {
  inspection: Inspection;
  editGuard: Guard;
}) {
  const { t } = useTranslation("inspections");
  const { t: tc } = useTranslation("common");
  const replace = useReplaceInspectionChecklist(inspection.id);

  const parsed = React.useMemo(
    () => parseChecklist(inspection.checklist),
    [inspection.checklist],
  );

  const [editing, setEditing] = React.useState(false);
  const [draft, setDraft] = React.useState<ChecklistSection[]>(parsed.sections);

  // Re-seed the draft from the server copy — but never while the user is
  // editing, so a background refetch cannot wipe unsaved work.
  React.useEffect(() => {
    if (editing) return;
    setDraft(parsed.sections);
  }, [parsed.sections, editing]);

  const sections = editing ? draft : parsed.sections;
  const counts = React.useMemo(() => countResults(sections), [sections]);

  const updateItem = (
    sectionUid: string,
    itemUid: string,
    patch: Partial<{ text: string; result: ResultToken; note: string }>,
  ) => {
    setDraft((prev) =>
      prev.map((section) =>
        section.uid !== sectionUid
          ? section
          : {
              ...section,
              items: section.items.map((item) =>
                item.uid !== itemUid ? item : { ...item, ...patch },
              ),
            },
      ),
    );
  };

  const removeItem = (sectionUid: string, itemUid: string) => {
    setDraft((prev) =>
      prev.map((section) =>
        section.uid !== sectionUid
          ? section
          : { ...section, items: section.items.filter((item) => item.uid !== itemUid) },
      ),
    );
  };

  const addItem = (sectionUid: string) => {
    setDraft((prev) =>
      prev.map((section) =>
        section.uid !== sectionUid
          ? section
          : {
              ...section,
              items: [
                ...section.items,
                { uid: nextUid("item"), text: "", result: "ok" as ResultToken, note: "" },
              ],
            },
      ),
    );
  };

  const addSection = () => {
    setDraft((prev) => [
      ...prev,
      { uid: nextUid("section"), name: t("checklist.newSection"), items: [] },
    ]);
  };

  const removeSection = (sectionUid: string) => {
    setDraft((prev) => prev.filter((section) => section.uid !== sectionUid));
  };

  const onSave = () => {
    replace.mutate(
      {
        checklist: serializeChecklist(draft, parsed.version),
        checklist_version: inspection.checklist_version ?? undefined,
      },
      {
        onSuccess: () => {
          toast.success(t("checklist.saved"));
          setEditing(false);
        },
        onError: () => toast.error(t("error.save")),
      },
    );
  };

  const summaryChips = (
    <div className="flex flex-wrap items-center gap-2">
      <Badge variant="success">
        {t("checklist.summary.ok")} · {counts.ok}
      </Badge>
      <Badge variant="warn">
        {t("checklist.summary.observation")} · {counts.observation}
      </Badge>
      <Badge variant="danger">
        {t("checklist.summary.critical")} · {counts.critical}
      </Badge>
      {counts.not_applicable > 0 ? (
        <Badge variant="muted">
          {t("checklist.summary.not_applicable")} · {counts.not_applicable}
        </Badge>
      ) : null}
    </div>
  );

  const actions = editing ? (
    <>
      <Button
        variant="secondary"
        size="sm"
        onClick={() => {
          setDraft(parsed.sections);
          setEditing(false);
        }}
        disabled={replace.isPending}
      >
        <X /> {t("checklist.discard")}
      </Button>
      <Button size="sm" onClick={onSave} disabled={replace.isPending}>
        {replace.isPending ? tc("actions.loading") : t("checklist.save")}
      </Button>
    </>
  ) : (
    <GuardedButton
      guard={editGuard}
      variant="secondary"
      size="sm"
      onClick={() => setEditing(true)}
    >
      <Pencil /> {tc("actions.edit")}
    </GuardedButton>
  );

  return (
    <SectionCard
      title={t("checklist.title")}
      icon={<ClipboardCheck />}
      description={
        parsed.version !== null ? t("checklist.version", { n: parsed.version }) : undefined
      }
      actions={actions}
    >
      {!parsed.recognized ? (
        <div className="flex flex-col gap-3">
          <p className="text-body text-text-muted">{t("checklist.unsupported")}</p>
          <pre className="max-h-80 overflow-auto rounded-lg border border-line bg-bg-recessed p-4 font-mono text-mono-sm text-text-secondary">
            {JSON.stringify(inspection.checklist, null, 2)}
          </pre>
        </div>
      ) : sections.length === 0 && !editing ? (
        <EmptyState>{t("checklist.empty")}</EmptyState>
      ) : (
        <div className="flex flex-col gap-5">
          {summaryChips}

          <Stagger className="flex flex-col gap-4">
            {sections.map((section) => (
              <FadeUp key={section.uid}>
                <div className="overflow-hidden rounded-lg border border-line">
                  <div className="flex items-center justify-between gap-3 bg-[color-mix(in_srgb,var(--bg-recessed)_60%,transparent)] px-4 py-2.5">
                    {editing ? (
                      <Input
                        value={section.name}
                        placeholder={t("checklist.sectionName")}
                        className="h-8 max-w-md"
                        onChange={(e) =>
                          setDraft((prev) =>
                            prev.map((s) =>
                              s.uid === section.uid ? { ...s, name: e.target.value } : s,
                            ),
                          )
                        }
                      />
                    ) : (
                      <h3 className="truncate font-display text-h3 text-text-primary">
                        {section.name || "—"}
                      </h3>
                    )}
                    <div className="flex shrink-0 items-center gap-2">
                      <span className="text-caption text-text-muted">
                        {section.items.length}
                      </span>
                      {editing ? (
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          aria-label={tc("actions.delete")}
                          onClick={() => removeSection(section.uid)}
                        >
                          <Trash2 />
                        </Button>
                      ) : null}
                    </div>
                  </div>

                  <ul className="divide-y divide-line">
                    {section.items.map((item) => (
                      <li
                        key={item.uid}
                        className={cn(
                          "flex flex-col gap-2 px-4 py-3 transition-colors sm:flex-row sm:items-start sm:gap-4",
                          !editing && "hover:bg-[color-mix(in_srgb,var(--teal)_4%,transparent)]",
                        )}
                      >
                        {editing ? (
                          <>
                            <div className="flex min-w-0 flex-1 flex-col gap-2">
                              <Input
                                value={item.text}
                                placeholder={t("checklist.itemPlaceholder")}
                                className="h-9"
                                onChange={(e) =>
                                  updateItem(section.uid, item.uid, { text: e.target.value })
                                }
                              />
                              <Input
                                value={item.note}
                                placeholder={t("checklist.notePlaceholder")}
                                className="h-9"
                                onChange={(e) =>
                                  updateItem(section.uid, item.uid, { note: e.target.value })
                                }
                              />
                            </div>
                            <div className="flex shrink-0 items-center gap-2">
                              <Select
                                value={item.result}
                                onValueChange={(value) =>
                                  updateItem(section.uid, item.uid, {
                                    result: value as ResultToken,
                                  })
                                }
                              >
                                <SelectTrigger className="h-9 w-[168px]">
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  {item.result === "unknown" ? (
                                    <SelectItem value="unknown">
                                      {t("result.unknown")}
                                    </SelectItem>
                                  ) : null}
                                  {RESULT_TOKENS.map((token) => (
                                    <SelectItem key={token} value={token}>
                                      {t(`result.${token}`)}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-9 w-9"
                                aria-label={t("checklist.removeItem")}
                                onClick={() => removeItem(section.uid, item.uid)}
                              >
                                <Trash2 />
                              </Button>
                            </div>
                          </>
                        ) : (
                          <>
                            <div className="min-w-0 flex-1">
                              <p className="text-body text-text-primary">{item.text || "—"}</p>
                              {item.note ? (
                                <p className="mt-1 text-caption text-text-muted">{item.note}</p>
                              ) : null}
                            </div>
                            <div className="shrink-0">
                              <ResultBadge result={item.result} />
                            </div>
                          </>
                        )}
                      </li>
                    ))}
                  </ul>

                  {editing ? (
                    <div className="border-t border-line px-4 py-2.5">
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => addItem(section.uid)}
                      >
                        <Plus /> {t("checklist.addItem")}
                      </Button>
                    </div>
                  ) : null}
                </div>
              </FadeUp>
            ))}
          </Stagger>

          {editing ? (
            <div className="flex items-center justify-between gap-3">
              <Button variant="secondary" size="sm" onClick={addSection}>
                <Plus /> {t("checklist.addSection")}
              </Button>
              <span className="text-caption text-amber-deep">{t("checklist.dirty")}</span>
            </div>
          ) : null}
        </div>
      )}
    </SectionCard>
  );
}
