import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { API_ORIGIN, renderApp, where } from "./test/renderApp";
import { errorBody, FakeApi, TOKEN } from "./test/fakeApi";
import { deferred, FakeAuth, sessionFor, USER_A } from "./test/fakeAuth";

const PEOPLE = /^\/api\/v1\/people$/;
const failure = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
const peopleHeading = () => screen.findByRole("heading", { name: "People" });

afterEach(() => {
  vi.restoreAllMocks();
});

describe("protected routes", () => {
  it("shows neither private content nor a redirect while the session is loading", async () => {
    const auth = new FakeAuth(sessionFor(USER_A, TOKEN));
    auth.getSessionGate = deferred<void>();
    const { api } = renderApp({ auth });
    expect(screen.getByRole("status")).toHaveTextContent("Checking your session");
    expect(screen.queryByRole("heading", { name: "People" })).not.toBeInTheDocument();
    expect(where()).toBe("/people");
    expect(api.calls).toHaveLength(0);
    await act(async () => auth.getSessionGate?.resolve());
    expect(await peopleHeading()).toBeInTheDocument();
  });

  it("redirects a signed-out visitor to sign-in, remembering the intended page, without calling the API", async () => {
    const { api } = renderApp({ auth: new FakeAuth(), entry: "/people?tab=notes" });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(where()).toBe("/sign-in?next=%2Fpeople%3Ftab%3Dnotes");
    expect(api.calls).toHaveLength(0);
  });

  it("redirects / to /people", async () => {
    renderApp({ entry: "/" });
    expect(await peopleHeading()).toBeInTheDocument();
    expect(where()).toBe("/people");
  });

  it("retires the old home page: nothing says 'You are signed in.' and /me is not called", async () => {
    const { api } = renderApp({ entry: "/" });
    await peopleHeading();
    expect(screen.queryByText("You are signed in.")).not.toBeInTheDocument();
    expect(api.calls.some((c) => c.path === "/api/v1/me")).toBe(false);
  });

  it("lets a signed-in user reach a private route, sending the bearer token to the API origin only", async () => {
    const { api } = renderApp();
    await peopleHeading();
    await screen.findByText("You have not added anyone yet.");
    expect(api.calls[0]).toMatchObject({ method: "GET", path: "/api/v1/people", authorization: `Bearer ${TOKEN}` });
    expect(document.body.textContent).not.toContain(TOKEN);
    expect(API_ORIGIN).toBe("https://api.example.test");
  });

  it("sends a signed-in user away from the sign-in page", async () => {
    renderApp({ entry: "/sign-in" });
    expect(await peopleHeading()).toBeInTheDocument();
    expect(where()).toBe("/people");
  });

  it("shows a page-not-found screen for unknown routes, without needing a session", async () => {
    renderApp({ auth: new FakeAuth(), entry: "/nowhere" });
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });
});

