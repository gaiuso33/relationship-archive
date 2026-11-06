import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";

import App from "./../App";
import { FakeApi, TOKEN } from "./fakeApi";
import { FakeAuth, sessionFor, USER_A } from "./fakeAuth";
import { LocationProbe } from "./LocationProbe";

export const API_ORIGIN = "https://api.example.test";

/** The current in-app location (path and query), for tests that check where the user ended up. */
export const where = () => screen.getByTestId("location").textContent;

/** Renders the whole application on a memory router, with a fake Supabase client and a fake API. */
export function renderApp(
  options: { auth?: FakeAuth; api?: FakeApi; entry?: string } = {},
): { auth: FakeAuth; api: FakeApi } {
  const auth = options.auth ?? new FakeAuth(sessionFor(USER_A, TOKEN));
  const api = options.api ?? new FakeApi();
  render(
    <MemoryRouter initialEntries={[options.entry ?? "/people"]}>
      <App auth={auth} apiBaseUrl={API_ORIGIN} fetchImpl={api.fetch} />
      <LocationProbe />
    </MemoryRouter>,
  );
  return { auth, api };
}
