import { useMemo, type ReactNode } from "react";

import { useAuth } from "../auth/useAuth";
import { ApiContext } from "./apiContext";
import { createApiClient } from "./client";

/** Gives screens an API client wired to the current session. Must sit inside `AuthProvider`. */
export function ApiProvider({
  baseUrl,
  fetchImpl,
  children,
}: {
  baseUrl: string;
  fetchImpl?: typeof fetch;
  children: ReactNode;
}) {
  const { getAccessToken, expireSession } = useAuth();
  const client = useMemo(
    () => createApiClient({ baseUrl, getAccessToken, onUnauthorized: expireSession, fetchImpl }),
    [baseUrl, getAccessToken, expireSession, fetchImpl],
  );
  return <ApiContext.Provider value={client}>{children}</ApiContext.Provider>;
}
