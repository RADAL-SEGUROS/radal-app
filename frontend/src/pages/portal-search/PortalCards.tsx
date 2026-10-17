/**
 * One card per portal from `GET /portal-searches/portals`: built connectors
 * (available | maintenance) and the "pronto" ones (`coming_soon`). A portal
 * that cannot be used renders its control visibly disabled with the SERVER's
 * Spanish reason — never hidden, never a silent no-op.
 */
import { useTranslation } from "react-i18next";
import { CircleAlert, CircleCheck, Globe, KeyRound } from "lucide-react";
import type { Portal } from "@/api/rpa";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DisabledHint, SoonButton } from "@/components/common/kit";
import { cn } from "@/lib/utils";

export function portalUsable(portal: Portal): boolean {
  return portal.status === "available" && !portal.coming_soon;
}

/**
 * ONE state per card, consistent with its reason: a portal that is off in
 * this environment (`rpa_disabled`) or not set up (`not_configured`) is NOT
 * "En mantención" — only a portal whose layout changed is.
 */
const UNAVAILABLE_LABEL: Record<string, string> = {
  rpa_disabled: "rpa:portal.disabledHere",
  not_configured: "rpa:portal.notConfigured",
  layout_changed: "rpa:portal.maintenance",
  coming_soon: "rpa:portal.comingSoon",
};

export function PortalStatusBadge({ portal }: { portal: Portal }) {
  const { t } = useTranslation("rpa");
  if (portal.coming_soon) return <Badge variant="muted">{t("portal.comingSoon")}</Badge>;
  if (portal.status === "available") {
    return (
      <Badge variant="success" dot>
        {t("portal.available")}
      </Badge>
    );
  }
  const code = portal.maintenance_code ?? "";
  const key = Object.prototype.hasOwnProperty.call(UNAVAILABLE_LABEL, code)
    ? UNAVAILABLE_LABEL[code]
    : "rpa:portal.maintenance";
  return (
    <Badge variant={code === "layout_changed" || !code ? "warn" : "muted"} dot>
      {t(key)}
    </Badge>
  );
}

export function PortalCards({
  portals,
  selectedKey,
  onSelect,
  onConfigure,
}: {
  portals: Portal[];
  selectedKey: string | null;
  onSelect: (key: string) => void;
  /** Opens the credentials tab. */
  onConfigure: () => void;
}) {
  const { t } = useTranslation("rpa");

  return (
    <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
      {portals.map((portal) => {
        const usable = portalUsable(portal);
        const selected = selectedKey === portal.key && usable;
        const scopeHint =
          portal.maintenance_code === "layout_changed"
            ? portal.maintenance_scope === "broker"
              ? t("portal.scopeBroker")
              : t("portal.scopeGlobal")
            : null;
        return (
          <Card
            key={portal.key}
            className={cn(
              "flex flex-col gap-3 p-4",
              selected && "border-brand ring-2 ring-brand-ring",
              portal.coming_soon && "bg-paper-2/40",
            )}
          >
            <div className="flex items-start gap-2.5">
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-sm bg-brand-soft text-brand-deep">
                <Globe className="h-4 w-4" strokeWidth={1.5} aria-hidden />
              </span>
              <div className="min-w-0 flex-1">
                <p className="truncate text-label font-medium text-ink">{portal.name}</p>
                {portal.version && !portal.coming_soon ? (
                  <p className="text-[11px] tabular-nums text-ink-3">
                    {t("portal.version", { version: portal.version })}
                  </p>
                ) : null}
              </div>
              <PortalStatusBadge portal={portal} />
            </div>

            {!usable && portal.maintenance_reason ? (
              <p className="text-caption text-ink-2 [text-wrap:pretty]">
                {portal.maintenance_reason}
                {scopeHint ? <span className="text-ink-3"> {scopeHint}</span> : null}
              </p>
            ) : null}

            {portal.subtypes.length > 0 ? (
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-caption text-ink-3">{t("portal.quotes")}</span>
                {portal.subtypes.map((subtype) => (
                  <Badge key={subtype.code} variant="outline" title={subtype.code}>
                    {subtype.name ?? subtype.code}
                  </Badge>
                ))}
              </div>
            ) : null}

            {!portal.coming_soon ? (
              portal.requires_credentials ? (
                <div className="flex flex-wrap items-center gap-2 text-caption">
                  {portal.has_credentials ? (
                    <span className="inline-flex items-center gap-1 text-pos-text">
                      <CircleCheck className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
                      {t("portal.credentialsOk")}
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 text-warn-text">
                      <CircleAlert className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
                      {t("portal.credentialsMissing")}
                    </span>
                  )}
                  <Button
                    type="button"
                    size="sm"
                    variant="link"
                    className="h-auto min-h-8 px-0"
                    onClick={onConfigure}
                  >
                    <KeyRound className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
                    {t("portal.configure")}
                  </Button>
                </div>
              ) : (
                <p className="text-caption text-ink-3">{t("portal.credentialsNotNeeded")}</p>
              )
            ) : null}

            <div className="mt-auto pt-1">
              {portal.coming_soon ? (
                <SoonButton reason={portal.maintenance_reason ?? undefined} className="min-h-10 w-full sm:min-h-8">
                  {t("portal.choose")}
                </SoonButton>
              ) : usable ? (
                <Button
                  type="button"
                  size="sm"
                  variant={selected ? "accent-soft" : "secondary"}
                  aria-pressed={selected}
                  className="min-h-10 w-full sm:min-h-8"
                  onClick={() => onSelect(portal.key)}
                >
                  {selected ? t("portal.selected") : t("portal.choose")}
                </Button>
              ) : (
                <DisabledHint hint={portal.maintenance_reason} className="w-full">
                  <Button type="button" size="sm" variant="secondary" disabled className="min-h-10 w-full sm:min-h-8">
                    {t("portal.choose")}
                  </Button>
                </DisabledHint>
              )}
            </div>
          </Card>
        );
      })}
    </div>
  );
}
