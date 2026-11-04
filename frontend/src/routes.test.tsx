import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import { deferred, FakeAuth, sessionFor, USER_A } from "./test/fakeAuth";

const API = "https://api.example.test";
const TOKEN = "synthetic-access-token";

function LocationProbe() {
  const location = useLocation();
  return <p data-testid="location">{`${location.pathname}${location.search}`}</p>;
}

function meResponse(status = 200, body: unknown = { user_id: USER_A }) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function renderApp(options: { auth: FakeAuth; entry?: string; fetchImpl?: typeof fetch }) {
  const fetchImpl = options.fetchImpl ?? (vi.fn(async () => meResponse()) as unknown as typeof fetch);
  render(
    <MemoryRouter initialEntries={[options.entry ?? "/"]}>
      <App auth={options.auth} apiBaseUrl={API} fetchImpl={fetchImpl} />
      <LocationProbe />
    </MemoryRouter>,
  );
  return fetchImpl as unknown as ReturnType<typeof vi.fn>;
}

const where = () => screen.getByTestId("location").textContent;

afterEach(() => {
  vi.restoreAllMocks();
});

describe("protected routes", () => {
  it("shows neither private content nor a redirect while the session is loading", async () => {
    const auth = new FakeAuth(sessionFor(USER_A, TOKEN));
    auth.getSessionGate = deferred<void>();
    renderApp({ auth });
    expect(screen.getByRole("status")).toHaveTextContent("Checking your session");
    expect(screen.queryByRole("heading", { name: "Relationship Archive" })).not.toBeInTheDocument();
    expect(where()).toBe("/");
    await act(async () => auth.getSessionGate?.resolve());
    expect(await screen.findByRole("heading", { name: "Relationship Archive" })).toBeInTheDocument();
  });

  it("redirects a signed-out visitor to sign-in, remembering the intended page", async () => {
    renderApp({ auth: new FakeAuth(), entry: "/?tab=notes" });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(where()).toBe("/sign-in?next=%2F%3Ftab%3Dnotes");
  });

  it("lets a signed-in user reach a private route and verifies the session with the API", async () => {
    const fetchImpl = renderApp({ auth: new FakeAuth(sessionFor(USER_A, TOKEN)) });
    expect(await screen.findByText("You are signed in.")).toBeInTheDocument();
    const [url, init] = fetchImpl.mock.calls[0] as [URL, RequestInit];
    expect(url.href).toBe(`${API}/api/v1/me`);
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${TOKEN}`);
    expect(document.body.textContent).not.toContain(TOKEN);
  });

  it("sends a signed-in user away from the sign-in page", async () => {
    renderApp({ auth: new FakeAuth(sessionFor(USER_A, TOKEN)), entry: "/sign-in" });
    expect(await screen.findByText("You are signed in.")).toBeInTheDocument();
    expect(where()).toBe("/");
  });

  it("shows a page-not-found screen for unknown routes, without needing a session", async () => {
    renderApp({ auth: new FakeAuth(), entry: "/nowhere" });
    expect(await screen.findByRole("heading", { name: "Page not found" })).toBeInTheDocument();
  });

  it("signs out from the home page and returns to sign-in", async () => {
    const auth = new FakeAuth(sessionFor(USER_A, TOKEN));
    renderApp({ auth });
    await screen.findByText("You are signed in.");
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(auth.signOut).toHaveBeenCalledWith({ scope: "local" });
  });

  it("tells the user if sign-out failed and keeps them signed in", async () => {
    const auth = new FakeAuth(sessionFor(USER_A, TOKEN));
    auth.signOutError = { name: "AuthRetryableFetchError" };
    renderApp({ auth });
    await screen.findByText("You are signed in.");
    fireEvent.click(screen.getByRole("button", { name: "Sign out" }));
    expect(await screen.findByText(/could not sign you out/i)).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Relationship Archive" })).toBeInTheDocument();
  });
});

describe("home page and API failures", () => {
  it("on 401 ends the session and explains why on the sign-in page", async () => {
    const auth = new FakeAuth(sessionFor(USER_A, TOKEN));
    const fetchImpl = vi.fn(async () => meResponse(401, { error: { code: "unauthorized", message: "x" } }));
    renderApp({ auth, fetchImpl: fetchImpl as unknown as typeof fetch });
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Your session has ended");
    expect(auth.signOut).toHaveBeenCalledTimes(1);
    expect(fetchImpl).toHaveBeenCalledTimes(1); // no retry loop
  });

  it("on 503 shows a safe message, offers a retry and keeps the session", async () => {
    const auth = new FakeAuth(sessionFor(USER_A, TOKEN));
    let calls = 0;
    const fetchImpl = vi.fn(async () =>
      ++calls === 1 ? meResponse(503, { error: { code: "service_unavailable", request_id: "req-9" } }) : meResponse(),
    );
    renderApp({ auth, fetchImpl: fetchImpl as unknown as typeof fetch });
    expect(await screen.findByText(/temporarily unavailable/i)).toBeInTheDocument();
    expect(screen.getByText("Reference: req-9")).toBeInTheDocument();
    expect(auth.signOut).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("You are signed in.")).toBeInTheDocument();
  });

  it("on a network failure shows a connectivity message and keeps the session", async () => {
    const auth = new FakeAuth(sessionFor(USER_A, TOKEN));
    const fetchImpl = vi.fn(async () => {
      throw new TypeError("Failed to fetch");
    });
    renderApp({ auth, fetchImpl: fetchImpl as unknown as typeof fetch });
    expect(await screen.findByText(/could not reach the service/i)).toBeInTheDocument();
    expect(auth.signOut).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Sign out" })).toBeInTheDocument();
  });

  it("does not show response bodies", async () => {
    const auth = new FakeAuth(sessionFor(USER_A, TOKEN));
    const fetchImpl = vi.fn(async () => meResponse(500, { error: { message: "LEAKED-DETAIL" }, detail: "LEAKED-DETAIL" }));
    renderApp({ auth, fetchImpl: fetchImpl as unknown as typeof fetch });
    expect(await screen.findByText(/went wrong on our side/i)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("LEAKED-DETAIL");
  });

  it("ends the session when another tab signs out", async () => {
    const auth = new FakeAuth(sessionFor(USER_A, TOKEN));
    renderApp({ auth });
    await screen.findByText("You are signed in.");
    act(() => auth.emit("SIGNED_OUT", null));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Your session has ended");
  });
});

describe("sign-in screen", () => {
  const fill = (email: string, password: string) => {
    fireEvent.change(screen.getByLabelText("Email"), { target: { value: email } });
    fireEvent.change(screen.getByLabelText("Password"), { target: { value: password } });
  };
  const submit = () => fireEvent.click(screen.getByRole("button", { name: /sign in/i }));

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
    expect(await screen.findByText("You are signed in.")).toBeInTheDocument();
  });

  it("signs in with a trimmed email and goes to the remembered page", async () => {
    const auth = new FakeAuth();
    auth.signInResult = { session: sessionFor(USER_A, TOKEN), error: null };
    renderApp({ auth, entry: "/sign-in?next=%2F" });
    await screen.findByLabelText("Email");
    fill("  person@example.test  ", "synthetic-password");
    submit();
    expect(await screen.findByText("You are signed in.")).toBeInTheDocument();
    expect(auth.signInWithPassword).toHaveBeenCalledWith({ email: "person@example.test", password: "synthetic-password" });
    expect(where()).toBe("/");
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
    expect(await screen.findByText("You are signed in.")).toBeInTheDocument();
    expect(where()).toBe("/");
  });

  it("never shows the password or token, and writes nothing to the console or browser storage", async () => {
    const spies = (["log", "info", "warn", "error", "debug"] as const).map((method) => vi.spyOn(console, method));
    const auth = new FakeAuth();
    auth.signInResult = { session: sessionFor(USER_A, TOKEN), error: null };
    renderApp({ auth, entry: "/sign-in" });
    await screen.findByLabelText("Email");
    fill("person@example.test", "synthetic-password");
    submit();
    await screen.findByText("You are signed in.");
    expect(document.body.textContent).not.toContain("synthetic-password");
    expect(document.body.textContent).not.toContain(TOKEN);
    expect(window.location.href).not.toContain(TOKEN);
    for (const spy of spies) expect(spy).not.toHaveBeenCalled();
    expect(window.localStorage.length).toBe(0);
    expect(window.sessionStorage.length).toBe(0);
    await waitFor(() => expect(where()).toBe("/"));
  });
});
