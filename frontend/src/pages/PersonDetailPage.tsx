import { useCallback } from "react";
import { Link, useParams } from "react-router";

import { getPerson, isUuid } from "../api/people";
import type { Person } from "../api/types";
import { useApi } from "../api/useApi";
import { ErrorPanel } from "../components/ErrorPanel";
import { PageHeading } from "../components/PageHeading";
import { formatDate } from "../format";
import { useFlashNotice } from "../hooks/useFlashNotice";
import { useResource } from "../hooks/useResource";
import { DeletePersonPanel } from "./DeletePersonPanel";
import { NotesSection } from "./NotesSection";
import { NotFoundPanel } from "./NotFoundPanel";

/** `/people/:personId` */
export function PersonDetailPage() {
  const { personId = "" } = useParams();
  // Keyed by the id, so moving from one person to another starts from a clean screen.
  if (!isUuid(personId)) return <NotFoundPanel />;
  return <PersonDetail key={personId} personId={personId} />;
}

function EntryList({ title, items }: { title: string; items: readonly string[] }) {
  return (
    <>
      <dt>{title}</dt>
      <dd>
        {items.length === 0 ? (
          <span className="meta">None added</span>
        ) : (
          <ul className="tags" aria-label={title}>
            {items.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        )}
      </dd>
    </>
  );
}

function PersonDetail({ personId }: { personId: string }) {
  const api = useApi();
  const notice = useFlashNotice();
  const load = useCallback((signal: AbortSignal) => getPerson(api, personId, signal), [api, personId]);
  const { state, reload } = useResource(load);

  if (state.status === "loading") {
    return (
      <>
        <PageHeading text="Person" />
        <p role="status">Loading…</p>
      </>
    );
  }
  if (state.status === "error") {
    if (state.error.kind === "not_found") return <NotFoundPanel />;
    return (
      <>
        <PageHeading text="Person" />
        <ErrorPanel error={state.error} onRetry={reload} />
        <p>
          <Link to="/people">Back to people</Link>
        </p>
      </>
    );
  }
  const person: Person = state.data;
  return (
    <>
      <PageHeading text={person.display_name} />
      {notice && (
        <p className="panel panel-success" role="status">
          {notice}
        </p>
      )}
      <p className="actions">
        <Link className="button secondary" to="/people">
          Back to people
        </Link>
        <Link className="button" to={`/people/${person.id}/edit`}>
          Edit
        </Link>
      </p>
      <dl className="details">
        <dt>Birthday</dt>
        <dd>{person.birthday ? formatDate(person.birthday) : <span className="meta">Not set</span>}</dd>
        <EntryList title="Interests" items={person.interests} />
        <EntryList title="Preferences" items={person.preferences} />
      </dl>
      <NotesSection personId={person.id} />
      <section aria-labelledby="danger-heading" className="danger-zone">
        <h2 id="danger-heading">Delete</h2>
        <DeletePersonPanel person={person} />
      </section>
    </>
  );
}
