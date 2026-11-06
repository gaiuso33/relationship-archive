import { useCallback, useEffect, useState } from "react";

import { ApiError } from "../api/client";

export type ResourceState<T> =
  | { status: "loading" }
  | { status: "error"; error: ApiError }
  | { status: "ready"; data: T };

export function toApiError(error: unknown): ApiError {
  // Anything that is not an ApiError is shown as a generic failure: its text is never used.
  return error instanceof ApiError ? error : new ApiError("unexpected");
}

/**
 * Loads something when the screen appears and again on `reload()`. A request that is still
 * running when the screen goes away (or is reloaded) is cancelled. `load` must be stable
 * (wrap it in `useCallback`), because a new `load` starts a new request.
 */
export function useResource<T>(load: (signal: AbortSignal) => Promise<T>): {
  state: ResourceState<T>;
  reload: () => void;
} {
  const [state, setState] = useState<ResourceState<T>>({ status: "loading" });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setState({ status: "ready", data });
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        const apiError = toApiError(error);
        if (apiError.kind !== "aborted") setState({ status: "error", error: apiError });
      },
    );
    return () => controller.abort();
  }, [load, attempt]);

  const reload = useCallback(() => {
    setState({ status: "loading" });
    setAttempt((value) => value + 1);
  }, []);

  return { state, reload };
}
