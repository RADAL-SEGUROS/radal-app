/**
 * The slash-command palette (agent spec §8.3).
 *
 * `/` at position 0 opens it. Each command is ONLY a prefill: it writes
 * template text into the composer and (where marked) demands a context chip —
 * the model does the tool work. No command calls an endpoint directly. A
 * command may name a v12 skill; the composer turns that skill on for the
 * thread (a visible chip the user can remove), never silently.
 * The footer teaches: writes always ask for confirmation (decision 6).
 */
import { useTranslation } from "react-i18next";
import { cn } from "@/lib/utils";
import { RefEntityType } from "@/components/agent/refs";

export interface SlashCommand {
  /** Stable id. */
  key: "comparar" | "crearGrupo" | "renovar" | "nota" | "cobranza" | "faltantes";
  /** The literal the user types (also what the palette filters on). */
  literal: string;
  /** Chip kinds the command demands; `null` = no chip needed; `[]` = any chip. */
  needsChip: RefEntityType[] | null;
  /** Explicit i18n keys (namespace `dashboard`) — never built from `key`. */
  hintKey: string;
  templateKey: string;
  /** Server skill key the command turns on, when the catalogue has it. */
  skill?: string;
}

export const SLASH_COMMANDS: SlashCommand[] = [
  {
    key: "comparar",
    literal: "/comparar",
    needsChip: ["case_file", "quote_request", "account_group"],
    hintKey: "agent.commands.comparar.hint",
    templateKey: "agent.commands.comparar.template",
  },
  {
    key: "crearGrupo",
    literal: "/crear-asegurado",
    needsChip: null,
    hintKey: "agent.commands.crearGrupo.hint",
    templateKey: "agent.commands.crearGrupo.template",
  },
  {
    key: "renovar",
    literal: "/renovar",
    needsChip: ["case_file"],
    hintKey: "agent.commands.renovar.hint",
    templateKey: "agent.commands.renovar.template",
  },
  {
    key: "nota",
    literal: "/nota",
    needsChip: [],
    hintKey: "agent.commands.nota.hint",
    templateKey: "agent.commands.nota.template",
  },
  {
    key: "cobranza",
    literal: "/cobranza",
    needsChip: null,
    hintKey: "agent.commands.cobranza.hint",
    templateKey: "agent.commands.cobranza.template",
    skill: "collections_review",
  },
  {
    key: "faltantes",
    literal: "/faltantes",
    needsChip: [],
    hintKey: "agent.commands.faltantes.hint",
    templateKey: "agent.commands.faltantes.template",
    skill: "file_check",
  },
];

export function filterCommands(query: string): SlashCommand[] {
  const lowered = query.toLowerCase();
  return SLASH_COMMANDS.filter((c) => c.literal.slice(1).toLowerCase().startsWith(lowered));
}

export function CommandPalette({
  commands,
  activeIndex,
  onSelect,
  listboxId,
}: {
  commands: SlashCommand[];
  activeIndex: number;
  onSelect: (command: SlashCommand) => void;
  listboxId: string;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <div
      id={listboxId}
      role="listbox"
      aria-label={t("agent.commands.title")}
      className="absolute inset-x-0 bottom-full z-30 mb-2 overflow-hidden rounded-lg bg-bg-surface p-1.5 shadow-overlay"
    >
      <p className="px-2.5 pb-1 pt-2 text-caption font-medium text-ink-3">
        {t("agent.commands.title")}
      </p>
      {commands.length === 0 ? (
        <p className="px-2.5 py-2 text-caption text-ink-3">{t("agent.commands.empty")}</p>
      ) : (
        commands.map((command, index) => (
          <button
            key={command.key}
            id={`agent-cmd-${command.key}`}
            type="button"
            role="option"
            aria-selected={index === activeIndex}
            onMouseDown={(e) => {
              e.preventDefault();
              onSelect(command);
            }}
            className={cn(
              "flex min-h-9 w-full items-baseline gap-2.5 rounded-sm px-2.5 py-1.5 text-left transition-[background-color,color] duration-150 ease-out",
              index === activeIndex
                ? "bg-brand-soft"
                : "hover:bg-[color-mix(in_srgb,var(--ink)_5%,transparent)]",
            )}
          >
            <span
              className={cn(
                "shrink-0 font-mono text-mono",
                index === activeIndex ? "text-brand-deep" : "text-ink",
              )}
            >
              {command.literal}
            </span>
            <span className="min-w-0 flex-1 truncate text-caption text-ink-3">
              {t(command.hintKey)}
            </span>
          </button>
        ))
      )}
      <p className="mt-1 border-t border-line px-2.5 pb-1 pt-1.5 text-caption text-ink-3 [text-wrap:pretty]">
        {t("agent.commands.footer")}
      </p>
    </div>
  );
}
