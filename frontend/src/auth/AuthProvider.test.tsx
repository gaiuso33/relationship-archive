import { act, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { deferred, FakeAuth, sessionFor, USER_A, USER_B } from "../test/fakeAuth";
import { AuthProvider } from "./AuthProvider";
import { useAuth } from "./useAuth";

function Probe() {
  const { state, signIn, signOut, getAccessToken, expireSession } = useAuth();
  return (
    <div>
      <p data-testid="status">{state.status === "signedOut" && state.reason ? `signedOut:${state.reason}` : state.status}</p>
      <p data-testid="user">{state.status === "signedIn" ? state.userId : "-"}</p>
      <button onClick={() => void signIn("a@example.test", "pw").then((r) => (window.__result = JSON.stringify(r)))}>in</button>
      <button onClick={() => void signOut().then((ok) => (window.__result = String(ok)))}>out</button>
      <button onClick={() => void getAccessToken().then((t) => (window.__result = String(t)))}>token</button>
      <button onClick={() => expireSession()}>expire</button>
    </div>
  );
}

declare global {
  interface Window {
    __result?: string;
  }
}

const status = () => screen.getByTestId("status").textContent;

function mount(auth: FakeAuth) {
  return render(
    <AuthProvider auth={auth}>
      <Probe />
    </AuthProvider>,
  );
}

describe("AuthProvider", () => {
  it("starts in the loading state until the session is known", async () => {
    const auth = new FakeAuth();
    auth.getSessionGate = deferred<void>();
    mount(auth);
    expect(status()).toBe("loading");
    await act(async () => auth.getSessionGate?.resolve());
    await waitFor(() => expect(status()).toBe("signedOut"));
  });

  it("reports signed-in with only the user id", async () => {
    mount(new FakeAuth(sessionFor(USER_A)));
    await waitFor(() => expect(status()).toBe("signedIn"));
    expect(screen.getByTestId("user")).toHaveTextContent(USER_A);
    expect(document.body.textContent).not.toContain("synthetic-access-token");
  });

  it("does not let a late getSession() overwrite a newer auth event", async () => {
    const auth = new FakeAuth(null);
    auth.getSessionGate = deferred<void>();
    mount(auth);
    // The sign-in event arrives while getSession() (which saw "no session") is still pending.
    act(() => auth.emit("SIGNED_IN", sessionFor(USER_A)));
    expect(status()).toBe("signedIn");
    await act(async () => auth.getSessionGate?.resolve());
    expect(status()).toBe("signedIn");
    expect(screen.getByTestId("user")).toHaveTextContent(USER_A);
  });

  it("does not let a late getSession() override a sign-out event either", async () => {
    const auth = new FakeAuth(sessionFor(USER_A));
    auth.getSessionGate = deferred<void>();
    mount(auth);
    act(() => auth.emit("INITIAL_SESSION", null));
    await act(async () => auth.getSessionGate?.resolve());
    expect(status()).toBe("signedOut");
  });

  it("falls back to signed-out if getSession() fails", async () => {
    const auth = new FakeAuth();
    auth.getSession.mockRejectedValueOnce(new Error("boom"));
    mount(auth);
    await waitFor(() => expect(status()).toBe("signedOut"));
  });

  it("follows auth-state changes and ignores repeated refreshes of the same user", async () => {
    const auth = new FakeAuth(null);
    mount(auth);
    await waitFor(() => expect(status()).toBe("signedOut"));
    act(() => auth.emit("SIGNED_IN", sessionFor(USER_A)));
    expect(status()).toBe("signedIn");
    act(() => auth.emit("TOKEN_REFRESHED", sessionFor(USER_A, "another-token")));
    expect(status()).toBe("signedIn");
    expect(screen.getByTestId("user")).toHaveTextContent(USER_A);
    act(() => auth.emit("SIGNED_IN", sessionFor(USER_B)));
    expect(screen.getByTestId("user")).toHaveTextContent(USER_B);
  });

  it("reports an ended session when the session disappears without the user asking", async () => {
    const auth = new FakeAuth(sessionFor(USER_A));
    mount(auth);
    await waitFor(() => expect(status()).toBe("signedIn"));
    act(() => auth.emit("SIGNED_OUT", null)); // refresh failed, or signed out in another tab
    expect(status()).toBe("signedOut:ended");
  });

  it("signs out on request without the 'ended' notice", async () => {
    const auth = new FakeAuth(sessionFor(USER_A));
    mount(auth);
    await waitFor(() => expect(status()).toBe("signedIn"));
    await act(async () => screen.getByRole("button", { name: "out" }).click());
    expect(status()).toBe("signedOut");
    expect(auth.signOut).toHaveBeenCalledWith({ scope: "local" });
    expect(window.__result).toBe("signed_out");
  });

  it("reports a clean sign-out", async () => {
    const auth = new FakeAuth(sessionFor(USER_A));
    mount(auth);
    await waitFor(() => expect(status()).toBe("signedIn"));
    await act(async () => screen.getByRole("button", { name: "out" }).click());
    expect(window.__result).toBe("signed_out");
  });

  it("is signed out on this device, and says the service was not reached, when the network is down", async () => {
    // Like the real SDK: the stored session is removed and SIGNED_OUT is emitted, but the call
    // reports an error because the refresh token could not be revoked.
    const auth = new FakeAuth(sessionFor(USER_A));
    auth.signOutMode = "network";
    mount(auth);
    await waitFor(() => expect(status()).toBe("signedIn"));
    await act(async () => screen.getByRole("button", { name: "out" }).click());
    expect(status()).toBe("signedOut:local_only");
    expect(window.__result).toBe("local_only");
    expect(auth.session).toBeNull();
  });

  it("stays signed in and reports a failure only if the session is really still there", async () => {
    const auth = new FakeAuth(sessionFor(USER_A));
    auth.signOutMode = "stuck";
    mount(auth);
    await waitFor(() => expect(status()).toBe("signedIn"));
    await act(async () => screen.getByRole("button", { name: "out" }).click());
    expect(status()).toBe("signedIn");
    expect(window.__result).toBe("failed");
  });

  it("treats a thrown sign-out as a failure when the session cannot be confirmed gone", async () => {
    const auth = new FakeAuth(sessionFor(USER_A));
    auth.signOut.mockRejectedValueOnce(new Error("boom"));
    auth.getSession.mockResolvedValueOnce({ data: { session: sessionFor(USER_A) } }); // initial
    auth.getSession.mockRejectedValueOnce(new Error("storage unavailable")); // the check
    mount(auth);
    await waitFor(() => expect(status()).toBe("signedIn"));
    await act(async () => screen.getByRole("button", { name: "out" }).click());
    expect(window.__result).toBe("failed");
    expect(status()).toBe("signedIn");
  });

  it("signs in successfully", async () => {
    const auth = new FakeAuth(null);
    auth.signInResult = { session: sessionFor(USER_A), error: null };
    mount(auth);
    await waitFor(() => expect(status()).toBe("signedOut"));
    await act(async () => screen.getByRole("button", { name: "in" }).click());
    expect(status()).toBe("signedIn");
    expect(window.__result).toBe('{"ok":true}');
  });

  it.each([
    ["a rejected password", { status: 400, name: "AuthApiError" }, "rejected"],
    ["a rate limit by status", { status: 429, name: "AuthApiError" }, "rate_limited"],
    ["a rate limit by code", { status: 400, code: "over_request_rate_limit" }, "rate_limited"],
    ["a server error", { status: 503, name: "AuthApiError" }, "unavailable"],
    ["a network failure", { name: "AuthRetryableFetchError", status: 0 }, "unavailable"],
  ])("reports %s without detail", async (_name, error, reason) => {
    const auth = new FakeAuth(null);
    auth.signInResult = { session: null, error };
    mount(auth);
    await waitFor(() => expect(status()).toBe("signedOut"));
    await act(async () => screen.getByRole("button", { name: "in" }).click());
    expect(status()).toBe("signedOut");
    expect(window.__result).toBe(JSON.stringify({ ok: false, reason }));
  });

  it("does not report success when there is neither a session nor an error", async () => {
    const auth = new FakeAuth(null);
    auth.signInWithPassword.mockResolvedValueOnce({ data: { session: null }, error: null });
    mount(auth);
    await waitFor(() => expect(status()).toBe("signedOut"));
    await act(async () => screen.getByRole("button", { name: "in" }).click());
    expect(window.__result).toBe(JSON.stringify({ ok: false, reason: "rejected" }));
    expect(status()).toBe("signedOut");
  });

  it("reports a thrown sign-in as unavailable", async () => {
    const auth = new FakeAuth(null);
    auth.signInWithPassword.mockRejectedValueOnce(new Error("secret detail"));
    mount(auth);
    await waitFor(() => expect(status()).toBe("signedOut"));
    await act(async () => screen.getByRole("button", { name: "in" }).click());
    expect(window.__result).toBe(JSON.stringify({ ok: false, reason: "unavailable" }));
  });

  it("ends the session locally when the API rejects it, once, without looping", async () => {
    const auth = new FakeAuth(sessionFor(USER_A));
    mount(auth);
    await waitFor(() => expect(status()).toBe("signedIn"));
    await act(async () => screen.getByRole("button", { name: "expire" }).click());
    expect(status()).toBe("signedOut:ended");
    await waitFor(() => expect(auth.signOut).toHaveBeenCalledTimes(1));
    expect(auth.signOut).toHaveBeenCalledWith({ scope: "local" });
    // Expiring again while signed out does nothing.
    await act(async () => screen.getByRole("button", { name: "expire" }).click());
    expect(status()).toBe("signedOut:ended");
  });

  it("returns the access token only to callers that ask for it", async () => {
    const auth = new FakeAuth(sessionFor(USER_A, "synthetic-access-token"));
    mount(auth);
    await waitFor(() => expect(status()).toBe("signedIn"));
    await act(async () => screen.getByRole("button", { name: "token" }).click());
    expect(window.__result).toBe("synthetic-access-token");
    expect(document.body.textContent).not.toContain("synthetic-access-token");
  });

  it("unsubscribes when it unmounts, and ignores events afterwards", async () => {
    const auth = new FakeAuth(null);
    const view = mount(auth);
    await waitFor(() => expect(status()).toBe("signedOut"));
    view.unmount();
    expect(auth.unsubscribed).toBe(1);
    expect(auth.listeners.size).toBe(0);
  });
});
