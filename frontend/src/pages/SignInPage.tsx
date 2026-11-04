import { useState, type FormEvent } from "react";
import { Navigate, useSearchParams } from "react-router";

import { safeInternalPath } from "../auth/safeRedirect";
import { useAuth } from "../auth/useAuth";

const EMAIL_SHAPE = /^[^\s@]+@[^\s@]+$/;
const MAX_EMAIL_LENGTH = 320;
const MAX_PASSWORD_LENGTH = 1024;

interface FieldErrors {
  email?: string;
  password?: string;
}

function validate(email: string, password: string): FieldErrors {
  const errors: FieldErrors = {};
  if (email.length === 0) errors.email = "Enter your email address.";
  else if (email.length > MAX_EMAIL_LENGTH || !EMAIL_SHAPE.test(email)) errors.email = "Enter a valid email address.";
  if (password.length === 0) errors.password = "Enter your password.";
  else if (password.length > MAX_PASSWORD_LENGTH) errors.password = "That password is too long.";
  return errors;
}

export function SignInPage() {
  const { state, signIn } = useAuth();
  const [searchParams] = useSearchParams();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fieldErrors, setFieldErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (state.status === "signedIn") {
    return <Navigate to={safeInternalPath(searchParams.get("next"))} replace />;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (submitting) return;
    const trimmedEmail = email.trim();
    const errors = validate(trimmedEmail, password);
    setFieldErrors(errors);
    setFormError(null);
    if (errors.email || errors.password) return;

    setSubmitting(true);
    const result = await signIn(trimmedEmail, password);
    setSubmitting(false);
    if (!result.ok) {
      // The same wording whatever was wrong, so the form cannot be used to find out which
      // email addresses have accounts.
      setFormError(
        result.reason === "unavailable"
          ? "Sign-in is temporarily unavailable. Please try again in a moment."
          : "We could not sign you in. Check your email and password and try again.",
      );
      setPassword("");
    }
    // On success the provider reports the new session and this component redirects.
  }

  return (
    <main>
      <h1>Sign in</h1>
      {state.status === "signedOut" && state.reason === "ended" && (
        <p role="status">Your session has ended. Please sign in again.</p>
      )}
      <form onSubmit={handleSubmit} noValidate aria-busy={submitting}>
        <p>
          <label htmlFor="email">Email</label>
          <br />
          <input
            id="email"
            name="email"
            type="email"
            autoComplete="username"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            aria-invalid={fieldErrors.email ? true : undefined}
            aria-describedby={fieldErrors.email ? "email-error" : undefined}
            disabled={submitting}
          />
          {fieldErrors.email && (
            <span id="email-error" role="alert">
              {" "}
              {fieldErrors.email}
            </span>
          )}
        </p>
        <p>
          <label htmlFor="password">Password</label>
          <br />
          <input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            aria-invalid={fieldErrors.password ? true : undefined}
            aria-describedby={fieldErrors.password ? "password-error" : undefined}
            disabled={submitting}
          />
          {fieldErrors.password && (
            <span id="password-error" role="alert">
              {" "}
              {fieldErrors.password}
            </span>
          )}
        </p>
        {formError && <p role="alert">{formError}</p>}
        <button type="submit" disabled={submitting}>
          {submitting ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
