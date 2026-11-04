import { vi } from "vitest";

import type { AuthClient, SessionLike } from "../auth/types";

export const USER_A = "11111111-1111-4111-8111-111111111111";
export const USER_B = "22222222-2222-4222-8222-222222222222";

export function sessionFor(userId: string, token = "synthetic-access-token"): SessionLike {
  return { access_token: token, user: { id: userId } };
}

export interface Deferred<T> {
  promise: Promise<T>;
  resolve(value: T): void;
  reject(reason: unknown): void;
}

export function deferred<T>(): Deferred<T> {
  let resolve!: (value: T) => void;
  let reject!: (reason: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

type Listener = (event: string, session: SessionLike | null) => void;

/** A controllable stand-in for the Supabase Auth client. No network, no storage, no real project. */
export class FakeAuth implements AuthClient {
  session: SessionLike | null;
  listeners = new Set<Listener>();
  unsubscribed = 0;
  getSessionGate: Deferred<void> | null = null;
  signInResult: { session: SessionLike | null; error: unknown } = { session: null, error: { status: 400 } };
  signInGate: Deferred<void> | null = null;
  signOutError: unknown = null;

  getSession = vi.fn(async () => {
    const snapshot = this.session;
    if (this.getSessionGate) await this.getSessionGate.promise;
    return { data: { session: snapshot } };
  });

  onAuthStateChange = vi.fn((callback: Listener) => {
    this.listeners.add(callback);
    return {
      data: {
        subscription: {
          unsubscribe: () => {
            this.listeners.delete(callback);
            this.unsubscribed += 1;
          },
        },
      },
    };
  });

  signInWithPassword = vi.fn<AuthClient["signInWithPassword"]>(async () => {
    if (this.signInGate) await this.signInGate.promise;
    const { session, error } = this.signInResult;
    if (!error) {
      this.session = session;
      this.emit("SIGNED_IN", session);
    }
    return { data: { session: error ? null : session }, error };
  });

  signOut = vi.fn<AuthClient["signOut"]>(async () => {
    if (this.signOutError) return { error: this.signOutError };
    this.session = null;
    this.emit("SIGNED_OUT", null);
    return { error: null };
  });

  constructor(session: SessionLike | null = null) {
    this.session = session;
  }

  emit(event: string, session: SessionLike | null) {
    for (const listener of [...this.listeners]) listener(event, session);
  }
}
