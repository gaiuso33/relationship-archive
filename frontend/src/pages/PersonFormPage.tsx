import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { ApiError } from "../api/client";
import { createPerson, getPerson, isUuid, updatePerson } from "../api/people";
import type { Person, PersonChanges } from "../api/types";
import { useApi } from "../api/useApi";
import { ErrorPanel } from "../components/ErrorPanel";
import { PageHeading } from "../components/PageHeading";
import { TagListInput } from "../components/TagListInput";
import { todayIso } from "../format";
import { toApiError, useResource } from "../hooks/useResource";
import { checkPersonForm, EARLIEST_BIRTHDAY, type PersonFormErrors } from "../people/validation";
import { NotFoundPanel } from "./NotFoundPanel";

/** `/people/new` */
export function CreatePersonPage() {
  return <PersonForm mode="create" />;
}

/** `/people/:personId/edit` */
export function EditPersonPage() {
  const { personId = "" } = useParams();
  if (!isUuid(personId)) return <NotFoundPanel />;
  return <EditLoader key={personId} personId={personId} />;
}

function EditLoader({ personId }: { personId: string }) {
  const api = useApi();
  const load = useCallback((signal: AbortSignal) => getPerson(api, personId, signal), [api, personId]);
  const { state, reload } = useResource(load);

  if (state.status === "loading") {
    return (
      <>
        <PageHeading text="Edit person" />
        <p role="status">Loading…</p>
      </>
    );
  }
  if (state.status === "error") {
    if (state.error.kind === "not_found") return <NotFoundPanel />;
    return (
      <>
        <PageHeading text="Edit person" />
        <ErrorPanel error={state.error} onRetry={reload} />
      </>
    );
  }
  return <PersonForm mode="edit" person={state.data} />;
}

function sameList(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((value, index) => value === b[index]);
}

const SERVER_FIELD_MESSAGES = {
  display_name: "That name was not accepted.",
  birthday: "That date was not accepted.",
} as const;

/** Turns the API's `fields` into messages beside the right inputs. Only the place and kind of problem are used. */
function serverFieldErrors(error: ApiError): { fields: PersonFormErrors; general: string | null } {
  const fields: PersonFormErrors = {};
  let general: string | null = null;
  for (const { loc } of error.fields) {
    const name = loc[1];
    const index = loc[2];
    if (name === "display_name" || name === "birthday") {
      fields[name === "display_name" ? "displayName" : "birthday"] = SERVER_FIELD_MESSAGES[name];
    } else if (name === "interests" || name === "preferences") {
      const word = name === "interests" ? "interest" : "preference";
      fields[name] =
        typeof index === "number"
          ? `The ${word} in position ${index + 1} was not accepted.`
          : `The ${name} were not accepted.`;
    } else {
      general = "Some details were not accepted.";
    }
  }
  if (error.fields.length === 0) general = "Some details were not accepted.";
  return { fields, general };
}

