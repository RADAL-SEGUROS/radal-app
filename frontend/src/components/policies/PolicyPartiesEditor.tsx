/**
 * "Partes" editor — `PUT /policies/{id}/parties` replaces who is who wholesale.
 *
 * Rules mirrored from the server (which re-checks and answers in Spanish):
 * exactly one contratante, at least one asegurado, at most one pagador (the
 * payer defaults to the policyholder when absent). Third parties keep only
 * name, RUT and contact — they never become clients (R14). A RUT that is one
 * of the broker's clients is linked server-side.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Loader2, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ErrorBanner } from "@/components/common/kit";
import { cn } from "@/lib/utils";
import {
  PARTY_ROLES,
  useReplacePolicyParties,
  type PartyInput,
  type PartyRole,
  type PolicyDetail,
} from "@/api/policies";
import { PARTY_ROLE_LABEL } from "@/components/policies/policyLabels";
import { formatRut, isValidRut, normalizeRut } from "@/pages/clients/rut";

interface DraftParty {
  key: number;
  role: PartyRole;
  client_id: number | null;
  name: string;
  rut: string;
  email: string;
  phone: string;
  beneficiary_kind: string;
  notify_collections: boolean;
  /** Not edited here, but carried through: the PUT replaces parties
   *  wholesale, so dropping them would silently wipe a beneficiary's share. */
  share_pct: string | null;
  is_primary: boolean;
}

const ISSUE_LABEL = {
  holder: "policies:party.errors.holder",
  insured: "policies:party.errors.insured",
  payer: "policies:party.errors.payer",
  name: "policies:party.errors.name",
  rut: "policies:party.errors.rut",
  email: "policies:party.errors.email",
} as const;
type Issue = keyof typeof ISSUE_LABEL;

/** The field-level marks — the same rules `validate` folds into the summary. */
const INVALID = "border-neg focus-visible:ring-neg";
const rutInvalid = (r: DraftParty) => !!r.rut.trim() && !isValidRut(r.rut);
const emailInvalid = (r: DraftParty) =>
  !!r.email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(r.email.trim());

function validate(rows: DraftParty[]): Issue[] {
  const issues = new Set<Issue>();
  const count = (role: PartyRole) => rows.filter((r) => r.role === role).length;
  if (count("policyholder") !== 1) issues.add("holder");
  if (count("insured") < 1) issues.add("insured");
  if (count("payer") > 1) issues.add("payer");
  for (const r of rows) {
    if (!r.name.trim()) issues.add("name");
    if (r.rut.trim() && !isValidRut(r.rut)) issues.add("rut");
    if (r.email.trim() && !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(r.email.trim())) issues.add("email");
  }
  return [...issues];
}

let seq = 0;
function fromPolicy(policy: PolicyDetail): DraftParty[] {
  return policy.parties.map((p) => ({
    key: ++seq,
    role: p.role,
    client_id: p.client_id,
    name: p.name,
    rut: p.rut ?? "",
    email: p.email ?? "",
    phone: p.phone ?? "",
    beneficiary_kind: p.beneficiary_kind ?? "",
    notify_collections: p.notify_collections,
    share_pct: p.share_pct,
    is_primary: p.is_primary,
  }));
}

