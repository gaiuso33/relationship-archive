import { useEffect } from "react";

const APP_NAME = "Relationship Archive";

/** Gives each screen its own browser-tab title, which also helps screen reader users know where they are. */
export function useDocumentTitle(title: string | null): void {
  useEffect(() => {
    document.title = title ? `${title} · ${APP_NAME}` : APP_NAME;
  }, [title]);
}
