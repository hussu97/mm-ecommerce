# Tabby & Tamara Setup Guide — Melting Moments Ecommerce

> This is a step-by-step guide for setting up both BNPL (Buy Now Pay Later) providers for your UAE bakery ecommerce site. Your codebase already has stubs for both providers — this guide covers everything **outside** the code.

---

## Part 1: TABBY

### What is Tabby?
Tabby lets your customers split payments into 4 interest-free installments or pay later. You (the merchant) get paid upfront in full — Tabby takes on the customer default risk.

---

### Step 1: Apply for a Merchant Account

1. Go to **https://tabby.ai/en-AE/business**
2. Click "Get started" or "Apply now"
3. Fill in:
   - Business name (Melting Moments)
   - Business type (Ecommerce / Food & Beverage)
   - Website URL
   - Country (UAE)
   - Contact details

**Documents you'll need:**
- Valid UAE trade license
- Business bank account details (IBAN, bank name, beneficiary name)
- ID/passport of the business owner
- Proof of address (utility bill or bank statement)

**Timeline:** Approval typically takes a few business days. Tabby will assign you an account manager.

---

### Step 2: Get Your Credentials

Once approved, Tabby provides **3 credentials** (first for sandbox/test, then for production):

| Credential | What it is | Where it goes |
|---|---|---|
| **Secret Key** | Server-side API authentication | `TABBY_API_KEY` in `.env` |
| **Public Key** | Webhook signature verification + frontend widgets | `TABBY_PUBLIC_KEY` in `.env` |
| **Merchant Code** | Your unique store identifier | `TABBY_MERCHANT_CODE` in `.env` |

You'll get **sandbox credentials first** for testing. Production keys come after Tabby QA-approves your integration.

---

### Step 3: Access the Merchant Portal

1. Go to **https://merchant.tabby.ai/**
2. Log in with credentials provided during onboarding
3. Set up **Two-Factor Authentication (2FA)** — required for security

**Portal features you'll use:**
- **Analytics Dashboard** — transaction overview, performance metrics
- **Settlement Reports** — track weekly payouts, fees, amounts
- **Refund Processing** — issue refunds directly
- **Dispute Management** — handle customer disputes (production only)
- **User Management** — invite team members with role-based permissions
- **Webhook Management** — configure webhook URLs

---

### Step 4: Configure Webhooks

In the merchant portal (or via API):

1. Register your webhook URL:
   - **Production:** `https://yourdomain.com/api/v1/payments/webhooks/tabby`
   - **Local dev:** Use ngrok (`ngrok http 8000`) then `https://xxxxx.ngrok.io/api/v1/payments/webhooks/tabby`
   - **Important:** `localhost` URLs are NOT allowed

2. Set webhook for both **test** and **live** environments

3. **Webhook events you'll receive:**
   | Event | Meaning |
   |---|---|
   | `payment_approved` | Customer approved, ready to capture |
   | `payment_captured` | Payment successfully captured |
   | `payment_declined` | Customer was declined |
   | `payment_refunded` | Refund processed |
   | `payment_voided` | Payment voided |
   | `payment.closed` | Final confirmation — payment complete |

---

### Step 5: Understand Payment Limits

| Payment Option | Min Order | Max Order |
|---|---|---|
| **Pay Later** (single deferred payment) | AED 50 | AED 1,000 |
| **Installments** (4 payments) | AED 50 | AED 3,000 |

- If an order is outside these limits, Tabby won't show as an option (handled automatically)
- You can set custom limits within these ranges

---

### Step 6: Understand Fees & Settlements

| Item | Details |
|---|---|
| **Commission** | ~6.9% per transaction (varies by business profile) |
| **Fixed fee** | ~AED 1 per transaction |
| **Payout schedule** | Weekly (every Monday, working day) |
| **Payout fee (< AED 2,500)** | AED 25 |
| **Payout fee (≥ AED 2,500)** | AED 6 |
| **You receive** | Full order amount upfront (minus fees) |
| **Customer default risk** | Tabby bears it, not you |

> Exact rates are negotiated during onboarding. Ask your account manager.

---

### Step 7: Test in Sandbox

