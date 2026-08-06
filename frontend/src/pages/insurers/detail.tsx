/**
 * Insurer — detail: identity, native partner profile, and contacts.
 *
 * Contacts fall back in a fixed order — (broker+line) → (broker) → (line) →
 * global — and `GET /insurers/{id}/contacts/resolve` says which tier actually
 * answered, which is shown so the broker knows whether they are looking at
 * their own override or Radal's default.
 *
 * `insurer.can_edit` / `contact.can_edit` come from the server: a global
 * (platform-owned) row renders its edit control DISABLED with the reason,
 * never hidden and never dead.
 */
import * as React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  ArrowLeft,
  CreditCard,
  Mail,
  Pencil,
  Phone,
  Plus,
  Star,
  Trash2,
  UserRound,
} from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDate } from "@/lib/format";
import {
  useCreateInsurerContact,
  useDeleteInsurerContact,
  useInsurer,
  useInsurerContacts,
  useResolvedInsurerContact,
  useUpdateInsurer,
  useUpdateInsurerContact,
} from "@/api/insurers";
import type { Insurer, InsurerContact } from "@/api/types";
import {
  DisabledHint,
  ErrorBanner,
  KeyValue,
  MonoChip,
  Section,
  StatusBadge,
  apiError,
} from "@/pages/proposals/shared";

export default function InsurerDetailPage() {
  const { insurerId } = useParams<{ insurerId: string }>();
  const id = Number(insurerId);
  const { t } = useTranslation("insurers");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const insurer = useInsurer(Number.isFinite(id) ? id : undefined);
  const [editOpen, setEditOpen] = React.useState(false);

  if (insurer.isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-9 w-72" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (insurer.isError || !insurer.data) {
    return (
      <>
        <PageHeader title={t("detail.title")} />
        <ErrorBanner error={insurer.error ?? t("detail.notFound")} />
        <Button variant="secondary" size="sm" onClick={() => navigate("/insurers")}>
          <ArrowLeft className="h-4 w-4" />
          {tc("actions.back")}
        </Button>
      </>
    );
  }

  const i = insurer.data;

  return (
    <>
      <PageHeader
        eyebrow={
          <Link to="/insurers" className="inline-flex items-center gap-1.5 no-underline">
            <ArrowLeft className="h-3.5 w-3.5" />
            {t("title")}
          </Link>
        }
        title={i.trade_name || i.legal_name}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <MonoChip>{i.rut}</MonoChip>
            <MonoChip>CMF {i.cmf_code}</MonoChip>
            <Badge variant={i.is_native ? "brand" : "neutral"}>
              {i.is_native ? t("origin.native") : t("origin.external")}
            </Badge>
            <StatusBadge value={i.status} label={t(`status.${i.status}`)} />
          </span>
        }
        actions={
          <>
            {i.payment_url ? (
              <Button variant="secondary" size="sm" asChild>
                <a href={i.payment_url} target="_blank" rel="noreferrer">
                  <CreditCard className="h-4 w-4" />
                  {t("detail.paymentPortal")}
                </a>
              </Button>
            ) : (
              <DisabledHint hint={t("detail.noPaymentUrl")}>
                <Button variant="secondary" size="sm" disabled>
                  <CreditCard className="h-4 w-4" />
                  {t("detail.paymentPortal")}
                </Button>
              </DisabledHint>
            )}
            <DisabledHint hint={i.can_edit ? null : t("detail.readOnly")}>
              <Button size="sm" disabled={!i.can_edit} onClick={() => setEditOpen(true)}>
                <Pencil className="h-4 w-4" />
                {tc("actions.edit")}
              </Button>
            </DisabledHint>
          </>
        }
      />

      <FadeUp delay={0.05}>
        <Card className="grid grid-cols-2 gap-5 p-5 md:grid-cols-4">
          <KeyValue label={t("fields.legalName")} value={i.legal_name} />
          <KeyValue label={t("fields.tradeName")} value={i.trade_name || "—"} />
          <KeyValue label={t("fields.rut")} value={i.rut} mono />
          <KeyValue label={t("fields.cmfCode")} value={i.cmf_code} mono />
          <KeyValue label={t("fields.cmfStatus")} value={i.cmf_status || "—"} />
          <KeyValue
            label={t("fields.ownership")}
            value={i.created_by_broker_id ? t("detail.brokerOwned") : t("detail.platformOwned")}
          />
          <KeyValue label={t("fields.createdAt")} value={formatDate(i.created_at)} />
          <KeyValue
            label={t("fields.paymentUrl")}
            value={
              i.payment_url ? (
                <a href={i.payment_url} target="_blank" rel="noreferrer" className="truncate">
                  {i.payment_url}
                </a>
              ) : (
                "—"
              )
            }
          />
        </Card>
      </FadeUp>

      {i.native_profile ? (
        <FadeUp delay={0.08}>
          <Section
            title={
              <span className="flex items-center gap-2">
                <Star className="h-4 w-4 text-teal" />
                {t("profile.title")}
              </span>
            }
            description={t("profile.description")}
          >
            <div className="grid grid-cols-2 gap-5 md:grid-cols-4">
              <KeyValue label={t("profile.priority")} value={i.native_profile.priority} />
              <KeyValue
                label={t("profile.sla")}
                value={i.native_profile.sla_hours ? `${i.native_profile.sla_hours} h` : "—"}
              />
              <KeyValue
                label={t("profile.onboardedAt")}
                value={formatDate(i.native_profile.onboarded_at)}
              />
              <KeyValue
                label={t("profile.agreement")}
                value={i.native_profile.commercial_agreement || "—"}
              />
              {i.native_profile.notes ? (
                <div className="col-span-2 md:col-span-4">
                  <KeyValue
                    label={t("profile.notes")}
                    value={
                      <span className="whitespace-pre-wrap text-body">
                        {i.native_profile.notes}
                      </span>
                    }
                  />
                </div>
              ) : null}
            </div>
          </Section>
        </FadeUp>
      ) : null}

      <ResolvedContactCard insurerId={i.id} />
      <ContactsPanel insurer={i} />
      <EditInsurerDialog insurer={i} open={editOpen} onOpenChange={setEditOpen} />
    </>
  );
}