describe("sign-out from the app layout", () => {
  it("signs out and returns to sign-in", async () => {
    const { auth } = renderApp();
    await peopleHeading();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(auth.signOut).toHaveBeenCalledWith({ scope: "local" });
  });

  it("offline sign-out: signed out here, and the user is told the service was not reached", async () => {
    const auth = new FakeAuth(sessionFor(USER_A, TOKEN));
    auth.signOutMode = "network";
    renderApp({ auth });
    await peopleHeading();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(await screen.findByText(/signed out on this device, but we could not reach the service/i)).toBeInTheDocument();
    expect(screen.queryByText(/could not sign you out/i)).not.toBeInTheDocument();
    expect(auth.session).toBeNull();
  });

  it("tells the user if the session is really still active after a failed sign-out", async () => {
    const auth = new FakeAuth(sessionFor(USER_A, TOKEN));
    auth.signOutMode = "stuck";
    renderApp({ auth });
    await peopleHeading();
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByText(/could not sign you out/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "People" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("ends the session when another tab signs out", async () => {
    const { auth } = renderApp();
    await peopleHeading();
    act(() => auth.emit("SIGNED_OUT", null));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Your session has ended");
  });
});

describe("API failures on a private screen", () => {
  it("on 401 ends the session and explains why on the sign-in page, with no retry loop", async () => {
    const api = new FakeApi();
    api.failAlways("GET", PEOPLE, () => failure(401, errorBody("unauthorized")));
    const { auth } = renderApp({ api });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Your session has ended");
    expect(auth.signOut).toHaveBeenCalledTimes(1);
    expect(api.callsTo("GET", PEOPLE)).toHaveLength(1);
  });

  it("a 401 for a token that was refreshed meanwhile does not sign out the newer session", async () => {
    // The request goes out with the old token, the SDK refreshes the session, then the API
    // answers 401 for the old token.
    const auth = new FakeAuth(sessionFor(USER_A, "old-token"));
    const api = new FakeApi();
    const gate = api.holdNext("GET", PEOPLE);
    renderApp({ auth, api });
    await vi.waitFor(() => expect(api.callsTo("GET", PEOPLE)).toHaveLength(1));
    act(() => {
      auth.session = sessionFor(USER_A, TOKEN);
      auth.emit("TOKEN_REFRESHED", auth.session);
    });
    await act(async () => gate.resolve(failure(401, errorBody("unauthorized"))));

    expect(await screen.findByText(/session was just refreshed/i)).toBeInTheDocument();
    expect(auth.signOut).not.toHaveBeenCalled();
    expect(auth.session).not.toBeNull();
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
    expect(api.callsTo("GET", PEOPLE)).toHaveLength(1); // nothing retried by itself

    // The user may try again, and now the new token is used.
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("You have not added anyone yet.")).toBeInTheDocument();
    expect(api.callsTo("GET", PEOPLE)[1].authorization).toBe(`Bearer ${TOKEN}`);
  });

  it("on 503 shows a safe message, offers a retry and keeps the session", async () => {
    const api = new FakeApi();
    api.failNext("GET", PEOPLE, () => failure(503, errorBody("service_unavailable", { request_id: "req-9" })));
    const { auth } = renderApp({ api });
    expect(await screen.findByText(/temporarily unavailable/i)).toBeInTheDocument();
    expect(screen.getByText("Reference: req-9")).toBeInTheDocument();
    expect(auth.signOut).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("You have not added anyone yet.")).toBeInTheDocument();
  });

  it("on a network failure shows a connectivity message and keeps the session", async () => {
    const api = new FakeApi();
    const real = api.fetch;
    let first = true;
    api.fetch = (async (...args: Parameters<typeof fetch>) => {
      if (first) {
        first = false;
        throw new TypeError("Failed to fetch");
      }
      return real(...args);
    }) as typeof fetch;
    const { auth } = renderApp({ api });
    expect(await screen.findByText(/could not reach the service/i)).toBeInTheDocument();
    expect(auth.signOut).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("does not show response bodies", async () => {
    const api = new FakeApi();
    api.failAlways("GET", PEOPLE, () => failure(500, { error: { message: "LEAKED-DETAIL" }, detail: "LEAKED-DETAIL" }));
    renderApp({ api });
    expect(await screen.findByText(/went wrong on our side/i)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("LEAKED-DETAIL");
  });
});

describe("sign-in screen", () => {
  const fill = (email: string, password: string) => {
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  };
  const submit = () => fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

  it("shows no interactive form while the session is still loading", async () => {
    const auth = new FakeAuth();
    auth.getSessionGate = deferred<void>();
    renderApp({ auth, entry: "/sign-in" });
    expect(screen.getByRole("status")).toHaveTextContent("Checking your session");
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /sign in/i })).not.toBeInTheDocument();
    await act(async () => auth.getSessionGate?.resolve());
    expect(await screen.findByLabelText("Email")).toBeInTheDocument();
  });

  it("does not flash the form at a user who already has a session", async () => {
    const auth = new FakeAuth(sessionFor(USER_A, TOKEN));
    auth.getSessionGate = deferred<void>();
    renderApp({ auth, entry: "/sign-in" });
    expect(screen.queryByLabelText("Email")).not.toBeInTheDocument();
    await act(async () => auth.getSessionGate?.resolve());
    expect(await screen.findByRole("heading", { name: "People" })).toBeInTheDocument();
  });

  it("explains a rate limit, distinctly from wrong credentials", async () => {
    const auth = new FakeAuth();
    auth.signInResult = { session: null, error: { status: 429, name: "AuthApiError", code: "over_request_rate_limit" } };
    renderApp({ auth, entry: "/sign-in" });
    await screen.findByLabelText("Email");
    fill("person@example.test", "synthetic-password");
    submit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent(/too many sign-in attempts/i);
    expect(alert).not.toHaveTextContent(/check your email and password/i);
  });

  it("shows a generic failure, not silence, when sign-in returns neither a session nor an error", async () => {
    const auth = new FakeAuth();
    auth.signInWithPassword.mockResolvedValueOnce({ data: { session: null }, error: null });
    renderApp({ auth, entry: "/sign-in" });
    await screen.findByLabelText("Email");
    fill("person@example.test", "synthetic-password");
    submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("We could not sign you in.");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("has labelled email and password fields of the right types", async () => {
    renderApp({ auth: new FakeAuth(), entry: "/sign-in" });
    const email = await screen.findByLabelText("Email");
    expect(email).toHaveAttribute("type", "email");
    expect(email).toHaveAttribute("autocomplete", "username");
    const password = screen.getByLabelText("Password");
    expect(password).toHaveAttribute("type", "password");
    expect(password).toHaveAttribute("autocomplete", "current-password");
  });

  it("validates before calling Supabase", async () => {
    const auth = new FakeAuth();
    renderApp({ auth, entry: "/sign-in" });
    await screen.findByLabelText("Email");
    submit();
    expect(await screen.findByText("Enter your email address.")).toBeInTheDocument();
    expect(screen.getByText("Enter your password.")).toBeInTheDocument();
    expect(screen.getByLabelText("Email")).toHaveAttribute("aria-invalid", "true");
    fill("not-an-email", "pw");
    submit();
    expect(await screen.findByText("Enter a valid email address.")).toBeInTheDocument();
    expect(auth.signInWithPassword).not.toHaveBeenCalled();
  });

  it("shows a submitting state, disables the button and blocks double submits", async () => {
    const auth = new FakeAuth();
    auth.signInGate = deferred<void>();
    auth.signInResult = { session: sessionFor(USER_A, TOKEN), error: null };
    renderApp({ auth, entry: "/sign-in" });
    await screen.findByLabelText("Email");
    fill("person@example.test", "synthetic-password");
    submit();
    const button = await screen.findByRole("button", { name: "Signing in…" });
    expect(button).toBeDisabled();
    fireEvent.submit(button.closest("form") as HTMLFormElement);
    expect(auth.signInWithPassword).toHaveBeenCalledTimes(1);
    await act(async () => auth.signInGate?.resolve());
    expect(await screen.findByRole("heading", { name: "People" })).toBeInTheDocument();
  });

  it("signs in with a trimmed email and goes to the remembered page", async () => {
    const auth = new FakeAuth();
    auth.signInResult = { session: sessionFor(USER_A, TOKEN), error: null };
    renderApp({ auth, entry: "/sign-in?next=%2Fpeople%2Fnew" });
    await screen.findByLabelText("Email");
    fill("  person@example.test  ", "synthetic-password");
    submit();
    expect(await screen.findByRole("heading", { name: "Add person" })).toBeInTheDocument();
    expect(auth.signInWithPassword).toHaveBeenCalledWith({ email: "person@example.test", password: "synthetic-password" });
    expect(where()).toBe("/people/new");
  });

  it("gives one generic message for every rejected sign-in, and clears the password", async () => {
    const auth = new FakeAuth();
    auth.signInResult = { session: null, error: { status: 400, name: "AuthApiError", message: "Invalid login credentials" } };
    renderApp({ auth, entry: "/sign-in" });
    await screen.findByLabelText("Email");
    fill("person@example.test", "synthetic-password");
    submit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("We could not sign you in. Check your email and password and try again.");
    expect(document.body.textContent).not.toMatch(/invalid login credentials|no account|not found|wrong password/i);
    expect(screen.getByLabelText("Password")).toHaveValue("");
    expect(screen.getByLabelText("Email")).toHaveValue("person@example.test");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("says the service is unavailable, not that the password is wrong, when Supabase is down", async () => {
    const auth = new FakeAuth();
    auth.signInResult = { session: null, error: { status: 503, name: "AuthApiError" } };
    renderApp({ auth, entry: "/sign-in" });
    await screen.findByLabelText("Email");
    fill("person@example.test", "synthetic-password");
    submit();
    expect(await screen.findByRole("alert")).toHaveTextContent(/temporarily unavailable/i);
  });

  it.each([
    ["an absolute URL", "https://evil.example/steal"],
    ["a protocol-relative URL", "//evil.example/steal"],
    ["a javascript: URL", "javascript:alert(1)"],
  ])("does not redirect to %s after sign-in", async (_name, destination) => {
    const auth = new FakeAuth();
    auth.signInResult = { session: sessionFor(USER_A, TOKEN), error: null };
    renderApp({ auth, entry: `/sign-in?next=${encodeURIComponent(destination)}` });
    await screen.findByLabelText("Email");
    fill("person@example.test", "synthetic-password");
    submit();
    expect(await screen.findByRole("heading", { name: "People" })).toBeInTheDocument();
    expect(where()).toBe("/people");
  });

  it("never shows the password or token, and writes nothing to the console or browser storage", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((method) => vi.spyOn(console, method));
    const auth = new FakeAuth();
    auth.signInResult = { session: sessionFor(USER_A, TOKEN), error: null };
    renderApp({ auth, entry: "/sign-in" });
    await screen.findByLabelText("Email");
    fill("person@example.test", "synthetic-password");
    submit();
    await screen.findByRole("heading", { name: "People" });
    expect(document.body.textContent).not.toContain("synthetic-password");
    expect(document.body.textContent).not.toContain(TOKEN);
    expect(window.location.href).not.toContain(TOKEN);
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    await waitFor(() => expect(where()).toBe("/people"));
  });
});
