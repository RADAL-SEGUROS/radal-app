/**
 * `DisabledHint`, but the reason also reaches a phone (lane F1).
 *
 * The shared `DisabledHint` is a Radix tooltip, and Radix tooltips never open
 * on touch — a broker on a phone saw a greyed button and no reason, which is a
 * dead button by another name. Here a TAP on the disabled control toggles the
 * same tooltip (and does not bubble to a tappable row); mouse and keyboard keep
 * hover/focus. Same pattern as `components/collections/LevelBadge.tsx`.
 *
 * A disabled `<button>` swallows pointer events, so the wrapper turns them off
 * on its disabled children and catches the tap itself.
 */
import * as React from "react";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export function TouchHint({
  hint,
  children,
  className,
}: {
  /** The reason the control is disabled; `null` renders the child as-is. */
  hint?: string | null;
  children: React.ReactNode;
  className?: string;
}) {
  const [open, setOpen] = React.useState(false);
  const touch = React.useRef<{ touch: boolean; wasOpen: boolean }>({ touch: false, wasOpen: false });

  if (!hint) {
    return className ? <span className={cn("inline-flex", className)}>{children}</span> : <>{children}</>;
  }
  return (
    <TooltipProvider delayDuration={150}>
      <Tooltip open={open} onOpenChange={setOpen}>
        <TooltipTrigger asChild>
          <span
            className={cn(
              "inline-flex [&_:disabled]:pointer-events-none",
              className,
            )}
            tabIndex={0}
            onPointerDown={(e) => {
              touch.current = { touch: e.pointerType !== "mouse", wasOpen: open };
            }}
            onClick={(e) => {
              e.stopPropagation();
              if (!touch.current.touch) return;
              // preventDefault skips Radix's own close-on-click; the tap toggles.
              e.preventDefault();
              setOpen(!touch.current.wasOpen);
            }}
          >
            {children}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-[260px] text-pretty text-center">
          {hint}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
