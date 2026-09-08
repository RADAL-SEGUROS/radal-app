import * as React from "react";
import { Toaster as Sonner } from "sonner";
import { useTheme } from "@/providers/ThemeProvider";

type ToasterProps = React.ComponentProps<typeof Sonner>;

function Toaster({ ...props }: ToasterProps) {
  const { theme } = useTheme();

  return (
    <Sonner
      theme={theme as ToasterProps["theme"]}
      className="toaster group"
      toastOptions={{
        classNames: {
          toast:
            "group toast group-[.toaster]:bg-bone group-[.toaster]:text-ink group-[.toaster]:border group-[.toaster]:border-line group-[.toaster]:shadow-overlay group-[.toaster]:rounded-card",
          description: "group-[.toast]:text-ink-3",
          // Action = brand (the accent), never ink.
          actionButton:
            "group-[.toast]:bg-brand group-[.toast]:text-cta-foreground group-[.toast]:hover:bg-brand-deep",
          cancelButton:
            "group-[.toast]:bg-paper-2 group-[.toast]:text-ink-2",
          success: "group-[.toaster]:[--success-text:var(--pos)]",
        },
      }}
      {...props}
    />
  );
}

export { Toaster };
