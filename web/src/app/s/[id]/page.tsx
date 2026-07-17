import SessionView from "@/components/SessionView";

/**
 * A session, addressable by its unguessable URL.
 *
 * Resumable: everything the page needs comes from the id, so a link works in a
 * new tab, tomorrow, on another machine — there are no accounts and no local
 * state to lose.
 */
export default async function SessionPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <SessionView sessionId={id} />;
}