function PersonForm({ mode, person }: { mode: "create" | "edit"; person?: Person }) {
  const api = useApi();
  const navigate = useNavigate();
  const [displayName, setDisplayName] = useState(person?.display_name ?? "");
  const [birthday, setBirthday] = useState(person?.birthday ?? "");
  const [interests, setInterests] = useState<string[]>(person?.interests ?? []);
  const [interestsDraft, setInterestsDraft] = useState("");
  const [preferences, setPreferences] = useState<string[]>(person?.preferences ?? []);
  const [preferencesDraft, setPreferencesDraft] = useState("");
  const [errors, setErrors] = useState<PersonFormErrors>({});
  const [formError, setFormError] = useState<ApiError | null>(null);
  const [generalError, setGeneralError] = useState<string | null>(null);
  const [noChanges, setNoChanges] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const controller = useRef<AbortController | null>(null);

  // A request still in flight when the user leaves the form is cancelled.
  useEffect(() => () => controller.current?.abort(), []);

  const heading = mode === "create" ? "Add person" : "Edit person";
  const cancelTo = person ? `/people/${person.id}` : "/people";

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    setFormError(null);
    setGeneralError(null);
    setNoChanges(false);

    const checked = checkPersonForm(
      { displayName, birthday, interests, interestsDraft, preferences, preferencesDraft },
      todayIso(),
    );
    if (!checked.ok) {
      setErrors(checked.errors);
      return;
    }
    setErrors({});

    let changes: PersonChanges | null = null;
    if (mode === "edit" && person) {
      // Only what changed is sent, and never the time zone: this form does not touch it.
      const diff: PersonChanges = {};
      if (checked.value.display_name !== person.display_name) diff.display_name = checked.value.display_name;
      if (checked.value.birthday !== person.birthday) diff.birthday = checked.value.birthday;
      if (!sameList(checked.value.interests, person.interests)) diff.interests = checked.value.interests;
      if (!sameList(checked.value.preferences, person.preferences)) diff.preferences = checked.value.preferences;
      if (Object.keys(diff).length === 0) {
        setNoChanges(true);
        return;
      }
      changes = diff;
    }

    const abort = new AbortController();
    controller.current = abort;
    setSubmitting(true);
    try {
      if (mode === "edit" && person && changes) {
        await updatePerson(api, person.id, changes, abort.signal);
        navigate(`/people/${person.id}`, { state: { notice: "person_updated" } });
      } else {
        const created = await createPerson(api, checked.value, abort.signal);
        navigate(`/people/${created.id}`, { state: { notice: "person_created" } });
      }
    } catch (error) {
      if (abort.signal.aborted) return;
      const apiError = toApiError(error);
      if (apiError.kind === "invalid" && apiError.status === 422) {
        const mapped = serverFieldErrors(apiError);
        setErrors(mapped.fields);
        setGeneralError(mapped.general);
      } else {
        setFormError(apiError);
      }
      setSubmitting(false);
    }
  }

  return (
    <>
      <PageHeading text={heading} />
      <form onSubmit={handleSubmit} noValidate aria-busy={submitting}>
        <div className="field">
          <label htmlFor="display-name">Name</label>
          <input
            id="display-name"
            type="text"
            autoComplete="off"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            aria-invalid={errors.displayName ? true : undefined}
            aria-describedby={errors.displayName ? "display-name-error" : undefined}
            disabled={submitting}
          />
          {errors.displayName && (
            <p id="display-name-error" className="field-error" role="alert">
              {errors.displayName}
            </p>
          )}
        </div>
        <div className="field">
          <label htmlFor="birthday">Birthday (optional)</label>
          <input
            id="birthday"
            type="date"
            min={EARLIEST_BIRTHDAY}
            max={todayIso()}
            value={birthday}
            onChange={(event) => setBirthday(event.target.value)}
            aria-invalid={errors.birthday ? true : undefined}
            aria-describedby={errors.birthday ? "birthday-error" : undefined}
            disabled={submitting}
          />
          {errors.birthday && (
            <p id="birthday-error" className="field-error" role="alert">
              {errors.birthday}
            </p>
          )}
        </div>
        <TagListInput
          label="Interests"
          singular="interest"
          items={interests}
          draft={interestsDraft}
          error={errors.interests}
          onItemsChange={setInterests}
          onDraftChange={setInterestsDraft}
        />
        <TagListInput
          label="Preferences"
          singular="preference"
          items={preferences}
          draft={preferencesDraft}
          error={errors.preferences}
          onItemsChange={setPreferences}
          onDraftChange={setPreferencesDraft}
        />
        {generalError && (
          <p className="panel panel-error" role="alert">
            {generalError}
          </p>
        )}
        {formError && <ErrorPanel error={formError} />}
        {noChanges && (
          <p className="panel" role="status">
            No changes to save.
          </p>
        )}
        <div className="actions">
          <button type="submit" disabled={submitting}>
            {submitting ? "Saving…" : mode === "create" ? "Add person" : "Save changes"}
          </button>
          <Link className="button secondary" to={cancelTo}>
            Cancel
          </Link>
        </div>
      </form>
    </>
  );
}
