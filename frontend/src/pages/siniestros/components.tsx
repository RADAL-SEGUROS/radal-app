import * as React from "react";
import { cn } from "@/lib/utils";

/** Small role-colored dot for activity rows (design-system §6.4). */
export function RoleDot({ rol }: { rol?: string | null }) {
  const color = rol?.includes("aseguradora")
    ? "bg-blue"
    : rol?.includes("asegurado")
      ? "bg-lime"
      : "bg-teal";
  return (
    <span
      className={cn("mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full", color)}
      aria-hidden
    />
  );
}

/** Labeled info line used inside detail cards. */
export function InfoRow({
  label,
  children,
  mono,
}: {
  label: React.ReactNode;
  children: React.ReactNode;
  mono?: boolean;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-caption text-text-muted">{label}</span>
      <span
        className={cn(
          "text-body text-text-primary",
          mono && "font-mono text-mono",
        )}
      >
        {children}
      </span>
    </div>
  );
}

/** Empty-state text used across accordion sections. */
export function EmptyLine({ children }: { children: React.ReactNode }) {
  return <p className="py-2 text-body text-text-muted">{children}</p>;
}

/** Accordion header with an optional count chip. */
export function SectionHeader({
  title,
  count,
}: {
  title: string;
  count?: number;
}) {
  return (
    <span className="flex items-center gap-2">
      {title}
      {typeof count === "number" ? (
        <span className="font-mono text-mono-sm text-text-muted">
          ({count})
        </span>
      ) : null}
    </span>
  );
}
