import createClient from "openapi-fetch";

import type { components, paths } from "./schema";

export type Operator = components["schemas"]["OperatorOut"];
export type Readiness = components["schemas"]["ReadinessOut"];

/**
 * Browser-side API client. Same origin as the dashboard (Caddy routes /api/* to the API),
 * so the HttpOnly session cookie is sent automatically and no CORS is involved.
 */
export const api = createClient<paths>({
  baseUrl: typeof window === "undefined" ? "" : window.location.origin,
  credentials: "same-origin",
  // Resolve fetch lazily so tests can stub it after this module is imported.
  fetch: (request) => globalThis.fetch(request),
});
