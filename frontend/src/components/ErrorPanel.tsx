import type { ReactNode } from "react";

import type { ApiError } from "../api/client";

/**
 * Shows a failed request. The text is the fixed message for the kind of failure, never anything
 * from the response. A retry button is offered where trying again can help.
 */
export function ErrorPanel({
  error,
  onRetry,
  children,
}: {
  error: ApiError;
  onRetry?: () => void;
  children?: ReactNode;
}) {
  const canRetry = onRetry !== undefined && error.kind !== "not_found" && error.kind !== "unauthorized";
  return (
    <div className="panel panel-error" role="alert">
      <p>{error.message}</p>
      {error.requestId && <p className="meta">Reference: {error.requestId}</p>}
      {canRetry && (
        <button type="button" onClick={onRetry}>
          Try again
        </button>
      )}
      {children}
    </div>
  );
}
