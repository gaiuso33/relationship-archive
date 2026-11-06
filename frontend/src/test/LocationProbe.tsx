import { useLocation } from "react-router";

/** Shows the current in-app path and query, so tests can check where the user ended up. */
export function LocationProbe() {
  const location = useLocation();
  return <p data-testid="location">{`${location.pathname}${location.search}`}</p>;
}
