import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-label font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg-app)] disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        // Blue = primary ACTION (main CTA)
        primary:
          "bg-blue text-primary-foreground hover:bg-blue-deep shadow-sm",
        // Teal = brand-forward action (rare; nav/brand contexts)
        teal: "bg-teal text-white hover:bg-teal-deep",
        secondary:
          "border border-line bg-transparent text-text-primary hover:bg-bg-recessed",
        outline:
          "border border-line bg-transparent text-text-primary hover:bg-bg-recessed",
        ghost: "bg-transparent text-text-secondary hover:bg-bg-recessed",
        success: "bg-lime text-ink hover:brightness-95",
        destructive:
          "bg-destructive text-destructive-foreground hover:brightness-95",
        link: "text-blue underline-offset-4 hover:underline hover:text-blue-deep",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 rounded-sm px-3 text-caption",
        lg: "h-11 rounded-md px-6",
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
