import { useContext } from "react";

import { ApiContext } from "./apiContext";
import type { ApiClient } from "./client";

export function useApi(): ApiClient {
  const client = useContext(ApiContext);
  if (client === null) {
    throw new Error("useApi must be used inside an ApiProvider.");
  }
  return client;
}
