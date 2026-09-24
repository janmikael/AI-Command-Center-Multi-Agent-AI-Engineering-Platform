/**
 * Only same-site, absolute paths are allowed as post-login destinations.
 * Blocks open redirects such as `//evil.example`, `/\evil.example` or `https://…`.
 */
export function safeNextPath(next: string | string[] | undefined | null): string {
  const value = Array.isArray(next) ? next[0] : next;
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) {
    return "/";
  }
  if (value === "/login" || value.startsWith("/login?")) return "/";
  return value;
}
