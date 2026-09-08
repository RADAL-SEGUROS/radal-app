import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/**
 * Signal badges: normal-case 12px Inter on a soft tint — mono and uppercase
 * are retired. Optional `dot` renders a 6px status dot in the variant's
 * strong color for at-a-glance status meaning.
 */
const badgeVariants = cva(
  "inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-0.5 text-[12px] font-medium leading-5",
  {
    variants: {
      variant: {
        neutral: "bg-paper-2 text-ink-2",
        muted: "bg-paper-2 text-ink-3",
        brand: "bg-brand-soft text-brand-deep",
        // Action = brand (blue is retired; one hue, one meaning).
        action: "bg-brand-soft text-brand-deep",
        success: "bg-pos-soft text-pos-text",
        warn: "bg-warn-soft text-warn-text",
        danger: "bg-neg-soft text-neg-text",
        outline: "border border-line bg-transparent text-ink-3",
      },
    },
    defaultVariants: {
      variant: "neutral",
    },
  },
);

const dotColor: Record<
  NonNullable<VariantProps<typeof badgeVariants>["variant"]>,
  string
> = {
  neutral: "bg-ink-3",
  muted: "bg-muted-foreground",
  brand: "bg-brand",
  action: "bg-brand",
  success: "bg-pos",
  warn: "bg-warn",
  danger: "bg-neg",
  outline: "bg-ink-3",
};

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {
  /** Renders a 6px status dot in the variant's strong color. */
  dot?: boolean;
}

function Badge({ className, variant, dot, children, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props}>
      {dot && (
        <span
          aria-hidden
          className={cn(
            "h-1.5 w-1.5 shrink-0 rounded-full",
            dotColor[variant ?? "neutral"],
          )}
        />
      )}
      {children}
    </div>
  );
}

export { Badge, badgeVariants };
