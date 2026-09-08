import { Construction } from "lucide-react";
import { useTranslation } from "react-i18next";
import { PageHeader } from "@/components/common/PageHeader";
import { Card } from "@/components/ui/card";

interface UnderConstructionProps {
  title: string;
  subtitle?: string;
  eyebrow?: string;
}

/**
 * Simple "en construcción" placeholder used by scaffolded list/detail pages.
 * Module agents replace the page bodies with real implementations.
 */
export function UnderConstruction({
  title,
  subtitle,
  eyebrow,
}: UnderConstructionProps) {
  const { t } = useTranslation("common");
  return (
    <div className="mx-auto max-w-6xl">
      <PageHeader title={title} subtitle={subtitle} eyebrow={eyebrow} />
      <Card className="flex flex-col items-center justify-center gap-3 py-16 text-center">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-brand-soft text-brand-deep">
          <Construction className="h-5 w-5" />
        </div>
        <p className="text-body text-ink-3">
          {t("state.underConstruction")}
        </p>
      </Card>
    </div>
  );
}