export function PolicyPartiesEditor({
  policy,
  open,
  onOpenChange,
}: {
  policy: PolicyDetail;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("policies");
  const replace = useReplacePolicyParties(policy.id);
  const [rows, setRows] = React.useState<DraftParty[]>([]);
  const [error, setError] = React.useState<unknown>(null);

  React.useEffect(() => {
    if (open) {
      setRows(fromPolicy(policy));
      setError(null);
    }
    // Reset only on open: a background refetch must not wipe the edit.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const issues = validate(rows);
  const patch = (key: number, change: Partial<DraftParty>) =>
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, ...change } : r)));

  const submit = async () => {
    const payload: PartyInput[] = rows.map((r) => ({
      role: r.role,
      // A client link only survives while the RUT is unchanged.
      client_id: r.client_id,
      name: r.name.trim(),
      rut: r.rut.trim() ? normalizeRut(r.rut) : null,
      email: r.email.trim() || null,
      phone: r.phone.trim() || null,
      beneficiary_kind: r.role === "beneficiary" ? r.beneficiary_kind.trim() || null : null,
      share_pct: r.role === "beneficiary" ? r.share_pct : null,
      is_primary: r.is_primary,
      notify_collections: r.notify_collections,
    }));
    try {
      await replace.mutateAsync(payload);
      toast.success(t("party.toast"));
      onOpenChange(false);
    } catch (e) {
      setError(e);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("party.editTitle")}</DialogTitle>
          <DialogDescription>{t("party.editHint")}</DialogDescription>
        </DialogHeader>

        <ul className="flex flex-col gap-3">
          {rows.map((r) => (
            <li key={r.key} className="flex flex-col gap-2.5 rounded-lg border border-line p-3">
              <div className="flex items-center gap-2">
                <Select
                  value={r.role}
                  onValueChange={(v) =>
                    patch(r.key, { role: v as PartyRole, client_id: v === r.role ? r.client_id : null })
                  }
                >
                  <SelectTrigger className="h-10 flex-1 sm:max-w-[220px]" aria-label={t("party.fields.role")}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {PARTY_ROLES.map((role) => (
                      <SelectItem key={role} value={role}>
                        {t(PARTY_ROLE_LABEL[role])}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  variant="ghost"
                  size="icon"
                  className="ml-auto h-10 w-10"
                  aria-label={t("party.remove")}
                  onClick={() => setRows((prev) => prev.filter((x) => x.key !== r.key))}
                >
                  <Trash2 className="h-4 w-4" />
                </Button>
              </div>
              <div className="grid grid-cols-1 gap-2.5 sm:grid-cols-2">
                <div className="flex flex-col gap-1">
                  <Label className="text-caption text-ink-3">{t("party.fields.name")}</Label>
                  <Input
                    className={cn("h-10", !r.name.trim() && INVALID)}
                    aria-invalid={!r.name.trim() || undefined}
                    value={r.name}
                    onChange={(e) => patch(r.key, { name: e.target.value })}
                  />
                  {!r.name.trim() ? (
                    <span className="text-caption text-neg-text">{t("party.fieldErrors.name")}</span>
                  ) : null}
                </div>
                <div className="flex flex-col gap-1">
                  <Label htmlFor={`party-rut-${r.key}`} className="text-caption text-ink-3">
                    {t("party.fields.rut")}
                  </Label>
                  <Input
                    id={`party-rut-${r.key}`}
                    className={cn("h-10 tabular-nums", rutInvalid(r) && INVALID)}
                    aria-invalid={rutInvalid(r) || undefined}
                    aria-describedby={rutInvalid(r) ? `party-rut-${r.key}-error` : undefined}
                    placeholder="76.086.428-5"
                    value={r.rut}
                    onChange={(e) => patch(r.key, { rut: e.target.value, client_id: null })}
                    // A valid RUT settles into its dotted display form.
                    onBlur={() => {
                      if (r.rut.trim() && isValidRut(r.rut)) patch(r.key, { rut: formatRut(r.rut) });
                    }}
                  />
                  {rutInvalid(r) ? (
                    <span id={`party-rut-${r.key}-error`} className="text-caption text-neg-text">
                      {t("party.fieldErrors.rut")}
                    </span>
                  ) : null}
                </div>
                <div className="flex flex-col gap-1">
                  <Label className="text-caption text-ink-3">{t("party.fields.email")}</Label>
                  <Input
                    className={cn("h-10", emailInvalid(r) && INVALID)}
                    aria-invalid={emailInvalid(r) || undefined}
                    type="email"
                    inputMode="email"
                    value={r.email}
                    onChange={(e) => patch(r.key, { email: e.target.value })}
                  />
                  {emailInvalid(r) ? (
                    <span className="text-caption text-neg-text">{t("party.fieldErrors.email")}</span>
                  ) : null}
                </div>
                <div className="flex flex-col gap-1">
                  <Label className="text-caption text-ink-3">{t("party.fields.phone")}</Label>
                  <Input
                    className="h-10"
                    type="tel"
                    inputMode="tel"
                    value={r.phone}
                    onChange={(e) => patch(r.key, { phone: e.target.value })}
                  />
                </div>
                {r.role === "beneficiary" ? (
                  <div className="flex flex-col gap-1 sm:col-span-2">
                    <Label className="text-caption text-ink-3">{t("party.fields.beneficiaryKind")}</Label>
                    <Input
                      className="h-10"
                      placeholder={t("party.fields.beneficiaryKindHint")}
                      value={r.beneficiary_kind}
                      onChange={(e) => patch(r.key, { beneficiary_kind: e.target.value })}
                    />
                  </div>
                ) : null}
              </div>
              {r.role === "payer" || r.role === "policyholder" ? (
                <label className="flex cursor-pointer items-center gap-2 text-caption text-ink-2">
                  <Checkbox
                    checked={r.notify_collections}
                    onCheckedChange={(c) => patch(r.key, { notify_collections: c === true })}
                  />
                  {t("party.fields.notifyCollections")}
                </label>
              ) : null}
            </li>
          ))}
        </ul>

        <Button
          variant="ghost"
          size="sm"
          className="self-start"
          onClick={() =>
            setRows((prev) => [
              ...prev,
              {
                key: ++seq,
                role: prev.some((r) => r.role === "payer") ? "beneficiary" : "payer",
                client_id: null,
                name: "",
                rut: "",
                email: "",
                phone: "",
                beneficiary_kind: "",
                notify_collections: true,
                share_pct: null,
                is_primary: false,
              },
            ])
          }
        >
          <Plus className="h-4 w-4" />
          {t("party.add")}
        </Button>

        {issues.length > 0 ? (
          <ul className="flex flex-col gap-0.5 text-caption text-warn-text">
            {issues.map((issue) => (
              <li key={issue}>{t(ISSUE_LABEL[issue])}</li>
            ))}
          </ul>
        ) : null}
        <ErrorBanner error={error} />

        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            {t("form.cancel")}
          </Button>
          <Button disabled={issues.length > 0 || replace.isPending} onClick={() => void submit()}>
            {replace.isPending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            {t("party.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
