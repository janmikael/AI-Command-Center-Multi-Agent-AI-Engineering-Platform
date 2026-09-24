import "server-only";

import createClient from "openapi-fetch";
import { cookies } from "next/headers";
import { cache } from "react";

import { SESSION_COOKIE } from "@/lib/auth/constants";

import type { Operator } from "./client";
import type { paths } from "./schema";

/** Server components talk to the API directly on the internal Docker network. */
const serverApi = createClient<paths>({
  baseUrl: process.env.API_INTERNAL_URL ?? "http://api:8000",
});

/**
 * The authoritative session check: asks the API whether the cookie's session is valid.
 * Returns null when signed out, expired or revoked. Deduplicated per request.
 */
export const getOperator = cache(async (): Promise<Operator | null> => {
  const session = (await cookies()).get(SESSION_COOKIE);
  if (!session) return null;

  const { data, response } = await serverApi.GET("/api/v1/auth/me", {
    headers: { cookie: `${SESSION_COOKIE}=${session.value}` },
    cache: "no-store",
  });
  if (response.status === 401) return null;
  if (!data) throw new Error(`Session check failed: API returned ${response.status}`);
  return data;
});