1. Add sandbox credentials to your `.env`:
   ```
   TABBY_API_KEY=sk_test_xxxxx
   TABBY_PUBLIC_KEY=pk_test_xxxxx
   TABBY_MERCHANT_CODE=your_merchant_code
   ```

2. Use ngrok for webhook testing locally

3. Test the full flow:
   - Customer selects Tabby at checkout
   - Redirect to Tabby payment page
   - Complete test payment
   - Verify webhook received
   - Verify order status updated

4. **Contact Tabby** when testing is complete — they'll QA your integration and then provide production keys

---

### Step 8: Go Live

1. Replace sandbox credentials with production keys in your production `.env`
2. Update webhook URL to your production domain
3. Enable Tabby in your checkout UI (remove "Coming Soon" badge)
4. Monitor first few transactions in the merchant portal

---
---

## Part 2: TAMARA

### What is Tamara?
Similar to Tabby — customers split into 3 interest-free installments or pay later. You get paid upfront. Very popular in UAE/Saudi.

---

### Step 1: Apply for a Merchant Account

1. Go to **https://partners.tamara.co/#/onboarding**
2. Fill in your business details:
   - Business name (Melting Moments)
   - Business type
   - Website URL
   - Country (UAE)
   - Contact info

**Documents you'll need:**
- Valid ID/passport of the business owner (Ultimate Beneficial Owner)
- Recent proof of address (utility bill, less than 3 months old)
- Bank statement (within 3 months) showing:
  - Bank name
  - Beneficiary name (must match your merchant company name)
  - Account number
  - IBAN
  - SWIFT code
- UAE trade license

**Timeline:** A few business days for approval. They'll assign a support contact.

**Support email:** merchant.support@tamara.co

---

### Step 2: Get Your Credentials

Once approved, Tamara provides **3 credentials**:

| Credential | What it is | Where it goes |
|---|---|---|
| **API Token** | Server-side authentication (Bearer token) | `TAMARA_API_KEY` in `.env` |
| **Notification Token** | JWT token for verifying webhooks | Used in webhook verification code |
| **Public Key** | Frontend widgets (product/cart/checkout promos) | Frontend config |

**Environment URLs:**
| Environment | API URL |
|---|---|
| **Sandbox** | `https://api-sandbox.tamara.co` |
| **Production** | `https://api.tamara.co` |

Your `.env` already has `TAMARA_API_URL` — set it to sandbox first, then production.

---

### Step 3: Access the Portals

Tamara has **two portals**:

1. **Partners Portal** — https://partners.tamara.co/
   - Onboarding & credential management
   - Webhook URL configuration
   - Integration settings

2. **Business Manager** — https://business.tamara.co/
   - Settlement details & reports
   - Refund processing
   - Transaction monitoring
   - Order status management

Log in with the credentials provided during onboarding.

---

### Step 4: Configure Webhooks

In the **Partners Portal**:

1. Navigate to webhook settings
2. Register your webhook URL:
   - **Production:** `https://yourdomain.com/api/v1/payments/webhooks/tamara`
   - **Local dev:** Use ngrok, then `https://xxxxx.ngrok.io/api/v1/payments/webhooks/tamara`

3. **Webhook events you'll receive:**

   | Event | Meaning | Action |
   |---|---|---|
   | `order_approved` | Customer approved by Tamara | **Capture the payment** (required!) |
   | `order_authorised` | Payment authorised | Ready for capture |
   | `order_captured` | Payment captured successfully | Fulfill the order |
   | `order_declined` | Customer declined | Show error, suggest alternative |
   | `order_canceled` | Order cancelled | Update order status |
   | `order_refunded` | Refund processed | Update order status |
   | `order_expired` | Order expired | Clean up |

4. **Important: Tamara requires a "capture" step after approval.**
   - When you receive `order_approved`, you must call Tamara's capture API
   - This is different from Stripe/Tabby where payment is auto-captured
   - If you don't capture, the payment expires

5. **Webhook security:**
   - Tamara includes a JWT token (`tamaraToken`) in webhook requests
   - Verify using HS256 algorithm with your Notification Token
   - Also sent in `Authorization: Bearer {token}` header
   - HTTPS required for webhook URLs

**Pro tip:** Test webhook payloads at https://webhook.site before connecting to your app.

---

### Step 5: Understand Payment Limits

