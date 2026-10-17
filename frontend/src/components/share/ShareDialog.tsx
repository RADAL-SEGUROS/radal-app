/**
 * Share a policy / plan / cuota / case / inquiry through a public link
 * (WhatsApp-ready) — v12 lane F4. F1 (policy detail) and F2 (cobranza) mount it.
 * Props are frozen: keep the signature.
 *
 * Flow: pick the audience (Asegurado / Compañía) → its capabilities start at
 * the server's defaults (insured: pay + upload receipt; insurer: report status)
 * → "Crear enlace" → copy the URL or open WhatsApp with the server's own
 * pre-filled Spanish message. Below, every link already minted for the entity,
 * with its state and a revoke action.
 *
 * Gate: minting needs `View` on the TARGET's module (the server's rule, mirrored
 * from `services.share_links.ENTITY_MODULES`); the server still decides.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  Building2,
  Check,
  Copy,
  ExternalLink,
  Link2,
  Loader2,
  MessageCircle,
  ShieldOff,
  User,
} from "lucide-react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { DisabledHint, ErrorBanner, LoadingRows, apiError } from "@/components/common/kit";
import { cn } from "@/lib/utils";
import { formatDate, formatDateTime } from "@/lib/format";
import { useCan, type PermissionModule } from "@/lib/permissions";
import {
  useCreateShareLink,
  useRevokeShareLink,
  useShareLinks,
  type ShareAudience,
  type ShareEntityType,
  type ShareLinkRead,
  type SharePermissionsInput,
} from "@/api/share";

export type { ShareAudience, ShareEntityType } from "@/api/share";

export interface ShareDialogProps {
  entityType: ShareEntityType;
  entityId: number;
  defaultAudience?: ShareAudience;
  /** The trigger element (e.g. a Button). Rendered as the dialog trigger. */
  trigger: React.ReactElement;
}

/** Mirror of the server's `ENTITY_MODULES` — whose `View` gates a link. */
const ENTITY_MODULE: Record<ShareEntityType, PermissionModule> = {
  policy: "Policies",
  collection_plan: "Collections",
  installment: "Collections",
  case_file: "CaseFiles",
  status_inquiry: "Collections",
};

/** What each entity is called in the dialog's description. */
const ENTITY_LABEL: Record<ShareEntityType, string> = {
  policy: "share:entity.policy",
  collection_plan: "share:entity.collection_plan",
  installment: "share:entity.installment",
  case_file: "share:entity.case_file",
  status_inquiry: "share:entity.status_inquiry",
};

/**
 * Audience refusals, mirroring `services.share_links.audience_refusal` (the
 * server answers 422 with the same Spanish reason). `null` = allowed.
 */
const AUDIENCE_REFUSAL: Record<ShareEntityType, Record<ShareAudience, string | null>> = {
  policy: { insured: null, insurer: null },
  collection_plan: { insured: null, insurer: null },
  installment: { insured: null, insurer: null },
  case_file: { insured: null, insurer: "share:refusal.caseFileInsurer" },
  status_inquiry: { insured: "share:refusal.inquiryInsured", insurer: null },
};

const AUDIENCE_LABEL: Record<ShareAudience, string> = {
  insured: "share:audience.insured",
  insurer: "share:audience.insurer",
};

const AUDIENCE_HINT: Record<ShareAudience, string> = {
  insured: "share:audience.insuredHint",
  insurer: "share:audience.insurerHint",
};

type PermKey = keyof SharePermissionsInput;

/** The capabilities that fit each audience (the server forces the others off). */
const AUDIENCE_PERMS: Record<ShareAudience, PermKey[]> = {
  insured: ["can_pay", "can_upload_receipt"],
  insurer: ["can_report_status"],
};

/** Server defaults (`DEFAULT_PERMISSIONS`). */
const DEFAULT_PERMS: Record<ShareAudience, Record<PermKey, boolean>> = {
  insured: { can_pay: true, can_upload_receipt: true, can_report_status: false },
  insurer: { can_pay: false, can_upload_receipt: false, can_report_status: true },
};

