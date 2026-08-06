import * as React from "react";
import { cn } from "@/lib/utils";

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          "flex h-10 w-full rounded-[10px] border border-line bg-bg-surface px-3.5 py-1 text-body text-text-primary transition-[border-color,box-shadow] duration-150",
          "placeholder:text-text-muted",
          "focus-visible:border-teal focus-visible:outline-none focus-visible:shadow-[0_0_0_3px_color-mix(in_srgb,var(--teal)_18%,transparent)]",
          "disabled:cursor-not-allowed disabled:opacity-50",
          "file:border-0 file:bg-transparent file:text-body file:font-medium",
          className,
        )}
        ref={ref}
        {...props}
      />
    );
  },
);
Input.displayName = "Input";

export { Input };
