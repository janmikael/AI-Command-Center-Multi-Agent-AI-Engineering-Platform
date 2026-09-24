import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "@/components/auth/login-form";

const router = { replace: vi.fn(), refresh: vi.fn() };
vi.mock("next/navigation", () => ({ useRouter: () => router }));

function stubFetch(response: Response | Error) {
  const fetchMock = vi.fn(async (request: Request) => {
    void request;
    if (response instanceof Error) throw response;
    return response;
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function submit(email = "op@example.com", password = "correct horse battery") {
  const user = userEvent.setup();
  await user.type(screen.getByLabelText("Email"), email);
  await user.type(screen.getByLabelText("Password"), password);
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("LoginForm", () => {
  beforeEach(() => {
    router.replace.mockReset();
    router.refresh.mockReset();
  });

  it("posts credentials and navigates to the requested page", async () => {
    const fetchMock = stubFetch(
      Response.json({ id: "1", email: "op@example.com", display_name: "Op", role: "operator" }),
    );
    render(<LoginForm next="/tasks" />);
    await submit();

    const request = fetchMock.mock.calls[0][0];
    expect(request.method).toBe("POST");
    expect(new URL(request.url).pathname).toBe("/api/v1/auth/login");
    expect(await request.json()).toEqual({
      email: "op@example.com",
      password: "correct horse battery",
    });
    expect(router.replace).toHaveBeenCalledWith("/tasks");
  });

  it("shows a generic error on bad credentials and stays on the page", async () => {
    stubFetch(Response.json({ title: "Invalid email or password" }, { status: 401 }));
    render(<LoginForm next="/" />);
    await submit();

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password.");
    expect(router.replace).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeEnabled();
  });

  it("shows the rate-limit wait time", async () => {
    stubFetch(
      Response.json({}, { status: 429, headers: { "retry-after": "600" } }),
    );
    render(<LoginForm next="/" />);
    await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("10 minutes");
  });

  it("reports a network failure", async () => {
    stubFetch(new TypeError("fetch failed"));
    render(<LoginForm next="/" />);
    await submit();
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach the server");
  });
});
