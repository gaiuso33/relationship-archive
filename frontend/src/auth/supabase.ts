import { createClient } from "@supabase/supabase-js";

import type { AppConfig } from "../config";
import type { AuthClient } from "./types";

/**
 * Creates the Supabase Auth client with the SDK's own session handling: it stores the session in
 * the browser (localStorage), refreshes the access token before it expires, and tells us about
 * changes through `onAuthStateChange`. There is no custom storage or refresh code.
 *
 * `detectSessionInUrl` is off because this application has no magic-link or OAuth sign-in, so it
 * has no reason to read credentials from the address bar.
 */
export function createAuthClient(config: AppConfig): AuthClient {
  const client = createClient(config.supabaseUrl, config.supabasePublishableKey, {
    auth: { detectSessionInUrl: false },
  });
  return client.auth;
}
