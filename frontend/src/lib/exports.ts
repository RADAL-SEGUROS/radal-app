/**
 * Exports — XLSX/PDF from the server, PNG from the chart in the browser.
 *
 * Two different problems, two different answers:
 *
 *  - **Tables (XLSX, PDF)** go to the server. The tables are server-paginated
 *    50 rows at a time, so exporting what the client happens to be holding
 *    would export one page and quietly call it "the export". `POST /exports/
 *    {entity}` re-runs the SAME filters server-side and returns every matching
 *    row. The PDF comes off the branded Playwright pipeline, so an exported
 *    report looks like the rest of the broker's paperwork.
 *
 *  - **Charts (PNG)** stay in the browser. Recharts renders real SVG, so the
 *    picture already exists on the page — serialising it and painting it onto a
 *    canvas is exact and needs no dependency and no round trip. This is the
 *    format brokers actually use: a number pasted into WhatsApp.
 *
 * CSV is deliberately absent — XLSX carries types (dates as dates, UF as
 * numbers) and is what the recipient opens anyway.
 */
import api from "@/lib/api";
import { saveBlob } from "@/lib/download";

export type ExportFormat = "xlsx" | "pdf";

/** The entities `POST /exports/{entity}` knows how to render. */
export type ExportEntity =
  | "case_files"
  | "clients"
  | "quotes"
  | "proposals"
  | "policies"
  | "endorsements"
  | "collections"
  | "claims"
  | "documents"
  | "placements"
  | "inspections"
  | "offerings"
  | "leads";

export interface ExportRequest {
  format: ExportFormat;
  filters?: Record<string, unknown>;
  columns?: string[] | null;
  group_by?: string | null;
}

const EXTENSION: Record<ExportFormat, string> = { xlsx: "xlsx", pdf: "pdf" };

/** Strips `undefined`/`null`/`""` so the body carries only real filters. */
function cleanFilters(filters: Record<string, unknown> | undefined) {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(filters ?? {})) {
    if (value === undefined || value === null || value === "") continue;
    out[key] = value;
  }
  return out;
}

/**
 * Export a table. Resolves once the browser has been handed the file.
 *
 * The server caps the row count and answers 422 above it rather than timing
 * out on the buffered Lambda — let that error reach the caller so the UI can
 * say so in Spanish instead of failing silently.
 */
export async function exportTable(
  entity: ExportEntity,
  request: ExportRequest,
  filenameStem?: string,
): Promise<void> {
  const res = await api.post<Blob>(
    `/exports/${entity}`,
    {
      format: request.format,
      filters: cleanFilters(request.filters),
      columns: request.columns ?? null,
      group_by: request.group_by ?? null,
    },
    { responseType: "blob" },
  );

  const disposition = res.headers?.["content-disposition"] as string | undefined;
  const fromServer = disposition
    ? /filename="?([^";]+)"?/i.exec(disposition)?.[1]
    : null;
  const stem = filenameStem || entity;
  const fallback = `${stem}-${new Date().toISOString().slice(0, 10)}.${EXTENSION[request.format]}`;
  saveBlob(res.data, fromServer || fallback);
}

/**
 * Rasterise a chart to PNG.
 *
 * `container` is any element holding exactly one `<svg>` (a recharts
 * ResponsiveContainer). The SVG is serialised, painted onto a canvas at 2×
 * for a crisp result, and saved. Returns `false` when there is no SVG to
 * capture, so the caller can disable the control rather than fail on click.
 */
export async function exportChartPng(
  container: HTMLElement | null,
  filename: string,
  /** Painted behind the chart — charts are drawn for a light surface. */
  background = "#ffffff",
  scale = 2,
): Promise<boolean> {
  const svg = container?.querySelector("svg");
  if (!svg) return false;

  const rect = svg.getBoundingClientRect();
  const width = Math.max(1, Math.round(rect.width));
  const height = Math.max(1, Math.round(rect.height));

  // Clone so the sizing attributes we need for a standalone file never touch
  // the live chart.
  const clone = svg.cloneNode(true) as SVGSVGElement;
  clone.setAttribute("xmlns", "http://www.w3.org/2000/svg");
  clone.setAttribute("width", String(width));
  clone.setAttribute("height", String(height));

  const source = new XMLSerializer().serializeToString(clone);
  const blobUrl = URL.createObjectURL(
    new Blob([source], { type: "image/svg+xml;charset=utf-8" }),
  );

  try {
    const image = await new Promise<HTMLImageElement>((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new Error("chart svg failed to load"));
      img.src = blobUrl;
    });

    const canvas = document.createElement("canvas");
    canvas.width = width * scale;
    canvas.height = height * scale;
    const ctx = canvas.getContext("2d");
    if (!ctx) return false;
    ctx.fillStyle = background;
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(image, 0, 0, canvas.width, canvas.height);

    const png = await new Promise<Blob | null>((resolve) =>
      canvas.toBlob(resolve, "image/png"),
    );
    if (!png) return false;
    saveBlob(png, filename.endsWith(".png") ? filename : `${filename}.png`);
    return true;
  } finally {
    URL.revokeObjectURL(blobUrl);
  }
}
