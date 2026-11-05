import { ConfigError } from "./config";

/** Shown instead of the app when the configuration is missing or unsafe. Lists variable names only. */
export function ConfigErrorScreen({ error }: { error: ConfigError }) {
  return (
    <main>
      <h1>Configuration problem</h1>
      <p role="alert">The application cannot start because its configuration is incomplete or unsafe.</p>
      <ul>
        {error.problems.map((problem) => (
          <li key={problem}>{problem}</li>
        ))}
      </ul>
      <p>See docs/frontend-auth.md for the settings the frontend needs.</p>
    </main>
  );
}
