/** Human-readable message for a failed login response. */
export function loginErrorMessage(status: number, retryAfter?: string | null): string {
  switch (status) {
    case 401:
      return "Invalid email or password.";
    case 429: {
      const minutes = Math.max(1, Math.ceil(Number(retryAfter ?? "60") / 60));
      return `Too many failed attempts. Try again in ${minutes} minute${minutes === 1 ? "" : "s"}.`;
    }
    case 403:
      return "Request blocked: open the dashboard through its configured address.";
    case 422:
      return "Enter a valid email address and password.";
    default:
      return `Something went wrong (HTTP ${status}). Try again.`;
  }
}
