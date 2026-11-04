import { useEffect, useState } from "react";

import { ApiError } from "../api/client";
import { useApi } from "../api/useApi";
import { useAuth } from "../auth/useAuth";

type SessionCheck =
  | { status: "checking" }
  | { status: "verified" }
  | { status: "failed"; message: string; requestId: string | null };

/** The signed-in landing page. It asks the API who we are, so the API (not the browser) vouches for the session. */
export function HomePage() {
  const api = useApi();
  const { signOut } = useAuth();
  const [check, setCheck] = useState<SessionCheck>({ status: "checking" });
  const [attempt, setAttempt] = useState(0);
  const [signOutFailed, setSignOutFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    api
      .getMe({ signal: controller.signal })
      .then(() => setCheck({ status: "verified" }))
      .catch((error: unknown) => {
        if (error instanceof ApiError && error.kind === "aborted") return;
        // A 401 has already ended the session, and the guard is sending the user to sign-in.
        if (error instanceof ApiError) {
          setCheck({ status: "failed", message: error.message, requestId: error.requestId });
        } else {
          setCheck({ status: "failed", message: "Something went wrong. Please try again.", requestId: null });
        }
      });
    return () => controller.abort();
  }, [api, attempt]);

  function retry() {
    setCheck({ status: "checking" });
    setAttempt((value) => value + 1);
  }

  async function handleSignOut() {
    setSignOutFailed(false);
    if (!(await signOut())) setSignOutFailed(true);
  }

  return (
    <main>
      <h1>Relationship Archive</h1>
      <p>A private place to remember what matters about the people in your life.</p>
      {check.status === "checking" && <p role="status">Checking your session with the service…</p>}
      {check.status === "verified" && <p role="status">You are signed in.</p>}
      {check.status === "failed" && (
        <div role="alert">
          <p>{check.message}</p>
          {check.requestId && <p>Reference: {check.requestId}</p>}
          <button type="button" onClick={retry}>
            Try again
          </button>
        </div>
      )}
      {signOutFailed && <p role="alert">We could not sign you out. Check your connection and try again.</p>}
      <button type="button" onClick={handleSignOut}>
        Sign out
      </button>
    </main>
  );
}
