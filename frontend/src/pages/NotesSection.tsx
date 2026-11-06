import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";

import type { ApiError } from "../api/client";
import { createNote, listNotes, NOTES_PAGE_SIZE } from "../api/notes";
import type { Note } from "../api/types";
import { useApi } from "../api/useApi";
import { ErrorPanel } from "../components/ErrorPanel";
import { formatTimestamp } from "../format";
import { toApiError } from "../hooks/useResource";
import { checkNoteContent, NOTE_MAX } from "../notes/validation";
import { countCharacters } from "../people/validation";

type Loaded = { status: "loading" } | { status: "error"; error: ApiError } | { status: "ready"; items: Note[]; hasMore: boolean };

/** Appends `incoming` to `current`, skipping any note already shown (the list can shift while someone adds notes). */
function mergeNotes(current: readonly Note[], incoming: readonly Note[]): Note[] {
  const seen = new Set(current.map((note) => note.id));
  return [...current, ...incoming.filter((note) => !seen.has(note.id))];
}

/**
 * The notes about one person: add a note, and read them newest first, 20 at a time. Note text is
 * kept only in component state. It is never written to browser storage, never logged, and is
 * shown as plain text (never as HTML).
 */
export function NotesSection({ personId }: { personId: string }) {
  const api = useApi();
  const [loaded, setLoaded] = useState<Loaded>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<ApiError | null>(null);
  const [draft, setDraft] = useState("");
  const [draftError, setDraftError] = useState<string | null>(null);
  const [saveError, setSaveError] = useState<ApiError | null>(null);
  const [saving, setSaving] = useState(false);
  const [added, setAdded] = useState(false);
  const textarea = useRef<HTMLTextAreaElement>(null);
  const writeController = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    listNotes(api, personId, { limit: NOTES_PAGE_SIZE, offset: 0 }, controller.signal).then(
      (items) => {
        if (!controller.signal.aborted) setLoaded({ status: "ready", items, hasMore: items.length === NOTES_PAGE_SIZE });
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        const apiError = toApiError(error);
        if (apiError.kind !== "aborted") setLoaded({ status: "error", error: apiError });
      },
    );
    return () => controller.abort();
  }, [api, personId, attempt]);
  useEffect(() => () => writeController.current?.abort(), []);
  // The box is disabled while saving, so focus goes back to it once it is enabled again.
  useEffect(() => {
    if (added) textarea.current?.focus();
  }, [added]);

  const retry = useCallback(() => {
    setLoaded({ status: "loading" });
    setAttempt((value) => value + 1);
  }, []);

  async function loadMore() {
    if (loaded.status !== "ready" || loadingMore) return;
    setLoadingMore(true);
    setMoreError(null);
    try {
      // The next page starts after everything shown, including notes added during this visit.
      const page = await listNotes(api, personId, { limit: NOTES_PAGE_SIZE, offset: loaded.items.length });
      setLoaded((previous) =>
        previous.status === "ready"
          ? { status: "ready", items: mergeNotes(previous.items, page), hasMore: page.length === NOTES_PAGE_SIZE }
          : previous,
      );
    } catch (error) {
      setMoreError(toApiError(error));
    } finally {
      setLoadingMore(false);
    }
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (saving) return;
    setSaveError(null);
    setAdded(false);
    const checked = checkNoteContent(draft);
    if (!checked.ok) {
      setDraftError(checked.error);
      return;
    }
    setDraftError(null);
    const abort = new AbortController();
    writeController.current = abort;
    setSaving(true);
    try {
      const note = await createNote(api, personId, checked.value, abort.signal);
      if (abort.signal.aborted) return;
      setLoaded((previous) =>
        previous.status === "ready" ? { ...previous, items: [note, ...previous.items.filter((n) => n.id !== note.id)] } : previous,
      );
      setDraft("");
      setAdded(true);
    } catch (error) {
      if (abort.signal.aborted) return;
      setSaveError(toApiError(error));
    } finally {
      setSaving(false);
    }
  }

  const count = countCharacters(draft);

  return (
    <section aria-labelledby="notes-heading" className="notes-section">
      <h2 id="notes-heading">Notes</h2>
      <form onSubmit={handleSubmit} noValidate aria-busy={saving}>
        <div className="field">
          <label htmlFor="new-note">New note</label>
          <textarea
            id="new-note"
            ref={textarea}
            rows={4}
            value={draft}
            onChange={(event) => {
              setDraft(event.target.value);
              setAdded(false);
            }}
            aria-invalid={draftError ? true : undefined}
            aria-describedby={draftError ? "new-note-count new-note-error" : "new-note-count"}
            disabled={saving}
          />
          <p id="new-note-count" className="meta">
            {count.toLocaleString("en-US")} of {NOTE_MAX.toLocaleString("en-US")} characters
          </p>
          {draftError && (
            <p id="new-note-error" className="field-error" role="alert">
              {draftError}
            </p>
          )}
        </div>
        {saveError && <ErrorPanel error={saveError} />}
        {added && (
          <p className="panel panel-success" role="status">
            Note added.
          </p>
        )}
        <button type="submit" disabled={saving}>
          {saving ? "Adding…" : "Add note"}
        </button>
      </form>

      {loaded.status === "loading" && <p role="status">Loading notes…</p>}
      {loaded.status === "error" && <ErrorPanel error={loaded.error} onRetry={retry} />}
      {loaded.status === "ready" && loaded.items.length === 0 && (
        <p className="panel">No notes yet. Write the first one above.</p>
      )}
      {loaded.status === "ready" && loaded.items.length > 0 && (
        <ol className="notes" aria-label="Notes, newest first">
          {loaded.items.map((note) => (
            <li key={note.id}>
              <article>
                <p className="note-text">{note.content}</p>
                <p className="meta">
                  <time dateTime={note.created_at}>{formatTimestamp(note.created_at)}</time>
                </p>
              </article>
            </li>
          ))}
        </ol>
      )}
      {loaded.status === "ready" && loaded.hasMore && (
        <div>
          {moreError && <ErrorPanel error={moreError} />}
          <button type="button" className="secondary" onClick={loadMore} disabled={loadingMore}>
            {loadingMore ? "Loading…" : "Load more notes"}
          </button>
        </div>
      )}
    </section>
  );
}