// =============================================================================
// Resolved contact
// =============================================================================

function ResolvedContactCard({ insurerId }: { insurerId: number }) {
  const { t } = useTranslation("insurers");
  const resolved = useResolvedInsurerContact(insurerId);

  if (resolved.isLoading) return <Skeleton className="h-24 w-full rounded-card" />;
  if (!resolved.data) return null;

  const { contact, match_level } = resolved.data;

  return (
    <FadeUp delay={0.1}>
      <Card className="flex flex-wrap items-center justify-between gap-4 p-5">
        <div className="flex items-center gap-3">
          <span className="rounded-[11px] bg-[color-mix(in_srgb,var(--teal)_13%,transparent)] p-2.5 text-teal-deep">
            <UserRound className="h-5 w-5" />
          </span>
          <div>
            <p className="text-caption uppercase tracking-[0.08em] text-text-muted">
              {t("contacts.effective")}
            </p>
            <p className="text-body font-medium text-text-primary">{contact.name}</p>
            <p className="flex flex-wrap items-center gap-3 text-caption text-text-muted">
              {contact.email ? (
                <span className="flex items-center gap-1">
                  <Mail className="h-3 w-3" />
                  {contact.email}
                </span>
              ) : null}
              {contact.phone ? (
                <span className="flex items-center gap-1">
                  <Phone className="h-3 w-3" />
                  {contact.phone}
                </span>
              ) : null}
            </p>
          </div>
        </div>
        <Badge variant="action">{t(`contacts.matchLevel.${match_level}`)}</Badge>
      </Card>
    </FadeUp>
  );
}

// =============================================================================
// Contacts
// =============================================================================

const EMPTY_CONTACT = {
  name: "",
  email: "",
  phone: "",
  role: "",
  is_primary: false,
};

