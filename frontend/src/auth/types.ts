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

/**
 * Why we are signed out, when there is something to tell the user.
 * - `ended`: the session ended without the user asking (expired, refresh failed, other tab).
 * - `local_only`: the user signed out, and this device is signed out, but the service could not be
 *   reached to end the session there.
 */
export type SignedOutReason = "ended" | "local_only";

/** What the UI may know. Tokens are deliberately not part of this. */
export type AuthState =
  | { status: "loading" }
  | { status: "signedOut"; reason?: SignedOutReason }
  | { status: "signedIn"; userId: string };

export type SignInResult = { ok: true } | { ok: false; reason: "rejected" | "rate_limited" | "unavailable" };

/**
 * - `signed_out`: signed out here and at the service.
 * - `local_only`: signed out on this device, but ending the session at the service failed (for
 *   example offline), so the refresh token may not have been revoked there.
 * - `failed`: the session is still active on this device.
 */
export type SignOutResult = "signed_out" | "local_only" | "failed";

export interface AuthContextValue {
  state: AuthState;
  /** Signs in with email and password. Never throws, and never reports which part was wrong. */
  signIn(email: string, password: string): Promise<SignInResult>;
  /** Ends the session on this device, and at the service when it can be reached. */
  signOut(): Promise<SignOutResult>;
  /** Called when the API says the session is not valid: ends it locally. Never retries. */
  expireSession(): void;
  /** The current access token, refreshed by the SDK if needed. Only the API client should call this. */
  getAccessToken(): Promise<string | null>;
}
