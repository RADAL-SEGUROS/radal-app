/**
 * Notes on any entity — case file, lead, policy, claim.
 *
 * Two things the team asked for explicitly and that are therefore first-class
 * here rather than buried in a form: the `is_internal` toggle (broker-private
 * commentary that must never reach an insured or insurer user) and
 * `follow_up_on`, the single date a note can carry.
 *
 * Permission is resolved server-side from the TARGET entity's module, so this
 * component takes `canComment` from the caller's `useCan(<module>, "Comment")`
 * and renders the composer disabled — never hidden — when it is false.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Lock, MessageSquare, Trash2, Unlock } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  DisabledHint,
  EmptyState,
  ErrorBanner,
} from "@/components/common/kit";
import { useCreateNote, useDeleteNote, useNotes } from "@/api/notes";
import { formatDate, formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { EntityType } from "@/api/types";

export interface NotesPanelProps {
  entityType: EntityType;
  entityId: number;
  canComment?: boolean;
  /** Author or `.Manage` — the server is still the authority on the delete. */
  canDelete?: boolean;
  className?: string;
}

export function NotesPanel({
  entityType,
  entityId,
  canComment = false,
  canDelete = false,
  className,
}: NotesPanelProps) {
  const { t } = useTranslation("cases");
  const { t: tc } = useTranslation("common");

  const [body, setBody] = React.useState("");
  const [isInternal, setIsInternal] = React.useState(true);
  const [followUp, setFollowUp] = React.useState("");

  const notes = useNotes({ entity_type: entityType, entity_id: entityId });
  const create = useCreateNote();
  const remove = useDeleteNote();

  const submit = () => {
    if (!body.trim()) return;
    create.mutate(
      {
        entity_type: entityType,
        entity_id: entityId,
        body: body.trim(),
        is_internal: isInternal,
        follow_up_on: followUp || null,
      },
      {
        onSuccess: () => {
          setBody("");
          setFollowUp("");
        },
      },
    );
  };

  const items = notes.data?.items ?? [];

  return (
    <div className={cn("flex flex-col gap-4", className)}>
      {/* Composer */}
      <div className="rounded-card border border-line bg-bone p-4">
        <Label htmlFor="note-body">{t("notes.new")}</Label>
        <textarea
          id="note-body"
          rows={3}
          value={body}
          disabled={!canComment}
          onChange={(e) => setBody(e.target.value)}
          placeholder={t("notes.placeholder")}
          className="mt-1.5 w-full rounded-sm border border-line bg-bone px-3 py-2 text-body text-ink outline-none transition-[border-color,box-shadow] duration-150 placeholder:text-muted-foreground focus-visible:border-brand focus-visible:ring-2 focus-visible:ring-brand-ring disabled:opacity-60"
        />

        <div className="mt-3 flex flex-wrap items-end gap-4">
          <button
            type="button"
            disabled={!canComment}
            onClick={() => setIsInternal((v) => !v)}
            className={cn(
              "flex items-center gap-2 rounded-full border px-3 py-1.5 text-caption font-medium transition-colors disabled:opacity-60",
              isInternal
                ? "border-warn-line bg-warn-soft text-warn-text"
                : "border-line text-ink-3 hover:border-line-strong",
            )}
          >
            {isInternal ? <Lock className="h-3.5 w-3.5" /> : <Unlock className="h-3.5 w-3.5" />}
            {isInternal ? t("notes.internal") : t("notes.shared")}
          </button>

          <div className="flex flex-col gap-1">
            <Label htmlFor="note-followup" className="text-caption">
              {t("notes.followUp")}
            </Label>
            <Input
              id="note-followup"
              type="date"
              value={followUp}
              disabled={!canComment}
              onChange={(e) => setFollowUp(e.target.value)}
              className="w-[170px]"
            />
          </div>

          <DisabledHint hint={canComment ? null : t("notes.noPermission")}>
            <Button
              size="sm"
              className="ml-auto"
              disabled={!canComment || !body.trim() || create.isPending}
              onClick={submit}
            >
              {create.isPending ? tc("actions.loading") : t("notes.add")}
            </Button>
          </DisabledHint>
        </div>

        {create.isError ? <ErrorBanner error={create.error} className="mt-3" /> : null}
      </div>

      {/* Feed */}
      {notes.isLoading ? (
        <div className="flex flex-col gap-2">
          <Skeleton className="h-16 w-full" />
          <Skeleton className="h-16 w-full" />
        </div>
      ) : items.length === 0 ? (
        <EmptyState
          title={t("notes.empty")}
          hint={t("notes.emptyHint")}
          icon={<MessageSquare className="h-6 w-6" />}
        />
      ) : (
        <ul className="flex flex-col gap-2.5">
          {items.map((note) => (
            <li
              key={note.id}
              className="rounded-card border border-line bg-bone p-3.5"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-caption font-semibold text-ink">
                  {note.author_name ?? t("notes.unknownAuthor")}
                </span>
                <span className="text-caption text-ink-3">
                  {formatDateTime(note.created_at)}
                </span>
                {note.is_internal ? (
                  <Badge variant="warn" className="gap-1">
                    <Lock className="h-3 w-3" />
                    {t("notes.internal")}
                  </Badge>
                ) : null}
                {note.follow_up_on ? (
                  <Badge variant="action">
                    {t("notes.followUpOn", { date: formatDate(note.follow_up_on) })}
                  </Badge>
                ) : null}
                {canDelete ? (
                  <Button
                    size="sm"
                    variant="ghost"
                    className="ml-auto"
                    disabled={remove.isPending}
                    onClick={() => remove.mutate(note.id)}
                    aria-label={tc("actions.delete")}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                ) : null}
              </div>
              <p className="mt-1.5 whitespace-pre-wrap text-body text-ink-2">
                {note.body}
              </p>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
