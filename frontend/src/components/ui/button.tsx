import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[11px] text-label font-semibold transition-all duration-150 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg-app)] disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-[17px] [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        // Blue = primary ACTION (main CTA). Solid, soft shadow, lifts on hover.
        primary:
          "border border-blue bg-blue text-primary-foreground shadow-[0_8px_18px_-9px_color-mix(in_srgb,var(--blue)_75%,transparent)] hover:-translate-y-px hover:border-blue-deep hover:bg-blue-deep hover:shadow-[0_12px_24px_-10px_color-mix(in_srgb,var(--blue)_80%,transparent)]",
        // Teal = brand-forward action (rare; nav/brand contexts)
        teal: "bg-teal text-white shadow-[0_8px_18px_-9px_color-mix(in_srgb,var(--teal)_75%,transparent)] hover:-translate-y-px hover:bg-teal-deep",
        // Secondary = bone surface, teal-tinted hover + lift.
        secondary:
          "border border-line bg-bg-surface text-text-secondary hover:-translate-y-px hover:border-[color-mix(in_srgb,var(--teal)_45%,var(--line))] hover:bg-[color-mix(in_srgb,var(--teal)_6%,transparent)] hover:text-teal-deep",
        outline:
          "border border-line bg-bg-surface text-text-secondary hover:border-[color-mix(in_srgb,var(--teal)_45%,var(--line))] hover:bg-[color-mix(in_srgb,var(--teal)_6%,transparent)] hover:text-teal-deep",
        ghost:
          "bg-transparent text-text-tertiary hover:bg-[color-mix(in_srgb,var(--ink)_5%,transparent)] hover:text-teal-deep",
        success: "bg-lime text-ink hover:brightness-95",
        destructive:
          "bg-destructive text-destructive-foreground shadow-[0_8px_18px_-9px_color-mix(in_srgb,var(--red)_75%,transparent)] hover:-translate-y-px hover:brightness-95",
        link: "text-blue underline-offset-4 hover:underline hover:text-blue-deep",
      },
      size: {
        default: "h-10 px-4",
        sm: "h-9 rounded-[10px] px-3 text-caption",
        lg: "h-11 px-6",
        icon: "h-10 w-10 rounded-[10px]",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
