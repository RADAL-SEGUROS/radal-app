import * as React from "react";
import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight, ChevronsUpDown } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { cn } from "@/lib/utils";

interface DataTableProps<TData, TValue> {
  columns: ColumnDef<TData, TValue>[];
  data: TData[];
  isLoading?: boolean;
  emptyMessage?: React.ReactNode;
  /** Row click handler (e.g. navigate to detail). */
  onRowClick?: (row: TData) => void;
  className?: string;
  /**
   * Controlled pagination — additive. When `total` and `onPageChange` are both
   * given, a quiet footer pager renders (caption range text + ghost icon
   * buttons). Without them the table behaves exactly as before.
   */
  pageIndex?: number;
  pageSize?: number;
  total?: number;
  onPageChange?: (pageIndex: number) => void;
}

export function DataTable<TData, TValue>({
  columns,
  data,
  isLoading,
  emptyMessage,
  onRowClick,
  className,
  pageIndex = 0,
  pageSize = 25,
  total,
  onPageChange,
}: DataTableProps<TData, TValue>) {
  const { t } = useTranslation("common");
  const [sorting, setSorting] = React.useState<SortingState>([]);

  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  const paged = total !== undefined && onPageChange !== undefined;
  const pageCount = paged ? Math.max(1, Math.ceil(total / Math.max(1, pageSize))) : 1;
  const from = paged && total > 0 ? pageIndex * pageSize + 1 : 0;
  const to = paged ? Math.min(total, (pageIndex + 1) * pageSize) : 0;

  return (
    <div
      className={cn(
        // Border does the ring (spec: hairline borders do structure; shadows
        // only whisper depth). `overflow-x-auto`, not `overflow-hidden`: a
        // table wider than its column must scroll inside its own container.
        // Clipping it hides the last columns entirely, and letting it push the
        // page makes the whole layout scroll sideways. `rounded-card` still
        // needs the clip on the y axis, which `overflow-x-auto` keeps by
        // making y `auto` as well.
        "overflow-x-auto rounded-card border border-line bg-bone",
        className,
      )}
    >
      <Table>
        <TableHeader>
          {table.getHeaderGroups().map((headerGroup) => (
            <TableRow key={headerGroup.id} className="hover:bg-transparent">
              {headerGroup.headers.map((header) => {
                const canSort = header.column.getCanSort();
                const sorted = header.column.getIsSorted();
                return (
                  <TableHead key={header.id}>
                    {header.isPlaceholder ? null : canSort ? (
                      <button
                        type="button"
                        onClick={header.column.getToggleSortingHandler()}
                        className="inline-flex items-center gap-1 text-[12px] font-medium normal-case text-ink-3 transition-[color] duration-150 hover:text-ink"
                      >
                        {flexRender(
                          header.column.columnDef.header,
                          header.getContext(),
                        )}
                        {sorted === "asc" ? (
                          <ArrowUp className="h-3.5 w-3.5" />
                        ) : sorted === "desc" ? (
                          <ArrowDown className="h-3.5 w-3.5" />
                        ) : (
                          <ChevronsUpDown className="h-3.5 w-3.5 opacity-50" />
                        )}
                      </button>
                    ) : (
                      flexRender(
                        header.column.columnDef.header,
                        header.getContext(),
                      )
                    )}
                  </TableHead>
                );
              })}
            </TableRow>
          ))}
        </TableHeader>
        <TableBody>
          {isLoading ? (
            Array.from({ length: 5 }).map((_, i) => (
              <TableRow key={`sk-${i}`} className="hover:bg-transparent">
                {columns.map((_col, j) => (
                  <TableCell key={j}>
                    <Skeleton className="h-4 w-full" />
                  </TableCell>
                ))}
              </TableRow>
            ))
          ) : table.getRowModel().rows.length ? (
            table.getRowModel().rows.map((row) => (
              <TableRow
                key={row.id}
                onClick={onRowClick ? () => onRowClick(row.original) : undefined}
                className={cn(onRowClick && "cursor-pointer")}
              >
                {row.getVisibleCells().map((cell) => (
                  <TableCell key={cell.id}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </TableCell>
                ))}
              </TableRow>
            ))
          ) : (
            <TableRow className="hover:bg-transparent">
              <TableCell
                colSpan={columns.length}
                className="h-24 text-center text-body text-ink-3"
              >
                {emptyMessage ?? t("table.empty")}
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>

      {paged ? (
        <div className="flex items-center justify-between gap-3 border-t border-line px-[18px] py-2">
          <span className="text-caption tabular-nums text-ink-3">
            {t("table.range", { from, to, total })}
          </span>
          <span className="flex items-center gap-1">
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              aria-label={t("table.prev")}
              disabled={pageIndex <= 0}
              onClick={() => onPageChange(pageIndex - 1)}
            >
              <ChevronLeft className="h-4 w-4" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="h-7 w-7"
              aria-label={t("table.next")}
              disabled={pageIndex >= pageCount - 1}
              onClick={() => onPageChange(pageIndex + 1)}
            >
              <ChevronRight className="h-4 w-4" />
            </Button>
          </span>
        </div>
      ) : null}
    </div>
  );
}
