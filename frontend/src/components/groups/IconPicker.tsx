/**
 * IconPicker — choose a group's avatar (v8): an emoji, a curated glyph, or an
 * uploaded image. It yields an `AccountGroupIconInput` the group create/update
 * payloads carry straight through.
 *
 * The image tab uploads through `POST /documents` FIRST (rule 8: the S3 key
 * lives only on the document row) and then hands back the `document_id`. There
 * is no `account_group` document entity, so the upload is filed against the
 * broker's own row as a `logo` — it goes through the 512×512 WEBP pipeline and
 * the group simply references the resulting document by id, which is all the
 * server needs (it re-checks the document is the broker's own).
 *
 * `value === null` means "leave the group's current icon untouched" — the edit
 * dialog relies on that, since an already-stored image icon comes back on read
 * without its `document_id` and so cannot be reconstructed as an input.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { ImagePlus, Loader2, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { DisabledHint, ErrorBanner } from "@/components/common/kit";
import { GroupAvatar } from "@/components/groups/GroupAvatar";
import {
  GROUP_ICON_CATEGORIES,
  GlyphIcon,
} from "@/components/groups/groupIcons";
import { useUploadDocument } from "@/api/documents";
import { useAuth } from "@/providers/AuthProvider";
import { cn } from "@/lib/utils";
import type { AccountGroupIcon, AccountGroupIconInput } from "@/api/types";

export function IconPicker({
  name,
  value,
  currentIcon,
  onChange,
  disabled,
}: {
  /** Group name — feeds the initials fallback preview. */
  name: string;
  value: AccountGroupIconInput | null;
  /** The already-stored icon (edit mode) shown while `value` is null. */
  currentIcon?: AccountGroupIcon | null;
  onChange: (next: AccountGroupIconInput | null) => void;
  disabled?: boolean;
}) {
  const { t } = useTranslation("accounts");
  const { user } = useAuth();
  const upload = useUploadDocument();
  const inputRef = React.useRef<HTMLInputElement | null>(null);
  const brokerId = user?.broker_id ?? null;
  const [query, setQuery] = React.useState("");
  const q = query.trim().toLowerCase();

  const categoryLabel = React.useCallback(
    (key: string) => t(`group.icon.categories.${key}`),
    [t],
  );

  // Filtered, section-preserving views per tab. A query matches the category
  // label for both tabs, and additionally the glyph name for the glyph tab.
  const emojiSections = React.useMemo(
    () =>
      GROUP_ICON_CATEGORIES.map((c) => ({
        key: c.key,
        items: !q || categoryLabel(c.key).toLowerCase().includes(q) ? c.emojis : [],
      })).filter((c) => c.items.length > 0),
    [q, categoryLabel],
  );

  const glyphSections = React.useMemo(
    () =>
      GROUP_ICON_CATEGORIES.map((c) => {
        const labelHit = !q || categoryLabel(c.key).toLowerCase().includes(q);
        return {
          key: c.key,
          items: c.glyphs.filter(
            (g) => labelHit || g.replace(/_/g, " ").includes(q),
          ),
        };
      }).filter((c) => c.items.length > 0),
    [q, categoryLabel],
  );

  // The preview: the pending selection, else the stored icon, else initials.
  const preview: AccountGroupIcon | null = value
    ? {
        kind: value.kind,
        value: value.value ?? null,
        // A freshly-uploaded image has no url yet; the preview shows the glyph
        // placeholder until the group reloads with its scoped url.
        url: null,
      }
    : (currentIcon ?? null);

  const activeTab = value?.kind ?? currentIcon?.kind ?? "emoji";

  const onPickFile = (event: React.ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file || brokerId == null) return;
    upload.mutate(
      { file, entity_type: "broker", entity_id: brokerId, category: "logo" },
      {
        onSuccess: (doc) => onChange({ kind: "image", document_id: doc.id }),
      },
    );
  };

  const uploadHint =
    brokerId == null ? t("group.icon.uploadNoBroker") : null;

  return (
    <div className="flex items-start gap-3">
      <GroupAvatar name={name} icon={preview} size="lg" />

      <div className="min-w-0 flex-1">
        <Tabs defaultValue={activeTab}>
          <TabsList variant="segmented">
            <TabsTrigger value="emoji">{t("group.icon.emoji")}</TabsTrigger>
            <TabsTrigger value="glyph">{t("group.icon.glyph")}</TabsTrigger>
            <TabsTrigger value="image">{t("group.icon.image")}</TabsTrigger>
          </TabsList>

          <TabsContent value="emoji" className="mt-2.5">
            <SearchBox value={query} onChange={setQuery} placeholder={t("group.icon.search")} />
            <div className="mt-2 max-h-56 space-y-2.5 overflow-y-auto pr-1">
              {emojiSections.length === 0 ? (
                <p className="py-4 text-center text-caption text-ink-3">{t("group.icon.noResults")}</p>
              ) : (
                emojiSections.map((section) => (
                  <div key={section.key}>
                    <p className="mb-1 text-caption font-medium text-ink-3">{categoryLabel(section.key)}</p>
                    <div className="grid grid-cols-10 gap-1">
                      {section.items.map((emoji) => {
                        const selected = value?.kind === "emoji" && value.value === emoji;
                        return (
                          <button
                            key={`${section.key}:${emoji}`}
                            type="button"
                            disabled={disabled}
                            aria-pressed={selected}
                            onClick={() => onChange({ kind: "emoji", value: emoji })}
                            className={cn(
                              "flex h-8 w-8 items-center justify-center rounded-md text-[17px] leading-none transition-colors duration-150 hover:bg-paper-2 disabled:opacity-60",
                              selected && "bg-brand-soft ring-1 ring-brand",
                            )}
                          >
                            {emoji}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ))
              )}
            </div>
          </TabsContent>

          <TabsContent value="glyph" className="mt-2.5">
            <SearchBox value={query} onChange={setQuery} placeholder={t("group.icon.search")} />
            <div className="mt-2 max-h-56 space-y-2.5 overflow-y-auto pr-1">
              {glyphSections.length === 0 ? (
                <p className="py-4 text-center text-caption text-ink-3">{t("group.icon.noResults")}</p>
              ) : (
                glyphSections.map((section) => (
                  <div key={section.key}>
                    <p className="mb-1 text-caption font-medium text-ink-3">{categoryLabel(section.key)}</p>
                    <div className="grid grid-cols-10 gap-1">
                      {section.items.map((glyph) => {
                        const selected = value?.kind === "glyph" && value.value === glyph;
                        return (
                          <button
                            key={`${section.key}:${glyph}`}
                            type="button"
                            disabled={disabled}
                            aria-pressed={selected}
                            aria-label={glyph.replace(/_/g, " ")}
                            onClick={() => onChange({ kind: "glyph", value: glyph })}
                            className={cn(
                              "flex h-8 w-8 items-center justify-center rounded-md text-ink-2 transition-colors duration-150 hover:bg-paper-2 disabled:opacity-60",
                              selected && "bg-brand-soft text-brand-deep ring-1 ring-brand",
                            )}
                          >
                            <GlyphIcon name={glyph} className="h-4 w-4" />
                          </button>
                        );
                      })}
                    </div>
                  </div>
                ))
              )}
            </div>
          </TabsContent>

          <TabsContent value="image" className="mt-2.5">
            <input
              ref={inputRef}
              type="file"
              accept="image/*"
              className="hidden"
              onChange={onPickFile}
              aria-hidden
            />
            <DisabledHint hint={uploadHint}>
              <Button
                type="button"
                size="sm"
                variant="secondary"
                disabled={disabled || !!uploadHint || upload.isPending}
                onClick={() => inputRef.current?.click()}
              >
                {upload.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <ImagePlus className="h-4 w-4" />
                )}
                {upload.isPending
                  ? t("group.icon.uploading")
                  : t("group.icon.upload")}
              </Button>
            </DisabledHint>
            <p className="mt-1.5 text-caption text-ink-3">{t("group.icon.imageHint")}</p>
            {upload.isError ? <ErrorBanner error={upload.error} className="mt-2" /> : null}
          </TabsContent>
        </Tabs>
      </div>
    </div>
  );
}

function SearchBox({
  value,
  onChange,
  placeholder,
}: {
  value: string;
  onChange: (next: string) => void;
  placeholder: string;
}) {
  return (
    <div className="relative">
      <Search
        className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-ink-3"
        aria-hidden
      />
      <input
        type="text"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
        className="h-8 w-full rounded-md border border-line bg-paper pl-7 pr-2 text-caption text-ink-1 outline-none transition-colors duration-150 placeholder:text-ink-3 focus:border-brand"
      />
    </div>
  );
}
