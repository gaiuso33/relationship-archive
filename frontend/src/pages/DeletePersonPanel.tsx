import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";

import type { ApiError } from "../api/client";
import { deletePerson } from "../api/people";
import type { Person } from "../api/types";
import { useApi } from "../api/useApi";
import { ErrorPanel } from "../components/ErrorPanel";
import { toApiError } from "../hooks/useResource";

/**
 * Deleting a person is permanent and also deletes their notes, so it takes a second, explicit
 * step. Opening the confirmation moves focus to Cancel (the safe choice); cancelling puts focus
 * back on the button that opened it.
 */
export function DeletePersonPanel({ person }: { person: Person }) {
  const api = useApi();
  const navigate = useNavigate();
  const [confirming, setConfirming] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const openButton = useRef<HTMLButtonElement>(null);
  const cancelButton = useRef<HTMLButtonElement>(null);
  const controller = useRef<AbortController | null>(null);

  useEffect(() => {
    if (confirming) cancelButton.current?.focus();
  }, [confirming]);
  useEffect(() => () => controller.current?.abort(), []);

  function cancel() {
    setConfirming(false);
    setError(null);
    // The button is rendered again once the confirmation closes, so focus it on the next frame.
    requestAnimationFrame(() => openButton.current?.focus());
  }

  async function confirmDelete() {
    if (deleting) return;
    const abort = new AbortController();
    controller.current = abort;
    setDeleting(true);
    setError(null);
    try {
      await deletePerson(api, person.id, abort.signal);
      navigate("/people", { state: { notice: "person_deleted" } });
    } catch (caught) {
      if (abort.signal.aborted) return;
      setError(toApiError(caught));
      setDeleting(false);
    }
  }

  if (!confirming) {
    return (
      <button ref={openButton} type="button" className="danger-outline" onClick={() => setConfirming(true)}>
        Delete person
      </button>
    );
  }
  return (
    <section className="panel panel-danger" role="group" aria-labelledby="delete-title" aria-describedby="delete-warning">
      <h2 id="delete-title">Delete {person.display_name}?</h2>
      <p id="delete-warning">
        This permanently deletes this person and all of their notes. It cannot be undone.
      </p>
      {error && <ErrorPanel error={error} />}
      <div className="actions">
        <button type="button" className="danger" onClick={confirmDelete} disabled={deleting}>
          {deleting ? "Deleting…" : "Delete permanently"}
        </button>
        <button ref={cancelButton} type="button" className="secondary" onClick={cancel} disabled={deleting}>
          Cancel
        </button>
      </div>
    </section>
  );
}