function ContactsPanel({ insurer }: { insurer: Insurer }) {
  const { t } = useTranslation("insurers");
  const { t: tc } = useTranslation("common");
  const contacts = useInsurerContacts(insurer.id);
  const create = useCreateInsurerContact(insurer.id);
  const update = useUpdateInsurerContact(insurer.id);
  const remove = useDeleteInsurerContact(insurer.id);

  const [open, setOpen] = React.useState(false);
  const [editing, setEditing] = React.useState<InsurerContact | null>(null);
  const [form, setForm] = React.useState({ ...EMPTY_CONTACT });

  const openCreate = () => {
    setEditing(null);
    setForm({ ...EMPTY_CONTACT });
    setOpen(true);
  };

  const openEdit = (contact: InsurerContact) => {
    setEditing(contact);
    setForm({
      name: contact.name,
      email: contact.email ?? "",
      phone: contact.phone ?? "",
      role: contact.role ?? "",
      is_primary: contact.is_primary,
    });
    setOpen(true);
  };

  const submit = () => {
    const payload = {
      name: form.name.trim(),
      email: form.email.trim() || null,
      phone: form.phone.trim() || null,
      role: form.role.trim() || null,
      is_primary: form.is_primary,
    };
    const handlers = {
      onSuccess: () => {
        toast.success(editing ? t("contacts.updated") : t("contacts.created"));
        setOpen(false);
      },
      onError: (error: unknown) => toast.error(apiError(error, tc("toast.error"))),
    };
    if (editing) {
      update.mutate({ contactId: editing.id, ...payload }, handlers);
    } else {
      // A broker-created contact is scoped to that broker, never global.
      create.mutate({ ...payload, scope: "broker" }, handlers);
    }
  };

  const rows = contacts.data ?? [];

  return (
    <>
      <FadeUp delay={0.12}>
        <Section
          title={t("contacts.title")}
          description={t("contacts.description")}
          actions={
            <Button variant="secondary" size="sm" onClick={openCreate}>
              <Plus className="h-4 w-4" />
              {t("contacts.add")}
            </Button>
          }
          bodyClassName={rows.length ? "p-0" : undefined}
        >
          {contacts.isLoading ? (
            <Skeleton className="h-24 w-full" />
          ) : rows.length === 0 ? (
            <p className="text-body text-text-muted">{t("contacts.empty")}</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>{t("contacts.name")}</TableHead>
                  <TableHead>{t("contacts.contact")}</TableHead>
                  <TableHead>{t("contacts.role")}</TableHead>
                  <TableHead>{t("contacts.scope")}</TableHead>
                  <TableHead className="w-[110px]" />
                </TableRow>
              </TableHeader>
              <TableBody>
                {rows.map((contact) => (
                  <TableRow key={contact.id} className="hover:bg-transparent">
                    <TableCell>
                      <span className="flex items-center gap-2 font-medium text-text-primary">
                        {contact.name}
                        {contact.is_primary ? (
                          <Badge variant="brand">{t("contacts.primary")}</Badge>
                        ) : null}
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="flex flex-col gap-0.5 text-caption text-text-secondary">
                        {contact.email ? (
                          <a href={`mailto:${contact.email}`} className="flex items-center gap-1">
                            <Mail className="h-3 w-3" />
                            {contact.email}
                          </a>
                        ) : null}
                        {contact.phone ? (
                          <a href={`tel:${contact.phone}`} className="flex items-center gap-1">
                            <Phone className="h-3 w-3" />
                            {contact.phone}
                          </a>
                        ) : null}
                        {!contact.email && !contact.phone ? "—" : null}
                      </span>
                    </TableCell>
                    <TableCell className="text-text-secondary">{contact.role || "—"}</TableCell>
                    <TableCell>
                      <Badge variant={contact.scope === "broker" ? "action" : "neutral"}>
                        {t(`contacts.scopes.${contact.scope}`)}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-1">
                        <DisabledHint hint={contact.can_edit ? null : t("contacts.readOnly")}>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8"
                            aria-label={tc("actions.edit")}
                            disabled={!contact.can_edit}
                            onClick={() => openEdit(contact)}
                          >
                            <Pencil className="h-3.5 w-3.5" />
                          </Button>
                        </DisabledHint>
                        <DisabledHint hint={contact.can_edit ? null : t("contacts.readOnly")}>
                          <Button
                            variant="ghost"
                            size="icon"
                            className="h-8 w-8"
                            aria-label={tc("actions.delete")}
                            disabled={!contact.can_edit || remove.isPending}
                            onClick={() =>
                              remove.mutate(contact.id, {
                                onSuccess: () => toast.success(t("contacts.deleted")),
                                onError: (error) =>
                                  toast.error(apiError(error, tc("toast.error"))),
                              })
                            }
                          >
                            <Trash2 className="h-3.5 w-3.5 text-signal-danger" />
                          </Button>
                        </DisabledHint>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Section>
      </FadeUp>

      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{editing ? t("contacts.editTitle") : t("contacts.addTitle")}</DialogTitle>
            <DialogDescription>{t("contacts.dialogDescription")}</DialogDescription>
          </DialogHeader>

          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-1.5 sm:col-span-2">
              <Label htmlFor="contact-name">{t("contacts.name")}</Label>
              <Input
                id="contact-name"
                value={form.name}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="contact-email">{t("contacts.email")}</Label>
              <Input
                id="contact-email"
                type="email"
                value={form.email}
                onChange={(e) => setForm((f) => ({ ...f, email: e.target.value }))}
              />
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="contact-phone">{t("contacts.phone")}</Label>
              <Input
                id="contact-phone"
                value={form.phone}
                onChange={(e) => setForm((f) => ({ ...f, phone: e.target.value }))}
              />
            </div>
            <div className="flex flex-col gap-1.5 sm:col-span-2">
              <Label htmlFor="contact-role">{t("contacts.role")}</Label>
              <Input
                id="contact-role"
                value={form.role}
                onChange={(e) => setForm((f) => ({ ...f, role: e.target.value }))}
              />
            </div>
            <label className="flex cursor-pointer items-center gap-2 text-caption text-text-secondary sm:col-span-2">
              <input
                type="checkbox"
                checked={form.is_primary}
                onChange={(e) => setForm((f) => ({ ...f, is_primary: e.target.checked }))}
                className="h-4 w-4 accent-[var(--teal)]"
              />
              {t("contacts.isPrimary")}
            </label>
          </div>

          <p className="text-caption text-text-muted">{t("contacts.scopeNote")}</p>
          {create.isError ? <ErrorBanner error={create.error} /> : null}
          {update.isError ? <ErrorBanner error={update.error} /> : null}

          <DialogFooter>
            <Button variant="secondary" onClick={() => setOpen(false)}>
              {tc("actions.cancel")}
            </Button>
            <DisabledHint hint={form.name.trim() ? null : t("contacts.nameRequired")}>
              <Button
                disabled={!form.name.trim() || create.isPending || update.isPending}
                onClick={submit}
              >
                {tc("actions.save")}
              </Button>
            </DisabledHint>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}

// =============================================================================
// Edit insurer
// =============================================================================

function EditInsurerDialog({
  insurer,
  open,
  onOpenChange,
}: {
  insurer: Insurer;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("insurers");
  const { t: tc } = useTranslation("common");
  const update = useUpdateInsurer(insurer.id);

  const [legalName, setLegalName] = React.useState(insurer.legal_name);
  const [tradeName, setTradeName] = React.useState(insurer.trade_name ?? "");
  const [paymentUrl, setPaymentUrl] = React.useState(insurer.payment_url ?? "");

  React.useEffect(() => {
    if (open) {
      setLegalName(insurer.legal_name);
      setTradeName(insurer.trade_name ?? "");
      setPaymentUrl(insurer.payment_url ?? "");
    }
  }, [open, insurer.legal_name, insurer.trade_name, insurer.payment_url]);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t("detail.editTitle")}</DialogTitle>
          <DialogDescription>{t("detail.editDescription")}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="edit-legal">{t("fields.legalName")}</Label>
            <Input
              id="edit-legal"
              value={legalName}
              onChange={(e) => setLegalName(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="edit-trade">{t("fields.tradeName")}</Label>
            <Input
              id="edit-trade"
              value={tradeName}
              onChange={(e) => setTradeName(e.target.value)}
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="edit-payment">{t("fields.paymentUrl")}</Label>
            <Input
              id="edit-payment"
              value={paymentUrl}
              onChange={(e) => setPaymentUrl(e.target.value)}
              placeholder="https://"
            />
          </div>
          <p className="text-caption text-text-muted">{t("detail.identityImmutable")}</p>
          {update.isError ? <ErrorBanner error={update.error} /> : null}
        </div>

        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            {tc("actions.cancel")}
          </Button>
          <Button
            disabled={update.isPending || !legalName.trim()}
            onClick={() =>
              update.mutate(
                {
                  legal_name: legalName.trim(),
                  trade_name: tradeName.trim() || null,
                  payment_url: paymentUrl.trim() || null,
                },
                {
                  onSuccess: () => {
                    toast.success(tc("toast.saved"));
                    onOpenChange(false);
                  },
                  onError: (error) => toast.error(apiError(error, tc("toast.error"))),
                },
              )
            }
          >
            {tc("actions.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
