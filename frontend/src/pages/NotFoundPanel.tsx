import { Link } from "react-router";

import { PageHeading } from "../components/PageHeading";

/** Shown for a person that does not exist, or that belongs to someone else: the two look the same. */
export function NotFoundPanel() {
  return (
    <>
      <PageHeading text="Not found" />
      <p>That could not be found.</p>
      <p>
        <Link to="/people">Back to people</Link>
      </p>
    </>
  );
}
