import { templateCsv } from "@/lib/schema";

/**
 * The CSV template.
 *
 * Served from the schema constant rather than a static file, so the template a
 * user downloads cannot drift from the columns the mapper and the reference
 * table describe.
 */
export function GET() {
  return new Response(templateCsv(), {
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": 'attachment; filename="routebench-template.csv"',
      "Cache-Control": "public, max-age=3600",
    },
  });
}
