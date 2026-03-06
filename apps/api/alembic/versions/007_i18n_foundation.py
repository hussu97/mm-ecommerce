"""i18n foundation: languages, ui_translations, entity translations JSONB

Revision ID: 007_i18n_foundation
Revises: 006_cart_user_unique
Create Date: 2026-03-06 00:00:00.000000

"""

import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID


revision: str = "007_i18n_foundation"
down_revision: Union[str, None] = "006_cart_user_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ── English UI translation seed data ──────────────────────────────────

EN_TRANSLATIONS: dict[str, dict[str, str]] = {
    "common": {
        "add_to_cart": "Add to Cart",
        "select_options": "Select Options",
        "continue_shopping": "Continue Shopping",
        "loading": "Loading...",
        "save": "Save",
        "cancel": "Cancel",
        "delete": "Delete",
        "edit": "Edit",
        "close": "Close",
        "back": "Back",
        "next": "Next",
        "submit": "Submit",
        "search": "Search",
        "no_results": "No results found",
        "error": "Something went wrong",
        "retry": "Try Again",
        "view_all": "View All",
        "read_more": "Read More",
        "required": "Required",
        "optional": "Optional",
        "or": "or",
        "and": "and",
        "aed": "AED",
        "free": "Free",
        "sold_out": "Sold Out",
        "out_of_stock": "Out of Stock",
        "in_stock": "In Stock",
        "quantity": "Quantity",
        "price": "Price",
        "total": "Total",
        "subtotal": "Subtotal",
        "calories": "Cal",
        "apply": "Apply",
        "remove": "Remove",
        "confirm": "Confirm",
        "yes": "Yes",
        "no": "No",
    },
    "nav": {
        "sign_in": "Sign in",
        "sign_up": "Sign up",
        "sign_out": "Sign out",
        "my_account": "My Account",
        "my_orders": "My Orders",
        "my_addresses": "My Addresses",
        "settings": "Settings",
        "cart": "Cart",
        "menu": "Menu",
        "home": "Home",
        "about": "About",
        "faq": "FAQ",
        "contact": "Contact",
        "privacy": "Privacy Policy",
    },
    "home": {
        "hero_tagline": "Handcrafted with Love",
        "hero_subtitle": "Artisanal brownies, cookies, and desserts made fresh in the UAE",
        "hero_cta": "Shop Now",
        "featured_title": "Featured Treats",
        "featured_subtitle": "Our most loved creations",
        "meet_baker_title": "Meet the Baker",
        "meet_baker_quote": "Every dessert tells a story of passion, patience, and the finest ingredients.",
        "cater_title": "We Cater To",
        "cater_birthdays": "Birthdays",
        "cater_weddings": "Weddings",
        "cater_corporate": "Corporate",
        "cater_eid": "Eid",
        "cater_ramadan": "Ramadan",
        "cater_celebrations": "Celebrations",
    },
    "product": {
        "description": "Description",
        "modifiers_title": "Customize Your Order",
        "select_required": "Select {min} option(s)",
        "select_optional": "Select up to {max} option(s)",
        "free_options": "{count} free option(s) included",
        "added_to_cart": "Added to cart!",
        "add_failed": "Failed to add to cart",
        "prep_time": "Prep time: {time} min",
        "sold_by_weight": "Sold by weight",
        "share": "Share",
        "related_title": "You May Also Like",
    },
    "category": {
        "empty_title": "No products yet",
        "empty_subtitle": "Check back soon for new additions!",
        "filter_all": "All",
        "sort_popular": "Popular",
        "sort_price_asc": "Price: Low to High",
        "sort_price_desc": "Price: High to Low",
        "sort_newest": "Newest",
        "products_count": "{count} product(s)",
    },
    "cart": {
        "title": "My Cart",
        "empty_title": "Your cart is empty",
        "empty_subtitle": "Looks like you haven't added any treats yet",
        "item_count": "{count} item(s)",
        "order_summary": "Order Summary",
        "promo_code": "Promo Code",
        "promo_placeholder": "Enter code",
        "promo_applied": "Promo applied: {code}",
        "promo_removed": "Promo removed",
        "promo_invalid": "Invalid promo code",
        "discount": "Discount",
        "delivery_note": "Delivery calculated at checkout",
        "proceed_checkout": "Proceed to Checkout",
        "update_failed": "Failed to update cart",
        "remove_confirm": "Remove this item?",
        "clear_confirm": "Clear your cart?",
        "clear_cart": "Clear Cart",
    },
    "checkout": {
        "title": "Checkout",
        "step_information": "Information",
        "step_delivery": "Delivery",
        "step_payment": "Payment",
        "email_label": "Email",
        "email_placeholder": "your@email.com",
        "first_name": "First Name",
        "last_name": "Last Name",
        "phone": "Phone Number",
        "address_line_1": "Address Line 1",
        "address_line_2": "Address Line 2 (Optional)",
        "city": "City",
        "emirate": "Emirate",
        "select_emirate": "Select Emirate",
        "saved_addresses": "Saved Addresses",
        "new_address": "New Address",
        "delivery_method": "Delivery Method",
        "delivery_option": "Delivery",
        "pickup_option": "Pickup",
        "delivery_fee": "Delivery Fee",
        "delivery_free_note": "Free delivery on orders above {amount} AED",
        "delivery_time_note": "Orders placed before 12PM are delivered same day",
        "pickup_location": "Pickup Location",
        "payment_method": "Payment Method",
        "pay_with_card": "Pay with Card",
        "pay_now": "Pay Now",
        "order_summary": "Order Summary",
        "place_order": "Place Order",
        "processing": "Processing...",
        "order_notes": "Order Notes (Optional)",
        "notes_placeholder": "Special instructions for your order",
        "tabby_coming_soon": "Coming Soon",
        "tamara_coming_soon": "Coming Soon",
    },
    "confirmation": {
        "title": "Order Confirmed!",
        "subtitle": "Thank you for your order",
        "order_number": "Order #{number}",
        "email_sent": "A confirmation email has been sent to {email}",
        "view_orders": "View My Orders",
    },
    "auth": {
        "login_title": "Sign In",
        "login_subtitle": "Welcome back",
        "signup_title": "Create Account",
        "signup_subtitle": "Join the Melting Moments family",
        "forgot_title": "Forgot Password",
        "forgot_subtitle": "Enter your email and we'll send you a reset link",
        "reset_title": "Reset Password",
        "reset_subtitle": "Enter your new password",
        "email_label": "Email",
        "password_label": "Password",
        "new_password_label": "New Password",
        "confirm_password_label": "Confirm Password",
        "remember_me": "Remember me",
        "forgot_password": "Forgot password?",
        "no_account": "Don't have an account?",
        "have_account": "Already have an account?",
        "guest_checkout": "Continue as guest",
        "login_failed": "Invalid email or password",
        "signup_failed": "Could not create account",
        "reset_sent": "Reset link sent to your email",
        "reset_success": "Password updated successfully",
        "password_min": "Password must be at least 8 characters",
        "passwords_mismatch": "Passwords do not match",
        "send_reset_link": "Send Reset Link",
    },
    "account": {
        "title": "My Account",
        "welcome": "Welcome, {name}!",
        "orders_title": "My Orders",
        "orders_empty": "You haven't placed any orders yet",
        "addresses_title": "My Addresses",
        "addresses_empty": "No saved addresses",
        "add_address": "Add Address",
        "edit_address": "Edit Address",
        "delete_address": "Delete Address",
        "set_default": "Set as Default",
        "default_badge": "Default",
        "settings_title": "Settings",
        "update_profile": "Update Profile",
        "change_password": "Change Password",
        "profile_updated": "Profile updated",
        "logout": "Sign Out",
    },
    "order": {
        "status_created": "Created",
        "status_confirmed": "Confirmed",
        "status_packed": "Packed",
        "status_cancelled": "Cancelled",
        "delivery_label": "Delivery",
        "pickup_label": "Pickup",
        "items_label": "Items",
        "payment_label": "Payment",
        "placed_on": "Placed on {date}",
        "total_label": "Total",
    },
    "search": {
        "title": "Search",
        "placeholder": "Search for treats...",
        "results_title": "Search Results",
        "results_count": '{count} result(s) for "{query}"',
        "no_results": 'No results found for "{query}"',
        "no_results_subtitle": "Try a different search term",
    },
    "footer": {
        "tagline": "Handcrafted brownies, cookies and desserts — made with 100% love in the UAE",
        "copyright": "All rights reserved.",
        "instagram": "Instagram",
        "whatsapp": "WhatsApp",
    },
    "seo": {
        "home_title": "Melting Moments Cakes — Handcrafted Brownies, Cookies & Desserts in UAE",
        "home_description": "Artisanal brownies, cookies, cookie melts, and desserts handcrafted with love and delivered across the UAE.",
        "about_title": "About Us — Melting Moments Cakes",
        "about_description": "Meet Fatema Abbasi, the baker behind Melting Moments Cakes. Handcrafted desserts made with passion in the UAE.",
        "faq_title": "FAQ — Melting Moments Cakes",
        "faq_description": "Frequently asked questions about ordering, delivery, and our handcrafted desserts.",
        "contact_title": "Contact Us — Melting Moments Cakes",
        "contact_description": "Get in touch with Melting Moments Cakes. WhatsApp, email, or visit us.",
        "cart_title": "Cart — Melting Moments Cakes",
        "checkout_title": "Checkout — Melting Moments Cakes",
        "login_title": "Sign In — Melting Moments Cakes",
        "signup_title": "Create Account — Melting Moments Cakes",
        "privacy_title": "Privacy Policy — Melting Moments Cakes",
        "account_title": "My Account — Melting Moments Cakes",
        "orders_title": "My Orders — Melting Moments Cakes",
        "search_title": "Search — Melting Moments Cakes",
    },
    "promo_banner": {
        "text": "Free Shipping above 200 AED | Use code FREESHIP",
    },
    "faq": {
        "q1": "What products do you offer?",
        "a1": "We offer a range of handcrafted brownies, cookies, cookie melts, mix boxes, and seasonal desserts. Everything is baked fresh to order.",
        "q2": "How do I place an order?",
        "a2": "Browse our menu, add items to your cart, and check out. You can pay online with card and we'll deliver to your door.",
        "q3": "What are your delivery areas?",
        "a3": "We deliver across all seven Emirates in the UAE. Delivery fees vary by location.",
        "q4": "How long does delivery take?",
        "a4": "Orders placed before 12PM are typically delivered the same day. Orders after 12PM are delivered the next day.",
        "q5": "Can I customize my order?",
        "a5": "Yes! Many of our products come with customization options like toppings and sizes. Look for the customization section on each product page.",
        "q6": "Do you cater for events?",
        "a6": "Absolutely! We cater for birthdays, weddings, corporate events, Eid, Ramadan, and all celebrations. Contact us via WhatsApp for custom orders.",
        "q7": "What is your return/refund policy?",
        "a7": "Due to the perishable nature of our products, we don't accept returns. However, if there's an issue with your order, please contact us immediately and we'll make it right.",
        "q8": "How do I contact you?",
        "a8": "You can reach us via WhatsApp, email, or through the contact form on our website. We typically respond within a few hours.",
    },
    "about": {
        "hero_title": "Our Story",
        "hero_subtitle": "Handcrafted with love in the UAE",
        "story_p1": "What started as a passion project in a home kitchen has grown into Melting Moments Cakes — a brand built on the belief that every dessert should be a moment of joy.",
        "story_p2": "Founded by Fatema Abbasi, we specialize in artisanal brownies, cookies, and desserts that are made from scratch using the finest ingredients.",
        "story_p3": "Every order is baked fresh, because we believe you deserve nothing less than perfection.",
        "values_title": "Our Values",
        "value_quality": "Quality",
        "value_quality_desc": "Only the finest ingredients, no shortcuts",
        "value_freshness": "Freshness",
        "value_freshness_desc": "Baked to order, never sitting on a shelf",
        "value_love": "Love",
        "value_love_desc": "Every dessert is made with genuine care",
        "value_community": "Community",
        "value_community_desc": "Proudly serving the UAE community",
    },
    "contact": {
        "title": "Get in Touch",
        "subtitle": "We'd love to hear from you",
        "whatsapp_title": "WhatsApp",
        "whatsapp_desc": "Chat with us directly",
        "email_title": "Email",
        "email_desc": "hello@meltingmomentscakes.com",
        "location_title": "Location",
        "location_desc": "Dubai, UAE",
        "hours_title": "Hours",
        "hours_desc": "Daily 9AM - 9PM",
        "form_name": "Your Name",
        "form_email": "Your Email",
        "form_message": "Your Message",
        "form_send": "Send via WhatsApp",
    },
    "privacy": {
        "title": "Privacy Policy",
        "last_updated": "Last updated: {date}",
    },
    "error": {
        "404_title": "Page Not Found",
        "404_subtitle": "The page you're looking for doesn't exist or has been moved.",
        "500_title": "Something Went Wrong",
        "500_subtitle": "We're sorry, an unexpected error occurred.",
        "back_home": "Back to Home",
    },
}


