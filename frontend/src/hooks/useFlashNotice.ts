import { useEffect, useState } from "react";
import { useLocation, useNavigate } from "react-router";

const NOTICES = {
  person_created: "Person added.",
  person_updated: "Changes saved.",
  person_deleted: "Person deleted.",
} as const;

export type NoticeKey = keyof typeof NOTICES;

export function isNoticeKey(value: unknown): value is NoticeKey {
  return typeof value === "string" && Object.hasOwn(NOTICES, value);
}

/**
 * A one-time message passed through navigation, such as "Person added.". Only a fixed key travels
 * in the history entry (never a name or any text from the archive), and the key is looked up in
 * a fixed table.
 */
export function useFlashNotice(): string | null {
  const location = useLocation();
  const navigate = useNavigate();
  const [notice] = useState<string | null>(() => {
    const key = (location.state as { notice?: unknown } | null)?.notice;
    return isNoticeKey(key) ? NOTICES[key] : null;
  });
  // Remove the key from the history entry, so reloading the page does not show the message again.
  const hasKey = notice !== null;
  const { pathname, search } = location;
  useEffect(() => {
    if (hasKey) navigate({ pathname, search }, { replace: true, state: null });
  }, [hasKey, navigate, pathname, search]);
  return notice;
}
