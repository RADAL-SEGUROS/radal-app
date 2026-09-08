/**
 * The premium movement of one endorsement.
 *
 * The invariant is the house arithmetic applied to DELTAS, sign preserved:
 *
 *     net_delta   = taxable_delta + exempt_delta
 *     vat_delta   = 0.19 * taxable_delta        (never on the net)
 *     total_delta = net_delta + vat_delta
 *
 * Two cases this card must get right:
 *   - a NEGATIVE movement (exclusion, sum-insured decrease) is normal and keeps
 *     its minus sign — never an absolute value;
 *   - an ALL-ZERO movement (pledge update, policyholder change) is a valid
 *     administrative endorsement and renders as "sin efecto en prima", not as
 *     an error.
 */
import { useTranslation } from "react-i18next";
import { AlertTriangle, Equal, TrendingDown, TrendingUp } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { num, type Endorsement } from "@/api/types";
import { KeyValue, Section, uf } from "@/pages/proposals/shared";
import { DeltaAmount, offBy } from "@/pages/policies/shared";

export function PremiumDeltaCard({ endorsement }: { endorsement: Endorsement }) {
  const { t } = useTranslation("postsale");

  const taxable = num(endorsement.taxable_premium_delta_uf) ?? 0;
  const exempt = num(endorsement.exempt_premium_delta_uf) ?? 0;
  const net = num(endorsement.net_premium_delta_uf) ?? 0;
  const vat = num(endorsement.vat_delta_uf) ?? 0;
  const total = num(endorsement.total_premium_delta_uf) ?? 0;

  const derivedNet = taxable + exempt;
  const derivedVat = taxable * 0.19;
  const derivedTotal = derivedNet + derivedVat;

  const broken =
    offBy(net, derivedNet) || offBy(vat, derivedVat) || offBy(total, derivedTotal);

  // "No premium effect" is about the premium only: an administrative endorsement
  // may still move the insured amount, and that is shown separately below.
  const allZero = [
    taxable,
    exempt,
    net,
    vat,
    total,
    num(endorsement.commission_delta_uf) ?? 0,
  ].every((value) => Math.abs(value) < 0.005);

  const direction =
    total > 0.005 ? "increase" : total < -0.005 ? "decrease" : null;

  return (
    <Section
      title={t("premiumDelta.title")}
      description={t("premiumDelta.rule")}
      actions={
        allZero ? (
          <Badge variant="neutral" className="gap-1">
            <Equal className="h-3 w-3" />
            {t("premiumDelta.noEffect")}
          </Badge>
        ) : broken ? (
          <Badge variant="danger" className="gap-1">
            <AlertTriangle className="h-3 w-3" />
            {t("premiumDelta.mismatch")}
          </Badge>
        ) : direction === "increase" ? (
          <Badge variant="warn" className="gap-1">
            <TrendingUp className="h-3 w-3" />
            {t("premiumDelta.increase")}
          </Badge>
        ) : direction === "decrease" ? (
          <Badge variant="success" className="gap-1">
            <TrendingDown className="h-3 w-3" />
            {t("premiumDelta.decrease")}
          </Badge>
        ) : null
      }
    >
      {allZero ? (
        <p className="mb-4 text-body text-text-secondary">{t("premiumDelta.noEffectHint")}</p>
      ) : null}

      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-6">
        <KeyValue
          label={t("premiumDelta.taxable")}
          value={<DeltaAmount value={endorsement.taxable_premium_delta_uf} />}
        />
        <KeyValue
          label={t("premiumDelta.exempt")}
          value={<DeltaAmount value={endorsement.exempt_premium_delta_uf} />}
        />
        <KeyValue
          label={t("premiumDelta.net")}
          value={<DeltaAmount value={endorsement.net_premium_delta_uf} />}
          tone={offBy(net, derivedNet) ? "danger" : "default"}
        />
        <KeyValue
          label={t("premiumDelta.vat")}
          value={<DeltaAmount value={endorsement.vat_delta_uf} />}
          tone={offBy(vat, derivedVat) ? "danger" : "default"}
        />
        <KeyValue
          label={t("premiumDelta.total")}
          value={<DeltaAmount value={endorsement.total_premium_delta_uf} />}
          tone={offBy(total, derivedTotal) ? "danger" : "default"}
        />
        <KeyValue
          label={t("premiumDelta.commission")}
          value={<DeltaAmount value={endorsement.commission_delta_uf} />}
        />
      </div>

      <div className="mt-4 flex flex-wrap items-center gap-4">
        <KeyValue
          label={t("endorsement.fields.insuredAmountDelta")}
          value={<DeltaAmount value={endorsement.insured_amount_delta_uf} decimals={0} />}
        />
        {endorsement.prorata_days !== null || endorsement.unexpired_days !== null ? (
          <span className="text-caption text-text-muted">
            {t("premiumDelta.prorata", {
              days: endorsement.prorata_days ?? "—",
              unexpired: endorsement.unexpired_days ?? "—",
            })}
          </span>
        ) : null}
      </div>

      {broken && !allZero ? (
        <p className="mt-3 text-caption text-signal-danger">
          {t("premiumDelta.net")}: {uf(derivedNet)} · {t("premiumDelta.vat")}: {uf(derivedVat)} ·{" "}
          {t("premiumDelta.total")}: {uf(derivedTotal)}
        </p>
      ) : null}
    </Section>
  );
}
