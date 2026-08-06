import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center justify-center whitespace-nowrap rounded-full border px-2.5 py-0.5 text-caption font-semibold leading-none transition-colors focus:outline-none",
  {
    variants: {
      variant: {
        // Neutral / gris — muted fill, tertiary text
        neutral:
          "border-[color-mix(in_srgb,var(--muted)_30%,transparent)] bg-[color-mix(in_srgb,var(--muted)_15%,transparent)] text-text-tertiary",
        muted:
          "border-[color-mix(in_srgb,var(--muted)_30%,transparent)] bg-[color-mix(in_srgb,var(--muted)_15%,transparent)] text-text-muted",
        // Brand / active (teal)
        brand:
          "border-[color-mix(in_srgb,var(--teal)_30%,transparent)] bg-[color-mix(in_srgb,var(--teal)_15%,transparent)] text-teal-deep",
        // Success / óptima (verde/lime)
        success:
          "border-[color-mix(in_srgb,var(--lime)_30%,transparent)] bg-[color-mix(in_srgb,var(--lime)_15%,transparent)] text-lime-deep",
        // Warn / ámbar
        warn: "border-[color-mix(in_srgb,var(--amber)_30%,transparent)] bg-[color-mix(in_srgb,var(--amber)_15%,transparent)] text-amber-deep",
        // Danger / rojo
        danger:
          "border-[color-mix(in_srgb,var(--red)_30%,transparent)] bg-[color-mix(in_srgb,var(--red)_15%,transparent)] text-red-deep",
        // Action / azul
        action:
          "border-[color-mix(in_srgb,var(--blue)_30%,transparent)] bg-[color-mix(in_srgb,var(--blue)_15%,transparent)] text-blue-deep",
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
