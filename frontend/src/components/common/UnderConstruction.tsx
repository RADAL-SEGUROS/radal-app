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
      <Card className="flex flex-col items-center justify-center gap-3 p-16 text-center">
        <div className="rounded-full bg-bg-recessed p-3 text-teal">
          <Construction className="h-6 w-6" />
        </div>
        <p className="text-body text-text-muted">
          {t("state.underConstruction")}
        </p>
      </Card>
    </div>
  );
}
