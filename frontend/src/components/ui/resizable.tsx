import * as React from "react";
import { GripVertical } from "lucide-react";
import * as ResizablePrimitive from "react-resizable-panels";
import { cn } from "@/lib/utils";

/**
 * Signal resizable panels — shadcn-shaped wrappers over react-resizable-panels
 * v4 (`Group` / `Panel` / `Separator`). The handle is a hairline that turns
 * brand while dragging; `withHandle` adds a small grip pill.
 *
 * Note: v4 renamed shadcn's `direction` prop to `orientation`; we accept
 * `direction` as an alias so shadcn-style call sites read naturally.
 */
type GroupPrimitiveProps = React.ComponentProps<typeof ResizablePrimitive.Group>;

interface ResizablePanelGroupProps extends GroupPrimitiveProps {
  /** Alias of `orientation` (shadcn naming). */
  direction?: "horizontal" | "vertical";
}

const ResizablePanelGroup = ({
  className,
  direction,
  orientation,
  ...props
}: ResizablePanelGroupProps) => (
  <ResizablePrimitive.Group
    orientation={orientation ?? direction ?? "horizontal"}
    className={cn(
      // display/flex-direction are set by the primitive and cannot be overridden.
      "h-full w-full",
      className,
    )}
    {...props}
  />
);

const ResizablePanel = ResizablePrimitive.Panel;

interface ResizableHandleProps
  extends React.ComponentProps<typeof ResizablePrimitive.Separator> {
  /** Renders a small grip pill on the hairline. */
  withHandle?: boolean;
}

const ResizableHandle = ({
  withHandle,
  className,
  ...props
}: ResizableHandleProps) => (
  <ResizablePrimitive.Separator
    className={cn(
      // The hairline itself; a widened hit area lives in the ::after layer.
      "group relative flex items-center justify-center bg-line transition-[background-color] duration-150 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 data-[separator=active]:bg-brand data-[separator=hover]:bg-line-strong",
      // Vertical divider (horizontal group):
      "aria-[orientation=vertical]:w-px aria-[orientation=vertical]:cursor-col-resize aria-[orientation=vertical]:after:absolute aria-[orientation=vertical]:after:inset-y-0 aria-[orientation=vertical]:after:-left-1 aria-[orientation=vertical]:after:w-2",
      // Horizontal divider (vertical group):
      "aria-[orientation=horizontal]:h-px aria-[orientation=horizontal]:cursor-row-resize aria-[orientation=horizontal]:after:absolute aria-[orientation=horizontal]:after:inset-x-0 aria-[orientation=horizontal]:after:-top-1 aria-[orientation=horizontal]:after:h-2",
      className,
    )}
    {...props}
  >
    {withHandle && (
      <div className="z-10 flex h-4 w-3 items-center justify-center rounded-[4px] border border-line bg-bone shadow-elev">
        <GripVertical className="h-2.5 w-2.5 text-ink-3" />
      </div>
    )}
  </ResizablePrimitive.Separator>
);

export { ResizablePanelGroup, ResizablePanel, ResizableHandle };
