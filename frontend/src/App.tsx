import { ApiProvider } from "./api/ApiProvider";
import { AuthProvider } from "./auth/AuthProvider";
import type { AuthClient } from "./auth/types";
import { AppRoutes } from "./routes";

/** The router (`BrowserRouter` in `main.tsx`, a memory router in tests) wraps this component. */
export default function App({
  auth,
  apiBaseUrl,
  fetchImpl,
}: {
  auth: AuthClient;
  apiBaseUrl: string;
  fetchImpl?: typeof fetch;
}) {
  return (
    <AuthProvider auth={auth}>
      <ApiProvider baseUrl={apiBaseUrl} fetchImpl={fetchImpl}>
        <AppRoutes />
      </ApiProvider>
    </AuthProvider>
  );
}
