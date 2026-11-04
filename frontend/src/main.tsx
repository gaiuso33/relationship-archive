import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";

import App from "./App";
import { createAuthClient } from "./auth/supabase";
import { ConfigError, loadConfig } from "./config";
import { ConfigErrorScreen } from "./ConfigErrorScreen";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("Root element #root not found");
}
const root = createRoot(rootElement);

try {
  const config = loadConfig();
  const auth = createAuthClient(config);
  root.render(
    <StrictMode>
      <BrowserRouter>
        <App auth={auth} apiBaseUrl={config.apiBaseUrl} />
      </BrowserRouter>
    </StrictMode>,
  );
} catch (error) {
  if (!(error instanceof ConfigError)) throw error;
  root.render(
    <StrictMode>
      <ConfigErrorScreen error={error} />
    </StrictMode>,
  );
}
