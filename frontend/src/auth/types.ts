/** The part of a Supabase session this application reads. Real sessions satisfy it structurally. */
export interface SessionLike {
  access_token: string;
  user: { id: string };
}

/**
 * The part of the Supabase Auth client this application uses. Depending on this small interface,
 * rather than on the SDK's whole client, keeps the provider testable without a Supabase project.
 */
export interface AuthClient {
  getSession(): Promise<{ data: { session: SessionLike | null } }>;
  onAuthStateChange(callback: (event: string, session: SessionLike | null) => void): {
    data: { subscription: { unsubscribe(): void } };
  };
  signInWithPassword(credentials: {
    email: string;
    password: string;
  }): Promise<{ data: { session: SessionLike | null }; error: unknown }>;
  signOut(options?: { scope?: "local" | "global" | "others" }): Promise<{ error: unknown }>;
}

/** What the UI may know. Tokens are deliberately not part of this. */
export type AuthState =
  | { status: "loading" }
  | { status: "signedOut"; reason?: "ended" }
  | { status: "signedIn"; userId: string };

export type SignInResult = { ok: true } | { ok: false; reason: "rejected" | "unavailable" };

export interface AuthContextValue {
  state: AuthState;
  /** Signs in with email and password. Never throws, and never reports which part was wrong. */
  signIn(email: string, password: string): Promise<SignInResult>;
  /** Ends the session on this device. Resolves `false` if it could not be ended. */
  signOut(): Promise<boolean>;
  /** Called when the API says the session is not valid: ends it locally. Never retries. */
  expireSession(): void;
  /** The current access token, refreshed by the SDK if needed. Only the API client should call this. */
  getAccessToken(): Promise<string | null>;
}
