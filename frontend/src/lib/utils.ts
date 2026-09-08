import { clsx, type ClassValue } from "clsx";
import { extendTailwindMerge } from "tailwind-merge";

/**
 * The Signal type scale lives in `tailwind.config.ts` under custom NAMES
 * (`text-body`, `text-label`, `text-caption`…). tailwind-merge cannot tell a
 * custom font size from a custom color — both look like `text-*` — so out of
 * the box it files `text-caption` in the text-COLOR group and drops whichever
 * of the two came first. That silently deleted `text-cta-foreground` from every
 * `size="sm"` button (a pine label on a pine fill — invisible) and deleted the
 * font size from every element that also set a colour. Teaching it the scale
 * fixes both directions at the root; anything added to `fontSize` in the
 * Tailwind config must be listed here too.
 */
const FONT_SIZES = [
  "display",
  "h1",
  "h2",
  "h3",
  "kpi",
  "body",
  "label",
  "caption",
  "mono",
  "mono-sm",
] as const;

const twMerge = extendTailwindMerge({
  extend: {
    classGroups: {
      "font-size": [{ text: [...FONT_SIZES] }],
    },
  },
});

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
