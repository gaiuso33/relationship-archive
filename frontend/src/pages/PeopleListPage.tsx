import { useCallback, useRef, useState, type FormEvent } from "react";
import { Link } from "react-router";

import { listPeople, searchPeople } from "../api/people";
import { useApi } from "../api/useApi";
import { ErrorPanel } from "../components/ErrorPanel";
import { PageHeading } from "../components/PageHeading";
import { formatDate } from "../format";
import { useFlashNotice } from "../hooks/useFlashNotice";
import { useResource } from "../hooks/useResource";
import { checkSearchQuery } from "../people/validation";

/** One run of the list or of a search. `run` changes on every submission, so each one starts fresh. */
interface Search {
  query: string | null;
  run: number;
}

export function PeopleListPage() {
  const notice = useFlashNotice();
  // What is typed, and what was last searched for. Both live only in this component: the text is
  // personal data, so it is never put in the URL, in browser storage or in the history.
  const [draft, setDraft] = useState("");
  const [search, setSearch] = useState<Search>({ query: null, run: 0 });
  const [draftError, setDraftError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const checked = checkSearchQuery(draft);
    if (!checked.ok) {
      setDraftError(checked.error);
      return;
    }
    setDraftError(null);
    setSearch((previous) => ({ query: checked.value, run: previous.run + 1 }));
  }

  function handleClear() {
    setDraft("");
    setDraftError(null);
    setSearch((previous) => ({ query: null, run: previous.run + 1 }));
    // The Clear button disappears, so keyboard users are put back in the box.
    input.current?.focus();
  }

  return (
    <>
      <PageHeading text="People" />
      {notice && (
        <p className="panel panel-success" role="status">
          {notice}
        </p>
      )}
      <p>
        <Link className="button" to="/people/new">
          Add person
        </Link>
      </p>
      <form role="search" className="search" onSubmit={handleSubmit} noValidate>
        <label htmlFor="people-search">Search by name</label>
        <div className="row">
          <input
            id="people-search"
            ref={input}
            type="search"
            value={draft}
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            enterKeyHint="search"
            onChange={(event) => {
              setDraft(event.target.value);
              setDraftError(null);
            }}
            aria-invalid={draftError ? true : undefined}
            aria-describedby={draftError ? "people-search-error" : undefined}
          />
          <button type="submit">Search</button>
          {(search.query !== null || draft !== "") && (
            <button type="button" className="secondary" onClick={handleClear}>
              Clear
            </button>
          )}
        </div>
        {draftError && (
          <p id="people-search-error" className="field-error" role="alert">
            {draftError}
          </p>
        )}
      </form>
      <PeopleResults key={search.run} query={search.query} />
    </>
  );
}

/**
 * The list, or the result of one search. It is keyed by the run, so starting another search
 * removes this component: its request is cancelled and its answer can never replace a newer one.
 */
function PeopleResults({ query }: { query: string | null }) {
  const api = useApi();
  const load = useCallback(
    (signal: AbortSignal) => (query === null ? listPeople(api, signal) : searchPeople(api, query, signal)),
    [api, query],
  );
  const { state, reload } = useResource(load);

  if (state.status === "loading") return <p role="status">{query === null ? "Loading people…" : "Searching…"}</p>;
  if (state.status === "error") {
    if (state.error.kind === "invalid" && state.error.status === 422) {
      // Trying the same text again cannot help, so no retry is offered.
      return (
        <p className="panel panel-error" role="alert">
          That text cannot be searched for. Remove any unusual characters and try again.
        </p>
      );
    }
    return <ErrorPanel error={state.error} onRetry={reload} />;
  }

  const people = state.data;
  if (people.length === 0) {
    if (query !== null) {
      return (
        <div className="panel">
          <p role="status">No one matches “{query}”.</p>
          <p className="meta">Search looks at names only, not notes, interests or preferences.</p>
        </div>
      );
    }
    return (
      <div className="panel">
        <p>You have not added anyone yet.</p>
        <p>
          <Link to="/people/new">Add your first person</Link>
        </p>
      </div>
    );
  }
  return (
    <>
      {query !== null && (
        <p role="status">
          {people.length === 1 ? "1 person matches" : `${people.length} people match`} “{query}”.
        </p>
      )}
      <ul className="list" aria-label="People">
        {people.map((person) => (
          <li key={person.id}>
            <Link to={`/people/${person.id}`}>{person.display_name}</Link>
            {person.birthday && <span className="meta"> Birthday: {formatDate(person.birthday)}</span>}
          </li>
        ))}
      </ul>
    </>
  );
}
