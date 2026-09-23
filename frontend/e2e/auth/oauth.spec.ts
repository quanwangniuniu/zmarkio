import { test, expect } from "@playwright/test";
import { getOAuthAuthDataBase64, setupLoginMock } from "./fixtures/mockUser";

test("OAuth callback with auth_data: redirect to /campaigns and auth state", async ({ page }) => {
  await setupLoginMock(page);
  const authData = getOAuthAuthDataBase64();
  await page.goto(`/auth/google/callback?auth_data=${encodeURIComponent(authData)}`);
  await page.waitForLoadState("domcontentloaded");

  await expect(page).toHaveURL(/\/(overview|campaigns|profile|tasks)/, { timeout: 10000 });
});

test("OAuth callback then protected route /tasks is accessible", async ({ page }) => {
  await setupLoginMock(page);
  const authData = getOAuthAuthDataBase64();
  await page.goto(`/auth/google/callback?auth_data=${encodeURIComponent(authData)}`);
  await expect(page).toHaveURL(/\/(overview|campaigns|profile|tasks)/, { timeout: 10000 });

  await page.goto("/tasks");
  await page.waitForLoadState("domcontentloaded");
  await expect(page).toHaveURL(/\/tasks/);
});

test("Google OAuth callback forwards state when browser cookies are unavailable", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(document, "cookie", {
      configurable: true,
      get: () => "",
      set: () => undefined,
    });
  });

  let backendCallbackUrl = "";
  await page.route("**/auth/google/callback/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/auth/google/callback/") {
      backendCallbackUrl = route.request().url();
      await route.fulfill({
        status: 400,
        contentType: "application/json",
        body: JSON.stringify({ error: "stubbed backend callback" }),
      });
      return;
    }
    await route.continue();
  });

  await page.goto("/auth/google/callback?code=fake-code&state=signed-query-state");

  await expect.poll(() => backendCallbackUrl).toContain("code=fake-code");
  expect(new URL(backendCallbackUrl).searchParams.get("state")).toBe("signed-query-state");
});

test("Slack OAuth callback submits signed state when no local state is available", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(document, "cookie", {
      configurable: true,
      get: () => "",
      set: () => undefined,
    });
  });

  let callbackPayload: { code?: string; state?: string } | null = null;
  await page.route("**/api/slack/oauth/callback/**", async (route) => {
    callbackPayload = JSON.parse(route.request().postData() || "{}");
    await route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        success: true,
        team_name: "MediaJira",
        team_id: "T_MEDIAJIRA",
      }),
    });
  });

  await page.goto("/slack/callback?code=slack-code&state=signed-slack-state");

  await expect(page.getByText("Connection Successful!")).toBeVisible();
  await expect.poll(() => callbackPayload?.code).toBe("slack-code");
  // Read through a closure like the line above: the route handler assigns
  // `callbackPayload` asynchronously, so a direct read is still narrowed to null.
  await expect.poll(() => callbackPayload?.state).toBe("signed-slack-state");
});
