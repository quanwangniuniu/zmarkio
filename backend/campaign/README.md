# Campaign platform sync health

The metrics sync implementation in this checkout is Meta's
`meta_ads.services.sync_ad_account`. It records health for each Meta campaign in
the ad account's linked project using `CampaignPlatformIntegration`. Other
campaign platforms do not currently have a metrics sync/OAuth adapter.

Each campaign/account pair stores the last attempt, last successful sync, and a
safe error code (`auth`, `transient`, or `unknown`). Auth failures notify the
campaign owner once per failure episode. Subsequent failures retain the
reconnect warning until a successful sync clears it. Metrics and their last
successful timestamp are retained on failure.

Campaign detail responses expose read-only `platform_integrations`. The
`POST /api/campaigns/{slug}/platform-integrations/{id}/reconnect/` action requires
project access and the original account connector. It starts the existing Meta
OAuth flow with signed state; it does not clear the sync error or refresh tokens
in the background. Consent returns through the existing integration callback.

Apply the campaign and notifications migrations before running the updated web
and sync workers. Status records are created on the next account sync.

Run the focused tests:

```sh
# From backend/
pytest campaign/tests/test_platform_sync.py -o addopts='' --ds=backend.settings --reuse-db

# From frontend/, with the local application running
E2E_USE_EXISTING_SERVER=1 BASE_URL=http://localhost npx playwright test --project=campaign-mock
```

Playwright exercises the real campaign page and API client with mocked backend
responses and an intercepted provider consent page. Pytest covers actual worker
state persistence, notification delivery, and OAuth state generation.
