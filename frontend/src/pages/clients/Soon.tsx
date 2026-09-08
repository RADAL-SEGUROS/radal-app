import * as React from "react";
import { Button, type ButtonProps } from "@/components/ui/button";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

/**
 * The "no dead buttons" primitive.
 *
 * A control the current user cannot use — because the RBAC matrix says no, or
 * because the endpoint behind it does not exist yet — is rendered VISIBLY
 * DISABLED with a tooltip that says why. It is never a button that silently
 * does nothing, and never a hidden affordance the user has to guess at.
 *
 * The wrapping <span> is required: a disabled <button> emits no pointer events,
 * so Radix would never see the hover that opens the tooltip.
 */
interface SoonButtonProps extends Omit<ButtonProps, "disabled" | "onClick"> {
  label: React.ReactNode;
  /** Why it is disabled — shown in the tooltip. */
  reason: string;
  icon?: React.ReactNode;
}

export function SoonButton({
  label,
  reason,
  icon,
  variant = "secondary",
  size,
  className,
  ...props
}: SoonButtonProps) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span className="inline-flex">
          <Button
            type="button"
            variant={variant}
            size={size}
            className={className}
            disabled
            {...props}
          >
            {icon}
            {label}
          </Button>
        </span>
      </TooltipTrigger>
      <TooltipContent>{reason}</TooltipContent>
    </Tooltip>
  );
}

/** Inline "pronto" chip for a whole panel that has no endpoint behind it yet. */
export function SoonNote({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed border-line bg-bg-recessed px-3.5 py-2.5 text-caption text-text-muted">
      {children}
    </p>
  );
}
