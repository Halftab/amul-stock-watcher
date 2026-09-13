import asyncio
import json
import os
import re
from datetime import datetime, timezone

import requests
from playwright.async_api import async_playwright


PRODUCT_URL = (
    "https://shop.amul.com/en/product/"
    "amul-high-protein-plain-lassi-200-ml-or-pack-of-30"
)

PRODUCT_NAME = "Amul High Protein Plain Lassi, 200 mL | Pack of 30"
PRODUCT_KEYWORDS = [
    "amul high protein plain lassi",
    "pack of 30",
]

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


async def apply_pincode(page):
    """
    Attempt to set the Amul delivery pincode.

    Amul currently exposes an input labelled/placeholdered
    "Enter Your Pincode".
    """

    try:
        # First try a placeholder-based locator.
        pincode_input = page.get_by_placeholder(
            "Enter Your Pincode",
            exact=False,
        ).first

        if await pincode_input.count() == 0:
            # Fallback: search inputs by their surrounding text/attributes.
            pincode_input = page.locator(
                'input[placeholder*="Pincode" i]'
            ).first

        if await pincode_input.count() == 0:
            print("Pincode input not found.")
            return False

        await pincode_input.fill(PINCODE)
        await pincode_input.press("Enter")

        # Allow the site time to process the pincode.
        await page.wait_for_timeout(3000)

        print(f"Pincode {PINCODE} submitted.")
        return True

    except Exception as exc:
        print(f"Could not apply pincode: {exc}")
        return False


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

        print(f"Opening: {PRODUCT_URL}")

        await page.goto(
            PRODUCT_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        # Let the JavaScript storefront finish rendering.
        await page.wait_for_timeout(5000)

        # Try to set the delivery pincode.
        pincode_applied = await apply_pincode(page)

        # Give the page another moment after the pincode operation.
        await page.wait_for_timeout(2000)

        body_text = await page.locator("body").inner_text()

        body_lower = body_text.lower()

        # ---------------------------------------------------------
        # Verify the product using multiple signals.
        # ---------------------------------------------------------

        product_name_found = (
            "amul high protein plain lassi" in body_lower
        )

        pack_size_found = "pack of 30" in body_lower

        product_verified = (
            product_name_found and pack_size_found
        )

        if not product_verified:
            await browser.close()

            return {
                "status": "UNKNOWN",
                "price": None,
                "pincode_applied": pincode_applied,
                "reason": (
                    "Product identity could not be reliably "
                    "confirmed."
                ),
            }

        # ---------------------------------------------------------
        # Extract price.
        # ---------------------------------------------------------

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

        # ---------------------------------------------------------
        # Detect explicit unavailable state.
        # ---------------------------------------------------------

        unavailable_phrases = [
            "sold out",
            "out of stock",
        ]

        unavailable = any(
            phrase in body_lower
            for phrase in unavailable_phrases
        )

        if unavailable:
            await browser.close()

            return {
                "status": "OUT_OF_STOCK",
                "price": price,
                "pincode_applied": pincode_applied,
                "reason": (
                    "Product page explicitly reports "
                    "Sold Out / Out of Stock."
                ),
            }

        # ---------------------------------------------------------
        # Look for an enabled Add to Cart button.
        # ---------------------------------------------------------

        add_to_cart = page.get_by_role(
            "button",
            name=re.compile(
                r"add to cart",
                re.IGNORECASE,
            ),
        )

        add_to_cart_count = await add_to_cart.count()

        for i in range(add_to_cart_count):
            button = add_to_cart.nth(i)

            try:
                if await button.is_visible():
                    if not await button.is_disabled():
                        await browser.close()

                        return {
                            "status": "IN_STOCK",
                            "price": price,
                            "pincode_applied": pincode_applied,
                            "reason": (
                                "Enabled Add to Cart button "
                                "found."
                            ),
                        }

            except Exception:
                continue

        # ---------------------------------------------------------
        # If product is verified but we cannot determine whether
        # it is purchasable, stay conservative.
        # ---------------------------------------------------------

        await browser.close()

        return {
            "status": "UNKNOWN",
            "price": price,
            "pincode_applied": pincode_applied,
            "reason": (
                "Product was found, but availability "
                "could not be confirmed."
            ),
        }


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
    print(f"Pincode applied: {pincode_applied}")
    print(
        f"Price: ₹{price if price is not None else 'unknown'}"
    )
    print(f"Status: {status}")
    print(f"Reason: {reason}")

    previous_state = load_state()
    previous_status = previous_state.get("status")

    print(f"Previous status: {previous_status}")

    save_state(status)

    # Only notify when transitioning into stock.
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

        print("✅ Telegram stock alert sent!")

    else:
        print("No Telegram alert needed.")

    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
