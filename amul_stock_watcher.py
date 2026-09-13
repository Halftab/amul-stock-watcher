import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone

import requests
from playwright.async_api import async_playwright


PRODUCT_URL = (
    "https://shop.amul.com/en/product/"
    "amul-high-protein-plain-lassi-200-ml-or-pack-of-30"
)

PRODUCT_NAME = "Amul High Protein Plain Lassi, 200 mL | Pack of 30"
PINCODE = "110044"
EXPECTED_PRICE = 900

STATE_FILE = "state.json"

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")


def load_state():
    """Load the last known stock status."""
    if not os.path.exists(STATE_FILE):
        return {"status": None}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"status": None}


def save_state(status):
    """Save the current stock status."""
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
    """Send a Telegram notification."""
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
    """Check the Amul product page."""

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        page = await browser.new_page(
            viewport={"width": 1440, "height": 1000}
        )

        print(f"Opening: {PRODUCT_URL}")

        await page.goto(
            PRODUCT_URL,
            wait_until="domcontentloaded",
            timeout=60000,
        )

        # Give the JavaScript storefront time to finish rendering.
        await page.wait_for_timeout(5000)

        body_text = await page.locator("body").inner_text()

        # Verify that this really is our product.
        if PRODUCT_NAME.lower() not in body_text.lower():
            await browser.close()

            return {
                "status": "UNKNOWN",
                "price": None,
                "reason": "Exact product title was not found.",
            }

        # Extract price.
        price = None

        price_match = re.search(
            r"(?:MRP\s*)?₹\s*([\d,]+)",
            body_text,
            re.IGNORECASE,
        )

        if price_match:
            price = int(price_match.group(1).replace(",", ""))

        text_lower = body_text.lower()

        # Amul currently exposes explicit "Sold Out" / "Out of Stock"
        # signals on unavailable product pages.
        unavailable_phrases = [
            "sold out",
            "out of stock",
        ]

        if any(phrase in text_lower for phrase in unavailable_phrases):
            await browser.close()

            return {
                "status": "OUT_OF_STOCK",
                "price": price,
                "reason": "Product page reports unavailable.",
            }

        # Check for an actual Add to Cart control.
        buttons = await page.locator("button").all()

        add_to_cart_enabled = False

        for button in buttons:
            try:
                text = (await button.inner_text()).strip().lower()

                if "add to cart" in text:
                    disabled = await button.is_disabled()

                    if not disabled:
                        add_to_cart_enabled = True
                        break

            except Exception:
                continue

        if add_to_cart_enabled:
            await browser.close()

            return {
                "status": "IN_STOCK",
                "price": price,
                "reason": "Enabled Add to Cart button found.",
            }

        await browser.close()

        return {
            "status": "UNKNOWN",
            "price": price,
            "reason": "Could not reliably determine availability.",
        }


async def main():
    print("=" * 60)
    print("Amul Stock Watcher")
    print("=" * 60)

    result = await check_stock()

    status = result["status"]
    price = result["price"]
    reason = result["reason"]

    print(f"Product: {PRODUCT_NAME}")
    print(f"Pincode: {PINCODE}")
    print(f"Price: ₹{price if price is not None else 'unknown'}")
    print(f"Status: {status}")
    print(f"Reason: {reason}")

    previous_state = load_state()
    previous_status = previous_state.get("status")

    print(f"Previous status: {previous_status}")

    # Save state after every successful determination.
    if status in ("IN_STOCK", "OUT_OF_STOCK", "UNKNOWN"):
        save_state(status)

    # Only alert on a transition into stock.
    if (
        status == "IN_STOCK"
        and previous_status in ("OUT_OF_STOCK", "UNKNOWN", None)
    ):
        message = (
            "🚨 BACK IN STOCK!\n\n"
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
