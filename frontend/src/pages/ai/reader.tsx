/**
 * Lector documental (`/ai/reader`) — spec v3 §5.4.
 *
 * A standalone door to the SAME suggest → human confirm → commit pipeline the
 * expediente already exposes, for the broker who arrives with a file in hand
 * rather than through a case:
 *
 *   1. pick a document from `GET /documents`, narrowed to the categories that
 *      actually have an extraction schema (`GET /ai/categories`) — offering a
 *      row the registry cannot read would be a dead button;
 *   2. `POST /ai/documents/extract` writes ONE `extraction` row and no domain
 *      entity;
 *   3. the human edits every field in the **shared**
 *      `components/common/SuggestionForm.tsx` — this page deliberately renders
 *      that component and never a local copy of it (docs/technical-reference.md §12.3: there are
 *      already two, a third would guarantee the drift);
 *   4. the form's own button posts `POST /ai/documents/{id}/confirm`.
 *
 * Nothing here commits anything on its own.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import { FileSearch, FileText, Sparkles } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { DataTable } from "@/components/common/DataTable";
import { SuggestionForm } from "@/components/common/SuggestionForm";
import { FadeUp } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCan } from "@/lib/permissions";
import { formatDate } from "@/lib/format";
import { useCategoryRegistry, useExtractDocument } from "@/api/ai";
import { useDocuments } from "@/api/documents";
import type {
  CategorySpec,
  DocumentCategory,
  DocumentExtractionResponse,
  RadalDocument,
} from "@/api/types";
import {
  DisabledHint,
  EmptyState,
  ErrorBanner,
  MonoChip,
  Section,
} from "@/pages/proposals/shared";

/** `GET /documents` caps `limit` at 200 (`routers/documents.py:600`). */
const PAGE_SIZE = 200;

