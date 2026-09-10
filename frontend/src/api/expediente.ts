/**
 * Expediente completo — the grupo-cuenta's super-overview.
 *
 * `GET /case-files/{id}/expediente` is a READ AGGREGATE: identity, empresas,
 * the journey with its real completion dates, antecedentes, comparación,
 * propuesta, pólizas, documents and money, in one payload. It replaces what the
 * "Ver expediente completo" button used to do — jump into the deprecated flat
 * `/cases/:id` table.
 *
 * Two properties the server guarantees and this page depends on:
 *
 *  - **It is always current.** Nothing is cached into a stored artifact; the
 *    aggregate is computed per request and the PDF renders from the same call,
 *    so the download can never disagree with the screen.
 *  - **Absence is explained.** Every block that has no data yet carries a
 *    Spanish `pending_reason` ("Pronto, al cerrar Comparación"), so the page
 *    never shows a blank where the broker cannot tell "empty" from "broken".
 */
import { useQuery } from "@tanstack/react-query";
import api from "@/lib/api";
import { qk } from "@/api/keys";
import { downloadFrom } from "@/lib/download";
import type { AccountExpediente } from "@/api/types";

export function useExpedienteCompleto(caseId: number | undefined) {
  return useQuery({
    queryKey: [...qk.caseFiles.detail(caseId ?? 0), "expediente"] as const,
    enabled: !!caseId,
    queryFn: async () => {
      const { data } = await api.get<AccountExpediente>(
        `/case-files/${caseId}/expediente`,
      );
      return data;
    },
  });
}

/**
 * Download the branded PDF of the whole expediente.
 *
 * Rendered on demand server-side (Playwright), so it is generated from the same
 * aggregate the page is showing. It can take a few seconds — the caller owns
 * the pending state.
 */
export async function downloadExpedientePdf(
  caseId: number,
  filename: string,
): Promise<void> {
  await downloadFrom(`/case-files/${caseId}/expediente/pdf`, filename);
}
