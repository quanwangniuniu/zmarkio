# Stripe Local Development Setup

This guide explains how to set up the Stripe integration locally. Since we use a shared Test Environment, you do not need your own Stripe account.

## 1. Environment Configuration

The project uses a shared set of Test Keys found in `.env.example`.

1.  Open your `.env` file (create one from `.env.example` if needed).
2.  Copy the `STRIPE_*` keys from `.env.example` to your `.env` file.

```ini
STRIPE_SECRET_KEY=sk_test_...  # Must match the one in .env.example
STRIPE_PUBLISHABLE_KEY=pk_test_...
```

## 2. Initialize Plans

The application code expects specific Subscription Plans to exist in the database with IDs that match the Stripe Test Environment.

Run the migration to seed these plans to your local database:

```bash
# Using Docker
docker compose exec backend python manage.py migrate

# Or using local Python
python backend/manage.py migrate
```

**Verification**:
After migration, you should be able to call the `/api/stripe/plans/` endpoint (or check the Plans page in the frontend) and see "Free", "Pro", and "Ultimate" plans with prices.

## 3. Webhook Setup (Crucial for Payments)

To receive payment confirmations (which unlock features locally), you need to forward Stripe events to your local machine.

Since accessing the Stripe Dashboard to add an ngrok URL is restricted, **use the Stripe CLI**.

### Step 3.1: Install Stripe CLI

*   **Mac**: `brew install stripe/stripe-cli/stripe`
*   **Windows**: [Download from Stripe](https://docs.stripe.com/stripe-cli)

### Step 3.2: Listen for Events

You do **NOT** need to log in to the Stripe CLI. You can listen by using the shared API Key directly.

Run this command in your terminal:

```bash
# Replace sk_test_... with the value from your .env file
stripe listen --forward-to localhost:8000/api/stripe/webhook/ --api-key sk_test_...
```

### Step 3.3: Configure Local Webhook Secret

1.  The command above will output a secret starting with `whsec_...`:
    ```
    > Ready! You are using Stripe API Version 2024-12-18. Your webhook signing secret is whsec_12345...
    ```
2.  Copy this `whsec_...` value.
3.  Update your local `.env` file:
    ```ini
    STRIPE_WEBHOOK_SECRET=whsec_12345...
    STRIPE_WEBHOOK_SECRET_NEXT=
    ```
4.  Restart your backend server to apply the change.

`STRIPE_WEBHOOK_SECRET_NEXT` is optional and should normally be left empty. It is used only during webhook signing secret rotation. See `docs/stripe-webhook-secret-rotation.md` for the rotation procedure.

## 4. Testing Payments

1.  Go to the Plans page in the frontend.
2.  Click "Subscribe" on a plan.
3.  You will be redirected to a Stripe Hosted Checkout page.
4.  Use Stripe Test Cards (e.g., `4242 4242 4242 4242`, any future date, any CVC).
5.  After payment, you will be redirected back to the app.
6.  Check your `stripe listen` terminal window. You should see `200 OK` for events like `checkout.session.completed`.
7.  The database will automatically update your user's subscription status.
