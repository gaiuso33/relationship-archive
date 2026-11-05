import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import App from "./App";
import { FakeAuth } from "./test/fakeAuth";

describe("App", () => {
  it("sends a signed-out visitor to the sign-in screen", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App auth={new FakeAuth()} apiBaseUrl="https://api.example.test" />
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { level: 1, name: "Sign in" })).toBeInTheDocument();
  });
});
