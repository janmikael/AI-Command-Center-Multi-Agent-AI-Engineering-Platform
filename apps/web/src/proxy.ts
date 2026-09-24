import { NextResponse, type NextRequest } from "next/server";

import { SESSION_COOKIE } from "@/lib/auth/constants";

/**
 * Optimistic check only: no session cookie → go to /login.
 * The real check (session valid in the database) happens server-side in the
 * dashboard layout via the API. Never rely on this file for security.
 */
export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  if (pathname === "/login" || request.cookies.has(SESSION_COOKIE)) {
    return NextResponse.next();
  }
  const url = request.nextUrl.clone();
  url.pathname = "/login";
  url.search = pathname === "/" ? "" : `?next=${encodeURIComponent(pathname + search)}`;
  return NextResponse.redirect(url);
}

export const config = {
  matcher: ["/((?!api/|ws|healthz|readyz|_next/static|_next/image|favicon.ico).*)"],
};
