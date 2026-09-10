/**
 * Download / preview a stored document — authenticated.
 *
 * The bytes live behind `GET /documents/{id}/content`, which requires the auth
 * header, so a plain `<a href>` 401s. These buttons fetch the bytes through the
 * shared axios instance (see `lib/download.ts`) and hand the browser a Blob.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Download, Eye, Loader2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { downloadDocument, previewDocument } from "@/lib/download";
import { apiError } from "@/components/common/kit";
import { cn } from "@/lib/utils";

type ButtonVariant = React.ComponentProps<typeof Button>["variant"];
type ButtonSize = React.ComponentProps<typeof Button>["size"];

export function DownloadDocButton({
  documentId,
  filename,
  label,
  variant = "ghost",
  size = "sm",
  className,
}: {
  documentId: number;
  filename?: string | null;
  label?: string;
  variant?: ButtonVariant;
  size?: ButtonSize;
  className?: string;
}) {
  const { t } = useTranslation("common");
  const [busy, setBusy] = React.useState(false);

  const onClick = async () => {
    setBusy(true);
    try {
      await downloadDocument(documentId, filename);
    } catch (error) {
      toast.error(apiError(error, t("actions.download")));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Button variant={variant} size={size} disabled={busy} onClick={onClick} className={className}>
      {busy ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
      ) : (
        <Download className="h-3.5 w-3.5" aria-hidden />
      )}
      {label ?? t("actions.download")}
    </Button>
  );
}

export function PreviewDocButton({
  documentId,
  label,
  variant = "ghost",
  size = "sm",
  className,
}: {
  documentId: number;
  label?: string;
  variant?: ButtonVariant;
  size?: ButtonSize;
  className?: string;
}) {
  const { t } = useTranslation("common");
  const [busy, setBusy] = React.useState(false);

  const onClick = async () => {
    setBusy(true);
    try {
      await previewDocument(documentId);
    } catch (error) {
      toast.error(apiError(error, t("actions.view")));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Button variant={variant} size={size} disabled={busy} onClick={onClick} className={cn(className)}>
      {busy ? (
        <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
      ) : (
        <Eye className="h-3.5 w-3.5" aria-hidden />
      )}
      {label ?? t("actions.view")}
    </Button>
  );
}
