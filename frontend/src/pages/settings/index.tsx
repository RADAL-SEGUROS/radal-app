import * as React from "react";
import { useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ShieldAlert } from "lucide-react";
import { PageHeader } from "@/components/common/PageHeader";
import { BuildVersion } from "@/components/common/BuildVersion";
import { FadeUp } from "@/components/common/motion";
import { Card } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useCan } from "@/lib/permissions";
import { ProfileTab } from "./components/ProfileTab";
import { TeamTab } from "./components/TeamTab";
import { DocumentsTab } from "./components/DocumentsTab";
import { MailTab } from "./components/MailTab";
import { CollectionsSettingsTab } from "./components/CollectionsSettingsTab";
import { InsurersCollectionTab } from "./components/InsurersCollectionTab";
import { IntegrationsTab } from "./components/IntegrationsTab";

/** Tab ids, also accepted as `?tab=` / `?section=` (notification deep links). */
const TAB_IDS = [
  "profile",
  "team",
  "documents",
  "mail",
  "collections",
  "insurers",
  "integrations",
] as const;
type TabId = (typeof TAB_IDS)[number];

function asTab(value: string | null): TabId | null {
  return value && (TAB_IDS as readonly string[]).includes(value) ? (value as TabId) : null;
}

/**
 * Settings: broker profile + logo, team & roles, the document library and —
 * v12 — Correo, Cobranza, Compañías y cobro and Integraciones.
 *
 * Each tab is gated by the RBAC matrix rather than by a hardcoded role check —
 * `broker_admin` is the only broker role holding Users/Settings grants, but the
 * server stays the single source of truth. A tab the role cannot read is
 * rendered disabled, never hidden-then-broken.
 */