# ── Arabic UI translation seed data ──────────────────────────────────

AR_TRANSLATIONS: dict[str, dict[str, str]] = {
    "common": {
        "add_to_cart": "\u0623\u0636\u0641 \u0625\u0644\u0649 \u0627\u0644\u0633\u0644\u0629",
        "select_options": "\u0627\u062e\u062a\u0631 \u0627\u0644\u062e\u064a\u0627\u0631\u0627\u062a",
        "continue_shopping": "\u0645\u0648\u0627\u0635\u0644\u0629 \u0627\u0644\u062a\u0633\u0648\u0642",
        "loading": "\u062c\u0627\u0631\u064a \u0627\u0644\u062a\u062d\u0645\u064a\u0644...",
        "save": "\u062d\u0641\u0638",
        "cancel": "\u0625\u0644\u063a\u0627\u0621",
        "delete": "\u062d\u0630\u0641",
        "edit": "\u062a\u0639\u062f\u064a\u0644",
        "close": "\u0625\u063a\u0644\u0627\u0642",
        "back": "\u0631\u062c\u0648\u0639",
        "next": "\u0627\u0644\u062a\u0627\u0644\u064a",
        "submit": "\u0625\u0631\u0633\u0627\u0644",
        "search": "\u0628\u062d\u062b",
        "no_results": "\u0644\u0627 \u062a\u0648\u062c\u062f \u0646\u062a\u0627\u0626\u062c",
        "error": "\u062d\u062f\u062b \u062e\u0637\u0623",
        "retry": "\u062d\u0627\u0648\u0644 \u0645\u0631\u0629 \u0623\u062e\u0631\u0649",
        "view_all": "\u0639\u0631\u0636 \u0627\u0644\u0643\u0644",
        "read_more": "\u0627\u0642\u0631\u0623 \u0627\u0644\u0645\u0632\u064a\u062f",
        "required": "\u0645\u0637\u0644\u0648\u0628",
        "optional": "\u0627\u062e\u062a\u064a\u0627\u0631\u064a",
        "or": "\u0623\u0648",
        "and": "\u0648",
        "aed": "\u062f.\u0625",
        "free": "\u0645\u062c\u0627\u0646\u064a",
        "sold_out": "\u0646\u0641\u062f\u062a \u0627\u0644\u0643\u0645\u064a\u0629",
        "out_of_stock": "\u063a\u064a\u0631 \u0645\u062a\u0648\u0641\u0631",
        "in_stock": "\u0645\u062a\u0648\u0641\u0631",
        "quantity": "\u0627\u0644\u0643\u0645\u064a\u0629",
        "price": "\u0627\u0644\u0633\u0639\u0631",
        "total": "\u0627\u0644\u0625\u062c\u0645\u0627\u0644\u064a",
        "subtotal": "\u0627\u0644\u0645\u062c\u0645\u0648\u0639 \u0627\u0644\u0641\u0631\u0639\u064a",
        "calories": "\u0633\u0639\u0631\u0629",
        "apply": "\u062a\u0637\u0628\u064a\u0642",
        "remove": "\u0625\u0632\u0627\u0644\u0629",
        "confirm": "\u062a\u0623\u0643\u064a\u062f",
        "yes": "\u0646\u0639\u0645",
        "no": "\u0644\u0627",
    },
    "nav": {
        "sign_in": "\u062a\u0633\u062c\u064a\u0644 \u0627\u0644\u062f\u062e\u0648\u0644",
        "sign_up": "\u0625\u0646\u0634\u0627\u0621 \u062d\u0633\u0627\u0628",
        "sign_out": "\u062a\u0633\u062c\u064a\u0644 \u0627\u0644\u062e\u0631\u0648\u062c",
        "my_account": "\u062d\u0633\u0627\u0628\u064a",
        "my_orders": "\u0637\u0644\u0628\u0627\u062a\u064a",
        "my_addresses": "\u0639\u0646\u0627\u0648\u064a\u0646\u064a",
        "settings": "\u0627\u0644\u0625\u0639\u062f\u0627\u062f\u0627\u062a",
        "cart": "\u0627\u0644\u0633\u0644\u0629",
        "menu": "\u0627\u0644\u0642\u0627\u0626\u0645\u0629",
        "home": "\u0627\u0644\u0631\u0626\u064a\u0633\u064a\u0629",
        "about": "\u0645\u0646 \u0646\u062d\u0646",
        "faq": "\u0627\u0644\u0623\u0633\u0626\u0644\u0629 \u0627\u0644\u0634\u0627\u0626\u0639\u0629",
        "contact": "\u0627\u062a\u0635\u0644 \u0628\u0646\u0627",
        "privacy": "\u0633\u064a\u0627\u0633\u0629 \u0627\u0644\u062e\u0635\u0648\u0635\u064a\u0629",
    },
    "home": {
        "hero_tagline": "\u0635\u0646\u0639 \u064a\u062f\u0648\u064a \u0628\u062d\u0628",
        "hero_subtitle": "\u0628\u0631\u0627\u0648\u0646\u064a\u0632 \u0648\u0643\u0648\u0643\u064a\u0632 \u0648\u062d\u0644\u0648\u064a\u0627\u062a \u0645\u0635\u0646\u0648\u0639\u0629 \u0637\u0627\u0632\u062c\u0629 \u0641\u064a \u0627\u0644\u0625\u0645\u0627\u0631\u0627\u062a",
        "hero_cta": "\u062a\u0633\u0648\u0642 \u0627\u0644\u0622\u0646",
        "featured_title": "\u0627\u0644\u0623\u0643\u062b\u0631 \u0631\u0648\u0627\u062c\u0627\u064b",
        "featured_subtitle": "\u0625\u0628\u062f\u0627\u0639\u0627\u062a\u0646\u0627 \u0627\u0644\u0623\u0643\u062b\u0631 \u0645\u062d\u0628\u0629",
        "meet_baker_title": "\u062a\u0639\u0631\u0641 \u0639\u0644\u0649 \u0627\u0644\u062e\u0628\u0627\u0632\u0629",
        "meet_baker_quote": "\u0643\u0644 \u062d\u0644\u0648\u0649 \u062a\u0631\u0648\u064a \u0642\u0635\u0629 \u0634\u063a\u0641 \u0648\u0635\u0628\u0631 \u0648\u0623\u062c\u0648\u062f \u0627\u0644\u0645\u0643\u0648\u0646\u0627\u062a.",
        "cater_title": "\u0646\u0642\u062f\u0645 \u062e\u062f\u0645\u0627\u062a\u0646\u0627 \u0644\u0640",
        "cater_birthdays": "\u0623\u0639\u064a\u0627\u062f \u0627\u0644\u0645\u064a\u0644\u0627\u062f",
        "cater_weddings": "\u062d\u0641\u0644\u0627\u062a \u0627\u0644\u0632\u0641\u0627\u0641",
        "cater_corporate": "\u0641\u0639\u0627\u0644\u064a\u0627\u062a \u0627\u0644\u0634\u0631\u0643\u0627\u062a",
        "cater_eid": "\u0627\u0644\u0639\u064a\u062f",
        "cater_ramadan": "\u0631\u0645\u0636\u0627\u0646",
        "cater_celebrations": "\u0627\u0644\u0627\u062d\u062a\u0641\u0627\u0644\u0627\u062a",
    },
    "product": {
        "description": "\u0627\u0644\u0648\u0635\u0641",
        "modifiers_title": "\u062e\u0635\u0635 \u0637\u0644\u0628\u0643",
        "select_required": "\u0627\u062e\u062a\u0631 {min} \u062e\u064a\u0627\u0631(\u0627\u062a)",
        "select_optional": "\u0627\u062e\u062a\u0631 \u062d\u062a\u0649 {max} \u062e\u064a\u0627\u0631(\u0627\u062a)",
        "free_options": "{count} \u062e\u064a\u0627\u0631(\u0627\u062a) \u0645\u062c\u0627\u0646\u064a\u0629",
        "added_to_cart": "\u062a\u0645\u062a \u0627\u0644\u0625\u0636\u0627\u0641\u0629 \u0625\u0644\u0649 \u0627\u0644\u0633\u0644\u0629!",
        "add_failed": "\u0641\u0634\u0644 \u0625\u0636\u0627\u0641\u0629 \u0627\u0644\u0645\u0646\u062a\u062c",
        "prep_time": "\u0648\u0642\u062a \u0627\u0644\u062a\u062d\u0636\u064a\u0631: {time} \u062f\u0642\u064a\u0642\u0629",
        "sold_by_weight": "\u064a\u0628\u0627\u0639 \u0628\u0627\u0644\u0648\u0632\u0646",
        "share": "\u0645\u0634\u0627\u0631\u0643\u0629",
        "related_title": "\u0642\u062f \u064a\u0639\u062c\u0628\u0643 \u0623\u064a\u0636\u0627\u064b",
    },
    "category": {
        "empty_title": "\u0644\u0627 \u062a\u0648\u062c\u062f \u0645\u0646\u062a\u062c\u0627\u062a \u0628\u0639\u062f",
        "empty_subtitle": "\u062a\u0627\u0628\u0639\u0646\u0627 \u0642\u0631\u064a\u0628\u0627\u064b \u0644\u0644\u0625\u0636\u0627\u0641\u0627\u062a \u0627\u0644\u062c\u062f\u064a\u062f\u0629!",
        "filter_all": "\u0627\u0644\u0643\u0644",
        "sort_popular": "\u0627\u0644\u0623\u0643\u062b\u0631 \u0634\u0639\u0628\u064a\u0629",
        "sort_price_asc": "\u0627\u0644\u0633\u0639\u0631: \u0645\u0646 \u0627\u0644\u0623\u0642\u0644 \u0625\u0644\u0649 \u0627\u0644\u0623\u0639\u0644\u0649",
        "sort_price_desc": "\u0627\u0644\u0633\u0639\u0631: \u0645\u0646 \u0627\u0644\u0623\u0639\u0644\u0649 \u0625\u0644\u0649 \u0627\u0644\u0623\u0642\u0644",
        "sort_newest": "\u0627\u0644\u0623\u062d\u062f\u062b",
        "products_count": "{count} \u0645\u0646\u062a\u062c(\u0627\u062a)",
    },
    "cart": {
        "title": "\u0633\u0644\u0629 \u0627\u0644\u062a\u0633\u0648\u0642",
        "empty_title": "\u0633\u0644\u062a\u0643 \u0641\u0627\u0631\u063a\u0629",
        "empty_subtitle": "\u064a\u0628\u062f\u0648 \u0623\u0646\u0643 \u0644\u0645 \u062a\u0636\u0641 \u0623\u064a \u062d\u0644\u0648\u064a\u0627\u062a \u0628\u0639\u062f",
        "item_count": "{count} \u0639\u0646\u0635\u0631(\u0639\u0646\u0627\u0635\u0631)",
        "order_summary": "\u0645\u0644\u062e\u0635 \u0627\u0644\u0637\u0644\u0628",
        "promo_code": "\u0631\u0645\u0632 \u0627\u0644\u062e\u0635\u0645",
        "promo_placeholder": "\u0623\u062f\u062e\u0644 \u0627\u0644\u0631\u0645\u0632",
        "promo_applied": "\u062a\u0645 \u062a\u0637\u0628\u064a\u0642 \u0627\u0644\u062e\u0635\u0645: {code}",
        "promo_removed": "\u062a\u0645 \u0625\u0632\u0627\u0644\u0629 \u0627\u0644\u062e\u0635\u0645",
        "promo_invalid": "\u0631\u0645\u0632 \u062e\u0635\u0645 \u063a\u064a\u0631 \u0635\u0627\u0644\u062d",
        "discount": "\u0627\u0644\u062e\u0635\u0645",
        "delivery_note": "\u064a\u062a\u0645 \u062d\u0633\u0627\u0628 \u0627\u0644\u062a\u0648\u0635\u064a\u0644 \u0639\u0646\u062f \u0627\u0644\u062f\u0641\u0639",
        "proceed_checkout": "\u0627\u0644\u0645\u062a\u0627\u0628\u0639\u0629 \u0644\u0644\u062f\u0641\u0639",
        "update_failed": "\u0641\u0634\u0644 \u062a\u062d\u062f\u064a\u062b \u0627\u0644\u0633\u0644\u0629",
        "remove_confirm": "\u0625\u0632\u0627\u0644\u0629 \u0647\u0630\u0627 \u0627\u0644\u0639\u0646\u0635\u0631\u061f",
        "clear_confirm": "\u0625\u0641\u0631\u0627\u063a \u0627\u0644\u0633\u0644\u0629\u061f",
        "clear_cart": "\u0625\u0641\u0631\u0627\u063a \u0627\u0644\u0633\u0644\u0629",
    },
    "checkout": {
        "title": "\u0625\u062a\u0645\u0627\u0645 \u0627\u0644\u0637\u0644\u0628",
        "step_information": "\u0627\u0644\u0645\u0639\u0644\u0648\u0645\u0627\u062a",
        "step_delivery": "\u0627\u0644\u062a\u0648\u0635\u064a\u0644",
        "step_payment": "\u0627\u0644\u062f\u0641\u0639",
        "email_label": "\u0627\u0644\u0628\u0631\u064a\u062f \u0627\u0644\u0625\u0644\u0643\u062a\u0631\u0648\u0646\u064a",
        "email_placeholder": "your@email.com",
        "first_name": "\u0627\u0644\u0627\u0633\u0645 \u0627\u0644\u0623\u0648\u0644",
        "last_name": "\u0627\u0633\u0645 \u0627\u0644\u0639\u0627\u0626\u0644\u0629",
        "phone": "\u0631\u0642\u0645 \u0627\u0644\u0647\u0627\u062a\u0641",
        "address_line_1": "\u0627\u0644\u0639\u0646\u0648\u0627\u0646 - \u0627\u0644\u0633\u0637\u0631 1",
        "address_line_2": "\u0627\u0644\u0639\u0646\u0648\u0627\u0646 - \u0627\u0644\u0633\u0637\u0631 2 (\u0627\u062e\u062a\u064a\u0627\u0631\u064a)",
        "city": "\u0627\u0644\u0645\u062f\u064a\u0646\u0629",
        "emirate": "\u0627\u0644\u0625\u0645\u0627\u0631\u0629",
        "select_emirate": "\u0627\u062e\u062a\u0631 \u0627\u0644\u0625\u0645\u0627\u0631\u0629",
        "saved_addresses": "\u0627\u0644\u0639\u0646\u0627\u0648\u064a\u0646 \u0627\u0644\u0645\u062d\u0641\u0648\u0638\u0629",
        "new_address": "\u0639\u0646\u0648\u0627\u0646 \u062c\u062f\u064a\u062f",
        "delivery_method": "\u0637\u0631\u064a\u0642\u0629 \u0627\u0644\u062a\u0648\u0635\u064a\u0644",
        "delivery_option": "\u062a\u0648\u0635\u064a\u0644",
        "pickup_option": "\u0627\u0633\u062a\u0644\u0627\u0645",
        "delivery_fee": "\u0631\u0633\u0648\u0645 \u0627\u0644\u062a\u0648\u0635\u064a\u0644",
        "delivery_free_note": "\u062a\u0648\u0635\u064a\u0644 \u0645\u062c\u0627\u0646\u064a \u0644\u0644\u0637\u0644\u0628\u0627\u062a \u0641\u0648\u0642 {amount} \u062f.\u0625",
        "delivery_time_note": "\u0627\u0644\u0637\u0644\u0628\u0627\u062a \u0642\u0628\u0644 12 \u0638\u0647\u0631\u0627\u064b \u062a\u0635\u0644 \u0641\u064a \u0646\u0641\u0633 \u0627\u0644\u064a\u0648\u0645",
        "pickup_location": "\u0645\u0648\u0642\u0639 \u0627\u0644\u0627\u0633\u062a\u0644\u0627\u0645",
        "payment_method": "\u0637\u0631\u064a\u0642\u0629 \u0627\u0644\u062f\u0641\u0639",
        "pay_with_card": "\u0627\u0644\u062f\u0641\u0639 \u0628\u0627\u0644\u0628\u0637\u0627\u0642\u0629",
        "pay_now": "\u0627\u062f\u0641\u0639 \u0627\u0644\u0622\u0646",
        "order_summary": "\u0645\u0644\u062e\u0635 \u0627\u0644\u0637\u0644\u0628",
        "place_order": "\u062a\u0623\u0643\u064a\u062f \u0627\u0644\u0637\u0644\u0628",
        "processing": "\u062c\u0627\u0631\u064a \u0627\u0644\u0645\u0639\u0627\u0644\u062c\u0629...",
        "order_notes": "\u0645\u0644\u0627\u062d\u0638\u0627\u062a \u0627\u0644\u0637\u0644\u0628 (\u0627\u062e\u062a\u064a\u0627\u0631\u064a)",
        "notes_placeholder": "\u062a\u0639\u0644\u064a\u0645\u0627\u062a \u062e\u0627\u0635\u0629 \u0644\u0637\u0644\u0628\u0643",
        "tabby_coming_soon": "\u0642\u0631\u064a\u0628\u0627\u064b",
        "tamara_coming_soon": "\u0642\u0631\u064a\u0628\u0627\u064b",
    },
    "confirmation": {
        "title": "\u062a\u0645 \u062a\u0623\u0643\u064a\u062f \u0627\u0644\u0637\u0644\u0628!",
        "subtitle": "\u0634\u0643\u0631\u0627\u064b \u0644\u0637\u0644\u0628\u0643",
        "order_number": "\u0637\u0644\u0628 #{number}",
        "email_sent": "\u062a\u0645 \u0625\u0631\u0633\u0627\u0644 \u0631\u0633\u0627\u0644\u0629 \u062a\u0623\u0643\u064a\u062f \u0625\u0644\u0649 {email}",
        "view_orders": "\u0639\u0631\u0636 \u0637\u0644\u0628\u0627\u062a\u064a",
    },
    "auth": {
        "login_title": "\u062a\u0633\u062c\u064a\u0644 \u0627\u0644\u062f\u062e\u0648\u0644",
        "login_subtitle": "\u0645\u0631\u062d\u0628\u0627\u064b \u0628\u0639\u0648\u062f\u062a\u0643",
        "signup_title": "\u0625\u0646\u0634\u0627\u0621 \u062d\u0633\u0627\u0628",
        "signup_subtitle": "\u0627\u0646\u0636\u0645 \u0625\u0644\u0649 \u0639\u0627\u0626\u0644\u0629 Melting Moments",
        "forgot_title": "\u0646\u0633\u064a\u062a \u0643\u0644\u0645\u0629 \u0627\u0644\u0645\u0631\u0648\u0631",
        "forgot_subtitle": "\u0623\u062f\u062e\u0644 \u0628\u0631\u064a\u062f\u0643 \u0627\u0644\u0625\u0644\u0643\u062a\u0631\u0648\u0646\u064a \u0648\u0633\u0646\u0631\u0633\u0644 \u0644\u0643 \u0631\u0627\u0628\u0637 \u0625\u0639\u0627\u062f\u0629 \u0627\u0644\u062a\u0639\u064a\u064a\u0646",
        "reset_title": "\u0625\u0639\u0627\u062f\u0629 \u062a\u0639\u064a\u064a\u0646 \u0643\u0644\u0645\u0629 \u0627\u0644\u0645\u0631\u0648\u0631",
        "reset_subtitle": "\u0623\u062f\u062e\u0644 \u0643\u0644\u0645\u0629 \u0627\u0644\u0645\u0631\u0648\u0631 \u0627\u0644\u062c\u062f\u064a\u062f\u0629",
        "email_label": "\u0627\u0644\u0628\u0631\u064a\u062f \u0627\u0644\u0625\u0644\u0643\u062a\u0631\u0648\u0646\u064a",
        "password_label": "\u0643\u0644\u0645\u0629 \u0627\u0644\u0645\u0631\u0648\u0631",
        "new_password_label": "\u0643\u0644\u0645\u0629 \u0627\u0644\u0645\u0631\u0648\u0631 \u0627\u0644\u062c\u062f\u064a\u062f\u0629",
        "confirm_password_label": "\u062a\u0623\u0643\u064a\u062f \u0643\u0644\u0645\u0629 \u0627\u0644\u0645\u0631\u0648\u0631",
        "remember_me": "\u062a\u0630\u0643\u0631\u0646\u064a",
        "forgot_password": "\u0646\u0633\u064a\u062a \u0643\u0644\u0645\u0629 \u0627\u0644\u0645\u0631\u0648\u0631\u061f",
        "no_account": "\u0644\u064a\u0633 \u0644\u062f\u064a\u0643 \u062d\u0633\u0627\u0628\u061f",
        "have_account": "\u0644\u062f\u064a\u0643 \u062d\u0633\u0627\u0628 \u0628\u0627\u0644\u0641\u0639\u0644\u061f",
        "guest_checkout": "\u0627\u0644\u0645\u062a\u0627\u0628\u0639\u0629 \u0643\u0636\u064a\u0641",
        "login_failed": "\u0628\u0631\u064a\u062f \u0625\u0644\u0643\u062a\u0631\u0648\u0646\u064a \u0623\u0648 \u0643\u0644\u0645\u0629 \u0645\u0631\u0648\u0631 \u063a\u064a\u0631 \u0635\u062d\u064a\u062d\u0629",
        "signup_failed": "\u062a\u0639\u0630\u0631 \u0625\u0646\u0634\u0627\u0621 \u0627\u0644\u062d\u0633\u0627\u0628",
        "reset_sent": "\u062a\u0645 \u0625\u0631\u0633\u0627\u0644 \u0631\u0627\u0628\u0637 \u0625\u0639\u0627\u062f\u0629 \u0627\u0644\u062a\u0639\u064a\u064a\u0646 \u0625\u0644\u0649 \u0628\u0631\u064a\u062f\u0643",
        "reset_success": "\u062a\u0645 \u062a\u062d\u062f\u064a\u062b \u0643\u0644\u0645\u0629 \u0627\u0644\u0645\u0631\u0648\u0631 \u0628\u0646\u062c\u0627\u062d",
        "password_min": "\u064a\u062c\u0628 \u0623\u0646 \u062a\u0643\u0648\u0646 \u0643\u0644\u0645\u0629 \u0627\u0644\u0645\u0631\u0648\u0631 8 \u0623\u062d\u0631\u0641 \u0639\u0644\u0649 \u0627\u0644\u0623\u0642\u0644",
        "passwords_mismatch": "\u0643\u0644\u0645\u062a\u0627 \u0627\u0644\u0645\u0631\u0648\u0631 \u063a\u064a\u0631 \u0645\u062a\u0637\u0627\u0628\u0642\u062a\u064a\u0646",
        "send_reset_link": "\u0625\u0631\u0633\u0627\u0644 \u0631\u0627\u0628\u0637 \u0625\u0639\u0627\u062f\u0629 \u0627\u0644\u062a\u0639\u064a\u064a\u0646",
    },
    "account": {
        "title": "\u062d\u0633\u0627\u0628\u064a",
        "welcome": "\u0645\u0631\u062d\u0628\u0627\u064b\u060c {name}!",
        "orders_title": "\u0637\u0644\u0628\u0627\u062a\u064a",
        "orders_empty": "\u0644\u0645 \u062a\u0642\u0645 \u0628\u0623\u064a \u0637\u0644\u0628\u0627\u062a \u0628\u0639\u062f",
        "addresses_title": "\u0639\u0646\u0627\u0648\u064a\u0646\u064a",
        "addresses_empty": "\u0644\u0627 \u062a\u0648\u062c\u062f \u0639\u0646\u0627\u0648\u064a\u0646 \u0645\u062d\u0641\u0648\u0638\u0629",
        "add_address": "\u0625\u0636\u0627\u0641\u0629 \u0639\u0646\u0648\u0627\u0646",
        "edit_address": "\u062a\u0639\u062f\u064a\u0644 \u0627\u0644\u0639\u0646\u0648\u0627\u0646",
        "delete_address": "\u062d\u0630\u0641 \u0627\u0644\u0639\u0646\u0648\u0627\u0646",
        "set_default": "\u062a\u0639\u064a\u064a\u0646 \u0643\u0627\u0641\u062a\u0631\u0627\u0636\u064a",
        "default_badge": "\u0627\u0641\u062a\u0631\u0627\u0636\u064a",
        "settings_title": "\u0627\u0644\u0625\u0639\u062f\u0627\u062f\u0627\u062a",
        "update_profile": "\u062a\u062d\u062f\u064a\u062b \u0627\u0644\u0645\u0644\u0641 \u0627\u0644\u0634\u062e\u0635\u064a",
        "change_password": "\u062a\u063a\u064a\u064a\u0631 \u0643\u0644\u0645\u0629 \u0627\u0644\u0645\u0631\u0648\u0631",
        "profile_updated": "\u062a\u0645 \u062a\u062d\u062f\u064a\u062b \u0627\u0644\u0645\u0644\u0641 \u0627\u0644\u0634\u062e\u0635\u064a",
        "logout": "\u062a\u0633\u062c\u064a\u0644 \u0627\u0644\u062e\u0631\u0648\u062c",
    },
    "order": {
        "status_created": "\u062a\u0645 \u0627\u0644\u0625\u0646\u0634\u0627\u0621",
        "status_confirmed": "\u0645\u0624\u0643\u062f",
        "status_packed": "\u062c\u0627\u0647\u0632 \u0644\u0644\u0634\u062d\u0646",
        "status_cancelled": "\u0645\u0644\u063a\u064a",
        "delivery_label": "\u062a\u0648\u0635\u064a\u0644",
        "pickup_label": "\u0627\u0633\u062a\u0644\u0627\u0645",
        "items_label": "\u0627\u0644\u0645\u0646\u062a\u062c\u0627\u062a",
        "payment_label": "\u0627\u0644\u062f\u0641\u0639",
        "placed_on": "\u062a\u0645 \u0627\u0644\u0637\u0644\u0628 \u0641\u064a {date}",
        "total_label": "\u0627\u0644\u0625\u062c\u0645\u0627\u0644\u064a",
    },
    "search": {
        "title": "\u0628\u062d\u062b",
        "placeholder": "\u0627\u0628\u062d\u062b \u0639\u0646 \u062d\u0644\u0648\u064a\u0627\u062a...",
        "results_title": "\u0646\u062a\u0627\u0626\u062c \u0627\u0644\u0628\u062d\u062b",
        "results_count": '{count} \u0646\u062a\u064a\u062c\u0629 \u0644\u0640 "{query}"',
        "no_results": '\u0644\u0627 \u062a\u0648\u062c\u062f \u0646\u062a\u0627\u0626\u062c \u0644\u0640 "{query}"',
        "no_results_subtitle": "\u062c\u0631\u0628 \u0643\u0644\u0645\u0629 \u0628\u062d\u062b \u0623\u062e\u0631\u0649",
    },
    "footer": {
        "tagline": "\u0628\u0631\u0627\u0648\u0646\u064a\u0632 \u0648\u0643\u0648\u0643\u064a\u0632 \u0648\u062d\u0644\u0648\u064a\u0627\u062a \u0645\u0635\u0646\u0648\u0639\u0629 \u0628\u062d\u0628 \u0641\u064a \u0627\u0644\u0625\u0645\u0627\u0631\u0627\u062a",
        "copyright": "\u062c\u0645\u064a\u0639 \u0627\u0644\u062d\u0642\u0648\u0642 \u0645\u062d\u0641\u0648\u0638\u0629.",
        "instagram": "\u0625\u0646\u0633\u062a\u063a\u0631\u0627\u0645",
        "whatsapp": "\u0648\u0627\u062a\u0633\u0627\u0628",
    },
    "seo": {
        "home_title": "Melting Moments Cakes \u2014 \u0628\u0631\u0627\u0648\u0646\u064a\u0632 \u0648\u0643\u0648\u0643\u064a\u0632 \u0648\u062d\u0644\u0648\u064a\u0627\u062a \u0641\u064a \u0627\u0644\u0625\u0645\u0627\u0631\u0627\u062a",
        "home_description": "\u0628\u0631\u0627\u0648\u0646\u064a\u0632 \u0648\u0643\u0648\u0643\u064a\u0632 \u0648\u062d\u0644\u0648\u064a\u0627\u062a \u0645\u0635\u0646\u0648\u0639\u0629 \u064a\u062f\u0648\u064a\u0627\u064b \u0628\u062d\u0628 \u0648\u062a\u0648\u0635\u064a\u0644 \u0641\u064a \u062c\u0645\u064a\u0639 \u0623\u0646\u062d\u0627\u0621 \u0627\u0644\u0625\u0645\u0627\u0631\u0627\u062a.",
        "about_title": "\u0645\u0646 \u0646\u062d\u0646 \u2014 Melting Moments Cakes",
        "about_description": "\u062a\u0639\u0631\u0641 \u0639\u0644\u0649 \u0641\u0627\u0637\u0645\u0629 \u0639\u0628\u0627\u0633\u064a\u060c \u0627\u0644\u062e\u0628\u0627\u0632\u0629 \u0648\u0631\u0627\u0621 Melting Moments Cakes.",
        "faq_title": "\u0627\u0644\u0623\u0633\u0626\u0644\u0629 \u0627\u0644\u0634\u0627\u0626\u0639\u0629 \u2014 Melting Moments Cakes",
        "faq_description": "\u0627\u0644\u0623\u0633\u0626\u0644\u0629 \u0627\u0644\u0634\u0627\u0626\u0639\u0629 \u062d\u0648\u0644 \u0627\u0644\u0637\u0644\u0628 \u0648\u0627\u0644\u062a\u0648\u0635\u064a\u0644 \u0648\u062d\u0644\u0648\u064a\u0627\u062a\u0646\u0627.",
        "contact_title": "\u0627\u062a\u0635\u0644 \u0628\u0646\u0627 \u2014 Melting Moments Cakes",
        "contact_description": "\u062a\u0648\u0627\u0635\u0644 \u0645\u0639 Melting Moments Cakes \u0639\u0628\u0631 \u0648\u0627\u062a\u0633\u0627\u0628 \u0623\u0648 \u0627\u0644\u0628\u0631\u064a\u062f \u0627\u0644\u0625\u0644\u0643\u062a\u0631\u0648\u0646\u064a.",
        "cart_title": "\u0627\u0644\u0633\u0644\u0629 \u2014 Melting Moments Cakes",
        "checkout_title": "\u0625\u062a\u0645\u0627\u0645 \u0627\u0644\u0637\u0644\u0628 \u2014 Melting Moments Cakes",
        "login_title": "\u062a\u0633\u062c\u064a\u0644 \u0627\u0644\u062f\u062e\u0648\u0644 \u2014 Melting Moments Cakes",
        "signup_title": "\u0625\u0646\u0634\u0627\u0621 \u062d\u0633\u0627\u0628 \u2014 Melting Moments Cakes",
        "privacy_title": "\u0633\u064a\u0627\u0633\u0629 \u0627\u0644\u062e\u0635\u0648\u0635\u064a\u0629 \u2014 Melting Moments Cakes",
        "account_title": "\u062d\u0633\u0627\u0628\u064a \u2014 Melting Moments Cakes",
        "orders_title": "\u0637\u0644\u0628\u0627\u062a\u064a \u2014 Melting Moments Cakes",
        "search_title": "\u0628\u062d\u062b \u2014 Melting Moments Cakes",
    },
    "promo_banner": {
        "text": "\u062a\u0648\u0635\u064a\u0644 \u0645\u062c\u0627\u0646\u064a \u0644\u0644\u0637\u0644\u0628\u0627\u062a \u0641\u0648\u0642 200 \u062f.\u0625 | \u0627\u0633\u062a\u062e\u062f\u0645 \u0643\u0648\u062f FREESHIP",
    },
    "faq": {
        "q1": "\u0645\u0627 \u0647\u064a \u0627\u0644\u0645\u0646\u062a\u062c\u0627\u062a \u0627\u0644\u062a\u064a \u062a\u0642\u062f\u0645\u0648\u0646\u0647\u0627\u061f",
        "a1": "\u0646\u0642\u062f\u0645 \u0645\u062c\u0645\u0648\u0639\u0629 \u0645\u0646 \u0627\u0644\u0628\u0631\u0627\u0648\u0646\u064a\u0632 \u0648\u0627\u0644\u0643\u0648\u0643\u064a\u0632 \u0648\u0627\u0644\u062d\u0644\u0648\u064a\u0627\u062a \u0627\u0644\u0645\u0635\u0646\u0648\u0639\u0629 \u064a\u062f\u0648\u064a\u0627\u064b. \u0643\u0644 \u0634\u064a\u0621 \u064a\u064f\u062e\u0628\u0632 \u0637\u0627\u0632\u062c\u0627\u064b \u062d\u0633\u0628 \u0627\u0644\u0637\u0644\u0628.",
        "q2": "\u0643\u064a\u0641 \u0623\u0642\u062f\u0645 \u0637\u0644\u0628\u0627\u064b\u061f",
        "a2": "\u062a\u0635\u0641\u062d \u0642\u0627\u0626\u0645\u062a\u0646\u0627\u060c \u0623\u0636\u0641 \u0627\u0644\u0645\u0646\u062a\u062c\u0627\u062a \u0625\u0644\u0649 \u0633\u0644\u062a\u0643\u060c \u0648\u0623\u0643\u0645\u0644 \u0627\u0644\u062f\u0641\u0639. \u064a\u0645\u0643\u0646\u0643 \u0627\u0644\u062f\u0641\u0639 \u0625\u0644\u0643\u062a\u0631\u0648\u0646\u064a\u0627\u064b \u0628\u0627\u0644\u0628\u0637\u0627\u0642\u0629 \u0648\u0633\u0646\u0648\u0635\u0644\u0647 \u0625\u0644\u0649 \u0628\u0627\u0628\u0643.",
        "q3": "\u0645\u0627 \u0647\u064a \u0645\u0646\u0627\u0637\u0642 \u0627\u0644\u062a\u0648\u0635\u064a\u0644\u061f",
        "a3": "\u0646\u0648\u0635\u0644 \u0625\u0644\u0649 \u062c\u0645\u064a\u0639 \u0627\u0644\u0625\u0645\u0627\u0631\u0627\u062a \u0627\u0644\u0633\u0628\u0639. \u062a\u062e\u062a\u0644\u0641 \u0631\u0633\u0648\u0645 \u0627\u0644\u062a\u0648\u0635\u064a\u0644 \u062d\u0633\u0628 \u0627\u0644\u0645\u0648\u0642\u0639.",
        "q4": "\u0643\u0645 \u064a\u0633\u062a\u063a\u0631\u0642 \u0627\u0644\u062a\u0648\u0635\u064a\u0644\u061f",
        "a4": "\u0627\u0644\u0637\u0644\u0628\u0627\u062a \u0642\u0628\u0644 12 \u0638\u0647\u0631\u0627\u064b \u062a\u0635\u0644 \u0641\u064a \u0646\u0641\u0633 \u0627\u0644\u064a\u0648\u0645. \u0627\u0644\u0637\u0644\u0628\u0627\u062a \u0628\u0639\u062f 12 \u0638\u0647\u0631\u0627\u064b \u062a\u0635\u0644 \u0641\u064a \u0627\u0644\u064a\u0648\u0645 \u0627\u0644\u062a\u0627\u0644\u064a.",
        "q5": "\u0647\u0644 \u064a\u0645\u0643\u0646\u0646\u064a \u062a\u062e\u0635\u064a\u0635 \u0637\u0644\u0628\u064a\u061f",
        "a5": "\u0646\u0639\u0645! \u0627\u0644\u0639\u062f\u064a\u062f \u0645\u0646 \u0645\u0646\u062a\u062c\u0627\u062a\u0646\u0627 \u062a\u0623\u062a\u064a \u0645\u0639 \u062e\u064a\u0627\u0631\u0627\u062a \u0627\u0644\u062a\u062e\u0635\u064a\u0635. \u0627\u0628\u062d\u062b \u0639\u0646 \u0642\u0633\u0645 \u0627\u0644\u062a\u062e\u0635\u064a\u0635 \u0641\u064a \u0643\u0644 \u0635\u0641\u062d\u0629 \u0645\u0646\u062a\u062c.",
        "q6": "\u0647\u0644 \u062a\u0642\u062f\u0645\u0648\u0646 \u062e\u062f\u0645\u0627\u062a \u0627\u0644\u062a\u0645\u0648\u064a\u0646\u061f",
        "a6": "\u0628\u0627\u0644\u062a\u0623\u0643\u064a\u062f! \u0646\u0642\u062f\u0645 \u062e\u062f\u0645\u0627\u062a\u0646\u0627 \u0644\u0623\u0639\u064a\u0627\u062f \u0627\u0644\u0645\u064a\u0644\u0627\u062f \u0648\u0627\u0644\u0632\u0641\u0627\u0641 \u0648\u0641\u0639\u0627\u0644\u064a\u0627\u062a \u0627\u0644\u0634\u0631\u0643\u0627\u062a. \u062a\u0648\u0627\u0635\u0644 \u0645\u0639\u0646\u0627 \u0639\u0628\u0631 \u0648\u0627\u062a\u0633\u0627\u0628.",
        "q7": "\u0645\u0627 \u0647\u064a \u0633\u064a\u0627\u0633\u0629 \u0627\u0644\u0625\u0631\u062c\u0627\u0639\u061f",
        "a7": "\u0646\u0638\u0631\u0627\u064b \u0644\u0637\u0628\u064a\u0639\u0629 \u0645\u0646\u062a\u062c\u0627\u062a\u0646\u0627 \u0627\u0644\u0642\u0627\u0628\u0644\u0629 \u0644\u0644\u062a\u0644\u0641\u060c \u0644\u0627 \u0646\u0642\u0628\u0644 \u0627\u0644\u0625\u0631\u062c\u0627\u0639. \u0644\u0643\u0646 \u0625\u0630\u0627 \u0643\u0627\u0646 \u0647\u0646\u0627\u0643 \u0645\u0634\u0643\u0644\u0629 \u0641\u064a \u0637\u0644\u0628\u0643\u060c \u062a\u0648\u0627\u0635\u0644 \u0645\u0639\u0646\u0627 \u0641\u0648\u0631\u0627\u064b.",
        "q8": "\u0643\u064a\u0641 \u0623\u062a\u0648\u0627\u0635\u0644 \u0645\u0639\u0643\u0645\u061f",
        "a8": "\u064a\u0645\u0643\u0646\u0643 \u0627\u0644\u062a\u0648\u0627\u0635\u0644 \u0639\u0628\u0631 \u0648\u0627\u062a\u0633\u0627\u0628 \u0623\u0648 \u0627\u0644\u0628\u0631\u064a\u062f \u0627\u0644\u0625\u0644\u0643\u062a\u0631\u0648\u0646\u064a \u0623\u0648 \u0646\u0645\u0648\u0630\u062c \u0627\u0644\u062a\u0648\u0627\u0635\u0644. \u0646\u0631\u062f \u0639\u0627\u062f\u0629 \u062e\u0644\u0627\u0644 \u0633\u0627\u0639\u0627\u062a.",
    },
    "about": {
        "hero_title": "\u0642\u0635\u062a\u0646\u0627",
        "hero_subtitle": "\u0635\u0646\u0639 \u064a\u062f\u0648\u064a \u0628\u062d\u0628 \u0641\u064a \u0627\u0644\u0625\u0645\u0627\u0631\u0627\u062a",
        "story_p1": "\u0645\u0627 \u0628\u062f\u0623 \u0643\u0645\u0634\u0631\u0648\u0639 \u0634\u063a\u0641 \u0641\u064a \u0645\u0637\u0628\u062e \u0645\u0646\u0632\u0644\u064a \u0623\u0635\u0628\u062d Melting Moments Cakes \u2014 \u0639\u0644\u0627\u0645\u0629 \u062a\u062c\u0627\u0631\u064a\u0629 \u0645\u0628\u0646\u064a\u0629 \u0639\u0644\u0649 \u0623\u0646 \u0643\u0644 \u062d\u0644\u0648\u0649 \u064a\u062c\u0628 \u0623\u0646 \u062a\u0643\u0648\u0646 \u0644\u062d\u0638\u0629 \u0641\u0631\u062d.",
        "story_p2": "\u0623\u0633\u0633\u062a\u0647\u0627 \u0641\u0627\u0637\u0645\u0629 \u0639\u0628\u0627\u0633\u064a\u060c \u0646\u062a\u062e\u0635\u0635 \u0641\u064a \u0627\u0644\u0628\u0631\u0627\u0648\u0646\u064a\u0632 \u0648\u0627\u0644\u0643\u0648\u0643\u064a\u0632 \u0648\u0627\u0644\u062d\u0644\u0648\u064a\u0627\u062a \u0627\u0644\u0645\u0635\u0646\u0648\u0639\u0629 \u0645\u0646 \u0627\u0644\u0635\u0641\u0631 \u0628\u0623\u0641\u0636\u0644 \u0627\u0644\u0645\u0643\u0648\u0646\u0627\u062a.",
        "story_p3": "\u0643\u0644 \u0637\u0644\u0628 \u064a\u064f\u062e\u0628\u0632 \u0637\u0627\u0632\u062c\u0627\u064b\u060c \u0644\u0623\u0646\u0646\u0627 \u0646\u0624\u0645\u0646 \u0623\u0646\u0643 \u062a\u0633\u062a\u062d\u0642 \u0627\u0644\u0623\u0641\u0636\u0644.",
        "values_title": "\u0642\u064a\u0645\u0646\u0627",
        "value_quality": "\u0627\u0644\u062c\u0648\u062f\u0629",
        "value_quality_desc": "\u0623\u0641\u0636\u0644 \u0627\u0644\u0645\u0643\u0648\u0646\u0627\u062a \u0641\u0642\u0637\u060c \u0628\u062f\u0648\u0646 \u0627\u062e\u062a\u0635\u0627\u0631\u0627\u062a",
        "value_freshness": "\u0627\u0644\u0637\u0632\u0627\u062c\u0629",
        "value_freshness_desc": "\u064a\u064f\u062e\u0628\u0632 \u062d\u0633\u0628 \u0627\u0644\u0637\u0644\u0628\u060c \u0644\u0627 \u064a\u0646\u062a\u0638\u0631 \u0639\u0644\u0649 \u0627\u0644\u0631\u0641",
        "value_love": "\u0627\u0644\u062d\u0628",
        "value_love_desc": "\u0643\u0644 \u062d\u0644\u0648\u0649 \u0645\u0635\u0646\u0648\u0639\u0629 \u0628\u0639\u0646\u0627\u064a\u0629 \u062d\u0642\u064a\u0642\u064a\u0629",
        "value_community": "\u0627\u0644\u0645\u062c\u062a\u0645\u0639",
        "value_community_desc": "\u0646\u062e\u062f\u0645 \u0645\u062c\u062a\u0645\u0639 \u0627\u0644\u0625\u0645\u0627\u0631\u0627\u062a \u0628\u0641\u062e\u0631",
    },
    "contact": {
        "title": "\u062a\u0648\u0627\u0635\u0644 \u0645\u0639\u0646\u0627",
        "subtitle": "\u0646\u062d\u0628 \u0623\u0646 \u0646\u0633\u0645\u0639 \u0645\u0646\u0643",
        "whatsapp_title": "\u0648\u0627\u062a\u0633\u0627\u0628",
        "whatsapp_desc": "\u062a\u062d\u062f\u062b \u0645\u0639\u0646\u0627 \u0645\u0628\u0627\u0634\u0631\u0629",
        "email_title": "\u0627\u0644\u0628\u0631\u064a\u062f \u0627\u0644\u0625\u0644\u0643\u062a\u0631\u0648\u0646\u064a",
        "email_desc": "hello@meltingmomentscakes.com",
        "location_title": "\u0627\u0644\u0645\u0648\u0642\u0639",
        "location_desc": "\u062f\u0628\u064a\u060c \u0627\u0644\u0625\u0645\u0627\u0631\u0627\u062a",
        "hours_title": "\u0633\u0627\u0639\u0627\u062a \u0627\u0644\u0639\u0645\u0644",
        "hours_desc": "\u064a\u0648\u0645\u064a\u0627\u064b 9 \u0635\u0628\u0627\u062d\u0627\u064b - 9 \u0645\u0633\u0627\u0621\u064b",
        "form_name": "\u0627\u0633\u0645\u0643",
        "form_email": "\u0628\u0631\u064a\u062f\u0643 \u0627\u0644\u0625\u0644\u0643\u062a\u0631\u0648\u0646\u064a",
        "form_message": "\u0631\u0633\u0627\u0644\u062a\u0643",
        "form_send": "\u0623\u0631\u0633\u0644 \u0639\u0628\u0631 \u0648\u0627\u062a\u0633\u0627\u0628",
    },
    "privacy": {
        "title": "\u0633\u064a\u0627\u0633\u0629 \u0627\u0644\u062e\u0635\u0648\u0635\u064a\u0629",
        "last_updated": "\u0622\u062e\u0631 \u062a\u062d\u062f\u064a\u062b: {date}",
    },
    "error": {
        "404_title": "\u0627\u0644\u0635\u0641\u062d\u0629 \u063a\u064a\u0631 \u0645\u0648\u062c\u0648\u062f\u0629",
        "404_subtitle": "\u0627\u0644\u0635\u0641\u062d\u0629 \u0627\u0644\u062a\u064a \u062a\u0628\u062d\u062b \u0639\u0646\u0647\u0627 \u063a\u064a\u0631 \u0645\u0648\u062c\u0648\u062f\u0629 \u0623\u0648 \u062a\u0645 \u0646\u0642\u0644\u0647\u0627.",
        "500_title": "\u062d\u062f\u062b \u062e\u0637\u0623",
        "500_subtitle": "\u0646\u0639\u062a\u0630\u0631\u060c \u062d\u062f\u062b \u062e\u0637\u0623 \u063a\u064a\u0631 \u0645\u062a\u0648\u0642\u0639.",
        "back_home": "\u0627\u0644\u0639\u0648\u062f\u0629 \u0644\u0644\u0631\u0626\u064a\u0633\u064a\u0629",
    },
}


