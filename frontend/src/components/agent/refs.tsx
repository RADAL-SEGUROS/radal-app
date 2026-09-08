/**
 * Shared vocabulary for the agent's context refs (@-mentions).
 *
 * A chip is a `ContextRef` plus the visible label the user picked it by.
 * Chips persist across sends within a conversation until removed (the working
 * context stays visible — decision 6); each send serialises the current chips
 * as `context_refs`.
 */
import {
  Building2,
  FileText,
  Folder,
  FolderTree,
  Send,
  ShieldCheck,
  Sparkles,
  Users,
} from "lucide-react";
import type { ContextRef } from "@/api/ai";

/** Ref entity types the agent endpoint resolves (backend spec §6.1). */
export type RefEntityType =
  | "account_group"
  | "client"
  | "case_file"
  | "policy"
  | "quote_request"
  | "proposal"
  | "document"
  | "sales_lead";

export interface AgentChip {
  /** Dedupe key: `${entity_type}:${entity_id}`. */
  key: string;
  ref: ContextRef;
  label: string;
  sublabel?: string;
}

export function chipKey(ref: ContextRef): string {
  return `${ref.entity_type}:${ref.entity_id}`;
}

export function makeChip(
  entityType: RefEntityType,
  entityId: number,
  label: string,
  sublabel?: string,
): AgentChip {
  const ref: ContextRef = { entity_type: entityType, entity_id: entityId };
  return { key: chipKey(ref), ref, label, sublabel };
}

/** 15px icons, strokeWidth 1.5 — beside regular-weight text (better-ui). */
export function RefIcon({ entityType, className }: { entityType: string; className?: string }) {
  const cls = className ?? "h-[15px] w-[15px] shrink-0";
  const props = { className: cls, strokeWidth: 1.5, "aria-hidden": true as const };
  switch (entityType) {
    case "account_group":
      return <FolderTree {...props} />;
    case "client":
      return <Users {...props} />;
    case "case_file":
      return <Folder {...props} />;
    case "policy":
      return <ShieldCheck {...props} />;
    case "quote_request":
      return <FileText {...props} />;
    case "proposal":
      return <Send {...props} />;
    case "document":
      return <FileText {...props} />;
    case "sales_lead":
      return <Sparkles {...props} />;
    default:
      return <Building2 {...props} />;
  }
}
