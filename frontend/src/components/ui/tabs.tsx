import * as React from "react";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { motion, useReducedMotion } from "framer-motion";
import { cn } from "@/lib/utils";

/**
 * Signal tabs, two shapes:
 * - `variant="segmented"` (default) — the recessed-track view toggle.
 * - `variant="underline"` — page/section tabs: transparent list on a hairline
 *   baseline, active trigger inked with a 2px brand underline.
 * The variant is set on <TabsList> and flows to its triggers via context, so
 * existing call sites keep compiling unchanged.
 *
 * **Motion lives here on purpose.** Switching a tab used to be an instant,
 * unanimated swap — the app felt dead exactly where the broker clicks most. So
 * the active marker is a shared-layout element (`layoutId`) that SLIDES from
 * the old trigger to the new one, and the panel fades+rises in on activation.
 * Putting it in the primitive means every tab in the app — account journey,
 * group hub, Datos, Analítica — gets it at once and none of them can drift.
 *
 * The `layoutId` is namespaced per <TabsList> with `useId`, so two tab bars on
 * one page never animate into each other. Everything is skipped under
 * `prefers-reduced-motion`, where the marker falls back to a plain border.
 */
export type TabsVariant = "segmented" | "underline";

interface TabsListContextValue {
  variant: TabsVariant;
  /** Unique per <TabsList>; namespaces the sliding marker's `layoutId`. */
  markerId: string;
}

const TabsListContext = React.createContext<TabsListContextValue>({
  variant: "segmented",
  markerId: "tabs",
});

const Tabs = TabsPrimitive.Root;

interface TabsListProps
  extends React.ComponentPropsWithoutRef<typeof TabsPrimitive.List> {
  variant?: TabsVariant;
}

const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  TabsListProps
>(({ className, variant = "segmented", ...props }, ref) => {
  const markerId = React.useId();
  const context = React.useMemo(
    () => ({ variant, markerId }),
    [variant, markerId],
  );
  return (
    <TabsListContext.Provider value={context}>
      <TabsPrimitive.List
        ref={ref}
        className={cn(
          variant === "underline"
            ? "flex h-auto items-center gap-4 border-b border-line bg-transparent p-0"
            : "inline-flex h-9 items-center justify-center rounded-[var(--r-seg)] bg-paper-2 p-[2px] text-ink-3",
          className,
        )}
        {...props}
      />
    </TabsListContext.Provider>
  );
});
TabsList.displayName = TabsPrimitive.List.displayName;

/** The marker that slides between triggers. One per <TabsList>, via layoutId. */
function ActiveMarker({
  variant,
  markerId,
}: {
  variant: TabsVariant;
  markerId: string;
}) {
  return (
    <motion.span
      aria-hidden
      layoutId={`tabs-marker-${markerId}`}
      transition={{ type: "spring", stiffness: 420, damping: 36, mass: 0.7 }}
      className={cn(
        "absolute",
        variant === "underline"
          ? "inset-x-0 -bottom-px h-[2px] rounded-full bg-brand"
          : "inset-0 rounded-sm bg-bone shadow-elev",
      )}
    />
  );
}

const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, children, ...props }, ref) => {
  const { variant, markerId } = React.useContext(TabsListContext);
  const reduce = useReducedMotion();

  return (
    <TabsPrimitive.Trigger
      ref={ref}
      className={cn(
        "group relative inline-flex items-center justify-center whitespace-nowrap text-label font-medium transition-[color,background-color,border-color,box-shadow] duration-150 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50",
        variant === "underline"
          ? "-mb-px rounded-none border-b-2 border-transparent px-1 pb-2.5 pt-1 text-ink-3 hover:text-ink data-[state=active]:text-ink"
          : "h-full rounded-sm px-3 text-ink-3 ring-offset-background data-[state=active]:text-ink",
        // Under reduced motion the sliding marker is skipped, so fall back to
        // the original static treatment — the active tab must never be unmarked.
        reduce &&
          (variant === "underline"
            ? "data-[state=active]:border-brand"
            : "data-[state=active]:bg-bone data-[state=active]:shadow-elev"),
        className,
      )}
      {...props}
    >
      {reduce ? null : (
        <span className="pointer-events-none absolute inset-0 hidden group-data-[state=active]:block">
          <ActiveMarker variant={variant} markerId={markerId} />
        </span>
      )}
      {/* Above the segmented marker, which is an opaque filled pill. */}
      <span className="relative z-[1] inline-flex items-center gap-1.5">
        {children}
      </span>
    </TabsPrimitive.Trigger>
  );
});
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName;

const TabsContent = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, children, ...props }, ref) => {
  const reduce = useReducedMotion();
  const classes = cn(
    "mt-2 ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
    className,
  );

  if (reduce) {
    return (
      <TabsPrimitive.Content ref={ref} className={classes} {...props}>
        {children}
      </TabsPrimitive.Content>
    );
  }

  // Radix unmounts the inactive panel, so every activation is a fresh mount —
  // the entrance animation fires on each switch without an AnimatePresence.
  // `asChild` keeps the DOM to ONE element, so no call site's flex/grid layout
  // gains a surprise wrapper.
  return (
    <TabsPrimitive.Content ref={ref} className={classes} asChild {...props}>
      <motion.div
        initial={{ opacity: 0, y: 6 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.22, ease: [0.22, 0.72, 0.24, 1] }}
      >
        {children}
      </motion.div>
    </TabsPrimitive.Content>
  );
});
TabsContent.displayName = TabsPrimitive.Content.displayName;

export { Tabs, TabsList, TabsTrigger, TabsContent };
