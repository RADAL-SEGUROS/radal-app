import * as React from "react";
import { Link } from "react-router-dom";
import { ChevronRight } from "lucide-react";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface SectionCardProps {
  title: React.ReactNode;
  icon?: React.ReactNode;
  /** Optional "Ver todos" link rendered in the header. */
  linkTo?: string;
  linkLabel?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  bodyClassName?: string;
}

export function SectionCard({
  title,
  icon,
  linkTo,
  linkLabel,
  children,
  className,
  bodyClassName,
}: SectionCardProps) {
  return (
    <Card className={cn("flex min-h-0 flex-col overflow-hidden", className)}>
      <div className="flex shrink-0 items-center justify-between border-b border-line px-4 py-3">
        <div className="flex items-center gap-2">
          {icon ? <span className="text-teal">{icon}</span> : null}
          <h2 className="font-display text-h3 text-text-primary">{title}</h2>
        </div>
        {linkTo && linkLabel ? (
          <Link
            to={linkTo}
            className="inline-flex items-center gap-0.5 text-label text-blue hover:text-blue-deep"
          >
            {linkLabel}
            <ChevronRight className="h-4 w-4" />
          </Link>
        ) : null}
      </div>
      <div className={cn("min-h-0 flex-1 overflow-y-auto", bodyClassName)}>
        {children}
      </div>
    </Card>
  );
}

export function EmptyState({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center px-4 py-6 text-center text-caption text-text-muted">
      {children}
    </div>
  );
}
