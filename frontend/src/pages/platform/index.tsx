/**
 * Mantenedor — the Radal maintainer console home (`/platform`).
 *
 * Platform users only: the route is wrapped in `RequirePermission` with
 * `Platform.View`, which no broker role holds. This home is a plain index of
 * the four console sections (the insurer × channel matrix lives inside
 * Compañías y cobro, `?tab=channels`, because App.tsx has no route for it).
 */
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowRight, Building2, KeyRound, ListChecks, Webhook, type LucideIcon } from "lucide-react";
import { PageHeader } from "@/components/common/PageHeader";
import { Stagger, fadeUpItem } from "@/components/common/motion";
import { Card } from "@/components/ui/card";
import { motion } from "framer-motion";

interface Section {
  to: string;
  icon: LucideIcon;
  label: string;
  description: string;
}

export default function PlatformHomePage() {
  const { t } = useTranslation("platform");
  // Explicit keys per section — never `t(\`${key}.title\`)` (dynamic-key trap).
  const sections: Section[] = [
    {
      to: "/platform/insurers",
      icon: Building2,
      label: t("nav.insurers"),
      description: t("insurers.description"),
    },
    {
      to: "/platform/provider-accounts",
      icon: KeyRound,
      label: t("nav.providerAccounts"),
      description: t("providerAccounts.description"),
    },
    {
      to: "/platform/events",
      icon: Webhook,
      label: t("nav.events"),
      description: t("events.description"),
    },
    {
      to: "/platform/jobs",
      icon: ListChecks,
      label: t("nav.jobs"),
      description: t("jobs.description"),
    },
  ];

  return (
    <>
      <PageHeader title={t("title")} subtitle={t("description")} />
      <Stagger className="grid gap-4 sm:grid-cols-2">
        {sections.map((section) => {
          const Icon = section.icon;
          return (
            <motion.div key={section.to} variants={fadeUpItem}>
              <Link
                to={section.to}
                className="group block rounded-lg outline-none focus-visible:ring-2 focus-visible:ring-brand-ring"
              >
                <Card className="flex h-full items-start gap-3 p-5 transition-[box-shadow] duration-150 ease-out group-hover:shadow-elev-hover">
                  <span className="grid h-9 w-9 shrink-0 place-items-center rounded-sm bg-brand-soft text-brand-deep">
                    <Icon className="h-[18px] w-[18px]" strokeWidth={1.5} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block text-body font-medium text-ink">{section.label}</span>
                    <span className="mt-1 block text-caption text-ink-3">{section.description}</span>
                  </span>
                  <ArrowRight className="mt-1 h-4 w-4 shrink-0 text-ink-3" strokeWidth={1.5} />
                </Card>
              </Link>
            </motion.div>
          );
        })}
      </Stagger>
    </>
  );
}