const PERM_LABEL: Record<PermKey, string> = {
  can_pay: "share:perm.can_pay",
  can_upload_receipt: "share:perm.can_upload_receipt",
  can_report_status: "share:perm.can_report_status",
};

const PERM_HINT: Record<PermKey, string> = {
  can_pay: "share:perm.can_payHint",
  can_upload_receipt: "share:perm.can_upload_receiptHint",
  can_report_status: "share:perm.can_report_statusHint",
};

/** Validity options in days (server: 1..365, default 60). */
const EXPIRY_OPTIONS = ["7", "30", "60", "90", "180", "365"] as const;
type ExpiryOption = (typeof EXPIRY_OPTIONS)[number];

const EXPIRY_LABEL: Record<ExpiryOption, string> = {
  "7": "share:expiry.d7",
  "30": "share:expiry.d30",
  "60": "share:expiry.d60",
  "90": "share:expiry.d90",
  "180": "share:expiry.d180",
  "365": "share:expiry.d365",
};

function firstAllowedAudience(
  entityType: ShareEntityType,
  preferred: ShareAudience | undefined,
): ShareAudience {
  const refusal = AUDIENCE_REFUSAL[entityType];
  if (preferred && refusal[preferred] === null) return preferred;
  return refusal.insured === null ? "insured" : "insurer";
}

