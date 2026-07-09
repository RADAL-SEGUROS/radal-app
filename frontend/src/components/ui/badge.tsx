import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center rounded-full border px-2.5 py-0.5 font-mono text-mono-sm font-medium transition-colors focus:outline-none",
  {
    variants: {
      variant: {
        // Neutral / gris
        neutral: "border-transparent bg-bg-recessed text-text-secondary",
        muted: "border-transparent bg-bg-recessed text-text-muted",
        // Brand / active (teal)
        brand: "border-transparent bg-teal-soft text-teal-deep",
        // Success / óptima (verde)
        success: "border-transparent bg-lime/20 text-ink dark:text-lime",
        // Warn / ámbar
        warn: "border-transparent bg-signal-warn/15 text-signal-warn",
        // Danger / rojo
        danger: "border-transparent bg-signal-danger/15 text-signal-danger",
        // Action / azul
        action: "border-transparent bg-blue/15 text-blue",
        outline: "border-line text-text-secondary",
      },
    },
    defaultVariants: {
      variant: "neutral",
    },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  );
}

export { Badge, badgeVariants };
