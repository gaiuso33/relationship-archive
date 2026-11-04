import { useContext } from "react";

import { AuthContext } from "./authContext";
import type { AuthContextValue } from "./types";

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value === null) {
    throw new Error("useAuth must be used inside an AuthProvider.");
  }
  return value;
}
