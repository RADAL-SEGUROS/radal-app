/**
 * GroupAvatar — the group's face across the app (list cards, the overview
 * header, the group rail and the account breadcrumbs).
 *
 * It renders whatever the group carries: an emoji character, a curated glyph,
 * or an uploaded image (`icon.url`). When the group has NO icon — the common
 * case — it falls back to the org initials in a tone hashed off the name
 * (José's "no-logo → styled name" note), NEVER a broken `<img>`. If an image
 * icon fails to load at runtime it degrades to the same initials chip.
 */
import * as React from "react";
import { GlyphIcon, hashedTone, initials } from "@/components/groups/groupIcons";
import { cn } from "@/lib/utils";
import type { AccountGroupIcon } from "@/api/types";

export type GroupAvatarSize = "sm" | "md" | "lg";

const SIZES: Record<GroupAvatarSize, { box: string; text: string; glyph: string; emoji: string }> = {
  sm: { box: "h-6 w-6 rounded-md", text: "text-[10px]", glyph: "h-3.5 w-3.5", emoji: "text-[13px]" },
  md: { box: "h-9 w-9 rounded-lg", text: "text-caption", glyph: "h-[18px] w-[18px]", emoji: "text-[18px]" },
  lg: { box: "h-12 w-12 rounded-card", text: "text-body font-semibold", glyph: "h-6 w-6", emoji: "text-2xl" },
};

export function GroupAvatar({
  name,
  icon,
  size = "md",
  className,
}: {
  name: string;
  icon: AccountGroupIcon | null | undefined;
  size?: GroupAvatarSize;
  className?: string;
}) {
  const [imgFailed, setImgFailed] = React.useState(false);
  const s = SIZES[size];
  const tone = hashedTone(name);

  const showImage = icon?.kind === "image" && icon.url && !imgFailed;
  const showEmoji = icon?.kind === "emoji" && !!icon.value;
  const showGlyph = icon?.kind === "glyph" && !!icon.value;

  const base = cn(
    "flex shrink-0 items-center justify-center overflow-hidden select-none",
    s.box,
    className,
  );

  if (showImage) {
    return (
      <img
        src={icon!.url as string}
        alt=""
        aria-hidden
        onError={() => setImgFailed(true)}
        className={cn(base, "border border-line object-cover")}
      />
    );
  }

  if (showEmoji) {
    return (
      <span className={cn(base, "bg-paper-2")} aria-hidden>
        <span className={cn("leading-none", s.emoji)}>{icon!.value}</span>
      </span>
    );
  }

  if (showGlyph) {
    return (
      <span className={base} style={{ background: tone.bg, color: tone.fg }} aria-hidden>
        <GlyphIcon name={icon!.value as string} className={s.glyph} />
      </span>
    );
  }

  // Fallback: initials in a hashed tone — never a broken image.
  return (
    <span
      className={cn(base, "font-semibold tracking-tight", s.text)}
      style={{ background: tone.bg, color: tone.fg }}
      aria-hidden
    >
      {initials(name)}
    </span>
  );
}