def upgrade() -> None:
    # 1. Create languages table
    op.create_table(
        "languages",
        sa.Column("code", sa.String(10), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("native_name", sa.String(100), nullable=False),
        sa.Column("direction", sa.String(3), nullable=False, server_default="ltr"),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
    )

    # Seed languages
    op.execute(
        "INSERT INTO languages (code, name, native_name, direction, is_default, is_active, display_order) "
        "VALUES ('en', 'English', 'English', 'ltr', true, true, 0)"
    )
    op.execute(
        "INSERT INTO languages (code, name, native_name, direction, is_default, is_active, display_order) "
        "VALUES ('ar', 'Arabic', '\u0627\u0644\u0639\u0631\u0628\u064a\u0629', 'rtl', false, true, 1)"
    )

    # 2. Create ui_translations table
    op.create_table(
        "ui_translations",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "locale",
            sa.String(10),
            sa.ForeignKey("languages.code", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("namespace", sa.String(50), nullable=False, index=True),
        sa.Column("key", sa.String(200), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.UniqueConstraint("locale", "namespace", "key", name="uq_ui_translation"),
    )

    # 3. Add translations JSONB to entity tables
    for table in ["products", "categories", "modifiers", "modifier_options"]:
        op.add_column(
            table,
            sa.Column("translations", JSONB(), nullable=False, server_default="{}"),
        )

    # 4. Add product_translations JSONB to order_items (replaces product_name_localized)
    op.add_column(
        "order_items",
        sa.Column("product_translations", JSONB(), nullable=False, server_default="{}"),
    )

    # 5. Migrate existing _localized data into JSONB translations
    # Products: name_localized → translations.ar.name, description_localized → translations.ar.description
    op.execute("""
        UPDATE products
        SET translations = jsonb_build_object(
            'ar', jsonb_strip_nulls(jsonb_build_object(
                'name', name_localized,
                'description', description_localized
            ))
        )
        WHERE name_localized IS NOT NULL OR description_localized IS NOT NULL
    """)

    # Categories: name_localized → translations.ar.name
    op.execute("""
        UPDATE categories
        SET translations = jsonb_build_object('ar', jsonb_build_object('name', name_localized))
        WHERE name_localized IS NOT NULL
    """)

    # Modifiers: name_localized → translations.ar.name
    op.execute("""
        UPDATE modifiers
        SET translations = jsonb_build_object('ar', jsonb_build_object('name', name_localized))
        WHERE name_localized IS NOT NULL
    """)

    # ModifierOptions: name_localized → translations.ar.name
    op.execute("""
        UPDATE modifier_options
        SET translations = jsonb_build_object('ar', jsonb_build_object('name', name_localized))
        WHERE name_localized IS NOT NULL
    """)

    # OrderItems: product_name_localized → product_translations.ar.name
    op.execute("""
        UPDATE order_items
        SET product_translations = jsonb_build_object('ar', jsonb_build_object('name', product_name_localized))
        WHERE product_name_localized IS NOT NULL
    """)

    # 6. Drop old _localized columns
    op.drop_column("products", "name_localized")
    op.drop_column("products", "description_localized")
    op.drop_column("categories", "name_localized")
    op.drop_column("modifiers", "name_localized")
    op.drop_column("modifier_options", "name_localized")
    op.drop_column("order_items", "product_name_localized")

    # 7. Seed English UI translations
    _seed_translations("en", EN_TRANSLATIONS)

    # 8. Seed Arabic UI translations
    _seed_translations("ar", AR_TRANSLATIONS)


def _seed_translations(locale: str, data: dict[str, dict[str, str]]) -> None:
    for namespace, entries in data.items():
        for key, value in entries.items():
            safe_value = value.replace("'", "''")
            op.execute(
                f"INSERT INTO ui_translations (id, locale, namespace, key, value) "
                f"VALUES ('{uuid.uuid4()}', '{locale}', '{namespace}', '{key}', '{safe_value}')"
            )


def downgrade() -> None:
    # Re-add _localized columns
    op.add_column(
        "order_items",
        sa.Column("product_name_localized", sa.String(300), nullable=True),
    )
    op.add_column(
        "modifier_options",
        sa.Column("name_localized", sa.String(300), nullable=True),
    )
    op.add_column(
        "modifiers", sa.Column("name_localized", sa.String(300), nullable=True)
    )
    op.add_column(
        "categories", sa.Column("name_localized", sa.String(300), nullable=True)
    )
    op.add_column(
        "products", sa.Column("description_localized", sa.Text(), nullable=True)
    )
    op.add_column(
        "products", sa.Column("name_localized", sa.String(300), nullable=True)
    )

    # Migrate data back from JSONB
    op.execute(
        "UPDATE products SET name_localized = translations->'ar'->>'name', "
        "description_localized = translations->'ar'->>'description'"
    )
    op.execute("UPDATE categories SET name_localized = translations->'ar'->>'name'")
    op.execute("UPDATE modifiers SET name_localized = translations->'ar'->>'name'")
    op.execute(
        "UPDATE modifier_options SET name_localized = translations->'ar'->>'name'"
    )
    op.execute(
        "UPDATE order_items SET product_name_localized = product_translations->'ar'->>'name'"
    )

    # Drop JSONB columns
    op.drop_column("order_items", "product_translations")
    for table in ["modifier_options", "modifiers", "categories", "products"]:
        op.drop_column(table, "translations")

    # Drop tables
    op.drop_table("ui_translations")
    op.drop_table("languages")
