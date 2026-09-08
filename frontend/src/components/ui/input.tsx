import * as React from "react";
import { cn } from "@/lib/utils";

export type InputProps = React.InputHTMLAttributes<HTMLInputElement>;

/**
 * Signal input: a real hairline border on a white field (no inset-shadow
 * hack). Focus turns the border brand and adds a soft brand ring. Mobile
 * keeps a 16px floor (`text-base sm:text-body`) so iOS never zooms.
 */
const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({ className, type, ...props }, ref) => {
    return (
      <input
        type={type}
        className={cn(
          "flex h-9 w-full rounded-sm border border-line bg-bone px-3 py-1 text-base text-ink transition-[border-color,box-shadow] duration-150 ease-out sm:text-body",
          "placeholder:text-muted-foreground",
          "hover:border-line-strong",
          "focus-visible:border-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-ring",
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