| Plan | Min Order | Max Order |
|---|---|---|
| **Pay in 3** (3 installments) | AED 99–100 | AED 3,000–7,000 |
| **Split into 3** | AED 1,500 | Varies |

- Limits vary by customer credit profile
- Tamara auto-hides if the order is outside limits
- Supported customer payment methods: Visa, Mastercard, Apple Pay

---

### Step 6: Understand Fees & Settlements

| Item | Details |
|---|---|
| **Commission** | Negotiated per merchant (not publicly disclosed) |
| **Settlement reports generated** | Every Sunday |
| **Settlement processed** | Every working Tuesday |
| **Min settlement amount** | AED 2,500 |
| **Fee if below AED 2,500** | AED 25 fixed fee |
| **Fee if above AED 2,500** | No fee |
| **Paid to** | Your merchant bank account |

> Contact merchant.support@tamara.co for exact fee schedule.

---

### Step 7: Test in Sandbox

1. Add sandbox credentials to your `.env`:
   ```
   TAMARA_API_KEY=your_sandbox_token
   TAMARA_API_URL=https://api-sandbox.tamara.co
   ```

2. Use ngrok for local webhook testing

3. Test the full flow:
   - Customer selects Tamara at checkout
   - Redirect to Tamara payment page
   - Complete test payment
   - Receive `order_approved` webhook
   - **Capture the payment** via API call
   - Receive `order_captured` webhook
   - Verify order status updated

4. **Notify Tamara** when testing is complete — they verify your integration and provide production credentials

---

### Step 8: Go Live

1. Replace sandbox credentials with production keys:
   ```
   TAMARA_API_KEY=your_production_token
   TAMARA_API_URL=https://api.tamara.co
   ```
2. Update webhook URL to your production domain
3. Enable Tamara in your checkout UI (remove "Coming Soon" badge)
4. Monitor first transactions in Business Manager portal

---
---

## Part 3: KEY DIFFERENCES (Tabby vs Tamara)

| Feature | Tabby | Tamara |
|---|---|---|
| **Installments** | 4 payments | 3 payments |
| **Min order** | AED 50 | AED 99–100 |
| **Max order** | AED 3,000 | AED 3,000–7,000 |
| **Capture step** | Not required (auto) | **Required** (must call capture API) |
| **Settlement day** | Monday | Tuesday |
| **Settlement reports** | In merchant portal | Generated Sunday |
| **Webhook auth** | HMAC-SHA256 with public key | JWT (HS256) with notification token |
| **Portals** | 1 (merchant.tabby.ai) | 2 (partners + business manager) |
| **Sandbox URL** | Same API, test keys | Separate URL (api-sandbox.tamara.co) |

---

## Part 4: YOUR CODEBASE STATUS

Everything below is **already built** in your codebase:

| Component | Status | File |
|---|---|---|
| Provider stubs | Done | `apps/api/app/services/providers/tabby_provider.py` |
| | | `apps/api/app/services/providers/tamara_provider.py` |
| Webhook endpoints | Done (stubs) | `apps/api/app/api/v1/payments.py` |
| Payment service dispatch | Done | `apps/api/app/services/payment_service.py` |
| Environment config | Done | `apps/api/app/core/config.py` + `.env.example` |
| Frontend UI (disabled) | Done | `apps/web/app/[locale]/checkout/page.tsx` |
| DB models | Done | `apps/api/app/models/order.py` |

**What still needs to be coded** (once you have credentials):
1. Replace stubs with actual API calls in `tabby_provider.py` and `tamara_provider.py`
2. Implement webhook signature verification
3. Implement Tamara's capture step
4. Enable Tabby/Tamara in the checkout UI
5. Add promo widgets (optional — "Pay in 4 with Tabby" badges on product pages)

---

## Part 5: RECOMMENDED ORDER OF OPERATIONS

1. **Apply to both** Tabby and Tamara simultaneously (they're independent)
2. **While waiting for approval:** No action needed — stubs are ready
3. **Once approved:** Add sandbox credentials to `.env`
4. **Implement & test** one provider at a time (suggest Tabby first — simpler, no capture step)
5. **Get QA approval** from each provider
6. **Switch to production** credentials
7. **Enable in checkout UI** and go live
