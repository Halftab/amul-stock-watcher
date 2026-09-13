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
    """

    try:
        pincode_input = page.get_by_placeholder(
            "Enter Your Pincode",
            exact=False,
        ).first

        if await pincode_input.count() == 0:
            pincode_input = page.locator(
                'input[placeholder*="Pincode" i]'
            ).first

        if await pincode_input.count() == 0:
            print("Pincode input not found.")
            return False

        await pincode_input.fill(PINCODE)
        await pincode_input.press("Enter")

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

        try:
            print(f"Opening: {PRODUCT_URL}")

            await page.goto(
                PRODUCT_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            # Give the storefront plenty of time to render.
            await page.wait_for_timeout(7000)

            # -----------------------------------------------------
            # Apply delivery pincode.
            # -----------------------------------------------------

            pincode_applied = await apply_pincode(page)

            await page.wait_for_timeout(3000)

            # -----------------------------------------------------
            # Basic page information.
            # -----------------------------------------------------

            current_url = page.url
            page_title = await page.title()

            print(f"Final URL: {current_url}")
            print(f"Page title: {page_title}")

            # The URL itself is the exact product URL, so we don't
            # need an H1 to prove product identity.
            product_page_verified = (
                "amul-high-protein-plain-lassi-200-ml-or-pack-of-30"
                in current_url.lower()
            )

            print(
                f"Product URL verified: "
                f"{product_page_verified}"
            )

            if not product_page_verified:
                return {
                    "status": "UNKNOWN",
                    "price": None,
                    "pincode_applied": pincode_applied,
                    "reason": (
                        "The browser was redirected away from "
                        "the expected Amul product URL."
                    ),
                }

            # -----------------------------------------------------
            # Read visible page text.
            # -----------------------------------------------------

            body_text = await page.locator(
                "body"
            ).inner_text()

            body_lower = body_text.lower()

            # Print a useful diagnostic.
            print(
                f"Page text length: {len(body_text)} characters"
            )

            # -----------------------------------------------------
            # Extract price.
            # -----------------------------------------------------

            price = None

            price_patterns = [
                r"MRP\s*[:\-]?\s*₹\s*([\d,]+)",
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

            # -----------------------------------------------------
            # Check the main Add to Cart button.
            #
            # The Amul page currently exposes an Add to Cart
            # button even when the product is sold out.
            # We therefore inspect whether it is disabled.
            # -----------------------------------------------------

            add_to_cart = page.get_by_role(
                "button",
                name=re.compile(
                    r"add\s*to\s*cart",
                    re.IGNORECASE,
                ),
            )

            add_to_cart_count = await add_to_cart.count()

            print(
                f"Add to Cart buttons found: "
                f"{add_to_cart_count}"
            )

            visible_add_to_cart = []

            for i in range(add_to_cart_count):

                button = add_to_cart.nth(i)

                try:
                    if await button.is_visible():

                        disabled = await button.is_disabled()

                        print(
                            f"Add to Cart button {i}: "
                            f"visible=True, "
                            f"disabled={disabled}"
                        )

                        visible_add_to_cart.append(
                            {
                                "button": button,
                                "disabled": disabled,
                            }
                        )

                except Exception as exc:
                    print(
                        f"Could not inspect Add to Cart "
                        f"button {i}: {exc}"
                    )

            # -----------------------------------------------------
            # Look for the product's Sold Out / Out of Stock state.
            #
            # Because this is the exact product URL, the first
            # occurrence of "Sold Out" near the top of the page
            # corresponds to the main product.
            # -----------------------------------------------------

            sold_out_match = re.search(
                r"\bsold\s*out\b",
                body_lower,
            )

            out_of_stock_match = re.search(
                r"\bout\s+of\s+stock\b",
                body_lower,
            )

            print(
                f"Sold Out text found: "
                f"{sold_out_match is not None}"
            )

            print(
                f"Out of Stock text found: "
                f"{out_of_stock_match is not None}"
            )

            # -----------------------------------------------------
            # Determine stock status.
            # -----------------------------------------------------

            # If the main page explicitly says Sold Out or
            # Out of Stock, classify as OUT_OF_STOCK.
            if (
                sold_out_match is not None
                or out_of_stock_match is not None
            ):
                return {
                    "status": "OUT_OF_STOCK",
                    "price": price,
                    "pincode_applied": pincode_applied,
                    "reason": (
                        "Amul product page reports "
                        "Sold Out / Out of Stock."
                    ),
                }

            # If an enabled Add to Cart button is present,
            # classify as IN_STOCK.
            for item in visible_add_to_cart:

                if not item["disabled"]:

                    return {
                        "status": "IN_STOCK",
                        "price": price,
                        "pincode_applied": pincode_applied,
                        "reason": (
                            "Enabled Add to Cart button "
                            "found on the product page."
                        ),
                    }

            # -----------------------------------------------------
            # We found the correct product page but cannot safely
            # determine availability.
            # -----------------------------------------------------

            return {
                "status": "UNKNOWN",
                "price": price,
                "pincode_applied": pincode_applied,
                "reason": (
                    "Product page loaded, but stock status "
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

    # ---------------------------------------------------------
    # Only alert when the product becomes available.
    # ---------------------------------------------------------

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
