import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

/**
 * Signal buttons: ONE accent primary per view (pine fill, white text — never
 * ink). Everything else is neutral: bordered secondary, real outline, ghost.
 * Destructive is quiet — red appears only on the destructive control itself.
 * Hover is a fill/border change, press is scale(0.98); 150ms named-property
 * transitions, nothing bouncy.
 */
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-sm text-label font-medium transition-[background-color,color,border-color,box-shadow,transform,scale] duration-150 ease-out active:scale-[0.98] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg-app)] disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        // THE accent button — one per view.
        primary: "bg-cta text-cta-foreground shadow-elev hover:bg-cta-hover",
        // Bordered neutral — the workhorse secondary.
        secondary:
          "border border-line bg-bone text-ink-2 hover:border-line-strong hover:bg-paper-2 hover:text-ink",
        // A REAL outline: transparent fill, hairline border.
        outline:
          "border border-line bg-transparent text-ink-2 hover:border-line-strong hover:bg-paper-2 hover:text-ink",
        ghost: "bg-transparent text-ink-3 hover:bg-paper-2 hover:text-ink",
        // Brand-soft tint — rare accent-adjacent actions.
        "accent-soft":
          "border border-transparent bg-brand-soft text-brand-deep hover:border-brand-line",
        // Legacy name → alias of accent-soft.
        teal: "border border-transparent bg-brand-soft text-brand-deep hover:border-brand-line",
        success:
          "border border-transparent bg-pos-soft text-pos-text hover:border-pos-line",
        // Quiet destructive: soft tint, red only here.
        destructive:
          "border border-transparent bg-neg-soft text-neg-text hover:border-neg-line",
        link: "text-brand underline-offset-4 hover:text-brand-deep hover:underline",
      },
      size: {
        default: "h-9 px-4",
        sm: "h-8 px-3 text-caption",
        lg: "h-10 px-5",
        icon: "h-9 w-9",
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
