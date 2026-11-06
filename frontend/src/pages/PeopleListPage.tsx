import { useCallback } from "react";
import { Link } from "react-router";

import { listPeople } from "../api/people";
import { useApi } from "../api/useApi";
import { ErrorPanel } from "../components/ErrorPanel";
import { PageHeading } from "../components/PageHeading";
import { useFlashNotice } from "../hooks/useFlashNotice";
import { useResource } from "../hooks/useResource";
import { formatDate } from "../format";

export function PeopleListPage() {
  const api = useApi();
  const notice = useFlashNotice();
  const load = useCallback((signal: AbortSignal) => listPeople(api, signal), [api]);
  const { state, reload } = useResource(load);

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
      {state.status === "loading" && <p role="status">Loading people…</p>}
      {state.status === "error" && <ErrorPanel error={state.error} onRetry={reload} />}
      {state.status === "ready" && state.data.length === 0 && (
        <div className="panel">
          <p>You have not added anyone yet.</p>
          <p>
            <Link to="/people/new">Add your first person</Link>
          </p>
        </div>
      )}
      {state.status === "ready" && state.data.length > 0 && (
        <ul className="list" aria-label="People">
          {state.data.map((person) => (
            <li key={person.id}>
              <Link to={`/people/${person.id}`}>{person.display_name}</Link>
              {person.birthday && <span className="meta"> Birthday: {formatDate(person.birthday)}</span>}
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
