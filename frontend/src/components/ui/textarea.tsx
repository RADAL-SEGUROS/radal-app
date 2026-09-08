import * as React from "react";
import { cn } from "@/lib/utils";

export type TextareaProps = React.TextareaHTMLAttributes<HTMLTextAreaElement>;

/**
 * Signal textarea: same doctrine as Input — hairline border on white, brand
 * border + soft ring on focus, 16px floor on mobile so iOS never zooms.
 */
const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, ...props }, ref) => {
    return (
      <textarea
        className={cn(
          "flex min-h-[72px] w-full rounded-sm border border-line bg-bone px-3 py-2 text-base text-ink transition-[border-color,box-shadow] duration-150 ease-out sm:text-body",
          "placeholder:text-muted-foreground",
          "hover:border-line-strong",
          "focus-visible:border-brand focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-ring",
          "disabled:cursor-not-allowed disabled:opacity-50",
          className,
        )}
        ref={ref}
        {...props}
      />
    );
  },
);
Textarea.displayName = "Textarea";

export { Textarea };
