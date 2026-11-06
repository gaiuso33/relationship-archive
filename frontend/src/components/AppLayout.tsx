import { useState } from "react";
import { Link, NavLink, Outlet } from "react-router";

import { useAuth } from "../auth/useAuth";

/** The frame around every private screen: a skip link, the header with navigation and sign-out, and the page. */
export function AppLayout() {
  const { signOut } = useAuth();
  const [signOutFailed, setSignOutFailed] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  async function handleSignOut() {
    setSignOutFailed(false);
    setSigningOut(true);
    const result = await signOut();
    setSigningOut(false);
    if (result === "failed") setSignOutFailed(true);
  }

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to main content
      </a>
      <header className="app-header">
        <Link to="/people" className="brand">
          Relationship Archive
        </Link>
        <nav aria-label="Main">
          <NavLink to="/people">People</NavLink>
        </nav>
        <button type="button" className="secondary" onClick={handleSignOut} disabled={signingOut}>
          Sign out
        </button>
      </header>
      {signOutFailed && (
        <p className="panel panel-error" role="alert">
          We could not sign you out. Please try again.
        </p>
      )}
      <main id="main" tabIndex={-1}>
        <Outlet />
      </main>
    </div>
  );
}
