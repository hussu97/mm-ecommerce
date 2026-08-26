"""
Idempotent seed script for i18n — Language records + UI translations.
Run: cd apps/api && python -m scripts.seed_i18n

**This file is the source of truth for every UI string, not the database.**
`app_setup` runs `seed()` in the API's lifespan hook, so it executes on every
boot, and it overwrites any row whose value differs from the constant below.
Two consequences that are easy to learn the hard way:

* **A migration cannot change a UI string.** It will apply, the API will
  restart, and the seed will put the old text straight back — then invalidate
  the Redis cache, so the restored value is serving within seconds. Migrations
  `121` and `122` were both written before anyone noticed this, deployed green,
  and changed nothing that lasted. Edit the string here instead.
* **The same is true of the console.** The Translations screen writes to the
  database, and this overwrites it on the next deploy. That is why the value
  here and the value a human last typed can disagree.

Removing a key from `ALL_TRANSLATIONS` does *not* delete it — `seed()` only adds
and updates. Retiring a key needs both: delete the line here so it stops being
restored, and a migration to remove the row that already exists.
"""

from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.models.language import Language, UiTranslation

LANGUAGES = [
    {
        "code": "en",
        "name": "English",
        "native_name": "English",
        "direction": "ltr",
        "is_default": True,
        "is_active": True,
        "display_order": 0,
    },
    {
        "code": "ar",
        "name": "Arabic",
        "native_name": "العربية",
        "direction": "rtl",
        "is_default": False,
        "is_active": True,
        "display_order": 1,
    },
]

