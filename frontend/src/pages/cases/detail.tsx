/**
 * `/cases/:caseId` — the legacy expediente route.
 *
 * The whole body lives in `components/cases/CaseDetailBody.tsx` so the group
 * family (`pages/groups/account.tsx`) can render the identical expediente
 * inside `GroupShell`. This file is deliberately nothing but the route
 * adapter: it reads the param and hands the id over.
 */
import { useParams } from "react-router-dom";
import { CaseDetailBody } from "@/components/cases/CaseDetailBody";

export default function CaseDetailPage() {
  const { caseId: caseIdParam } = useParams();
  return <CaseDetailBody caseId={Number(caseIdParam)} />;
}
