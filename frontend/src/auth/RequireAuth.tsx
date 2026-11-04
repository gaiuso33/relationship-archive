import { Navigate, Outlet, useLocation } from "react-router";

import { safeInternalPath } from "./safeRedirect";
import { useAuth } from "./useAuth";

/**
 * Keeps signed-out visitors away from private screens. This is a convenience for the user, not
 * security: the API checks the token on every request and is the real boundary.
 */
export function RequireAuth() {
  const { state } = useAuth();
  const location = useLocation();

  if (state.status === "loading") {
    // Neither the private content nor a redirect until we know whether there is a session.
    return <p role="status">Checking your session…</p>;
  }
  if (state.status === "signedOut") {
    const destination = safeInternalPath(`${location.pathname}${location.search}${location.hash}`);
    const query = destination === "/" ? "" : `?next=${encodeURIComponent(destination)}`;
    return <Navigate to={`/sign-in${query}`} replace />;
  }
  return <Outlet />;
}