# (namespace, key, value)
EN_TRANSLATIONS: list[tuple[str, str, str]] = [
    ("nav", "home", "Home"),
    ("nav", "menu", "Menu"),
    ("nav", "about", "About Us"),
    ("nav", "contact", "Contact Us"),
    ("nav", "faq", "FAQ"),
    ("nav", "privacy", "Privacy Policy"),
    ("nav", "cart", "Cart"),
    ("nav", "sign_in", "Sign In"),
    ("nav", "sign_up", "Create Account"),
    ("nav", "sign_out", "Sign Out"),
    ("nav", "my_account", "My Account"),
    ("nav", "all", "All"),
    ("nav", "brownies", "Brownies"),
    ("nav", "cookies", "Cookies"),
    ("nav", "cookie_melt", "Cookie Melt"),
    ("nav", "mix_boxes", "Mix Boxes"),
    ("nav", "desserts", "Desserts"),
    ("footer", "tagline", "Made with 100% Love"),
    (
        "footer",
        "service_area",
        "Baked to order in Sharjah. Brownies, cookies, cookie melts, cakes and "
        "desserts delivered to Dubai, Sharjah, Ajman, Abu Dhabi, Al Ain, Ras Al "
        "Khaimah, Fujairah and Umm Al Quwain.",
    ),
    ("footer", "copyright", "All rights reserved"),
    # common
    ("common", "qty", "Qty"),
    ("common", "previous", "Previous"),
    ("common", "next", "Next"),
    ("common", "page_of", "Page {page} of {pages}"),
    # breadcrumb
    ("breadcrumb", "home", "Home"),
    ("breadcrumb", "cart", "Cart"),
    # product
    ("product", "add_to_cart", "Add to Cart"),
    ("product", "adding", "Adding..."),
    ("product", "select_options", "Select Options"),
    ("product", "select_required_options", "Select required options"),
    ("product", "from", "From"),
    ("product", "added_to_cart", "{name} added to cart"),
    ("product", "failed_to_add", "Failed to add to cart"),
    ("product", "pick_exactly", "Pick {n}"),
    ("product", "pick_range", "Pick {min}–{max}"),
    ("product", "up_to", "Up to {n}"),
    ("product", "add_short", "Add"),
    ("product", "out_of_stock", "Out of Stock"),
    ("product", "select_short", "Select"),
    ("product", "you_may_also_like", "You May Also Like"),
    ("product", "recently_viewed", "Recently Viewed"),
    # cart
    ("cart", "title", "My Cart"),
    ("cart", "item", "item"),
    ("cart", "items", "items"),
    ("cart", "empty_title", "Your cart is empty"),
    (
        "cart",
        "empty_body",
        "Nothing in here yet. Have a look at the menu and find something you'll like.",
    ),
    ("cart", "continue_shopping", "Continue Shopping"),
    ("cart", "order_summary", "Order Summary"),
    ("cart", "subtotal", "Subtotal"),
    ("cart", "discount", "Discount"),
    ("cart", "delivery", "Delivery"),
    ("cart", "calculated_at_checkout", "Calculated at checkout"),
    ("cart", "total", "Total"),
    ("cart", "promo_placeholder", "Promo code"),
    ("cart", "apply", "Apply"),
    ("cart", "proceed_to_checkout", "Proceed to Checkout"),
    ("cart", "continue_shopping_link", "← Continue Shopping"),
    ("cart", "failed_update", "Failed to update quantity"),
    ("cart", "failed_remove", "Failed to remove item"),
    ("cart", "failed_add", "Could not add that. Please try again."),
    # ── Add-on tray and personalisation ───────────────────────────────────────
    ("cart", "addons_title", "Make it a gift"),
    ("cart", "addons_subtitle", "Small touches, added to this order."),
    ("cart", "addon_add", "Add"),
    ("cart", "addon_adding", "Adding…"),
    ("cart", "note_label", "What should we write?"),
    ("cart", "note_placeholder", "Happy birthday, Sara — love from all of us"),
    ("cart", "note_needed", "Add your message to continue"),
    ("cart", "note_saving", "Saving…"),
    ("cart", "note_saved", "Saved"),
    ("cart", "note_save_failed", "Could not save your message. Keep typing to retry."),
    (
        "cart",
        "note_required_before_checkout",
        "Add your message for {product} before checking out.",
    ),
    # ── Free delivery, said only where it can be bounded to a zone ────────────
    ("cart", "free_delivery_here", "Delivery is free in {area}."),
    ("cart", "free_delivery_qualified", "You've earned free delivery in {area}."),
    (
        "cart",
        "free_delivery_remaining",
        "Add {amount} AED more for free delivery in {area}.",
    ),
    ("cart", "free_delivery_progress_label", "Progress towards free delivery"),
    ("cart", "promo_applied", 'Promo code "{code}" applied!'),
    ("cart", "invalid_promo", "Invalid promo code"),
    ("cart", "promo_error", "Failed to validate promo code. Please try again."),
    (
        "cart",
        "promo_verify_at_checkout",
        "You'll verify your mobile number at checkout to use this on a delivery order.",
    ),
    ("cart", "something_wrong", "Something went wrong. Please try again."),
    # common — shared form labels & actions
    ("common", "first_name", "First Name"),
    ("common", "last_name", "Last Name"),
    ("common", "email", "Email"),
    ("common", "phone", "Phone"),
    ("common", "password", "Password"),
    ("common", "confirm_password", "Confirm Password"),
    ("common", "address_line_1", "Address Line 1"),
    ("common", "address_line_2_optional", "Address Line 2 (optional)"),
    ("common", "subtotal", "Subtotal"),
    ("common", "delivery", "Delivery"),
    ("common", "discount", "Discount"),
    ("common", "total", "Total"),
    ("common", "free", "Free"),
    ("common", "save_changes", "Save Changes"),
    ("common", "cancel", "Cancel"),
    ("common", "back", "Back"),
    ("common", "loading", "Loading"),
    ("common", "remove", "Remove"),
    ("common", "edit", "Edit"),
    ("common", "required", "Required"),
    ("common", "email_placeholder", "you@example.com"),
    ("common", "phone_placeholder", "+971 50 000 0000"),
    # checkout
    ("checkout", "email_hint", "Order notifications will be sent to this email"),
    ("checkout", "step_information", "Information"),
    ("checkout", "step_delivery", "Delivery"),
    ("checkout", "step_payment", "Payment"),
    ("checkout", "contact_information", "Contact Information"),
    ("checkout", "delivery_address", "Delivery Address"),
    ("checkout", "delivery_method", "Delivery Method"),
    ("checkout", "review_and_pay", "Review & Pay"),
    ("checkout", "payment_method", "Payment Method"),
    ("checkout", "order_summary", "Order Summary"),
    (
        "checkout",
        "address_hint",
        "Where should we deliver your order?",
    ),
    ("checkout", "loading_addresses", "Loading saved addresses…"),
    ("checkout", "new_address_option", "+ Enter a new address"),
    ("checkout", "address_placeholder", "Building, Street"),
    ("checkout", "address2_placeholder", "Apartment, floor"),
    ("checkout", "first_name_placeholder", "First name"),
    ("checkout", "last_name_placeholder", "Last name"),
    ("checkout", "home_delivery", "Home Delivery"),
    ("checkout", "store_pickup", "Store Pickup"),
    ("checkout", "free_delivery_qualified", "Free delivery — your order qualifies!"),
    # Shown before there is a pin, when whether the offer reaches this customer
    # is still unknown. The qualifier is the whole point of the separate key:
    # promising free delivery flatly and then charging 137 to Abu Dhabi is a
    # broken promise however correct the arithmetic was.
    (
        "checkout",
        "free_delivery_upsell_areas",
        "Add {amount} AED more for free delivery in selected areas",
    ),
    # Shown once the pin lands somewhere the offer cannot reach, so a full
    # basket is not left waiting for a discount that is never coming.
    (
        "checkout",
        "free_delivery_not_in_area",
        "Free delivery isn't available for this address",
    ),
    ("checkout", "delivery_time", "Delivered in 2–3 business days"),
    ("checkout", "free_delivery_upsell", "Add {amount} AED more for free delivery"),
    (
        "checkout",
        "pickup_description",
        "Pickup from our location · We'll notify you when your order is ready",
    ),
    (
        "checkout",
        "delivery_time_note",
        "Orders placed before 12 PM are delivered the next day. We'll send you an email confirmation once your order is packed.",
    ),
    ("checkout", "credit_debit_card", "Credit / Debit Card"),
    ("checkout", "payment_sublabel", "Visa, Mastercard · Apple Pay · Google Pay"),
    # Used in place of the line above when Apple Pay is offered as its own row,
    # so the card row does not name it twice.
    ("checkout", "payment_sublabel_google", "Visa, Mastercard · Google Pay"),
    ("checkout", "cash_on_delivery", "Cash on Collection"),
    ("checkout", "cod_sublabel", "Pay in cash when your order arrives"),
    ("checkout", "cod_pickup_sublabel", "Pay in cash when you collect your order"),
    # Read only by a screen reader. The row it sits on is not a choice — there
    # is one way to pay for a delivery — and sighted customers are told that by
    # the tick and the chosen-state border. Neither of those says anything out
    # loud, so this does.
    (
        "checkout",
        "only_payment_method",
        "Selected — the only payment method for this order",
    ),
    ("checkout", "place_order", "Place Order · {total} AED"),
    ("checkout", "coming_soon", "Coming soon"),
    (
        "checkout",
        "security_note",
        "Payments are processed securely via Stripe. We never store your card details.",
    ),
    ("checkout", "order_notes_label", "Order notes (optional)"),
    ("checkout", "notes_placeholder", "Any special requests or allergies?"),
    ("checkout", "continue_to_delivery", "Continue to Delivery"),
    ("checkout", "continue_to_payment", "Continue to Payment"),
    ("checkout", "pay_now", "Pay Now — {total} AED"),
    ("checkout", "no_items", "No items"),
    ("checkout", "next_step", "Next step"),
    ("checkout", "valid_email_required", "Valid email address is required"),
    ("checkout", "valid_phone_required", "Valid phone number is required"),
    ("checkout", "first_name_required", "First name is required"),
    ("checkout", "last_name_required", "Last name is required"),
    ("checkout", "address_required", "Address is required"),
    ("checkout", "loading_cart", "Loading your cart…"),
    ("checkout", "cart_load_failed", "We couldn't load your cart"),
    ("auth", "password_min", "Password must be at least 8 characters."),
    ("auth", "signup_failed", "Could not create the account. Please try again."),
    ("product", "in_basket", "{n} in basket"),
    ("product", "add_more", "Add more"),
    ("product", "increase", "Increase quantity"),
    ("product", "decrease", "Decrease quantity"),
    ("cart", "remove_item", "Remove from basket"),
    ("checkout", "add_promo_or_note", "Add a promo code or a note"),
    ("checkout", "delivery_option", "Delivery"),
    ("checkout", "fee_from_address", "Confirmed once you add your address"),
    # Shown when the pin has no price at all — nothing can be delivered there.
    # Deliberately says nothing about couriers: who carries an order is not the
    # shopper's business, and naming one here would be the first place the
    # fulfilment map leaked onto the storefront.
    ("checkout", "unserviceable_title", "We can't deliver to this address"),
    (
        "checkout",
        "verify_phone_required",
        "Verify your mobile number to use this code on a delivery order",
    ),
    (
        "checkout",
        "unserviceable_body",
        "This location is outside our delivery range at the moment. Try a nearby "
        "address, or collect your order from the store instead.",
    ),
    ("checkout", "unserviceable_change", "Change address"),
    ("checkout", "unserviceable_pickup", "Collect from store instead"),
    ("checkout", "unserviceable_short", "Delivery unavailable here"),
    # The arrival estimate. "Today"/"Tomorrow" are words; the date and the time
    # are formatted by the browser, which knows the customer's locale far better
    # than a translation table does.
    ("checkout", "estimated_delivery", "Estimated delivery"),
    # ── the small-basket fee ──────────────────────────────────────────────
    ("checkout", "low_order_fee", "Small order fee"),
    (
        "checkout",
        "low_order_fee_info",
        "Orders of {threshold} AED or less carry a {fee} AED fee. It covers the "
        "cost of baking, boxing and getting a small order to you — add {remaining} "
        "AED more and it comes off.",
    ),
    ("checkout", "low_order_fee_remaining", "Add {amount} AED more to remove this fee"),
    ("checkout", "low_order_fee_what_is_this", "What is this?"),
    # ── the new-customer coupon ───────────────────────────────────────────
    ("promo", "new_customer_title", "{percent}% off your first {orders} orders"),
    ("promo", "use_code", "Use code"),
    ("promo", "new_customer_apply", "Apply"),
    ("promo", "new_customer_applied", "Applied"),
    ("promo", "terms_title", "Terms"),
    ("promo", "terms_max", "Up to {max} AED off per order."),
    ("promo", "terms_first_orders", "Valid on your first {orders} orders."),
    (
        "promo",
        "terms_verify",
        "Delivery orders need a verified mobile number.",
    ),
    ("promo", "terms_limited", "Limited time promotion."),
    ("promo", "terms_combine", "Cannot be combined with other offers."),
    ("promo", "terms_more", "Show more"),
    ("promo", "terms_less", "Show less"),
    ("promo", "dismiss_banner", "Dismiss offer"),
    # ── phone verification ────────────────────────────────────────────────
    ("verify", "title", "Verify your mobile number"),
    ("verify", "subtitle", "We'll text you a 6-digit code."),
    ("verify", "send_code", "Send code"),
    ("verify", "resend", "Resend code"),
    ("verify", "code_label", "6-digit code"),
    ("verify", "confirm", "Confirm"),
    ("verify", "verified", "Verified"),
    # Shown when a coupon needs a proved number and the form has none yet — the
    # refusal has to name the step that fixes it, or it reads as a dead end.
    (
        "verify",
        "enter_phone_first",
        "Add your mobile number above, then apply the code again.",
    ),
    ("verify", "failed", "That code didn't match. Try again."),
    ("verify", "too_many", "Too many attempts. Try again in a few minutes."),
    ("verify", "unavailable", "Verification is unavailable right now."),
    # ── delivery speed, by zone ───────────────────────────────────────────
    ("usp", "speed_express", "Get it in {minutes} minutes"),
    ("usp", "speed_same_day", "Get it today"),
    ("usp", "speed_next_day", "Get it tomorrow"),
    ("usp", "free_delivery_here", "Free delivery in {area}"),
    ("usp", "free_delivery_over", "Free delivery over {amount} AED in {area}"),
    ("usp", "delivering_to", "Delivering to {area}"),
    ("usp", "change_area", "Change"),
    # ── product sort ──────────────────────────────────────────────────────
    ("plp", "sort_label", "Sort"),
    ("plp", "sort_default", "Featured"),
    ("plp", "sort_price_asc", "Price: low to high"),
    ("plp", "sort_price_desc", "Price: high to low"),
    ("plp", "bestseller", "Bestseller"),
    ("checkout", "delivery_today", "Today"),
    ("checkout", "delivery_tomorrow", "Tomorrow"),
    # A day with no hour on it. Used where the van is a partner's and naming an
    # hour would be promising something we do not control.
    ("checkout", "delivery_by_day", "{day}"),
    ("checkout", "delivery_by_time", "{day}, {time}"),
    ("checkout", "view_pickup_location", "See where to collect from"),
    ("checkout", "pickup_branch", "Where would you like to collect from?"),
    (
        "checkout",
        "pickup_branch_hint",
        "Your order will be boxed and waiting at the branch you choose.",
    ),
    ("checkout", "pickup_branch_required", "Please choose where to collect from."),
    ("checkout", "pickup_branch_unavailable", "Collection isn't available right now."),
    ("checkout", "branch_directions", "Directions"),
    ("common", "address", "Address"),
    # Kept because a browser holding the previous bundle still asks for them.
    # Nothing in the storefront reads either one since the address stopped being
    # optional: the confirmation is sent to it, and it is the second identity
    # behind the first-order coupon gate.
    ("checkout", "email_optional", "Email (optional)"),
    (
        "checkout",
        "email_optional_hint",
        "We'll send delivery updates for your order to this email.",
    ),
    ("checkout", "email", "Email"),
    ("checkout", "email_required", "Email address is required"),
    (
        "checkout",
        "email_required_hint",
        "We'll send your order confirmation and delivery updates here.",
    ),
    (
        "checkout",
        "address_pin_required",
        "Please drop a pin on the map so we know where to deliver.",
    ),
    # ── what the Place Order button says when something is still missing ──
    # One label per unmet requirement, so the button names the next step
    # instead of refusing a press and leaving the customer to hunt for the
    # reason. Resolved in `checkout-gate.ts`, in the order the form reads.
    ("checkout", "placing_order", "Placing order…"),
    # `pay_now` is not here: it was seeded for the returned-unpaid-order flow
    # long before this button had states, and it already names the figure.
    ("checkout", "choose_store", "Choose a store"),
    ("checkout", "set_address", "Set address"),
    ("checkout", "choose_another_address", "Choose another address"),
    ("checkout", "complete_address_details", "Complete address details"),
    ("checkout", "verify_your_phone", "Verify your phone"),
    ("checkout", "enter_contact_info", "Enter contact info"),
    ("checkout", "enter_email_address", "Enter email address"),
    ("checkout", "calculating_total", "Calculating total…"),
    (
        "checkout",
        "email_signed_in_hint",
        "Your order confirmation and updates will be sent here.",
    ),
    ("checkout", "add_delivery_address", "Add a delivery address"),
    ("checkout", "add_address_hint", "Drop a pin on the map — we'll fill in the rest."),
    (
        "checkout",
        "address_contact_incomplete",
        "Please add a name and phone number to this address.",
    ),
    ("address", "unit_number", "Flat / Office / Floor"),
    ("address", "unit_placeholder", "Flat 1203, 12th floor"),
    ("address", "label", "Save as"),
    ("address", "label_placeholder", "Home"),
    ("address", "default", "Default"),
    ("address", "finding_address", "Finding your address…"),
    ("address", "save_and_continue", "Save address"),
    ("common", "delete", "Delete"),
    ("common", "close", "Close"),
    ("confirmation", "create_account_title", "Save your details for next time"),
    (
        "confirmation",
        "create_account_body",
        "Set a password and your address and order history will be waiting for you.",
    ),
    ("confirmation", "create_account_cta", "Create Account"),
    ("confirmation", "create_account_done", "Account created — you're signed in."),
    ("confirmation", "password_placeholder", "Choose a password"),
    # Shown instead of the sign-up form when we recognise the email. Offering
    # "create an account" to a returning customer is a dead end whose only
    # outcome is "that email is taken".
    ("confirmation", "sign_in_title", "Welcome back — sign in"),
    (
        "confirmation",
        "sign_in_body",
        "You already have an account with this email. Sign in to keep this order with it.",
    ),
    (
        "confirmation",
        "sign_in_body_address",
        "You already have an account with this email. Sign in and we'll save this "
        "delivery address to it for next time.",
    ),
    ("confirmation", "sign_in_password_placeholder", "Your password"),
    ("confirmation", "sign_in_cta", "Sign In"),
    ("confirmation", "signed_in_done", "Signed in — welcome back."),
    (
        "confirmation",
        "signed_in_address_saved",
        "Signed in, and this delivery address is saved to your account.",
    ),
    (
        "confirmation",
        "create_account_body_address",
        "Create an account and we'll save this delivery address to it, so your "
        "next order takes seconds.",
    ),
    (
        "confirmation",
        "create_account_address_saved",
        "Account created, and this delivery address is saved to it.",
    ),
    ("common", "try_again", "Try Again"),
    ("checkout", "cart_empty", "Your cart is empty"),
    ("checkout", "payment_cancelled", "Payment was cancelled. Please try again."),
    # Per-reason decline toasts, keyed by `PaymentFailureReason`. Shown on return
    # to the payment step when the gateway said why the card was refused; the
    # generic `payment_cancelled` above stays the fallback. Worded so none of
    # them names a reason Stripe requires we keep quiet — fraud/lost/stolen all
    # arrive as `card_declined`.
    (
        "checkout",
        "payment_failure.insufficient_funds",
        "Your card was declined — it may not have enough funds. Please try another card.",
    ),
    (
        "checkout",
        "payment_failure.expired_card",
        "Your card has expired. Please try a different card.",
    ),
    (
        "checkout",
        "payment_failure.incorrect_cvc",
        "The card's security code (CVC) looks incorrect. Please check it and try again.",
    ),
    (
        "checkout",
        "payment_failure.incorrect_number",
        "The card number looks incorrect. Please check it and try again.",
    ),
    (
        "checkout",
        "payment_failure.incorrect_details",
        "Your billing details didn't match your card. Please check them and try again.",
    ),
    (
        "checkout",
        "payment_failure.card_not_supported",
        "This card can't be used for this payment. Please try another card.",
    ),
    (
        "checkout",
        "payment_failure.authentication_required",
        "Your bank needs to verify this payment. Please try again and complete the verification.",
    ),
    (
        "checkout",
        "payment_failure.processing_error",
        "Something went wrong processing your card. Please wait a moment and try again.",
    ),
    (
        "checkout",
        "payment_failure.duplicate",
        "This looks like a repeat of a recent payment. Please check whether it already went through before retrying.",
    ),
    (
        "checkout",
        "payment_failure.card_declined",
        "Your card was declined. Please contact your bank or try another card.",
    ),
    ("checkout", "promo_placeholder", "Promo code"),
    ("checkout", "apply", "Apply"),
    ("checkout", "remove_promo", "Remove promo"),
    ("checkout", "promo_applied", 'Promo "{code}" applied!'),
    ("checkout", "invalid_promo", "Invalid promo code"),
    ("checkout", "promo_error", "Could not validate promo code. Please try again."),
    ("breadcrumb", "checkout", "Checkout"),
    # confirmation
    ("confirmation", "title", "Order Placed!"),
    (
        "confirmation",
        "thank_you",
        "Thank you for your order. We'll send a confirmation to {email}.",
    ),
    ("confirmation", "order_number", "Order Number"),
    ("confirmation", "total_paid", "Total Paid"),
    ("confirmation", "total_due", "Total Due on Collection / Delivery"),
    (
        "confirmation",
        "thank_you_no_email",
        "Thank you for your order — we'll be in touch on WhatsApp.",
    ),
    ("confirmation", "delivering_to", "Delivering to"),
    (
        "confirmation",
        "pickup_note",
        "You selected store pickup. We'll contact you via WhatsApp when your order is ready to collect.",
    ),
    ("confirmation", "continue_shopping", "Continue Shopping"),
    ("confirmation", "view_orders", "View My Orders"),
    ("confirmation", "not_found_title", "Order not found"),
    (
        "confirmation",
        "not_found_body",
        "We couldn't retrieve your order details. If you completed payment, you'll receive a confirmation email shortly.",
    ),
    ("confirmation", "back_to_home", "Back to Home"),
    ("confirmation", "loading", "Loading your order…"),
    # auth
    ("auth", "welcome_back", "Welcome Back"),
    ("auth", "sign_in_subtitle", "Sign in to your Melting Moments account"),
    ("auth", "forgot_password_link", "Forgot password?"),
    ("auth", "or_divider", "or"),
    ("auth", "no_account", "Don't have an account?"),
    ("auth", "sign_up_link", "Sign up"),
    ("auth", "just_browsing", "Just browsing?"),
    ("auth", "continue_as_guest", "Continue as guest"),
    ("auth", "login_failed", "Login failed. Please try again."),
    ("auth", "create_account", "Create Account"),
    (
        "auth",
        "create_account_subtitle",
        "Join Melting Moments for a sweeter experience",
    ),
    ("auth", "password_helper", "At least 8 characters"),
    ("auth", "tos_text", "By signing up you agree to our"),
    ("auth", "tos_privacy", "Privacy Policy"),
    ("auth", "already_have_account", "Already have an account?"),
    ("auth", "registration_failed", "Registration failed. Please try again."),
    ("auth", "reset_password", "Reset Password"),
    ("auth", "reset_subtitle", "Enter your email and we'll send you a reset link."),
    ("auth", "send_reset_link", "Send Reset Link"),
    ("auth", "back_to_sign_in", "Back to Sign In"),
    ("auth", "check_email_title", "Check Your Email"),
    (
        "auth",
        "check_email_body",
        "If an account exists for {email}, we've sent a password reset link. Check your inbox (and spam folder).",
    ),
    ("auth", "set_new_password", "Set New Password"),
    ("auth", "set_password_subtitle", "Choose a strong password for your account."),
    ("auth", "new_password", "New Password"),
    ("auth", "update_password", "Update Password"),
    ("auth", "password_updated", "Password Updated"),
    (
        "auth",
        "password_updated_body",
        "Your password has been changed. You can now sign in with your new password.",
    ),
    ("auth", "invalid_link", "Invalid Reset Link"),
    (
        "auth",
        "invalid_link_body",
        "This password reset link is invalid or has expired.",
    ),
    ("auth", "request_new_link", "Request a new link"),
    ("auth", "reset_failed", "Reset failed. The link may have expired."),
    ("auth", "email_required", "Email is required"),
    ("auth", "password_required", "Password is required"),
    ("auth", "password_min_length", "Password must be at least 8 characters"),
    ("auth", "passwords_no_match", "Passwords do not match"),
    # account
    ("account", "hello", "Hello, {name}"),
    ("account", "welcome", "Welcome to your Melting Moments account."),
    ("account", "my_orders", "My Orders"),
    ("account", "my_orders_desc", "Track and view your past orders"),
    ("account", "addresses", "Addresses"),
    ("account", "addresses_desc", "Manage your saved delivery addresses"),
    ("account", "settings", "Settings"),
    ("account", "settings_desc", "Edit your profile and password"),
    ("account", "member_since", "Member since {date}"),
    ("account", "signed_in_as", "Signed in as"),
    ("account", "my_profile", "My Profile"),
    # order
    ("order", "status_pending", "Pending"),
    ("order", "status_confirmed", "Confirmed"),
    ("order", "status_packed", "Packed"),
    ("order", "status_cancelled", "Cancelled"),
    ("order", "status_payment_failed", "Payment Failed"),
    ("order", "my_orders", "My Orders"),
    ("order", "no_orders", "You haven't placed any orders yet."),
    ("order", "start_shopping", "Start shopping"),
    ("order", "failed_to_load", "Failed to load orders."),
    ("order", "placed", "Placed"),
    ("order", "order_progress", "Order Progress"),
    ("order", "items", "Items"),
    ("order", "summary", "Summary"),
    ("order", "cancellation_note", "Cancellation Note"),
    ("order", "back_to_orders", "Back to Orders"),
    ("order", "not_found", "Order not found."),
    ("order", "payment", "Payment"),
    ("order", "notes", "Notes"),
    ("order", "no_address", "No address on file"),
    ("order", "store_pickup", "Store Pickup"),
    ("order", "timeline_placed", "Order Placed"),
    ("order", "timeline_confirmed", "Order Confirmed"),
    ("order", "timeline_ready", "Ready / Dispatched"),
    # ── Fulfilment: when it arrives, where to collect, whether it can be watched.
    #    Read by `components/order/FulfilmentPanel.tsx`, which serves both the
    #    account order page and the guest tracking page.
    ("order", "status_out_for_delivery", "Out for Delivery"),
    ("order", "status_delivered", "Delivered"),
    # A rider reached the door and could not hand it over. Its own status on
    # the order now, not just a note on the courier record.
    ("order", "status_undelivered", "Delivery Attempted"),
    ("order", "status_collected", "Collected"),
    ("order", "status_refunded", "Refunded"),
    ("order", "status_disputed", "Disputed"),
    ("order", "estimate_delivery", "Estimated delivery"),
    ("order", "estimate_ready", "Ready to collect"),
    ("order", "estimate_ready_since", "Ready since"),
    ("order", "estimate_arriving", "Arriving"),
    ("order", "estimate_delivered", "Delivered"),
    ("order", "estimate_collected", "Collected"),
    (
        "order",
        "estimate_note_rider",
        "Now that a driver is carrying it, this is our best estimate.",
    ),
    ("order", "estimate_note_day", "We'll confirm a time once it's collected."),
    # A date bounded by an hour, for the `day_by` precision. Their van, their
    # schedule — so this is a commitment the customer can plan around without
    # borrowing a precision that is not ours. Mirrors the mailer's
    # `date.by_time` in `email_copy.py`, which has always rendered it.
    ("order", "estimate_by_time", "{day} before {time}"),
    ("order", "track_live", "Track live"),
    (
        "order",
        "track_live_pending",
        "A live tracking link will appear here once a driver collects your order.",
    ),
    ("order", "step_preparing", "Baking your order"),
    ("order", "step_ready", "Packed"),
    ("order", "step_on_the_way", "Out for delivery"),
    ("order", "step_delivered", "Delivered"),
    ("order", "step_pickup_preparing", "Baking your order"),
    ("order", "step_pickup_ready", "Ready to collect"),
    ("order", "step_pickup_collected", "Collected"),
    ("order", "collect_from", "Collect from"),
    ("order", "branch_open", "Open"),
    ("order", "branch_phone", "Phone"),
    ("order", "open_in_maps", "Open in Google Maps"),
    ("order", "undelivered_title", "Delivery attempted"),
    (
        "order",
        "undelivered_body",
        "Our driver reached your address but couldn't complete the delivery. "
        "Your order is safe with us and we'll be in touch to arrange another attempt.",
    ),
    ("order", "delivery_address", "Delivering to"),
    # address
    ("address", "title", "Addresses"),
    ("address", "add_address", "Add Address"),
    ("address", "edit_address", "Edit Address"),
    ("address", "new_address", "New Address"),
    ("address", "label_hint", "Label (e.g. Home, Work)"),
    ("address", "set_as_default", "Set as default address"),
    ("address", "default_badge", "Default"),
    ("address", "set_default", "Set Default"),
    ("address", "removing", "Removing..."),
    ("address", "no_addresses", "No saved addresses yet."),
    ("address", "add_first", "Add Your First Address"),
    # settings
    ("settings", "title", "Settings"),
    ("settings", "profile_info", "Profile Information"),
    ("settings", "email_helper", "Contact support to change your email address"),
    ("settings", "phone_optional", "Phone (optional)"),
    ("settings", "change_password", "Change Password"),
    (
        "settings",
        "password_desc",
        "We'll send a reset link to your email address so you can choose a new password.",
    ),
    ("settings", "send_reset_email", "Send Password Reset Email"),
    ("settings", "reset_sent", "Password reset email sent"),
    (
        "settings",
        "reset_sent_body",
        "A password reset link has been sent to {email}. Check your inbox and follow the instructions.",
    ),
    ("settings", "delete_account", "Delete Account"),
    (
        "settings",
        "delete_desc",
        "Permanently delete your account and all associated data. This action cannot be undone.",
    ),
    ("settings", "delete_button", "Delete My Account"),
    ("settings", "delete_confirm", "Are you sure?"),
    (
        "settings",
        "delete_instructions",
        "To delete your account, please contact us at {email} or via {whatsapp}. We'll process your request within 48 hours.",
    ),
    ("settings", "profile_updated", "Profile updated"),
    ("settings", "failed_update", "Failed to update profile"),
    # search
    ("search", "title", "Search"),
    ("search", "results_for", 'Results for "{q}"'),
    ("search", "product_count", "{count} {label} found"),
    ("search", "product_singular", "product"),
    ("search", "product_plural", "products"),
    ("search", "empty_prompt", "Enter a search term to find products"),
    ("search", "no_results", 'No products found for "{q}"'),
    ("search", "no_results_hint", "Try a different search term or browse our {link}."),
    ("search", "categories_link", "categories"),
    # contact
    ("contact", "eyebrow", "Reach Out"),
    ("contact", "whatsapp", "WhatsApp"),
    ("contact", "email_label", "Email"),
    ("contact", "location", "Location"),
    ("contact", "hours", "Hours"),
    ("contact", "message_us", "Message us"),
    ("contact", "send_email", "Send email"),
    ("contact", "follow_along", "Follow Along"),
    # about
    ("about", "our_story", "Our Story"),
    ("about", "our_promise", "Our Promise"),
    ("about", "what_we_stand_for", "What we stand for"),
    ("about", "get_in_touch", "Get in Touch"),
    # faq
    ("faq", "help_centre", "Help Centre"),
    ("faq", "still_have_questions", "Still have questions?"),
    # track
    ("track", "title", "Track Your Order"),
    ("track", "subtitle", "Enter your order number and email to check the status."),
    ("track", "order_number", "Order Number"),
    ("track", "email_address", "Email Address"),
    ("track", "track_button", "Track Order"),
    ("track", "validation_error", "Please enter your order number and email."),
    ("track", "generic_error", "Something went wrong. Please try again."),
    ("track", "order_label", "Order"),
    ("track", "status", "Status"),
    ("track", "delivery", "Delivery"),
    ("track", "items", "Items"),
    ("track", "placed", "Placed"),
    ("track", "total", "Total"),
    ("track", "view_full_order", "Sign in to see the full order"),
    # error / not-found
    ("error", "not_found_title", "Page Not Found"),
    (
        "error",
        "not_found_body",
        "The page you're looking for doesn't exist. It may have been moved, or the link is incorrect.",
    ),
    ("error", "back_to_home", "Back to Home"),
    ("error", "contact_us", "Contact Us"),
    ("error", "tagline", "Made with 100% Love"),
    # address labels
    ("address", "pin_location", "Pin Location"),
    ("address", "search_location", "Search for a location…"),
    (
        "checkout",
        "pin_location_required",
        "Please drop a pin on the map to confirm your delivery location",
    ),
]

