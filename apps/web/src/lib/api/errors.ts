/** The RFC 9457 problem+json shape every API error uses (see backend acc.api.errors). */
export interface Problem {
  type: string;
  title: string;
  status: number;
  detail?: string;
  request_id?: string;
  [extra: string]: unknown;
}

export class ApiError extends Error {
  readonly status: number;
  readonly problem: Problem;

  constructor(problem: Problem) {
    super(problem.detail || problem.title);
    this.status = problem.status;
    this.problem = problem;
  }
}

/** openapi-fetch gives {data, error} — turn `error` into a throw so React Query's
 * normal error handling (isError, error, onError) works without extra plumbing. */
export function unwrap<T>(result: { data?: T; error?: unknown }): T {
  if (result.error) throw new ApiError(result.error as Problem);
  if (result.data === undefined) throw new Error("Empty response");
  return result.data;
}
