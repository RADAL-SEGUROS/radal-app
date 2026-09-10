import { useTranslation } from "react-i18next";
import { ShieldAlert } from "lucide-react";
import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { Card } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useCan } from "@/lib/permissions";
import { ProfileTab } from "./components/ProfileTab";
import { TeamTab } from "./components/TeamTab";
import { DocumentsTab } from "./components/DocumentsTab";

/**
 * Settings: broker profile + logo, team & roles, and the document library.
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

  return (
    <>
      <PageHeader title={t("title")} subtitle={t("subtitle")} />

      <FadeUp>
        <Tabs defaultValue="profile">
          <TabsList variant="underline">
            <TabsTrigger value="profile">{t("tabs.profile")}</TabsTrigger>
            <TabsTrigger value="team" disabled={!loading && !canViewUsers.allowed}>
              {t("tabs.team")}
            </TabsTrigger>
            <TabsTrigger value="documents" disabled={!loading && !canViewDocuments.allowed}>
              {t("tabs.documents")}
            </TabsTrigger>
          </TabsList>

          <TabsContent value="profile" className="mt-5">
            <ProfileTab />
          </TabsContent>
          <TabsContent value="team" className="mt-5">
            <TeamTab />
          </TabsContent>
          <TabsContent value="documents" className="mt-5">
            <DocumentsTab />
          </TabsContent>
        </Tabs>
      </FadeUp>
    </>
  );
}