AR_TRANSLATIONS: list[tuple[str, str, str]] = [
    # (namespace, key, value)
    ("nav", "home", "الرئيسية"),
    ("nav", "menu", "القائمة"),
    ("nav", "about", "من نحن"),
    ("nav", "contact", "تواصل معنا"),
    ("nav", "faq", "الأسئلة الشائعة"),
    ("nav", "privacy", "سياسة الخصوصية"),
    ("nav", "cart", "سلة التسوق"),
    ("nav", "sign_in", "تسجيل الدخول"),
    ("nav", "sign_up", "إنشاء حساب"),
    ("nav", "sign_out", "تسجيل الخروج"),
    ("nav", "my_account", "حسابي"),
    ("nav", "all", "الكل"),
    ("nav", "brownies", "براونيز"),
    ("nav", "cookies", "كوكيز"),
    ("nav", "cookie_melt", "كوكي ميلت"),
    ("nav", "mix_boxes", "صناديق مشكلة"),
    ("nav", "desserts", "حلويات"),
    ("footer", "tagline", "مصنوعة بـ 100% حب"),
    (
        "footer",
        "service_area",
        "تُخبز عند الطلب في الشارقة. براوني وكوكيز وكوكي ملت وكيك وحلويات تُوصَّل "
        "إلى دبي والشارقة وعجمان وأبوظبي والعين ورأس الخيمة والفجيرة وأم القيوين.",
    ),
    ("footer", "copyright", "جميع الحقوق محفوظة"),
    # common
    ("common", "qty", "الكمية"),
    ("common", "previous", "السابق"),
    ("common", "next", "التالي"),
    ("common", "page_of", "صفحة {page} من {pages}"),
    # breadcrumb
    ("breadcrumb", "home", "الرئيسية"),
    ("breadcrumb", "cart", "سلة التسوق"),
    # product
    ("product", "add_to_cart", "أضف للسلة"),
    ("product", "adding", "جاري الإضافة..."),
    ("product", "select_options", "اختر الخيارات"),
    ("product", "select_required_options", "اختر الخيارات المطلوبة"),
    ("product", "from", "من"),
    ("product", "added_to_cart", "أُضيف {name} إلى السلة"),
    ("product", "failed_to_add", "فشل الإضافة إلى السلة"),
    ("product", "pick_exactly", "اختر {n}"),
    ("product", "pick_range", "اختر {min}–{max}"),
    ("product", "up_to", "حتى {n}"),
    ("product", "add_short", "أضف"),
    ("product", "out_of_stock", "نفذ من المخزون"),
    ("product", "select_short", "اختر"),
    ("product", "you_may_also_like", "قد يعجبك أيضاً"),
    ("product", "recently_viewed", "شاهدت مؤخراً"),
    # cart
    ("cart", "title", "سلة التسوق"),
    ("cart", "item", "منتج"),
    ("cart", "items", "منتجات"),
    ("cart", "empty_title", "سلتك فارغة"),
    ("cart", "empty_body", "لم تضف شيئاً بعد. تصفّح القائمة وستجد ما يعجبك."),
    ("cart", "continue_shopping", "متابعة التسوق"),
    ("cart", "order_summary", "ملخص الطلب"),
    ("cart", "subtotal", "المجموع الفرعي"),
    ("cart", "discount", "الخصم"),
    ("cart", "delivery", "التوصيل"),
    ("cart", "calculated_at_checkout", "يحسب عند الدفع"),
    ("cart", "total", "الإجمالي"),
    ("cart", "promo_placeholder", "رمز الخصم"),
    ("cart", "apply", "تطبيق"),
    ("cart", "proceed_to_checkout", "المتابعة للدفع"),
    ("cart", "continue_shopping_link", "→ متابعة التسوق"),
    ("cart", "failed_update", "فشل تحديث الكمية"),
    ("cart", "failed_remove", "فشل حذف المنتج"),
    ("cart", "failed_add", "تعذّرت الإضافة. حاول مرة أخرى."),
    # ── Add-on tray and personalisation ───────────────────────────────────────
    ("cart", "addons_title", "اجعلها هدية"),
    ("cart", "addons_subtitle", "لمسات صغيرة تُضاف إلى هذا الطلب."),
    ("cart", "addon_add", "إضافة"),
    ("cart", "addon_adding", "جارٍ الإضافة…"),
    ("cart", "note_label", "ماذا نكتب لك؟"),
    ("cart", "note_placeholder", "كل عام وأنتِ بخير يا سارة — مع حبنا"),
    ("cart", "note_needed", "أضف رسالتك للمتابعة"),
    ("cart", "note_saving", "جارٍ الحفظ…"),
    ("cart", "note_saved", "تم الحفظ"),
    ("cart", "note_save_failed", "تعذّر حفظ رسالتك. تابع الكتابة لإعادة المحاولة."),
    (
        "cart",
        "note_required_before_checkout",
        "أضف رسالتك الخاصة بـ {product} قبل إتمام الطلب.",
    ),
    # ── Free delivery, said only where it can be bounded to a zone ────────────
    ("cart", "free_delivery_here", "التوصيل مجاني في {area}."),
    ("cart", "free_delivery_qualified", "حصلت على توصيل مجاني في {area}."),
    (
        "cart",
        "free_delivery_remaining",
        "أضف {amount} درهم للحصول على توصيل مجاني في {area}.",
    ),
    ("cart", "free_delivery_progress_label", "التقدّم نحو التوصيل المجاني"),
    ("cart", "promo_applied", 'تم تطبيق رمز الخصم "{code}"!'),
    ("cart", "invalid_promo", "رمز الخصم غير صحيح"),
    ("cart", "promo_error", "فشل التحقق من رمز الخصم. حاول مجدداً."),
    (
        "cart",
        "promo_verify_at_checkout",
        "ستتحقق من رقم هاتفك عند الدفع لاستخدامه على طلب توصيل.",
    ),
    ("cart", "something_wrong", "حدث خطأ. حاول مجدداً."),
    # common — shared form labels & actions
    ("common", "first_name", "الاسم الأول"),
    ("common", "last_name", "اسم العائلة"),
    ("common", "email", "البريد الإلكتروني"),
    ("common", "phone", "الهاتف"),
    ("common", "password", "كلمة المرور"),
    ("common", "confirm_password", "تأكيد كلمة المرور"),
    ("common", "address_line_1", "العنوان - السطر الأول"),
    ("common", "address_line_2_optional", "العنوان - السطر الثاني (اختياري)"),
    ("common", "subtotal", "المجموع الفرعي"),
    ("common", "delivery", "التوصيل"),
    ("common", "discount", "الخصم"),
    ("common", "total", "الإجمالي"),
    ("common", "free", "مجاني"),
    ("common", "save_changes", "حفظ التغييرات"),
    ("common", "cancel", "إلغاء"),
    ("common", "back", "رجوع"),
    ("common", "loading", "جارٍ التحميل"),
    ("common", "remove", "إزالة"),
    ("common", "edit", "تعديل"),
    ("common", "required", "مطلوب"),
    ("common", "email_placeholder", "you@example.com"),
    ("common", "phone_placeholder", "+971 50 000 0000"),
    # checkout
    ("checkout", "email_hint", "ستُرسل إشعارات الطلب إلى هذا البريد الإلكتروني"),
    ("checkout", "step_information", "المعلومات"),
    ("checkout", "step_delivery", "التوصيل"),
    ("checkout", "step_payment", "الدفع"),
    ("checkout", "contact_information", "معلومات التواصل"),
    ("checkout", "delivery_address", "عنوان التوصيل"),
    ("checkout", "delivery_method", "طريقة التوصيل"),
    ("checkout", "review_and_pay", "المراجعة والدفع"),
    ("checkout", "payment_method", "طريقة الدفع"),
    ("checkout", "order_summary", "ملخص الطلب"),
    (
        "checkout",
        "address_hint",
        "أين نوصّل طلبك؟",
    ),
    ("checkout", "loading_addresses", "جارٍ تحميل العناوين المحفوظة…"),
    ("checkout", "new_address_option", "+ إدخال عنوان جديد"),
    ("checkout", "address_placeholder", "المبنى، الشارع"),
    ("checkout", "address2_placeholder", "الشقة، الطابق"),
    ("checkout", "first_name_placeholder", "الاسم الأول"),
    ("checkout", "last_name_placeholder", "اسم العائلة"),
    ("checkout", "home_delivery", "التوصيل المنزلي"),
    ("checkout", "store_pickup", "الاستلام من المتجر"),
    ("checkout", "free_delivery_qualified", "توصيل مجاني — طلبك يستوفي الشرط!"),
    (
        "checkout",
        "free_delivery_upsell_areas",
        "أضف {amount} درهم أكثر للتوصيل المجاني في مناطق مختارة",
    ),
    (
        "checkout",
        "free_delivery_not_in_area",
        "التوصيل المجاني غير متاح لهذا العنوان",
    ),
    ("checkout", "delivery_time", "التوصيل خلال 2–3 أيام عمل"),
    ("checkout", "free_delivery_upsell", "أضف {amount} درهم أكثر للتوصيل المجاني"),
    (
        "checkout",
        "pickup_description",
        "الاستلام من موقعنا · سنُخطرك حين يكون طلبك جاهزاً",
    ),
    (
        "checkout",
        "delivery_time_note",
        "الطلبات قبل الساعة 12 ظهراً تُسلَّم في اليوم التالي. سنُرسل لك تأكيداً عبر البريد الإلكتروني حين يُعبَّأ طلبك.",
    ),
    ("checkout", "credit_debit_card", "بطاقة ائتمانية / مدى"),
    ("checkout", "payment_sublabel", "فيزا، ماستركارد · آبل باي · جوجل باي"),
    ("checkout", "payment_sublabel_google", "فيزا، ماستركارد · جوجل باي"),
    ("checkout", "cash_on_delivery", "الدفع نقداً عند الاستلام"),
    ("checkout", "cod_sublabel", "ادفع نقداً عند وصول طلبك"),
    ("checkout", "cod_pickup_sublabel", "ادفع نقداً عند استلام طلبك"),
    (
        "checkout",
        "only_payment_method",
        "محددة — طريقة الدفع الوحيدة لهذا الطلب",
    ),
    ("checkout", "place_order", "تأكيد الطلب · {total} درهم"),
    ("checkout", "coming_soon", "قريباً"),
    (
        "checkout",
        "security_note",
        "تُعالَج المدفوعات بأمان عبر Stripe. لا نحفظ بيانات بطاقتك أبداً.",
    ),
    ("checkout", "order_notes_label", "ملاحظات الطلب (اختياري)"),
    ("checkout", "notes_placeholder", "أي طلبات خاصة أو حساسية من مكونات؟"),
    ("checkout", "continue_to_delivery", "المتابعة للتوصيل"),
    ("checkout", "continue_to_payment", "المتابعة للدفع"),
    ("checkout", "pay_now", "الدفع — {total} درهم"),
    ("checkout", "no_items", "لا توجد منتجات"),
    ("checkout", "next_step", "الخطوة التالية"),
    ("checkout", "valid_email_required", "البريد الإلكتروني الصحيح مطلوب"),
    ("checkout", "valid_phone_required", "رقم الهاتف الصحيح مطلوب"),
    ("checkout", "first_name_required", "الاسم الأول مطلوب"),
    ("checkout", "last_name_required", "اسم العائلة مطلوب"),
    ("checkout", "address_required", "العنوان مطلوب"),
    ("checkout", "loading_cart", "جارٍ تحميل سلتك…"),
    ("checkout", "cart_load_failed", "تعذّر تحميل سلتك"),
    ("auth", "password_min", "يجب أن تتكون كلمة المرور من 8 أحرف على الأقل."),
    ("auth", "signup_failed", "تعذّر إنشاء الحساب. حاول مرة أخرى."),
    ("product", "in_basket", "{n} في السلة"),
    ("product", "add_more", "أضف المزيد"),
    ("product", "increase", "زيادة الكمية"),
    ("product", "decrease", "تقليل الكمية"),
    ("cart", "remove_item", "إزالة من السلة"),
    ("checkout", "add_promo_or_note", "إضافة رمز خصم أو ملاحظة"),
    ("checkout", "delivery_option", "التوصيل"),
    ("checkout", "fee_from_address", "يُحتسب بدقة بعد إضافة عنوانك"),
    ("checkout", "unserviceable_title", "لا يمكننا التوصيل إلى هذا العنوان"),
    (
        "checkout",
        "verify_phone_required",
        "تحقق من رقم هاتفك لاستخدام هذا الرمز على طلب توصيل",
    ),
    (
        "checkout",
        "unserviceable_body",
        "هذا الموقع خارج نطاق التوصيل لدينا حالياً. جرّب عنواناً قريباً، أو استلم "
        "طلبك من المتجر.",
    ),
    ("checkout", "unserviceable_change", "تغيير العنوان"),
    ("checkout", "unserviceable_pickup", "الاستلام من المتجر بدلاً من ذلك"),
    ("checkout", "unserviceable_short", "التوصيل غير متاح هنا"),
    ("checkout", "estimated_delivery", "موعد التوصيل المتوقع"),
    # ── the small-basket fee ──────────────────────────────────────────────
    ("checkout", "low_order_fee", "رسوم الطلب الصغير"),
    (
        "checkout",
        "low_order_fee_info",
        "الطلبات بقيمة {threshold} درهم أو أقل تُضاف إليها رسوم {fee} درهم. تغطي "
        "تكلفة التحضير والتغليف وتوصيل الطلب الصغير — أضف {remaining} درهم أكثر "
        "وتُلغى الرسوم.",
    ),
    ("checkout", "low_order_fee_remaining", "أضف {amount} درهم أكثر لإلغاء هذه الرسوم"),
    ("checkout", "low_order_fee_what_is_this", "ما هذا؟"),
    # ── the new-customer coupon ───────────────────────────────────────────
    ("promo", "new_customer_title", "خصم {percent}% على أول {orders} طلبات"),
    ("promo", "use_code", "استخدم كود"),
    ("promo", "new_customer_apply", "تطبيق"),
    ("promo", "new_customer_applied", "تم التطبيق"),
    ("promo", "terms_title", "الشروط"),
    ("promo", "terms_max", "خصم يصل إلى {max} درهم لكل طلب."),
    ("promo", "terms_first_orders", "صالح على أول {orders} طلبات لك."),
    ("promo", "terms_verify", "طلبات التوصيل تتطلب رقم هاتف متحقق منه."),
    ("promo", "terms_limited", "عرض لفترة محدودة."),
    ("promo", "terms_combine", "لا يمكن دمجه مع العروض الأخرى."),
    ("promo", "terms_more", "عرض المزيد"),
    ("promo", "terms_less", "عرض أقل"),
    ("promo", "dismiss_banner", "إخفاء العرض"),
    # ── phone verification ────────────────────────────────────────────────
    ("verify", "title", "تحقق من رقم هاتفك"),
    ("verify", "subtitle", "سنرسل لك رمزاً من 6 أرقام."),
    ("verify", "send_code", "إرسال الرمز"),
    ("verify", "resend", "إعادة إرسال الرمز"),
    ("verify", "code_label", "الرمز المكوّن من 6 أرقام"),
    ("verify", "confirm", "تأكيد"),
    ("verify", "verified", "تم التحقق"),
    (
        "verify",
        "enter_phone_first",
        "أضف رقم هاتفك أعلاه ثم طبّق الكود مرة أخرى.",
    ),
    ("verify", "failed", "الرمز غير مطابق. حاول مرة أخرى."),
    ("verify", "too_many", "محاولات كثيرة. حاول بعد بضع دقائق."),
    ("verify", "unavailable", "التحقق غير متاح حالياً."),
    # ── delivery speed, by zone ───────────────────────────────────────────
    ("usp", "speed_express", "استلمه خلال {minutes} دقيقة"),
    ("usp", "speed_same_day", "استلمه اليوم"),
    ("usp", "speed_next_day", "استلمه غداً"),
    ("usp", "free_delivery_here", "توصيل مجاني في {area}"),
    ("usp", "free_delivery_over", "توصيل مجاني فوق {amount} درهم في {area}"),
    ("usp", "delivering_to", "التوصيل إلى {area}"),
    ("usp", "change_area", "تغيير"),
    # ── product sort ──────────────────────────────────────────────────────
    ("plp", "sort_label", "ترتيب"),
    ("plp", "sort_default", "المميزة"),
    ("plp", "sort_price_asc", "السعر: من الأقل للأعلى"),
    ("plp", "sort_price_desc", "السعر: من الأعلى للأقل"),
    ("plp", "bestseller", "الأكثر مبيعاً"),
    ("checkout", "delivery_today", "اليوم"),
    ("checkout", "delivery_tomorrow", "غداً"),
    ("checkout", "delivery_by_day", "{day}"),
    ("checkout", "delivery_by_time", "{day}، {time}"),
    ("checkout", "view_pickup_location", "شاهد مكان الاستلام"),
    ("checkout", "pickup_branch", "من أين تود الاستلام؟"),
    (
        "checkout",
        "pickup_branch_hint",
        "سيتم تجهيز طلبك وانتظارك في الفرع الذي تختاره.",
    ),
    ("checkout", "pickup_branch_required", "يرجى اختيار مكان الاستلام."),
    ("checkout", "pickup_branch_unavailable", "الاستلام من المتجر غير متاح حالياً."),
    ("checkout", "branch_directions", "الاتجاهات"),
    ("common", "address", "العنوان"),
    # See the note on the English pair: unread since the address stopped being
    # optional, kept for browsers still holding the previous bundle.
    ("checkout", "email_optional", "البريد الإلكتروني (اختياري)"),
    (
        "checkout",
        "email_optional_hint",
        "سنرسل تحديثات توصيل طلبك إلى هذا البريد الإلكتروني.",
    ),
    ("checkout", "email", "البريد الإلكتروني"),
    ("checkout", "email_required", "البريد الإلكتروني مطلوب"),
    (
        "checkout",
        "email_required_hint",
        "سنرسل تأكيد طلبك وتحديثات التوصيل إلى هذا البريد الإلكتروني.",
    ),
    (
        "checkout",
        "address_pin_required",
        "يرجى تحديد موقعك على الخريطة لنعرف أين نوصل طلبك.",
    ),
    # ── ما يقوله زر تأكيد الطلب حين ينقص شيء ──
    ("checkout", "placing_order", "جارٍ تأكيد الطلب…"),
    # `pay_now` is already seeded above for the returned-unpaid-order flow.
    ("checkout", "choose_store", "اختر الفرع"),
    ("checkout", "set_address", "حدّد العنوان"),
    ("checkout", "choose_another_address", "اختر عنواناً آخر"),
    ("checkout", "complete_address_details", "أكمل بيانات العنوان"),
    ("checkout", "verify_your_phone", "وثّق رقم هاتفك"),
    ("checkout", "enter_contact_info", "أدخل بيانات التواصل"),
    ("checkout", "enter_email_address", "أدخل البريد الإلكتروني"),
    ("checkout", "calculating_total", "جارٍ حساب الإجمالي…"),
    (
        "checkout",
        "email_signed_in_hint",
        "سيصلك تأكيد الطلب وتحديثاته على هذا البريد الإلكتروني",
    ),
    ("checkout", "add_delivery_address", "أضف عنوان التوصيل"),
    ("checkout", "add_address_hint", "حدّد موقعك على الخريطة وسنكمل الباقي."),
    (
        "checkout",
        "address_contact_incomplete",
        "الرجاء إضافة الاسم ورقم الهاتف لهذا العنوان.",
    ),
    ("address", "unit_number", "شقة / مكتب / طابق"),
    ("address", "unit_placeholder", "شقة 1203، الطابق 12"),
    ("address", "label", "حفظ باسم"),
    ("address", "label_placeholder", "المنزل"),
    ("address", "default", "الافتراضي"),
    ("address", "finding_address", "جارٍ تحديد عنوانك…"),
    ("address", "save_and_continue", "حفظ العنوان"),
    ("common", "delete", "حذف"),
    ("common", "close", "إغلاق"),
    ("confirmation", "create_account_title", "احفظ بياناتك للمرة القادمة"),
    (
        "confirmation",
        "create_account_body",
        "اختر كلمة مرور وسيبقى عنوانك وسجل طلباتك في انتظارك.",
    ),
    ("confirmation", "create_account_cta", "إنشاء حساب"),
    ("confirmation", "create_account_done", "تم إنشاء الحساب — تم تسجيل دخولك."),
    ("confirmation", "password_placeholder", "اختر كلمة مرور"),
    ("confirmation", "sign_in_title", "أهلاً بعودتك — سجّل دخولك"),
    (
        "confirmation",
        "sign_in_body",
        "لديك حساب بهذا البريد الإلكتروني. سجّل دخولك لربط هذا الطلب به.",
    ),
    (
        "confirmation",
        "sign_in_body_address",
        "لديك حساب بهذا البريد الإلكتروني. سجّل دخولك وسنحفظ عنوان التوصيل هذا في حسابك للمرة القادمة.",
    ),
    ("confirmation", "sign_in_password_placeholder", "كلمة المرور"),
    ("confirmation", "sign_in_cta", "تسجيل الدخول"),
    ("confirmation", "signed_in_done", "تم تسجيل الدخول — أهلاً بعودتك."),
    (
        "confirmation",
        "signed_in_address_saved",
        "تم تسجيل الدخول، وحُفظ عنوان التوصيل هذا في حسابك.",
    ),
    (
        "confirmation",
        "create_account_body_address",
        "أنشئ حساباً وسنحفظ عنوان التوصيل هذا فيه، ليستغرق طلبك القادم ثوانٍ فقط.",
    ),
    (
        "confirmation",
        "create_account_address_saved",
        "تم إنشاء الحساب، وحُفظ عنوان التوصيل هذا فيه.",
    ),
    ("common", "try_again", "حاول مرة أخرى"),
    ("checkout", "cart_empty", "سلتك فارغة"),
    ("checkout", "payment_cancelled", "تم إلغاء الدفع. يرجى المحاولة مجدداً."),
    (
        "checkout",
        "payment_failure.insufficient_funds",
        "تم رفض بطاقتك — قد يكون الرصيد غير كافٍ. يرجى استخدام بطاقة أخرى.",
    ),
    (
        "checkout",
        "payment_failure.expired_card",
        "انتهت صلاحية بطاقتك. يرجى استخدام بطاقة أخرى.",
    ),
    (
        "checkout",
        "payment_failure.incorrect_cvc",
        "رمز التحقق (CVC) غير صحيح. يرجى التحقق منه والمحاولة مرة أخرى.",
    ),
    (
        "checkout",
        "payment_failure.incorrect_number",
        "رقم البطاقة غير صحيح. يرجى التحقق منه والمحاولة مرة أخرى.",
    ),
    (
        "checkout",
        "payment_failure.incorrect_details",
        "بيانات الفوترة لا تطابق بطاقتك. يرجى التحقق منها والمحاولة مرة أخرى.",
    ),
    (
        "checkout",
        "payment_failure.card_not_supported",
        "لا يمكن استخدام هذه البطاقة لهذه العملية. يرجى استخدام بطاقة أخرى.",
    ),
    (
        "checkout",
        "payment_failure.authentication_required",
        "يحتاج بنكك إلى التحقق من هذه العملية. يرجى إعادة المحاولة وإكمال خطوة التحقق.",
    ),
    (
        "checkout",
        "payment_failure.processing_error",
        "حدث خطأ أثناء معالجة بطاقتك. يرجى الانتظار قليلاً والمحاولة مرة أخرى.",
    ),
    (
        "checkout",
        "payment_failure.duplicate",
        "يبدو أن هذه عملية دفع مكررة تمت مؤخراً. يرجى التحقق مما إذا كانت قد تمت بالفعل قبل إعادة المحاولة.",
    ),
    (
        "checkout",
        "payment_failure.card_declined",
        "تم رفض بطاقتك. يرجى التواصل مع بنكك أو استخدام بطاقة أخرى.",
    ),
    ("checkout", "promo_placeholder", "رمز الخصم"),
    ("checkout", "apply", "تطبيق"),
    ("checkout", "remove_promo", "إزالة الخصم"),
    ("checkout", "promo_applied", 'تم تطبيق الخصم "{code}"!'),
    ("checkout", "invalid_promo", "رمز الخصم غير صحيح"),
    ("checkout", "promo_error", "تعذّر التحقق من رمز الخصم. حاول مجدداً."),
    ("breadcrumb", "checkout", "الدفع"),
    # confirmation
    ("confirmation", "title", "تم تقديم الطلب!"),
    ("confirmation", "thank_you", "شكراً لطلبك. سنُرسل تأكيداً إلى {email}."),
    ("confirmation", "order_number", "رقم الطلب"),
    ("confirmation", "total_paid", "الإجمالي المدفوع"),
    ("confirmation", "total_due", "المبلغ المستحق عند الاستلام"),
    ("confirmation", "thank_you_no_email", "شكراً لطلبك — سنتواصل معك عبر واتساب."),
    ("confirmation", "delivering_to", "التوصيل إلى"),
    (
        "confirmation",
        "pickup_note",
        "اخترت الاستلام من المتجر. سنتواصل معك عبر واتساب حين يصبح طلبك جاهزاً للاستلام.",
    ),
    ("confirmation", "continue_shopping", "متابعة التسوق"),
    ("confirmation", "view_orders", "عرض طلباتي"),
    ("confirmation", "not_found_title", "الطلب غير موجود"),
    (
        "confirmation",
        "not_found_body",
        "تعذّر استرداد تفاصيل طلبك. إن أتممت الدفع، ستتلقى بريداً تأكيدياً قريباً.",
    ),
    ("confirmation", "back_to_home", "العودة للرئيسية"),
    ("confirmation", "loading", "جارٍ تحميل طلبك…"),
    # auth
    ("auth", "welcome_back", "أهلاً بعودتك"),
    ("auth", "sign_in_subtitle", "سجّل دخولك إلى حساب ملتينج مومنتس"),
    ("auth", "forgot_password_link", "نسيت كلمة المرور؟"),
    ("auth", "or_divider", "أو"),
    ("auth", "no_account", "ليس لديك حساب؟"),
    ("auth", "sign_up_link", "سجّل الآن"),
    ("auth", "just_browsing", "مجرد تصفح؟"),
    ("auth", "continue_as_guest", "المتابعة كضيف"),
    ("auth", "login_failed", "فشل تسجيل الدخول. حاول مجدداً."),
    ("auth", "create_account", "إنشاء حساب"),
    ("auth", "create_account_subtitle", "انضم إلى ملتينج مومنتس لتجربة أحلى"),
    ("auth", "password_helper", "8 أحرف على الأقل"),
    ("auth", "tos_text", "بالتسجيل توافق على"),
    ("auth", "tos_privacy", "سياسة الخصوصية"),
    ("auth", "already_have_account", "لديك حساب بالفعل؟"),
    ("auth", "registration_failed", "فشل إنشاء الحساب. حاول مجدداً."),
    ("auth", "reset_password", "إعادة تعيين كلمة المرور"),
    ("auth", "reset_subtitle", "أدخل بريدك الإلكتروني وسنُرسل لك رابط إعادة التعيين."),
    ("auth", "send_reset_link", "إرسال رابط إعادة التعيين"),
    ("auth", "back_to_sign_in", "العودة لتسجيل الدخول"),
    ("auth", "check_email_title", "تحقق من بريدك الإلكتروني"),
    (
        "auth",
        "check_email_body",
        "إن كان هناك حساب لـ {email}، أرسلنا رابط إعادة تعيين كلمة المرور. تحقق من صندوق الوارد (والبريد المزعج).",
    ),
    ("auth", "set_new_password", "تعيين كلمة مرور جديدة"),
    ("auth", "set_password_subtitle", "اختر كلمة مرور قوية لحسابك."),
    ("auth", "new_password", "كلمة المرور الجديدة"),
    ("auth", "update_password", "تحديث كلمة المرور"),
    ("auth", "password_updated", "تم تحديث كلمة المرور"),
    (
        "auth",
        "password_updated_body",
        "تم تغيير كلمة مرورك. يمكنك الآن تسجيل الدخول بكلمة المرور الجديدة.",
    ),
    ("auth", "invalid_link", "رابط إعادة التعيين غير صحيح"),
    (
        "auth",
        "invalid_link_body",
        "رابط إعادة تعيين كلمة المرور غير صحيح أو انتهت صلاحيته.",
    ),
    ("auth", "request_new_link", "طلب رابط جديد"),
    ("auth", "reset_failed", "فشل إعادة التعيين. قد يكون الرابط قد انتهت صلاحيته."),
    ("auth", "email_required", "البريد الإلكتروني مطلوب"),
    ("auth", "password_required", "كلمة المرور مطلوبة"),
    ("auth", "password_min_length", "يجب أن تكون كلمة المرور 8 أحرف على الأقل"),
    ("auth", "passwords_no_match", "كلمتا المرور غير متطابقتين"),
    # account
    ("account", "hello", "أهلاً، {name}"),
    ("account", "welcome", "مرحباً بك في حسابك في ملتينج مومنتس."),
    ("account", "my_orders", "طلباتي"),
    ("account", "my_orders_desc", "تتبع وعرض طلباتك السابقة"),
    ("account", "addresses", "العناوين"),
    ("account", "addresses_desc", "إدارة عناوين التوصيل المحفوظة"),
    ("account", "settings", "الإعدادات"),
    ("account", "settings_desc", "تعديل ملفك الشخصي وكلمة المرور"),
    ("account", "member_since", "عضو منذ {date}"),
    ("account", "signed_in_as", "مسجّل دخول بـ"),
    ("account", "my_profile", "ملفي الشخصي"),
    # order
    ("order", "status_pending", "قيد الانتظار"),
    ("order", "status_confirmed", "مؤكد"),
    ("order", "status_packed", "معبّأ"),
    ("order", "status_cancelled", "ملغى"),
    ("order", "status_payment_failed", "فشل الدفع"),
    ("order", "my_orders", "طلباتي"),
    ("order", "no_orders", "لم تُقدّم أي طلبات بعد."),
    ("order", "start_shopping", "ابدأ التسوق"),
    ("order", "failed_to_load", "فشل تحميل الطلبات."),
    ("order", "placed", "تاريخ الطلب"),
    ("order", "order_progress", "مراحل الطلب"),
    ("order", "items", "المنتجات"),
    ("order", "summary", "الملخص"),
    ("order", "cancellation_note", "ملاحظة الإلغاء"),
    ("order", "back_to_orders", "العودة للطلبات"),
    ("order", "not_found", "الطلب غير موجود."),
    ("order", "payment", "الدفع"),
    ("order", "notes", "ملاحظات"),
    ("order", "no_address", "لا يوجد عنوان"),
    ("order", "store_pickup", "الاستلام من المتجر"),
    ("order", "timeline_placed", "تم تقديم الطلب"),
    ("order", "timeline_confirmed", "تم تأكيد الطلب"),
    ("order", "timeline_ready", "جاهز / تم الإرسال"),
    ("order", "status_out_for_delivery", "في الطريق إليك"),
    ("order", "status_delivered", "تم التوصيل"),
    ("order", "status_undelivered", "محاولة توصيل"),
    ("order", "status_collected", "تم الاستلام"),
    ("order", "status_refunded", "تم رد المبلغ"),
    ("order", "status_disputed", "قيد النزاع"),
    ("order", "estimate_delivery", "موعد التوصيل المتوقع"),
    ("order", "estimate_ready", "جاهز للاستلام"),
    ("order", "estimate_ready_since", "جاهز منذ"),
    ("order", "estimate_arriving", "الوصول المتوقع"),
    ("order", "estimate_delivered", "تم التوصيل"),
    ("order", "estimate_collected", "تم الاستلام"),
    (
        "order",
        "estimate_note_rider",
        "بعد استلام السائق للطلب، هذا هو أدق تقدير لدينا.",
    ),
    ("order", "estimate_note_day", "سنؤكد الوقت بمجرد استلام الطلب."),
    ("order", "estimate_by_time", "{day} قبل {time}"),
    ("order", "track_live", "تتبع مباشر"),
    (
        "order",
        "track_live_pending",
        "سيظهر رابط التتبع المباشر هنا بمجرد استلام السائق لطلبك.",
    ),
    ("order", "step_preparing", "جارٍ تحضير طلبك"),
    ("order", "step_ready", "تم التعبئة"),
    ("order", "step_on_the_way", "في الطريق إليك"),
    ("order", "step_delivered", "تم التوصيل"),
    ("order", "step_pickup_preparing", "جارٍ تحضير طلبك"),
    ("order", "step_pickup_ready", "جاهز للاستلام"),
    ("order", "step_pickup_collected", "تم الاستلام"),
    ("order", "collect_from", "الاستلام من"),
    ("order", "branch_open", "أوقات العمل"),
    ("order", "branch_phone", "الهاتف"),
    ("order", "open_in_maps", "افتح في خرائط جوجل"),
    ("order", "undelivered_title", "محاولة توصيل"),
    (
        "order",
        "undelivered_body",
        "وصل السائق إلى عنوانك لكنه لم يتمكن من إتمام التوصيل. طلبك محفوظ لدينا "
        "وسنتواصل معك لترتيب محاولة أخرى.",
    ),
    ("order", "delivery_address", "التوصيل إلى"),
    # address
    ("address", "title", "العناوين"),
    ("address", "add_address", "إضافة عنوان"),
    ("address", "edit_address", "تعديل العنوان"),
    ("address", "new_address", "عنوان جديد"),
    ("address", "label_hint", "التسمية (مثال: المنزل، العمل)"),
    ("address", "set_as_default", "تعيين كعنوان افتراضي"),
    ("address", "default_badge", "افتراضي"),
    ("address", "set_default", "تعيين كافتراضي"),
    ("address", "removing", "جارٍ الإزالة..."),
    ("address", "no_addresses", "لا توجد عناوين محفوظة بعد."),
    ("address", "add_first", "إضافة أول عنوان"),
    # settings
    ("settings", "title", "الإعدادات"),
    ("settings", "profile_info", "معلومات الملف الشخصي"),
    ("settings", "email_helper", "تواصل مع الدعم لتغيير بريدك الإلكتروني"),
    ("settings", "phone_optional", "الهاتف (اختياري)"),
    ("settings", "change_password", "تغيير كلمة المرور"),
    (
        "settings",
        "password_desc",
        "سنُرسل رابط إعادة تعيين إلى بريدك الإلكتروني لتختار كلمة مرور جديدة.",
    ),
    ("settings", "send_reset_email", "إرسال بريد إعادة تعيين كلمة المرور"),
    ("settings", "reset_sent", "تم إرسال بريد إعادة تعيين كلمة المرور"),
    (
        "settings",
        "reset_sent_body",
        "تم إرسال رابط إعادة تعيين إلى {email}. تحقق من صندوق الوارد واتبع التعليمات.",
    ),
    ("settings", "delete_account", "حذف الحساب"),
    (
        "settings",
        "delete_desc",
        "حذف حسابك وجميع بياناتك بشكل دائم. لا يمكن التراجع عن هذا الإجراء.",
    ),
    ("settings", "delete_button", "حذف حسابي"),
    ("settings", "delete_confirm", "هل أنت متأكد؟"),
    (
        "settings",
        "delete_instructions",
        "لحذف حسابك، تواصل معنا على {email} أو عبر {whatsapp}. سنعالج طلبك خلال 48 ساعة.",
    ),
    ("settings", "profile_updated", "تم تحديث الملف الشخصي"),
    ("settings", "failed_update", "فشل تحديث الملف الشخصي"),
    # search
    ("search", "title", "البحث"),
    ("search", "results_for", 'نتائج "{q}"'),
    ("search", "product_count", "تم العثور على {count} {label}"),
    ("search", "product_singular", "منتج"),
    ("search", "product_plural", "منتجات"),
    ("search", "empty_prompt", "أدخل كلمة بحث للعثور على المنتجات"),
    ("search", "no_results", 'لا توجد منتجات لـ "{q}"'),
    ("search", "no_results_hint", "جرّب كلمة بحث مختلفة أو تصفح {link}."),
    ("search", "categories_link", "الفئات"),
    # contact
    ("contact", "eyebrow", "تواصل معنا"),
    ("contact", "whatsapp", "واتساب"),
    ("contact", "email_label", "البريد الإلكتروني"),
    ("contact", "location", "الموقع"),
    ("contact", "hours", "أوقات العمل"),
    ("contact", "message_us", "راسلنا"),
    ("contact", "send_email", "إرسال بريد"),
    ("contact", "follow_along", "تابعنا"),
    # about
    ("about", "our_story", "قصتنا"),
    ("about", "our_promise", "وعدنا"),
    ("about", "what_we_stand_for", "ما نؤمن به"),
    ("about", "get_in_touch", "تواصل معنا"),
    # faq
    ("faq", "help_centre", "مركز المساعدة"),
    ("faq", "still_have_questions", "لا تزال لديك أسئلة؟"),
    # track
    ("track", "title", "تتبع طلبك"),
    ("track", "subtitle", "أدخل رقم طلبك وبريدك الإلكتروني للاطلاع على الحالة."),
    ("track", "order_number", "رقم الطلب"),
    ("track", "email_address", "عنوان البريد الإلكتروني"),
    ("track", "track_button", "تتبع الطلب"),
    ("track", "validation_error", "يرجى إدخال رقم الطلب والبريد الإلكتروني."),
    ("track", "generic_error", "حدث خطأ. حاول مجدداً."),
    ("track", "order_label", "الطلب"),
    ("track", "status", "الحالة"),
    ("track", "delivery", "التوصيل"),
    ("track", "items", "المنتجات"),
    ("track", "placed", "تاريخ الطلب"),
    ("track", "total", "الإجمالي"),
    ("track", "view_full_order", "سجّل الدخول لعرض الطلب كاملاً"),
    # error / not-found
    ("error", "not_found_title", "الصفحة غير موجودة"),
    (
        "error",
        "not_found_body",
        "الصفحة التي تبحث عنها غير موجودة. ربما نُقلت أو أن الرابط غير صحيح.",
    ),
    ("error", "back_to_home", "العودة للرئيسية"),
    ("error", "contact_us", "تواصل معنا"),
    ("error", "tagline", "مصنوعة بـ 100% حب"),
    # address labels
    ("address", "pin_location", "تحديد الموقع على الخريطة"),
    ("address", "search_location", "ابحث عن موقع…"),
    (
        "checkout",
        "pin_location_required",
        "يرجى تحديد الموقع على الخريطة لتأكيد عنوان التوصيل",
    ),
]

