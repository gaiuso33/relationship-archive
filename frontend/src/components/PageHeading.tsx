import { useEffect, useRef } from "react";

import { useDocumentTitle } from "../hooks/useDocumentTitle";

/**
 * The screen's one `h1`. It also sets the browser-tab title and takes keyboard focus when the
 * screen (or its text) changes, so keyboard and screen reader users start at the top of the new
 * screen instead of on a link that is no longer there.
 */
export function PageHeading({ text }: { text: string }) {
  const ref = useRef<HTMLHeadingElement>(null);
  useDocumentTitle(text);
  useEffect(() => {
    ref.current?.focus();
  }, [text]);
  return (
    <h1 ref={ref} tabIndex={-1}>
      {text}
    </h1>
  );
}
