/**
 * `/groups/new` — the full-page version of "create a group".
 *
 * Same fields, same mutation and same 422 translation as the dialog on the list
 * page; this exists because "+ Nuevo grupo" in the main rail is a link, and a
 * link must land somewhere real (rule 3: no dead affordances).
 */
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowLeft } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { DisabledHint, ErrorBanner } from "@/components/common/kit";
import { GroupFormFields, useGroupCreate } from "@/pages/groups/GroupForm";
import { GroupCrumbs } from "@/pages/groups/shared";
import { useCan } from "@/lib/permissions";

export default function NewGroupPage() {
  const { t } = useTranslation("accounts");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const canCreate = useCan("Groups", "Create");
  const form = useGroupCreate((group) => navigate(`/groups/${group.id}`));

  const blocked = !canCreate.allowed && !canCreate.isLoading;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={
          <GroupCrumbs
            items={[{ label: t("group.title"), to: "/groups" }, { label: t("group.create.title") }]}
          />
        }
        title={t("group.create.title")}
        subtitle={t("group.subtitle")}
        actions={
          <Button variant="secondary" size="sm" onClick={() => navigate("/groups")}>
            <ArrowLeft className="h-4 w-4" />
            {tc("actions.back")}
          </Button>
        }
      />

      {form.error ? <ErrorBanner error={form.error} /> : null}

      <FadeUp>
        <Card className="flex max-w-2xl flex-col gap-5 p-5">
          <GroupFormFields
            value={form.state}
            onChange={form.setState}
            disabled={form.isPending || blocked}
          />

          <div className="flex items-center justify-end gap-2 border-t border-line pt-4">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => navigate("/groups")}
              disabled={form.isPending}
            >
              {tc("actions.cancel")}
            </Button>
            <DisabledHint hint={blocked ? t("group.noCreatePermission") : null}>
              <Button
                size="sm"
                onClick={form.submit}
                disabled={!form.canSubmit || form.isPending || blocked}
              >
                {t("group.create.submit")}
              </Button>
            </DisabledHint>
          </div>
        </Card>
      </FadeUp>
    </div>
  );
}