ALL_TRANSLATIONS = [("en", *row) for row in EN_TRANSLATIONS] + [
    ("ar", *row) for row in AR_TRANSLATIONS
]


async def seed(session: AsyncSession) -> None:
    print("🌱 Seeding i18n data...")

    # Languages
    for lang_data in LANGUAGES:
        result = await session.execute(
            select(Language).where(Language.code == lang_data["code"])
        )
        existing = result.scalar_one_or_none()
        if not existing:
            session.add(Language(**lang_data))
            print(f"  ✅ Language: {lang_data['code']} ({lang_data['name']})")

    await session.flush()

    # Translations
    for locale, namespace, key, value in ALL_TRANSLATIONS:
        result = await session.execute(
            select(UiTranslation).where(
                UiTranslation.locale == locale,
                UiTranslation.namespace == namespace,
                UiTranslation.key == key,
            )
        )
        existing = result.scalar_one_or_none()
        if not existing:
            session.add(
                UiTranslation(locale=locale, namespace=namespace, key=key, value=value)
            )
            print(f"  ✅ {locale}:{namespace}.{key}")
        elif existing.value != value:
            existing.value = value
            print(f"  🔄 {locale}:{namespace}.{key} updated")

    await session.commit()
    # Redis outlives the restart this seed runs inside. Without this, a deploy
    # that adds a key writes it to Postgres and then serves the pre-deploy copy
    # until the TTL lapses — which the storefront renders as raw key names.
    from app.services import i18n_service

    await i18n_service.invalidate_translations()
    print("\n✨ i18n seed complete!")


async def main() -> None:
    from app.core.config import settings

    engine = create_async_engine(settings.DATABASE_URL, echo=False)
    async_session = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )

    async with async_session() as session:
        await seed(session)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
