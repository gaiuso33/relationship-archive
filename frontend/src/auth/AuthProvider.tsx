import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { AuthContext } from "./authContext";
import type { AuthClient, AuthContextValue, AuthState, SessionLike, SignInResult } from "./types";

/**
 * Works out the next state from the session the SDK reports. Returns the same object when nothing
 * changed, so the frequent token refreshes do not re-render the application.
 */
function nextState(previous: AuthState, session: SessionLike | null, userInitiated: boolean): AuthState {
  if (session) {
    return previous.status === "signedIn" && previous.userId === session.user.id
      ? previous
      : { status: "signedIn", userId: session.user.id };
  }
  if (previous.status === "signedIn") {
    // The session disappeared without the user asking: it expired, could not be refreshed, or was
    // ended in another tab.
    return userInitiated ? { status: "signedOut" } : { status: "signedOut", reason: "ended" };
  }
  return previous.status === "signedOut" ? previous : { status: "signedOut" };
}

/** Network and server problems are "unavailable"; anything else is a generic rejection. */
function isServiceProblem(error: unknown): boolean {
  if (typeof error !== "object" || error === null) return false;
  const { name, status } = error as { name?: unknown; status?: unknown };
  return name === "AuthRetryableFetchError" || (typeof status === "number" && status >= 500);
}

export function AuthProvider({ auth, children }: { auth: AuthClient; children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: "loading" });
  const userInitiated = useRef(false);

  useEffect(() => {
    let active = true;
    let eventSeen = false;

    // The callback only updates state. It never calls back into the SDK: doing that from inside
    // the callback can deadlock the SDK's internal lock.
    const {
      data: { subscription },
    } = auth.onAuthStateChange((_event, session) => {
      if (!active) return;
      eventSeen = true;
      setState((previous) => nextState(previous, session, userInitiated.current));
    });

    // `getSession()` may finish after an auth event has already told us something newer, so its
    // answer is used only if no event has arrived.
    auth
      .getSession()
      .then(({ data }) => {
        if (active && !eventSeen) {
          setState((previous) => nextState(previous, data.session, false));
        }
      })
      .catch(() => {
        if (active && !eventSeen) setState({ status: "signedOut" });
      });

    return () => {
      active = false;
      subscription.unsubscribe();
    };
  }, [auth]);

  const signIn = useCallback(
    async (email: string, password: string): Promise<SignInResult> => {
      try {
        const { data, error } = await auth.signInWithPassword({ email, password });
        if (error) return { ok: false, reason: isServiceProblem(error) ? "unavailable" : "rejected" };
        if (data.session) {
          const session = data.session;
          setState((previous) => nextState(previous, session, false));
        }
        return { ok: true };
      } catch {
        return { ok: false, reason: "unavailable" };
      }
    },
    [auth],
  );

  const signOut = useCallback(async (): Promise<boolean> => {
    userInitiated.current = true;
    try {
      const { error } = await auth.signOut({ scope: "local" });
      if (error) return false;
      setState({ status: "signedOut" });
      return true;
    } catch {
      return false;
    } finally {
      userInitiated.current = false;
    }
  }, [auth]);

  const expireSession = useCallback(() => {
    setState((previous) => (previous.status === "signedIn" ? { status: "signedOut", reason: "ended" } : previous));
    // Clear the stored session too. A failure here is not actionable: the session is already invalid.
    void Promise.resolve()
      .then(() => auth.signOut({ scope: "local" }))
      .catch(() => undefined);
  }, [auth]);

  const getAccessToken = useCallback(async (): Promise<string | null> => {
    const { data } = await auth.getSession();
    return data.session?.access_token ?? null;
  }, [auth]);

  const value = useMemo<AuthContextValue>(
    () => ({ state, signIn, signOut, expireSession, getAccessToken }),
    [state, signIn, signOut, expireSession, getAccessToken],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
