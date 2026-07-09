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
            "group toast group-[.toaster]:bg-bg-surface group-[.toaster]:text-text-primary group-[.toaster]:border-line group-[.toaster]:shadow-card group-[.toaster]:rounded-card",
          description: "group-[.toast]:text-text-muted",
          actionButton:
            "group-[.toast]:bg-blue group-[.toast]:text-primary-foreground",
          cancelButton:
            "group-[.toast]:bg-bg-recessed group-[.toast]:text-text-secondary",
          success: "group-[.toaster]:[--success-text:var(--lime)]",
        },
      }}
      {...props}
    />
  );
}

export { Toaster };
