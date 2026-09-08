import * as React from "react";
import * as TabsPrimitive from "@radix-ui/react-tabs";
import { cn } from "@/lib/utils";

/**
 * Signal tabs, two shapes:
 * - `variant="segmented"` (default) — the recessed-track view toggle.
 * - `variant="underline"` — page/section tabs: transparent list on a hairline
 *   baseline, active trigger inked with a 2px brand underline.
 * The variant is set on <TabsList> and flows to its triggers via context, so
 * existing call sites keep compiling unchanged.
 */
export type TabsVariant = "segmented" | "underline";

const TabsVariantContext = React.createContext<TabsVariant>("segmented");

const Tabs = TabsPrimitive.Root;

interface TabsListProps
  extends React.ComponentPropsWithoutRef<typeof TabsPrimitive.List> {
  variant?: TabsVariant;
}

const TabsList = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.List>,
  TabsListProps
>(({ className, variant = "segmented", ...props }, ref) => (
  <TabsVariantContext.Provider value={variant}>
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
  </TabsVariantContext.Provider>
));
TabsList.displayName = TabsPrimitive.List.displayName;

const TabsTrigger = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Trigger>
>(({ className, ...props }, ref) => {
  const variant = React.useContext(TabsVariantContext);
  return (
    <TabsPrimitive.Trigger
      ref={ref}
      className={cn(
        "inline-flex items-center justify-center whitespace-nowrap text-label font-medium transition-[color,background-color,border-color,box-shadow] duration-150 ease-out focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50",
        variant === "underline"
          ? "-mb-px rounded-none border-b-2 border-transparent px-1 pb-2.5 pt-1 text-ink-3 hover:text-ink data-[state=active]:border-brand data-[state=active]:text-ink"
          : "h-full rounded-sm px-3 text-ink-3 ring-offset-background data-[state=active]:bg-bone data-[state=active]:text-ink data-[state=active]:shadow-elev",
        className,
      )}
      {...props}
    />
  );
});
TabsTrigger.displayName = TabsPrimitive.Trigger.displayName;

const TabsContent = React.forwardRef<
  React.ElementRef<typeof TabsPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof TabsPrimitive.Content>
>(({ className, ...props }, ref) => (
  <TabsPrimitive.Content
    ref={ref}
    className={cn(
      "mt-2 ring-offset-background focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
      className,
    )}
    {...props}
  />
));
TabsContent.displayName = TabsPrimitive.Content.displayName;

export { Tabs, TabsList, TabsTrigger, TabsContent };
