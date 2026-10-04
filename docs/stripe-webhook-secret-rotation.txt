# Stripe Webhook Signing Secret Rotation Runbook

This runbook describes how to rotate the Stripe webhook signing secret without interrupting webhook processing.

## Configuration

The application supports two webhook signing secrets:

```ini
STRIPE_WEBHOOK_SECRET=whsec_current
STRIPE_WEBHOOK_SECRET_NEXT=
```

- `STRIPE_WEBHOOK_SECRET` is the current active signing secret.
- `STRIPE_WEBHOOK_SECRET_NEXT` is optional and is used only during a rotation window.

Under normal operation, `STRIPE_WEBHOOK_SECRET_NEXT` should remain empty.

## Rotation Procedure

### 1. Prepare the new signing secret

Obtain the new webhook signing secret for the target Stripe webhook endpoint and environment.

Keep the existing signing secret active. Do not remove or revoke it before the application has been configured to accept the new secret.

### 2. Configure both active secrets

Keep the existing secret as the current secret and set the new secret as the next secret:

```ini
STRIPE_WEBHOOK_SECRET=whsec_current
STRIPE_WEBHOOK_SECRET_NEXT=whsec_new
```

Restart or redeploy the backend so the updated environment variables are loaded.

During this rotation window, the application accepts a webhook when its signature validates with either the current secret or the next secret.

### 3. Verify webhook delivery

Verify that Stripe webhook events are being accepted and processed successfully.

Confirm that:

- valid webhook requests continue to return successful responses;
- there are no unexpected `Invalid signature` responses;
- expected Stripe webhook events continue to be processed.

Do not proceed to remove the old secret if verification is failing.

### 4. Promote the new secret

After the new signing secret is confirmed to be working and the old secret is no longer required by Stripe, promote the new secret to the current setting and clear the next setting:

```ini
STRIPE_WEBHOOK_SECRET=whsec_new
STRIPE_WEBHOOK_SECRET_NEXT=
```

Restart or redeploy the backend again so the final configuration is loaded.

### 5. Final verification

Verify webhook delivery again after promotion.

Confirm that valid webhook events continue to be processed and that the application is running with only the new current signing secret configured.

## Rollback

If webhook verification fails during the rotation window:

1. Keep or restore the previous value of `STRIPE_WEBHOOK_SECRET`.
2. Clear `STRIPE_WEBHOOK_SECRET_NEXT` if the new secret must be withdrawn.
3. Restart or redeploy the backend.
4. Verify that webhook processing has returned to normal.

## Security Notes

- Never commit real Stripe signing secrets to the repository.
- Store signing secrets only in the approved environment or secret-management system.
- Keep `STRIPE_WEBHOOK_SECRET_NEXT` empty outside an active rotation window.
- Remove the old secret after the rotation is successfully completed.
- This procedure rotates webhook signing secrets only. It does not rotate `STRIPE_SECRET_KEY`.
- Automated key rotation is outside the scope of this runbook.