export default function AiReaderPage() {
  const { t } = useTranslation("accounts");
  const { t: td } = useTranslation("documents");
  const { t: tc } = useTranslation("common");

  const canView = useCan("Documents", "View");
  const canAnalyze = useCan("Documents", "Upload");

  const [search, setSearch] = React.useState("");
  const [debounced, setDebounced] = React.useState("");
  const [category, setCategory] = React.useState<string>("all");
  const [active, setActive] = React.useState<RadalDocument | null>(null);
  const [result, setResult] = React.useState<DocumentExtractionResponse | null>(null);

  React.useEffect(() => {
    const id = window.setTimeout(() => setDebounced(search.trim().toLowerCase()), 300);
    return () => window.clearTimeout(id);
  }, [search]);

  const registry = useCategoryRegistry();
  const extract = useExtractDocument();

  /** Every category the registry can read, aliases folded onto their canonical. */
  const readable = React.useMemo(() => {
    const set = new Set<string>();
    for (const spec of registry.data?.items ?? []) {
      set.add(spec.category);
      set.add(spec.canonical_category);
    }
    return set;
  }, [registry.data]);

  /** One option per canonical category — the alias would be a duplicate row. */
  const options = React.useMemo<CategorySpec[]>(() => {
    const seen = new Set<string>();
    const out: CategorySpec[] = [];
    for (const spec of registry.data?.items ?? []) {
      if (spec.is_alias || seen.has(spec.canonical_category)) continue;
      seen.add(spec.canonical_category);
      out.push(spec);
    }
    return out.sort((a, b) =>
      td(`categories.${a.canonical_category}`, { defaultValue: a.canonical_category }).localeCompare(
        td(`categories.${b.canonical_category}`, { defaultValue: b.canonical_category }),
      ),
    );
  }, [registry.data, td]);

  const documents = useDocuments(
    {
      category: category === "all" ? undefined : (category as DocumentCategory),
      limit: PAGE_SIZE,
    },
    canView.allowed,
  );

  const rows = React.useMemo(() => {
    const items = documents.data?.items ?? [];
    return items.filter((doc) => {
      // The registry is what makes a row analysable; while it loads, show none
      // rather than a list of buttons that would 422.
      if (!readable.has(doc.category)) return false;
      if (!debounced) return true;
      const haystack = [doc.original_name, doc.document_code, doc.category]
        .filter(Boolean)
        .join(" ")
        .toLowerCase();
      return haystack.includes(debounced);
    });
  }, [documents.data, readable, debounced]);

  const spec = React.useMemo(() => {
    if (!result) return undefined;
    return registry.data?.items.find(
      (s) => s.category === result.category || s.canonical_category === result.canonical_category,
    );
  }, [result, registry.data]);

  const analyze = (doc: RadalDocument) => {
    setActive(doc);
    setResult(null);
    extract.mutate(
      { document_id: doc.id, category: doc.category },
      { onSuccess: (data) => setResult(data) },
    );
  };

  const columns = React.useMemo<ColumnDef<RadalDocument>[]>(
    () => [
      {
        accessorKey: "original_name",
        header: () => t("ai.reader.table.document"),
        cell: ({ row }) => (
          <div className="flex min-w-0 items-center gap-2">
            <FileText className="h-4 w-4 shrink-0 text-teal" />
            <span className="truncate text-text-primary">{row.original.original_name}</span>
          </div>
        ),
      },
      {
        accessorKey: "category",
        header: () => t("ai.reader.table.category"),
        cell: ({ row }) => (
          <Badge variant="neutral">
            {td(`categories.${row.original.category}`, { defaultValue: row.original.category })}
          </Badge>
        ),
      },
      {
        id: "code",
        header: () => t("ai.reader.table.code"),
        accessorFn: (row) => row.document_code ?? "",
        cell: ({ row }) =>
          row.original.document_code ? (
            <MonoChip>{row.original.document_code}</MonoChip>
          ) : (
            <span className="text-text-muted">—</span>
          ),
      },
      {
        id: "created",
        header: () => t("ai.reader.table.uploaded"),
        accessorFn: (row) => row.created_at ?? "",
        cell: ({ row }) => (
          <span className="text-caption text-text-muted">
            {formatDate(row.original.created_at)}
          </span>
        ),
      },
      {
        id: "action",
        header: () => "",
        cell: ({ row }) => {
          const pending = extract.isPending && active?.id === row.original.id;
          return (
            <DisabledHint
              hint={
                canAnalyze.isLoading
                  ? tc("actions.loading")
                  : canAnalyze.allowed
                    ? null
                    : t("ai.reader.noAnalyzePermission")
              }
            >
              <Button
                size="sm"
                variant="secondary"
                disabled={!canAnalyze.allowed || extract.isPending}
                onClick={() => analyze(row.original)}
              >
                <Sparkles className="h-4 w-4" />
                {pending ? t("ai.reader.analyzing") : t("ai.reader.analyze")}
              </Button>
            </DisabledHint>
          );
        },
      },
    ],
    // `analyze` is recreated every render but is stable in behaviour; the deps
    // that actually change the rendered cell are listed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [t, td, tc, canAnalyze.allowed, canAnalyze.isLoading, extract.isPending, active?.id],
  );

  if (!canView.isLoading && !canView.allowed) {
    return (
      <>
        <PageHeader title={t("nav.reader")} subtitle={t("ai.reader.subtitle")} />
        <Card>
          <EmptyState
            icon={<FileSearch className="h-6 w-6" />}
            title={t("ai.reader.noPermission")}
          />
        </Card>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={t("nav.reader")}
        subtitle={t("ai.reader.subtitle")}
        actions={
          <Badge variant="brand" className="gap-1">
            <Sparkles className="h-3 w-3" />
            {t("ai.reader.disclaimer")}
          </Badge>
        }
      />

      <FadeUp>
        <Card className="flex flex-wrap items-center gap-3 p-4">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("ai.reader.search")}
            className="h-9 max-w-xs"
          />
          <Select value={category} onValueChange={setCategory}>
            <SelectTrigger className="h-9 w-[260px]">
              <SelectValue placeholder={t("ai.reader.category")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("ai.reader.allCategories")}</SelectItem>
              {options.map((s) => (
                <SelectItem key={s.canonical_category} value={s.canonical_category}>
                  {td(`categories.${s.canonical_category}`, {
                    defaultValue: s.canonical_category,
                  })}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {debounced || category !== "all" ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setSearch("");
                setCategory("all");
              }}
            >
              {tc("actions.clear")}
            </Button>
          ) : null}
          <span className="ml-auto text-caption text-text-muted">
            {t("ai.reader.showing", {
              shown: rows.length,
              total: documents.data?.total ?? 0,
            })}
          </span>
          {/*
           * `GET /documents` caps `limit` at 200 and `category` is a single value
           * on the client, so the "todas las categorías" view filters a first
           * page rather than the whole book. Say so instead of letting a broker
           * conclude a document is missing.
           */}
          {(documents.data?.total ?? 0) > PAGE_SIZE ? (
            <span className="basis-full text-caption text-signal-warn">
              {t("ai.reader.truncated", { limit: PAGE_SIZE })}
            </span>
          ) : null}
        </Card>
      </FadeUp>

      {documents.isError ? <ErrorBanner error={documents.error} /> : null}
      {registry.isError ? <ErrorBanner error={registry.error} /> : null}
      {extract.isError ? <ErrorBanner error={extract.error} /> : null}

      <FadeUp delay={0.06}>
        <Section title={t("ai.reader.pick")} description={t("ai.reader.pickHint")}>
          {!documents.isLoading && !registry.isLoading && rows.length === 0 ? (
            <EmptyState
              icon={<FileSearch className="h-6 w-6" />}
              title={t("ai.reader.empty")}
              hint={t("ai.reader.emptyHint")}
            />
          ) : (
            <DataTable
              columns={columns}
              data={rows}
              isLoading={documents.isLoading || registry.isLoading}
            />
          )}
        </Section>
      </FadeUp>

      {result ? (
        <FadeUp delay={0.1}>
          <div className="flex flex-col gap-3">
            <div className="flex flex-wrap items-center gap-3">
              <span className="text-caption text-text-muted">{t("ai.reader.selected")}</span>
              <span className="text-body text-text-primary">{active?.original_name}</span>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setResult(null);
                  setActive(null);
                }}
              >
                {t("ai.reader.clear")}
              </Button>
            </div>
            <SuggestionForm
              result={result}
              spec={spec}
              documentName={active?.original_name ?? null}
              canConfirm={canAnalyze.allowed}
              onConfirmed={() => {
                setResult(null);
                setActive(null);
                void documents.refetch();
              }}
            />
          </div>
        </FadeUp>
      ) : null}
    </>
  );
}
