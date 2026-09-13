import asyncio
import json
import os
import re
from datetime import datetime, timezone

import requests
from playwright.async_api import async_playwright


PRODUCT_URL = (
    "https://shop.amul.com/product/"
    "amul-high-protein-plain-lassi-200-ml-or-pack-of-30"
)

PRODUCT_NAME = "Amul High Protein Plain Lassi, 200 mL | Pack of 30"

PINCODE = "110044"
STATE_FILE = "state.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"status": None}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"status": None}


def save_state(status):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            {
                "status": status,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            },
            f,
            indent=2,
        )


def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be configured."
        )

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    response = requests.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "disable_web_page_preview": False,
        },
        timeout=20,
    )

    response.raise_for_status()


async def check_stock():

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        page = await browser.new_page(
            viewport={
                "width": 1440,
                "height": 1000,
            },
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 "
                "(KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
        )

        try:

            print(f"Opening: {PRODUCT_URL}")

            # IMPORTANT:
            # Do NOT enter the pincode here.
            # Entering it causes Amul to redirect to a broken
            # /en/product URL on the automated browser.

            await page.goto(
                PRODUCT_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            # Allow JavaScript storefront to render.
            await page.wait_for_timeout(7000)

            final_url = page.url

            print(f"Final URL: {final_url}")

            # -------------------------------------------------
            # Verify that we stayed on the expected product page.
            # -------------------------------------------------

            if (
                "amul-high-protein-plain-lassi-200-ml-or-pack-of-30"
                not in final_url.lower()
            ):
                return {
                    "status": "UNKNOWN",
                    "price": None,
                    "pincode_applied": False,
                    "reason": (
                        "Amul redirected away from the expected "
                        "product page."
                    ),
                }

            # -------------------------------------------------
            # Read page text.
            # -------------------------------------------------

            body_text = await page.locator(
                "body"
            ).inner_text()

            body_lower = body_text.lower()

            print(
                f"Page text length: {len(body_text)} characters"
            )

            # Print useful diagnostic information.
            preview = re.sub(
                r"\s+",
                " ",
                body_text,
            ).strip()

            print(
                f"Page preview: {preview[:1000]}"
            )

            # -------------------------------------------------
            # Verify product.
            # -------------------------------------------------

            product_found = (
                "amul high protein plain lassi" in body_lower
                and "pack of 30" in body_lower
            )

            print(
                f"Product found: {product_found}"
            )

            if not product_found:
                return {
                    "status": "UNKNOWN",
                    "price": None,
                    "pincode_applied": False,
                    "reason": (
                        "Expected product was not found "
                        "on the product page."
                    ),
                }

            # -------------------------------------------------
            # Extract price.
            # -------------------------------------------------

            price = None

            price_patterns = [
                r"MRP\s*₹\s*([\d,]+)",
                r"₹\s*([\d,]+)",
            ]

            for pattern in price_patterns:

                match = re.search(
                    pattern,
                    body_text,
                    re.IGNORECASE,
                )

                if match:

                    price = int(
                        match.group(1).replace(",", "")
                    )

                    break

            # -------------------------------------------------
            # Check explicit Sold Out / Out of Stock text.
            # -------------------------------------------------

            sold_out = "sold out" in body_lower
            out_of_stock = "out of stock" in body_lower

            print(
                f"Sold Out detected: {sold_out}"
            )

            print(
                f"Out of Stock detected: {out_of_stock}"
            )

            if sold_out or out_of_stock:

                return {
                    "status": "OUT_OF_STOCK",
                    "price": price,
                    "pincode_applied": False,
                    "reason": (
                        "Amul product page explicitly "
                        "reports Sold Out / Out of Stock."
                    ),
                }

            # -------------------------------------------------
            # Check Add to Cart.
            # -------------------------------------------------

            add_to_cart = page.get_by_role(
                "button",
                name=re.compile(
                    r"add\s*to\s*cart",
                    re.IGNORECASE,
                ),
            )

            count = await add_to_cart.count()

            print(
                f"Add to Cart buttons found: {count}"
            )

            for i in range(count):

                button = add_to_cart.nth(i)

                try:

                    if await button.is_visible():

                        disabled = await button.is_disabled()

                        print(
                            f"Add to Cart {i}: "
                            f"disabled={disabled}"
                        )

                        if not disabled:

                            return {
                                "status": "IN_STOCK",
                                "price": price,
                                "pincode_applied": False,
                                "reason": (
                                    "Enabled Add to Cart "
                                    "button found."
                                ),
                            }

                except Exception:
                    continue

            # -------------------------------------------------
            # Product exists but stock cannot be determined.
            # -------------------------------------------------

            return {
                "status": "UNKNOWN",
                "price": price,
                "pincode_applied": False,
                "reason": (
                    "Product page loaded, but availability "
                    "could not be confirmed."
                ),
            }

        finally:
            await browser.close()


async def main():

    print("=" * 60)
    print("Amul Stock Watcher")
    print("=" * 60)

    result = await check_stock()

    status = result["status"]
    price = result["price"]
    pincode_applied = result["pincode_applied"]
    reason = result["reason"]

    print(f"Product: {PRODUCT_NAME}")
    print(f"Pincode: {PINCODE}")
    print(
        f"Pincode applied: {pincode_applied}"
    )

    print(
        f"Price: ₹{price if price is not None else 'unknown'}"
    )

    print(f"Status: {status}")
    print(f"Reason: {reason}")

    previous_state = load_state()
    previous_status = previous_state.get("status")

    print(
        f"Previous status: {previous_status}"
    )

    save_state(status)

    # -------------------------------------------------
    # Alert only when product becomes available.
    # -------------------------------------------------

    if (
        status == "IN_STOCK"
        and previous_status in (
            "OUT_OF_STOCK",
            "UNKNOWN",
            None,
        )
    ):

        message = (
            "🚨 AMUL LASSI IS BACK IN STOCK!\n\n"
            f"{PRODUCT_NAME}\n"
            f"Price: ₹{price if price is not None else 'Unknown'}\n"
            f"Delivery pincode: {PINCODE}\n\n"
            f"🛒 Buy now:\n{PRODUCT_URL}"
        )

        send_telegram(message)

        print(
            "✅ Telegram stock alert sent!"
        )

    else:

        print(
            "No Telegram alert needed."
        )

    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
