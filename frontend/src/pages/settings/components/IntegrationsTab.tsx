import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowRight, Globe2, KeyRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DisabledHint } from "@/components/common/kit";
import { useCan } from "@/lib/permissions";
import { EmptyState, SectionCard } from "./shared";

/**
 * Configuración → Integraciones (v12). The portal-search credentials live on
 * `/portal-search?tab=credentials` (lane F6); this card only routes there, so
 * there is one place to manage them. Credentials need Settings.Manage; the
 * search itself needs Integrations.View.
 */
export function IntegrationsTab() {
  const { t } = useTranslation(["settings", "common"]);
  const canView = useCan("Integrations", "View");
  const canManage = useCan("Settings", "Manage");

  return (
    <div className="flex flex-col gap-5">
      <p className="max-w-3xl text-body text-ink-3">{t("settings:integrationsTab.description")}</p>
      <div className="grid gap-5 lg:grid-cols-2">
        <SectionCard
          title={t("settings:integrationsTab.portals.title")}
          icon={<Globe2 />}
          description={t("settings:integrationsTab.portals.description")}
        >
          {!canView.isLoading && !canView.allowed ? (
            <EmptyState>{t("settings:integrationsTab.portals.noAccess")}</EmptyState>
          ) : (
            <div className="flex flex-col gap-2 sm:flex-row sm:flex-wrap">
              {canManage.allowed ? (
                <Button asChild variant="secondary" className="min-h-10">
                  <Link to="/portal-search?tab=credentials">
                    <KeyRound />
                    {t("settings:integrationsTab.portals.credentials")}
                  </Link>
                </Button>
              ) : (
                <DisabledHint hint={t("settings:integrationsTab.portals.credentialsReason")}>
                  <Button variant="secondary" className="min-h-10" disabled>
                    <KeyRound />
                    {t("settings:integrationsTab.portals.credentials")}
                  </Button>
                </DisabledHint>
              )}
              <Button asChild variant="ghost" className="min-h-10">
                <Link to="/portal-search">
                  {t("settings:integrationsTab.portals.open")}
                  <ArrowRight />
                </Link>
              </Button>
            </div>
          )}
        </SectionCard>
      </div>
    </div>
  );
}
