/**
 * `/policies/:policyId` — the legacy policy route.
 *
 * The whole body lives in `components/policies/PolicyDetailBody.tsx` so
 * `pages/groups/policy.tsx` can render the identical policy inside
 * `GroupShell`. This file is deliberately nothing but the route adapter.
 *
 * `Number(undefined)` is `NaN`, which the body already treats as "no policy"
 * exactly as the previous `Number.isFinite(id) ? id : undefined` guard did.
 */
import { useParams } from "react-router-dom";
import { PolicyDetailBody } from "@/components/policies/PolicyDetailBody";

export default function PolicyDetailPage() {
  const { policyId } = useParams<{ policyId: string }>();
  return <PolicyDetailBody policyId={Number(policyId)} />;
}
