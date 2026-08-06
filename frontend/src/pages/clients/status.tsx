import { useTranslation } from "react-i18next";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import type { AssetStatus, ClientStatus } from "@/api/types";

type BadgeVariant = NonNullable<BadgeProps["variant"]>;

const CLIENT_VARIANT: Record<ClientStatus, BadgeVariant> = {
  prospect: "brand",
  onboarding: "action",
  active: "success",
  archived: "muted",
};

const ASSET_VARIANT: Record<AssetStatus, BadgeVariant> = {
  active: "success",
  inactive: "warn",
  archived: "muted",
};

export function ClientStatusBadge({ status }: { status: ClientStatus }) {
  const { t } = useTranslation("clients");
  return <Badge variant={CLIENT_VARIANT[status]}>{t(`status.${status}`)}</Badge>;
}

export function AssetStatusBadge({ status }: { status: AssetStatus }) {
  const { t } = useTranslation("clients");
  return <Badge variant={ASSET_VARIANT[status]}>{t(`assets.status.${status}`)}</Badge>;
}