export default function SettingsPage() {
  const { t } = useTranslation("settings");
  const canViewSettings = useCan("Settings", "View");
  const canViewUsers = useCan("Users", "View");
  const canViewDocuments = useCan("Documents", "View");
  const canViewMail = useCan("Mail", "View");
  const canViewCollections = useCan("Collections", "View");
  const canViewIntegrations = useCan("Integrations", "View");
  const [params, setParams] = useSearchParams();
  const tab = asTab(params.get("tab")) ?? asTab(params.get("section")) ?? "profile";
  const onTab = (next: string) => {
    const nextParams = new URLSearchParams(params);
    nextParams.delete("section");
    if (next === "profile") nextParams.delete("tab");
    else nextParams.set("tab", next);
    setParams(nextParams, { replace: true });
  };

  // On a phone the active tab (e.g. a `?tab=collections` deep link) can sit
  // past the strip's edge: scroll the STRIP horizontally to centre it — never
  // `scrollIntoView`, which would also scroll the page.
  const stripRef = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    const strip = stripRef.current;
    if (!strip) return;
    const id = window.requestAnimationFrame(() => {
      const active = strip.querySelector<HTMLElement>('[role="tab"][data-state="active"]');
      if (!active || strip.scrollWidth <= strip.clientWidth) return;
      const stripRect = strip.getBoundingClientRect();
      const tabRect = active.getBoundingClientRect();
      const fullyVisible = tabRect.left >= stripRect.left && tabRect.right <= stripRect.right;
      if (fullyVisible) return;
      const target = strip.scrollLeft + (tabRect.left - stripRect.left) - (stripRect.width - tabRect.width) / 2;
      const reduce = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
      strip.scrollTo({ left: Math.max(0, target), behavior: reduce ? "auto" : "smooth" });
    });
    return () => window.cancelAnimationFrame(id);
  }, [tab]);

  const loading =
    canViewSettings.isLoading || canViewUsers.isLoading || canViewDocuments.isLoading;

  if (!loading && !canViewSettings.allowed && !canViewUsers.allowed && !canViewDocuments.allowed) {
    return (
      <>
        <PageHeader title={t("title")} subtitle={t("subtitle")} />
        <FadeUp>
          <Card className="flex flex-col items-center gap-3 p-14 text-center">
            <span className="flex h-11 w-11 items-center justify-center rounded-full bg-warn-soft text-warn-text">
              <ShieldAlert className="h-5 w-5" />
            </span>
            <h2 className="font-display text-h2 text-text-primary">
              {t("noPermission.title")}
            </h2>
            <p className="max-w-md text-body text-text-muted">
              {t("noPermission.description")}
            </p>
          </Card>
        </FadeUp>
      </>
    );
  }

  const lockedTabs = [
    !loading && !canViewUsers.allowed ? t("tabs.team") : null,
    !loading && !canViewDocuments.allowed ? t("tabs.documents") : null,
    !canViewMail.isLoading && !canViewMail.allowed ? t("tabs.mail") : null,
    !canViewCollections.isLoading && !canViewCollections.allowed ? t("tabs.collections") : null,
    !canViewCollections.isLoading && !canViewCollections.allowed ? t("tabs.insurers") : null,
    !canViewIntegrations.isLoading && !canViewIntegrations.allowed ? t("tabs.integrations") : null,
  ].filter((label): label is string => label !== null);

  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />

      <FadeUp>
        <Tabs value={tab} onValueChange={onTab}>
          {/* Seven tabs overflow a 375 px phone: the bar scrolls sideways
              inside its own strip instead of pushing the page. */}
          <div ref={stripRef} className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
            <TabsList variant="underline" className="w-max min-w-full">
              <TabsTrigger value="profile">{t("tabs.profile")}</TabsTrigger>
              <TabsTrigger
                value="team"
                disabled={!loading && !canViewUsers.allowed}
                title={!loading && !canViewUsers.allowed ? t("tabLockedHint") : undefined}
              >
                {t("tabs.team")}
              </TabsTrigger>
              <TabsTrigger value="documents" disabled={!loading && !canViewDocuments.allowed}>
                {t("tabs.documents")}
              </TabsTrigger>
              <TabsTrigger value="mail" disabled={!canViewMail.isLoading && !canViewMail.allowed}>
                {t("tabs.mail")}
              </TabsTrigger>
              <TabsTrigger
                value="collections"
                disabled={!canViewCollections.isLoading && !canViewCollections.allowed}
              >
                {t("tabs.collections")}
              </TabsTrigger>
              <TabsTrigger
                value="insurers"
                disabled={!canViewCollections.isLoading && !canViewCollections.allowed}
              >
                {t("tabs.insurers")}
              </TabsTrigger>
              <TabsTrigger
                value="integrations"
                disabled={!canViewIntegrations.isLoading && !canViewIntegrations.allowed}
              >
                {t("tabs.integrations")}
              </TabsTrigger>
            </TabsList>
          </div>
          {/* No dead buttons: a disabled tab says why, visibly (touch has no tooltip). */}
          {lockedTabs.length > 0 ? (
            <p className="mt-2 text-caption text-ink-3">
              {t("tabsLocked", { tabs: lockedTabs.join(" · ") })}
            </p>
          ) : null}

          <TabsContent value="profile" className="mt-5">
            <ProfileTab />
          </TabsContent>
          <TabsContent value="team" className="mt-5">
            <TeamTab />
          </TabsContent>
          <TabsContent value="documents" className="mt-5">
            <DocumentsTab />
          </TabsContent>
          <TabsContent value="mail" className="mt-5">
            <MailTab />
          </TabsContent>
          <TabsContent value="collections" className="mt-5">
            <CollectionsSettingsTab />
          </TabsContent>
          <TabsContent value="insurers" className="mt-5">
            <InsurersCollectionTab />
          </TabsContent>
          <TabsContent value="integrations" className="mt-5">
            <IntegrationsTab />
          </TabsContent>
        </Tabs>

        {/* The build stamp the bug-report form asks for. Quiet, but one click
            copies it — see components/common/BuildVersion.tsx. */}
        <div className="mt-8 flex justify-end border-t border-line pt-3">
          <BuildVersion />
        </div>
      </FadeUp>
    </>
  );
}