export function ShareDialog({ entityType, entityId, defaultAudience, trigger }: ShareDialogProps) {
  const { t } = useTranslation(["share", "common"]);
  const { allowed, isLoading } = useCan(ENTITY_MODULE[entityType], "View");
  const [open, setOpen] = React.useState(false);

  if (!isLoading && !allowed) {
    // Never a silent no-op: the trigger stays visible, disabled, with the reason.
    return (
      <DisabledHint hint={t("share:noPermission")}>
        {React.cloneElement(trigger as React.ReactElement<{ disabled?: boolean }>, {
          disabled: true,
        })}
      </DisabledHint>
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-h-[calc(100dvh-2rem)] w-[calc(100%-2rem)] max-w-lg overflow-y-auto p-5 sm:p-6">
        {open ? (
          <ShareDialogBody
            entityType={entityType}
            entityId={entityId}
            defaultAudience={defaultAudience}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  );
}

function ShareDialogBody({
  entityType,
  entityId,
  defaultAudience,
}: Omit<ShareDialogProps, "trigger">) {
  const { t } = useTranslation(["share", "common"]);
  const [audience, setAudience] = React.useState<ShareAudience>(() =>
    firstAllowedAudience(entityType, defaultAudience),
  );
  const [perms, setPerms] = React.useState<Record<PermKey, boolean>>(
    () => DEFAULT_PERMS[firstAllowedAudience(entityType, defaultAudience)],
  );
  const [expiry, setExpiry] = React.useState<ExpiryOption>("60");
  const [label, setLabel] = React.useState("");
  const [created, setCreated] = React.useState<ShareLinkRead | null>(null);
  // The ready block must never keep offering Copiar / WhatsApp for a link
  // that was just revoked from the list below.
  const [revokedReady, setRevokedReady] = React.useState(false);

  const create = useCreateShareLink();
  const links = useShareLinks(entityType, entityId);
  const createdRow = created ? links.data?.items.find((l) => l.id === created.id) : undefined;
  const readyLink = created && !createdRow?.revoked_at && !created.revoked_at ? created : null;
  const onRevoked = (linkId: number) => {
    if (created?.id === linkId) {
      setCreated(null);
      setRevokedReady(true);
    }
  };

  const pickAudience = (next: ShareAudience) => {
    if (AUDIENCE_REFUSAL[entityType][next] !== null) return;
    setAudience(next);
    setPerms(DEFAULT_PERMS[next]);
    setCreated(null);
    create.reset();
  };

  const submit = () => {
    const permissions: SharePermissionsInput = {};
    for (const key of AUDIENCE_PERMS[audience]) permissions[key] = perms[key];
    create.mutate(
      {
        entity_type: entityType,
        entity_id: entityId,
        audience,
        permissions,
        expires_in_days: Number(expiry),
        label: label.trim() || null,
      },
      {
        onSuccess: (link) => {
          setCreated(link);
          setRevokedReady(false);
          setLabel("");
          toast.success(t("share:created"));
        },
      },
    );
  };

  return (
    <div className="flex min-w-0 flex-col gap-5">
      <DialogHeader className="pr-6">
        <DialogTitle className="flex items-center gap-2">
          <Link2 className="h-4 w-4 text-brand" />
          {t("share:dialog.title")}
        </DialogTitle>
        <DialogDescription>
          {t("share:dialog.description", { entity: t(ENTITY_LABEL[entityType]) })}
        </DialogDescription>
      </DialogHeader>

      {/* Audience */}
      <div className="flex flex-col gap-2">
        <Label>{t("share:audience.label")}</Label>
        <div
          role="radiogroup"
          aria-label={t("share:audience.label")}
          className="grid grid-cols-2 gap-1 rounded-lg bg-paper-2 p-1"
        >
          {(["insured", "insurer"] as const).map((option) => {
            const refusal = AUDIENCE_REFUSAL[entityType][option];
            const active = audience === option;
            const button = (
              <button
                key={option}
                type="button"
                role="radio"
                aria-checked={active}
                disabled={refusal !== null}
                onClick={() => pickAudience(option)}
                className={cn(
                  "flex h-10 w-full items-center justify-center gap-2 rounded-sm px-3 text-label font-medium transition-[background-color,color,box-shadow,transform] duration-150 ease-out active:scale-[0.96]",
                  active ? "bg-bone text-ink shadow-elev" : "text-ink-3 hover:text-ink",
                  refusal !== null && "cursor-not-allowed opacity-50",
                )}
              >
                {option === "insured" ? (
                  <User className="h-4 w-4" />
                ) : (
                  <Building2 className="h-4 w-4" />
                )}
                {t(AUDIENCE_LABEL[option])}
              </button>
            );
            return refusal !== null ? (
              <DisabledHint key={option} hint={t(refusal)} className="w-full">
                {button}
              </DisabledHint>
            ) : (
              button
            );
          })}
        </div>
        <p className="text-caption text-ink-3">{t(AUDIENCE_HINT[audience])}</p>
      </div>

      {/* Capabilities */}
      <div className="flex flex-col gap-2">
        <Label>{t("share:perm.label")}</Label>
        <div className="flex flex-col divide-y divide-line rounded-lg border border-line">
          {AUDIENCE_PERMS[audience].map((key) => (
            <label
              key={key}
              className="flex cursor-pointer items-start justify-between gap-3 px-3 py-2.5"
            >
              <span className="min-w-0">
                <span className="block text-body text-ink">{t(PERM_LABEL[key])}</span>
                <span className="block text-caption text-ink-3">{t(PERM_HINT[key])}</span>
              </span>
              <Switch
                checked={perms[key]}
                onCheckedChange={(checked) => setPerms((p) => ({ ...p, [key]: checked }))}
                className="mt-0.5"
              />
            </label>
          ))}
        </div>
      </div>

      {/* Validity + label */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="share-expiry">{t("share:expiry.label")}</Label>
          <Select value={expiry} onValueChange={(v) => setExpiry(v as ExpiryOption)}>
            <SelectTrigger id="share-expiry" className="h-10">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {EXPIRY_OPTIONS.map((option) => (
                <SelectItem key={option} value={option}>
                  {t(EXPIRY_LABEL[option])}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor="share-label">{t("share:labelField")}</Label>
          <Input
            id="share-label"
            value={label}
            maxLength={160}
            placeholder={t("share:labelPlaceholder")}
            onChange={(e) => setLabel(e.target.value)}
            className="h-10"
          />
        </div>
      </div>

      {create.isError ? <ErrorBanner error={create.error} /> : null}

      {revokedReady && !readyLink ? (
        <p role="status" className="rounded-sm bg-paper-2 px-3 py-2.5 text-caption text-ink-2">
          {t("share:createdRevoked")}
        </p>
      ) : null}

      {readyLink ? (
        <div className="flex flex-col gap-2">
          <CreatedLink link={readyLink} />
          <Button variant="ghost" size="sm" className="h-9 self-start" onClick={() => setCreated(null)}>
            <Link2 className="h-4 w-4" />
            {t("share:createAnother")}
          </Button>
        </div>
      ) : (
        <Button size="lg" className="w-full sm:w-auto sm:self-end" onClick={submit} disabled={create.isPending}>
          {create.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
          {t("share:create")}
        </Button>
      )}

      {/* Existing links */}
      <div className="flex flex-col gap-2 border-t border-line pt-4">
        <div className="flex items-center justify-between gap-2">
          <h3 className="text-label font-medium text-ink">{t("share:list.title")}</h3>
          {links.data ? (
            <span className="text-caption tabular-nums text-ink-3">
              {t("share:list.count", { count: links.data.total })}
            </span>
          ) : null}
        </div>
        {links.isLoading ? (
          <LoadingRows rows={2} />
        ) : links.isError ? (
          <ErrorBanner error={links.error} />
        ) : !links.data || links.data.items.length === 0 ? (
          <p className="text-caption text-ink-3">{t("share:list.empty")}</p>
        ) : (
          <ul className="flex flex-col gap-2">
            {links.data.items.map((link) => (
              <ExistingLinkRow
                key={link.id}
                link={link}
                highlighted={readyLink?.id === link.id}
                onRevoked={onRevoked}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function useCopy() {
  const { t } = useTranslation("share");
  return React.useCallback(
    async (value: string) => {
      try {
        await navigator.clipboard.writeText(value);
      } catch {
        const el = document.createElement("textarea");
        el.value = value;
        document.body.appendChild(el);
        el.select();
        document.execCommand("copy");
        document.body.removeChild(el);
      }
      toast.success(t("copied"));
    },
    [t],
  );
}

function CreatedLink({ link }: { link: ShareLinkRead }) {
  const { t } = useTranslation("share");
  const copy = useCopy();
  const [copied, setCopied] = React.useState(false);
  const whatsapp = whatsappHref(link);

  return (
    <div className="flex flex-col gap-3 rounded-lg bg-pos-soft/50 p-3">
      <div className="flex items-center gap-2 text-label font-medium text-pos-text">
        <Check className="h-4 w-4" />
        {t("createdTitle")}
      </div>
      <div className="flex min-w-0 items-center gap-2 rounded-sm border border-line bg-bone px-3 py-2">
        <span className="min-w-0 flex-1 truncate text-caption tabular-nums text-ink-2">{link.url}</span>
      </div>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <Button
          variant="secondary"
          className="h-10"
          onClick={() => {
            void copy(link.url);
            setCopied(true);
            window.setTimeout(() => setCopied(false), 2000);
          }}
        >
          {copied ? <Check className="h-4 w-4 text-pos-text" /> : <Copy className="h-4 w-4" />}
          {copied ? t("copiedShort") : t("copy")}
        </Button>
        <Button variant="secondary" className="h-10" asChild>
          <a href={whatsapp} target="_blank" rel="noopener noreferrer">
            <MessageCircle className="h-4 w-4" />
            {t("whatsapp")}
          </a>
        </Button>
      </div>
    </div>
  );
}

/** The server's ready-made `whatsapp_url`, or the same wa.me URL built here. */
function whatsappHref(link: ShareLinkRead): string {
  if (link.whatsapp_url?.startsWith("https://wa.me/")) return link.whatsapp_url;
  const text = link.whatsapp_text || link.url;
  return `https://wa.me/?text=${encodeURIComponent(text)}`;
}

type LinkState = "live" | "revoked" | "expired";

const LINK_STATE_LABEL: Record<LinkState, string> = {
  live: "share:state.live",
  revoked: "share:state.revoked",
  expired: "share:state.expired",
};

const LINK_STATE_VARIANT: Record<LinkState, "success" | "muted" | "warn"> = {
  live: "success",
  revoked: "muted",
  expired: "warn",
};

function linkState(link: ShareLinkRead): LinkState {
  if (link.revoked_at) return "revoked";
  return link.is_live ? "live" : "expired";
}

function ExistingLinkRow({
  link,
  highlighted,
  onRevoked,
}: {
  link: ShareLinkRead;
  highlighted: boolean;
  onRevoked: (linkId: number) => void;
}) {
  const { t } = useTranslation("share");
  const copy = useCopy();
  const revoke = useRevokeShareLink();
  const [confirming, setConfirming] = React.useState(false);
  const state = linkState(link);

  const doRevoke = () => {
    revoke.mutate(link.id, {
      onSuccess: () => {
        setConfirming(false);
        onRevoked(link.id);
        toast.success(t("revoked"));
      },
      onError: (error) => toast.error(apiError(error, t("revokeError"))),
    });
  };

  return (
    <li
      className={cn(
        "flex flex-col gap-2 rounded-lg border border-line p-3 transition-[border-color] duration-150",
        highlighted && "border-brand-line",
      )}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="neutral">
          {link.audience === "insured" ? <User className="h-3 w-3" /> : <Building2 className="h-3 w-3" />}
          {t(AUDIENCE_LABEL[link.audience])}
        </Badge>
        <Badge variant={LINK_STATE_VARIANT[state]} dot>
          {t(LINK_STATE_LABEL[state])}
        </Badge>
        {link.label ? <span className="min-w-0 truncate text-caption text-ink-2">{link.label}</span> : null}
      </div>
      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-caption text-ink-3">
        <span>{t("list.createdAt", { date: formatDate(link.created_at) })}</span>
        {state === "revoked" ? (
          <span>{t("list.revokedAt", { date: formatDate(link.revoked_at) })}</span>
        ) : link.expires_at ? (
          <span>
            {state === "live"
              ? t("list.expiresAt", { date: formatDate(link.expires_at) })
              : t("list.expiredAt", { date: formatDate(link.expires_at) })}
          </span>
        ) : null}
        <span className="tabular-nums">
          {link.view_count > 0
            ? t("list.views", { count: link.view_count, date: formatDateTime(link.last_viewed_at) })
            : t("list.notViewed")}
        </span>
      </div>
      {state === "live" ? (
        confirming ? (
          <div className="flex flex-col gap-2 rounded-sm bg-neg-soft/60 p-2.5 sm:flex-row sm:items-center sm:justify-between">
            <p className="text-caption text-ink-2">{t("revokeConfirm")}</p>
            <div className="flex gap-2">
              <Button variant="ghost" size="sm" className="h-9" onClick={() => setConfirming(false)}>
                {t("cancel")}
              </Button>
              <Button variant="destructive" size="sm" className="h-9" onClick={doRevoke} disabled={revoke.isPending}>
                {revoke.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : <ShieldOff className="h-4 w-4" />}
                {t("revoke")}
              </Button>
            </div>
          </div>
        ) : (
          <div className="flex flex-wrap gap-2">
            <Button variant="ghost" size="sm" className="h-9" onClick={() => void copy(link.url)}>
              <Copy className="h-4 w-4" />
              {t("copy")}
            </Button>
            <Button variant="ghost" size="sm" className="h-9" asChild>
              <a href={whatsappHref(link)} target="_blank" rel="noopener noreferrer">
                <MessageCircle className="h-4 w-4" />
                {t("whatsappShort")}
              </a>
            </Button>
            <Button variant="ghost" size="sm" className="h-9" asChild>
              <a href={link.url} target="_blank" rel="noopener noreferrer">
                <ExternalLink className="h-4 w-4" />
                {t("open")}
              </a>
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-9 text-neg-text hover:bg-neg-soft hover:text-neg-text sm:ml-auto"
              onClick={() => setConfirming(true)}
            >
              <ShieldOff className="h-4 w-4" />
              {t("revoke")}
            </Button>
          </div>
        )
      ) : null}
    </li>
  );
}

export default ShareDialog;
