import { useId, useState, type KeyboardEvent } from "react";

import { checkEntry } from "../people/validation";

/**
 * A list of short text entries (interests or preferences) that can be added to and removed from.
 * It works from the keyboard: type, then press Enter or the Add button; every entry has its own
 * Remove button that names the entry. The parent owns the list and the text in the box, so it can
 * tell, when the form is sent, whether something was typed but never added.
 */
export function TagListInput({
  label,
  singular,
  items,
  draft,
  error,
  onItemsChange,
  onDraftChange,
}: {
  /** The plural name shown as the heading, for example "Interests". */
  label: string;
  /** The singular name, for example "interest". */
  singular: string;
  items: readonly string[];
  draft: string;
  /** A problem with the list as a whole, such as one reported when the form was sent. */
  error?: string;
  onItemsChange: (items: string[]) => void;
  onDraftChange: (draft: string) => void;
}) {
  const id = useId();
  const [addError, setAddError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState("");
  const shownError = addError ?? error ?? null;
  const errorId = `${id}-error`;
  const plural = label.toLowerCase();

  function add() {
    const checked = checkEntry(draft, items);
    if (!checked.ok) {
      setAddError(checked.error);
      return;
    }
    setAddError(null);
    onItemsChange([...items, checked.value]);
    onDraftChange("");
    setAnnouncement(`Added ${checked.value} to ${plural}.`);
  }

  function remove(item: string) {
    onItemsChange(items.filter((entry) => entry !== item));
    setAddError(null);
    setAnnouncement(`Removed ${item} from ${plural}.`);
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      // Enter adds the entry. It must not send the whole form.
      event.preventDefault();
      add();
    }
  }

  return (
    <fieldset className="field tag-list" aria-describedby={shownError ? errorId : undefined}>
      <legend>{label}</legend>
      {items.length === 0 ? (
        <p className="meta">None added yet.</p>
      ) : (
        <ul className="tags" aria-label={`${label} added`}>
          {items.map((item) => (
            <li key={item}>
              <span>{item}</span>
              <button type="button" className="tag-remove" aria-label={`Remove ${item} from ${plural}`} onClick={() => remove(item)}>
                <span aria-hidden="true">×</span>
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="tag-add">
        <label htmlFor={`${id}-input`}>New {singular}</label>
        <div className="row">
          <input
            id={`${id}-input`}
            type="text"
            value={draft}
            autoComplete="off"
            onChange={(event) => {
              onDraftChange(event.target.value);
              setAddError(null);
            }}
            onKeyDown={handleKeyDown}
            aria-invalid={shownError ? true : undefined}
          />
          <button type="button" aria-label={`Add ${singular}`} onClick={add}>
            Add
          </button>
        </div>
      </div>
      {shownError && (
        <p id={errorId} className="field-error" role="alert">
          {shownError}
        </p>
      )}
      <p className="visually-hidden" role="status">
        {announcement}
      </p>
    </fieldset>
  );
}
