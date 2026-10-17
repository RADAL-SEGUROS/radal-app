/**
 * Mantenedor → Compañías y cobro (`/platform/insurers`, v12 02 §5, decision 4).
 *
 * Two tabs (`?tab=`), because App.tsx is frozen and the channels matrix has
 * no route of its own:
 *
 * - **Perfiles** — every canonical insurer with its RADAL collection profile
 *   (`GET /platform/insurer-profiles`; no profile reads as `not_contacted`).
 *   The sheet edits onboarding status, internal note, expected go-live date,
 *   the payment-instruction fields (as a form, not raw JSON), the collections
 *   email and the sender domains (`PUT` upserts the profile).
 * - **Canales de pago** — the insurer × channel matrix. Each cell opens a
 *   dialog: status, provider account and store commerce code
 *   (`PUT /platform/insurer-channels/{insurer_id}/{channel}`). An active
 *   channel needs an account (the server says so too, in Spanish).
 *
 * Platform users only — nothing here touches a broker endpoint.
 */
import * as React from "react";
import { useSearchParams } from "react-router-dom";
import { Controller, useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import { Building2, Loader2, Pencil, Plus, Search, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { FadeUp } from "@/components/common/motion";
import { DisabledHint, EmptyState, ErrorBanner, LoadingRows, apiError } from "@/components/common/kit";
import {
  CHANNEL_STATUSES,
  INSTRUCTION_KEYS,
  ONBOARDING_STATUSES,
  PAYMENT_CHANNELS,
  useInsurerProfiles,
  useProviderAccounts,
  useUpdateInsurerProfile,
  useUpsertInsurerChannel,
  type ChannelStatus,
  type InstructionKey,
  type InsurerProfile,
  type OnboardingStatus,
  type PaymentChannel,
  type PaymentInstructions,
  type PlatformChannel,
} from "@/api/platform";
import { formatDate, formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { formatRut } from "@/pages/clients/rut";
import { Field, GuardedButton, type Guard } from "@/pages/settings/components/shared";
import {
  CHANNEL_LABEL,
  CHANNEL_STATUS_LABEL,
  CHANNEL_STATUS_TONE,
  INSTRUCTION_LABEL,
  ONBOARDING_LABEL,
  ONBOARDING_TONE,
  PlatformHeader,
  usePlatformGuards,
} from "./components/shared";

type TFn = (key: string, options?: Record<string, unknown>) => string;

const TABS = ["profiles", "channels"] as const;
type TabId = (typeof TABS)[number];
const ALL = "__all__";
const NONE = "__none__";

function asTab(value: string | null): TabId {
  return value && (TABS as readonly string[]).includes(value) ? (value as TabId) : "profiles";
}

export default function PlatformInsurersPage() {
  const { t } = useTranslation("platform");
  const [params, setParams] = useSearchParams();
  const tab = asTab(params.get("tab"));
  const onTab = (next: string) => {
    const nextParams = new URLSearchParams(params);
    if (next === "profiles") nextParams.delete("tab");
    else nextParams.set("tab", next);
    setParams(nextParams, { replace: true });
  };

  return (
    <>
      <PlatformHeader title={t("insurers.title")} subtitle={t("insurers.description")} />
      <FadeUp>
        <Tabs value={tab} onValueChange={onTab}>
          <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
            <TabsList variant="underline" className="w-max min-w-full">
              <TabsTrigger value="profiles">{t("insurers.tabs.profiles")}</TabsTrigger>
              <TabsTrigger value="channels">{t("insurers.tabs.channels")}</TabsTrigger>
            </TabsList>
          </div>
          <TabsContent value="profiles" className="mt-5">
            <ProfilesTab />
          </TabsContent>
          <TabsContent value="channels" className="mt-5">
            <ChannelsTab />
          </TabsContent>
        </Tabs>
      </FadeUp>
    </>
  );
}

// --- Perfiles -----------------------------------------------------------------------

function activeChannels(profile: InsurerProfile): PlatformChannel[] {
  return profile.channels.filter((c) => c.status === "active");
}

function ProfilesTab() {
  const { t: rawT } = useTranslation("platform");
  const t = rawT as unknown as TFn;
  const guards = usePlatformGuards();
  const [q, setQ] = React.useState("");
  const [status, setStatus] = React.useState<OnboardingStatus | typeof ALL>(ALL);
  const [editingId, setEditingId] = React.useState<number | null>(null);
  const deferredQ = React.useDeferredValue(q.trim());
  const list = useInsurerProfiles({
    q: deferredQ || undefined,
    onboarding_status: status === ALL ? undefined : status,
  });
  const rows = list.data ?? [];
  const editing = editingId === null ? null : rows.find((r) => r.insurer_id === editingId) ?? null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative w-full sm:w-80">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t("insurers.search")}
            aria-label={t("insurers.search")}
            className="pl-9"
          />
        </div>
        <Select value={status} onValueChange={(v) => setStatus(v as OnboardingStatus | typeof ALL)}>
          <SelectTrigger className="w-full sm:w-56" aria-label={t("insurers.columns.status")}>
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={ALL}>{t("insurers.allStatuses")}</SelectItem>
            {ONBOARDING_STATUSES.map((s) => (
              <SelectItem key={s} value={s}>
                {t(ONBOARDING_LABEL[s])}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <ErrorBanner error={list.error} />

      <Card className="overflow-hidden">
        {list.isLoading ? (
          <div className="p-5">
            <LoadingRows rows={5} />
          </div>
        ) : rows.length === 0 ? (
          <EmptyState icon={<Building2 />} title={t("insurers.empty")} />
        ) : (
          <>
            {/* ≥ md: a table. */}
            <div className="hidden md:block">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("insurers.columns.insurer")}</TableHead>
                    <TableHead>{t("insurers.columns.status")}</TableHead>
                    <TableHead>{t("insurers.columns.email")}</TableHead>
                    <TableHead>{t("insurers.columns.domains")}</TableHead>
                    <TableHead>{t("insurers.columns.channels")}</TableHead>
                    <TableHead>{t("insurers.columns.updated")}</TableHead>
                    <TableHead className="w-0" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={row.insurer_id}>
                      <TableCell>
                        <p className="font-medium text-ink">{row.insurer_name}</p>
                        <p className="text-caption tabular-nums text-ink-3">
                          {row.insurer_rut ? formatRut(row.insurer_rut) : "—"}
                          {!row.has_profile ? ` · ${t("insurers.noProfile")}` : ""}
                        </p>
                      </TableCell>
                      <TableCell>
                        <OnboardingBadge status={row.onboarding_status} t={t} />
                      </TableCell>
                      <TableCell className="max-w-[16rem] break-all text-caption">
                        {row.collections_email ?? "—"}
                      </TableCell>
                      <TableCell className="text-caption">
                        {row.sender_domains.length ? row.sender_domains.join(", ") : "—"}
                      </TableCell>
                      <TableCell className="text-caption">
                        <ChannelsSummary profile={row} t={t} />
                      </TableCell>
                      <TableCell className="whitespace-nowrap text-caption text-ink-3">
                        {row.updated_at ? formatDateTime(row.updated_at) : "—"}
                      </TableCell>
                      <TableCell>
                        <GuardedButton
                          guard={guards.edit}
                          variant="secondary"
                          size="sm"
                          className="min-h-9"
                          onClick={() => setEditingId(row.insurer_id)}
                        >
                          <Pencil />
                          {t("common.edit")}
                        </GuardedButton>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>

            {/* < md: cards. */}
            <ul className="flex flex-col divide-y divide-line md:hidden">
              {rows.map((row) => (
                <li key={row.insurer_id} className="flex flex-col gap-2.5 p-4">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-medium text-ink">{row.insurer_name}</p>
                      <p className="text-caption tabular-nums text-ink-3">{row.insurer_rut ? formatRut(row.insurer_rut) : "—"}</p>
                    </div>
                    <OnboardingBadge status={row.onboarding_status} t={t} />
                  </div>
                  <p className="break-all text-caption text-ink-2">
                    {row.collections_email ?? t("common.none")}
                  </p>
                  <p className="text-caption text-ink-3">
                    <ChannelsSummary profile={row} t={t} />
                  </p>
                  <GuardedButton
                    guard={guards.edit}
                    variant="secondary"
                    className="min-h-10 w-full"
                    onClick={() => setEditingId(row.insurer_id)}
                  >
                    <Pencil />
                    {t("common.edit")}
                  </GuardedButton>
                </li>
              ))}
            </ul>
          </>
        )}
      </Card>

      <ProfileSheet profile={editing} onClose={() => setEditingId(null)} />
    </div>
  );
}

function OnboardingBadge({ status, t }: { status: OnboardingStatus; t: TFn }) {
  return (
    <Badge variant={ONBOARDING_TONE[status] ?? "neutral"} dot className="whitespace-nowrap">
      {ONBOARDING_LABEL[status] ? t(ONBOARDING_LABEL[status]) : status}
    </Badge>
  );
}

function ChannelsSummary({ profile, t }: { profile: InsurerProfile; t: TFn }) {
  const active = activeChannels(profile);
  if (active.length === 0) return <span className="text-ink-3">{t("insurers.noChannels")}</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {active.map((c) => (
        <Badge key={c.channel} variant="success" className="px-1.5 py-0">
          {t(CHANNEL_LABEL[c.channel])}
        </Badge>
      ))}
    </span>
  );
}

// --- Profile sheet ---------------------------------------------------------------------

const DOMAIN_RE = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;

function profileSchema(t: TFn) {
  const instructions: Record<string, z.ZodTypeAny> = {};
  for (const key of INSTRUCTION_KEYS) {
    instructions[key] =
      key === "portal_url"
        ? z
            .string()
            .trim()
            .refine((v) => v === "" || /^https?:\/\//i.test(v), {
              message: t("insurers.sheet.invalidUrl"),
            })
        : z.string();
  }
  return z.object({
    onboarding_status: z.enum([
      "not_contacted",
      "in_conversation",
      "onboarding",
      "sandbox",
      "active",
      "suspended",
    ]),
    status_note: z.string().max(4000),
    expected_live_on: z.string(),
    collections_email: z
      .string()
      .trim()
      .refine((v) => v === "" || z.string().email().safeParse(v).success, {
        message: t("insurers.sheet.invalidEmail"),
      }),
    sender_domains: z.array(z.string()).max(20, t("insurers.sheet.tooManyDomains")),
    instructions: z.object(instructions),
  });
}

interface ProfileValues {
  onboarding_status: OnboardingStatus;
  status_note: string;
  expected_live_on: string;
  collections_email: string;
  sender_domains: string[];
  instructions: Record<InstructionKey, string>;
}

function toProfileValues(p: InsurerProfile | null): ProfileValues {
  return {
    onboarding_status: p?.onboarding_status ?? "not_contacted",
    status_note: p?.status_note ?? "",
    expected_live_on: p?.expected_live_on ?? "",
    collections_email: p?.collections_email ?? "",
    sender_domains: [...(p?.sender_domains ?? [])],
    instructions: Object.fromEntries(
      INSTRUCTION_KEYS.map((key) => [key, p?.payment_instructions?.[key] ?? ""]),
    ) as Record<InstructionKey, string>,
  };
}

function ProfileSheet({ profile, onClose }: { profile: InsurerProfile | null; onClose: () => void }) {
  const { t: rawT } = useTranslation("platform");
  const t = rawT as unknown as TFn;
  const save = useUpdateInsurerProfile();
  const schema = React.useMemo(() => profileSchema(t), [t]);
  const form = useForm<ProfileValues>({
    resolver: zodResolver(schema),
    defaultValues: toProfileValues(profile),
  });
  const insurerId = profile?.insurer_id ?? null;

  React.useEffect(() => {
    form.reset(toProfileValues(profile));
    save.reset();
    // Re-seed only when another insurer is opened, not on every refetch.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [insurerId]);

  const errors = form.formState.errors;

  const onSubmit = form.handleSubmit((values) => {
    if (!profile) return;
    const instructions: PaymentInstructions = {};
    for (const key of INSTRUCTION_KEYS) {
      const v = values.instructions[key]?.trim();
      if (v) instructions[key] = v;
    }
    save.mutate(
      {
        insurerId: profile.insurer_id,
        payload: {
          onboarding_status: values.onboarding_status,
          status_note: values.status_note.trim() || null,
          expected_live_on: values.expected_live_on || null,
          collections_email: values.collections_email.trim() || null,
          sender_domains: values.sender_domains,
          payment_instructions: instructions,
        },
      },
      {
        onSuccess: () => {
          toast.success(t("insurers.sheet.saved"));
          onClose();
        },
        onError: (error) => toast.error(apiError(error)),
      },
    );
  });

  return (
    <Sheet open={profile !== null} onOpenChange={(open) => (!open && !save.isPending ? onClose() : undefined)}>
      <SheetContent className="flex w-full flex-col gap-0 overflow-y-auto p-0 sm:max-w-xl">
        <SheetHeader className="border-b border-line px-5 py-4 pr-12">
          <SheetTitle>{t("insurers.sheet.title", { name: profile?.insurer_name ?? "" })}</SheetTitle>
          <SheetDescription>{t("insurers.sheet.description")}</SheetDescription>
        </SheetHeader>
        <form onSubmit={onSubmit} noValidate className="flex flex-1 flex-col">
          <div className="flex flex-1 flex-col gap-6 px-5 py-5">
            <section className="flex flex-col gap-3">
              <h3 className="text-body font-medium text-ink">{t("insurers.sheet.statusTitle")}</h3>
              <div className="grid gap-3 sm:grid-cols-2">
                <Field label={t("insurers.sheet.onboarding")}>
                  <Controller
                    control={form.control}
                    name="onboarding_status"
                    render={({ field }) => (
                      <Select value={field.value} onValueChange={field.onChange}>
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {ONBOARDING_STATUSES.map((s) => (
                            <SelectItem key={s} value={s}>
                              {t(ONBOARDING_LABEL[s])}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    )}
                  />
                </Field>
                <Field label={t("insurers.sheet.expectedLive")} htmlFor="ip-live">
                  <Input id="ip-live" type="date" {...form.register("expected_live_on")} />
                </Field>
              </div>
              <Field label={t("insurers.sheet.statusNote")} htmlFor="ip-note" error={errors.status_note?.message}>
                <Textarea id="ip-note" rows={3} {...form.register("status_note")} />
              </Field>
            </section>

            <section className="flex flex-col gap-3">
              <h3 className="text-body font-medium text-ink">{t("insurers.sheet.contactTitle")}</h3>
              <Field
                label={t("insurers.sheet.collectionsEmail")}
                htmlFor="ip-email"
                hint={t("insurers.sheet.collectionsEmailHint")}
                error={errors.collections_email?.message}
              >
                <Input id="ip-email" type="email" autoComplete="off" {...form.register("collections_email")} />
              </Field>
              <Controller
                control={form.control}
                name="sender_domains"
                render={({ field }) => (
                  <DomainsEditor
                    value={field.value}
                    onChange={field.onChange}
                    error={errors.sender_domains?.message}
                    t={t}
                  />
                )}
              />
            </section>

            <section className="flex flex-col gap-3">
              <div>
                <h3 className="text-body font-medium text-ink">{t("insurers.sheet.instructionsTitle")}</h3>
                <p className="text-caption text-ink-3">{t("insurers.sheet.instructionsHint")}</p>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {INSTRUCTION_KEYS.map((key) => {
                  const fieldError = (
                    errors.instructions as Record<string, { message?: string }> | undefined
                  )?.[key]?.message;
                  return (
                    <Field
                      key={key}
                      label={t(INSTRUCTION_LABEL[key])}
                      htmlFor={`ip-${key}`}
                      error={fieldError}
                      className={key === "notes" || key === "portal_url" ? "sm:col-span-2" : undefined}
                    >
                      {key === "notes" ? (
                        <Textarea id={`ip-${key}`} rows={3} {...form.register(`instructions.${key}`)} />
                      ) : (
                        <Input id={`ip-${key}`} autoComplete="off" {...form.register(`instructions.${key}`)} />
                      )}
                    </Field>
                  );
                })}
              </div>
            </section>

            {profile && profile.channels.length > 0 ? (
              <section className="flex flex-col gap-2">
                <h3 className="text-body font-medium text-ink">{t("insurers.sheet.channelsTitle")}</h3>
                <div className="flex flex-wrap gap-1.5">
                  {profile.channels.map((c) => (
                    <Badge key={c.channel} variant={CHANNEL_STATUS_TONE[c.status] ?? "neutral"}>
                      {t(CHANNEL_LABEL[c.channel])} · {t(CHANNEL_STATUS_LABEL[c.status])}
                    </Badge>
                  ))}
                </div>
                <p className="text-caption text-ink-3">{t("insurers.sheet.channelsHint")}</p>
              </section>
            ) : null}

            <ErrorBanner error={save.error} />
          </div>
          <SheetFooter className="sticky bottom-0 gap-2 border-t border-line bg-bone px-5 py-4 sm:gap-0">
            <Button type="button" variant="ghost" onClick={onClose} disabled={save.isPending}>
              {t("common.cancel")}
            </Button>
            <Button type="submit" disabled={save.isPending}>
              {save.isPending ? <Loader2 className="animate-spin" /> : null}
              {save.isPending ? t("common.saving") : t("common.save")}
            </Button>
          </SheetFooter>
        </form>
      </SheetContent>
    </Sheet>
  );
}

function DomainsEditor({
  value,
  onChange,
  error,
  t,
}: {
  value: string[];
  onChange: (next: string[]) => void;
  error?: string;
  t: TFn;
}) {
  const [draft, setDraft] = React.useState("");
  const [local, setLocal] = React.useState<string | null>(null);

  const add = () => {
    const domain = draft.trim().toLowerCase().replace(/^@/, "");
    if (!domain) return;
    if (!DOMAIN_RE.test(domain)) return setLocal(t("insurers.sheet.invalidDomain"));
    if (value.includes(domain)) return setLocal(t("insurers.sheet.duplicateDomain"));
    if (value.length >= 20) return setLocal(t("insurers.sheet.tooManyDomains"));
    onChange([...value, domain]);
    setDraft("");
    setLocal(null);
  };

  return (
    <Field
      label={t("insurers.sheet.senderDomains")}
      htmlFor="ip-domain"
      hint={t("insurers.sheet.senderDomainsHint")}
      error={local ?? error}
    >
      <div className="flex flex-col gap-2">
        {value.length > 0 ? (
          <ul className="flex flex-wrap gap-1.5">
            {value.map((domain) => (
              <li
                key={domain}
                className="inline-flex min-h-8 items-center gap-1 rounded-full border border-line bg-paper-2 py-0.5 pl-3 pr-1 text-caption text-ink-2"
              >
                {domain}
                <button
                  type="button"
                  onClick={() => onChange(value.filter((d) => d !== domain))}
                  aria-label={t("insurers.sheet.removeDomain", { domain })}
                  className="grid h-7 w-7 place-items-center rounded-full transition-[background-color,transform] duration-150 ease-out hover:bg-black/5 active:scale-[0.96]"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </li>
            ))}
          </ul>
        ) : null}
        <div className="flex gap-2">
          <Input
            id="ip-domain"
            value={draft}
            placeholder={t("insurers.sheet.domainPlaceholder")}
            autoComplete="off"
            onChange={(e) => {
              setDraft(e.target.value);
              setLocal(null);
            }}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                add();
              }
            }}
          />
          <Button type="button" variant="secondary" className="min-h-9" onClick={add} disabled={!draft.trim()}>
            <Plus />
            {t("insurers.sheet.addDomain")}
          </Button>
        </div>
      </div>
    </Field>
  );
}

// --- Canales de pago (matrix) --------------------------------------------------------------

interface CellTarget {
  profile: InsurerProfile;
  channel: PaymentChannel;
  current: PlatformChannel | null;
}

function ChannelsTab() {
  const { t: rawT } = useTranslation("platform");
  const t = rawT as unknown as TFn;
  const guards = usePlatformGuards();
  const [q, setQ] = React.useState("");
  const [onlyConfigured, setOnlyConfigured] = React.useState(false);
  const [target, setTarget] = React.useState<CellTarget | null>(null);
  const deferredQ = React.useDeferredValue(q.trim());
  const list = useInsurerProfiles({ q: deferredQ || undefined });
  const rows = (list.data ?? []).filter((p) => !onlyConfigured || p.channels.length > 0);

  return (
    <div className="flex flex-col gap-4">
      <p className="max-w-3xl text-body text-ink-3">{t("channels.description")}</p>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <div className="relative w-full sm:w-80">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder={t("insurers.search")}
            aria-label={t("insurers.search")}
            className="pl-9"
          />
        </div>
        <label className="flex min-h-10 items-center gap-2 text-caption text-ink-2">
          <Switch checked={onlyConfigured} onCheckedChange={setOnlyConfigured} />
          {t("channels.onlyConfigured")}
        </label>
      </div>

      <ErrorBanner error={list.error} />

      <Card className="overflow-hidden">
        {list.isLoading ? (
          <div className="p-5">
            <LoadingRows rows={5} />
          </div>
        ) : rows.length === 0 ? (
          <EmptyState icon={<Building2 />} title={t("channels.empty")} />
        ) : (
          <Table className="min-w-[44rem]">
            <TableHeader>
              <TableRow>
                <TableHead className="sticky left-0 z-[1] bg-bone">{t("insurers.columns.insurer")}</TableHead>
                {PAYMENT_CHANNELS.map((channel) => (
                  <TableHead key={channel}>{t(CHANNEL_LABEL[channel])}</TableHead>
                ))}
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((profile) => (
                <TableRow key={profile.insurer_id}>
                  <TableCell className="sticky left-0 z-[1] bg-bone">
                    <p className="max-w-[12rem] truncate font-medium text-ink">{profile.insurer_name}</p>
                    <p className="text-caption tabular-nums text-ink-3">{profile.insurer_rut ? formatRut(profile.insurer_rut) : "—"}</p>
                  </TableCell>
                  {PAYMENT_CHANNELS.map((channel) => {
                    const current = profile.channels.find((c) => c.channel === channel) ?? null;
                    return (
                      <TableCell key={channel} className="p-1.5">
                        <ChannelCell
                          current={current}
                          guard={guards.edit}
                          label={t("channels.edit", {
                            channel: t(CHANNEL_LABEL[channel]),
                            insurer: profile.insurer_name,
                          })}
                          onOpen={() => setTarget({ profile, channel, current })}
                          t={t}
                        />
                      </TableCell>
                    );
                  })}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </Card>

      <ChannelDialog target={target} onClose={() => setTarget(null)} />
    </div>
  );
}

function ChannelCell({
  current,
  guard,
  label,
  onOpen,
  t,
}: {
  current: PlatformChannel | null;
  guard: Guard;
  label: string;
  onOpen: () => void;
  t: TFn;
}) {
  const body = (
    <button
      type="button"
      onClick={onOpen}
      disabled={!guard.allowed}
      aria-label={label}
      className={cn(
        "flex min-h-11 w-full min-w-[9rem] flex-col items-start gap-1 rounded-lg px-2.5 py-2 text-left transition-[background-color,transform] duration-150 ease-out hover:bg-paper-2 active:scale-[0.96] disabled:pointer-events-none",
        !current && "border border-dashed border-line",
      )}
    >
      {current ? (
        <>
          <Badge variant={CHANNEL_STATUS_TONE[current.status] ?? "neutral"} dot className="px-1.5 py-0">
            {t(CHANNEL_STATUS_LABEL[current.status])}
          </Badge>
          <span className="max-w-[10rem] truncate text-[12px] text-ink-3">
            {current.provider_account_label ?? t("channels.dialog.noAccount")}
            {current.store_commerce_code ? ` · ${current.store_commerce_code}` : ""}
          </span>
        </>
      ) : (
        <span className="text-caption text-ink-3">{t("channelStatus.none")}</span>
      )}
    </button>
  );
  return guard.allowed ? body : <DisabledHint hint={guard.reason}>{body}</DisabledHint>;
}

function ChannelDialog({ target, onClose }: { target: CellTarget | null; onClose: () => void }) {
  const { t: rawT } = useTranslation("platform");
  const t = rawT as unknown as TFn;
  const accounts = useProviderAccounts();
  const save = useUpsertInsurerChannel();
  const [status, setStatus] = React.useState<ChannelStatus>("pending");
  const [accountId, setAccountId] = React.useState<string>(NONE);
  const [storeCode, setStoreCode] = React.useState("");
  const [localError, setLocalError] = React.useState<string | null>(null);

  React.useEffect(() => {
    if (!target) return;
    setStatus(target.current?.status ?? "pending");
    setAccountId(target.current?.provider_account_id ? String(target.current.provider_account_id) : NONE);
    setStoreCode(target.current?.store_commerce_code ?? "");
    setLocalError(null);
    save.reset();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [target]);

  // An insurer channel may use RADAL's mall account or that insurer's own one.
  const options = (accounts.data ?? []).filter(
    (a) => a.owner === "radal_mall" || a.insurer_id === target?.profile.insurer_id,
  );

  const onSave = () => {
    if (!target) return;
    if (status === "active" && accountId === NONE) {
      setLocalError(t("channels.dialog.activeNeedsAccount"));
      return;
    }
    save.mutate(
      {
        insurerId: target.profile.insurer_id,
        channel: target.channel,
        payload: {
          status,
          provider_account_id: accountId === NONE ? null : Number(accountId),
          store_commerce_code: storeCode.trim() || null,
        },
      },
      {
        onSuccess: () => {
          toast.success(t("channels.dialog.saved"));
          onClose();
        },
        onError: (error) => toast.error(apiError(error)),
      },
    );
  };

  const channelLabel = target ? t(CHANNEL_LABEL[target.channel]) : "";

  return (
    <Dialog open={target !== null} onOpenChange={(open) => (!open && !save.isPending ? onClose() : undefined)}>
      <DialogContent className="max-w-md">
        <DialogHeader>
          <DialogTitle>
            {t("channels.dialog.title", { channel: channelLabel, insurer: target?.profile.insurer_name ?? "" })}
          </DialogTitle>
          <DialogDescription>{t("channels.dialog.description")}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-4">
          <Field label={t("channels.dialog.status")}>
            <Select
              value={status}
              onValueChange={(v) => {
                setStatus(v as ChannelStatus);
                setLocalError(null);
              }}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {CHANNEL_STATUSES.map((s) => (
                  <SelectItem key={s} value={s}>
                    {t(CHANNEL_STATUS_LABEL[s])}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label={t("channels.dialog.account")} error={localError}>
            <Select
              value={accountId}
              onValueChange={(v) => {
                setAccountId(v);
                setLocalError(null);
              }}
              disabled={accounts.isLoading}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={NONE}>{t("channels.dialog.noAccount")}</SelectItem>
                {options.map((a) => (
                  <SelectItem key={a.id} value={String(a.id)}>
                    {a.label} · {t(a.environment === "production" ? "environment.production" : "environment.sandbox")}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label={t("channels.dialog.storeCode")} htmlFor="ch-store">
            <Input
              id="ch-store"
              value={storeCode}
              maxLength={32}
              autoComplete="off"
              className="tabular-nums"
              onChange={(e) => setStoreCode(e.target.value)}
            />
          </Field>
          {target?.current?.activated_at ? (
            <p className="text-caption text-ink-3">
              {t("channels.dialog.activatedAt", { date: formatDate(target.current.activated_at) })}
            </p>
          ) : null}
          <ErrorBanner error={save.error ?? accounts.error} />
        </div>
        <DialogFooter className="gap-2 sm:gap-0">
          <Button variant="ghost" onClick={onClose} disabled={save.isPending}>
            {t("common.cancel")}
          </Button>
          <Button onClick={onSave} disabled={save.isPending}>
            {save.isPending ? <Loader2 className="animate-spin" /> : null}
            {save.isPending ? t("common.saving") : t("common.save")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
