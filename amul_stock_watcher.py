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


async def get_product_heading(page):
    """
    Find the actual product H1 heading.
    """

    try:
        headings = page.locator("h1")

        count = await headings.count()

        print(f"H1 headings found: {count}")

        for i in range(count):
            try:
                text = await headings.nth(i).inner_text()

                cleaned = re.sub(
                    r"\s+",
                    " ",
                    text.replace("\xa0", " "),
                ).strip()

                print(f"H1[{i}]: {cleaned}")

                if (
                    "amul high protein plain lassi" in cleaned.lower()
                    and "pack of 30" in cleaned.lower()
                ):
                    return cleaned

            except Exception:
                continue

    except Exception as exc:
        print(f"Could not inspect H1 headings: {exc}")

    return None


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

        try:
            await page.goto(
                PRODUCT_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await page.wait_for_timeout(5000)

            # -----------------------------------------------------
            # Apply delivery pincode.
            # -----------------------------------------------------

            pincode_applied = await apply_pincode(page)

            await page.wait_for_timeout(2000)

            # -----------------------------------------------------
            # Get page text.
            # -----------------------------------------------------

            body_text = await page.locator("body").inner_text()

            # -----------------------------------------------------
            # Verify product using the actual H1 heading.
            # -----------------------------------------------------

            product_heading = await get_product_heading(page)

            product_verified = product_heading is not None

            print(
                f"Product heading found: {product_verified}"
            )

            if product_heading:
                print(
                    f"Product heading: {product_heading}"
                )

            if not product_verified:
                return {
                    "status": "UNKNOWN",
                    "price": None,
                    "pincode_applied": pincode_applied,
                    "reason": (
                        "Product identity could not be reliably "
                        "confirmed from the product heading."
                    ),
                }

            # -----------------------------------------------------
            # Extract price.
            # -----------------------------------------------------

            price = None

            # First try the product area / page text.
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
            # Find the product's main container.
            #
            # We deliberately avoid searching the entire page for
            # "Sold Out", because Amul may display other products
            # lower down that are also sold out.
            # -----------------------------------------------------

            product_text = ""

            try:
                h1_locator = page.locator("h1").filter(
                    has_text=re.compile(
                        r"amul\s+high\s+protein\s+plain\s+lassi",
                        re.IGNORECASE,
                    )
                ).first

                if await h1_locator.count() > 0:

                    # Walk upward through parents and look for a
                    # reasonably-sized product section.
                    product_text = await h1_locator.evaluate(
                        """
                        (el) => {
                            let current = el;

                            for (let i = 0; i < 8; i++) {
                                if (!current) break;

                                const text =
                                    current.innerText || "";

                                const lower =
                                    text.toLowerCase();

                                if (
                                    text.length < 12000 &&
                                    (
                                        lower.includes("mrp") ||
                                        lower.includes("sold out") ||
                                        lower.includes("out of stock") ||
                                        lower.includes("add to cart")
                                    )
                                ) {
                                    return text;
                                }

                                current = current.parentElement;
                            }

                            return el.parentElement
                                ? el.parentElement.innerText
                                : el.innerText;
                        }
                        """
                    )

            except Exception as exc:
                print(
                    f"Could not isolate product section: {exc}"
                )

            if not product_text:
                product_text = body_text

            product_text_clean = re.sub(
                r"\s+",
                " ",
                product_text.replace("\xa0", " "),
            ).strip()

            product_text_lower = (
                product_text_clean.lower()
            )

            print(
                f"Product section length: "
                f"{len(product_text_clean)} characters"
            )

            # -----------------------------------------------------
            # Extract price again from the product section.
            # This is preferred because it avoids prices from
            # unrelated products.
            # -----------------------------------------------------

            section_price_patterns = [
                r"MRP\s*[:\-]?\s*₹\s*([\d,]+)",
                r"₹\s*([\d,]+)",
            ]

            for pattern in section_price_patterns:
                match = re.search(
                    pattern,
                    product_text_clean,
                    re.IGNORECASE,
                )

                if match:
                    price = int(
                        match.group(1).replace(",", "")
                    )
                    break

            # -----------------------------------------------------
            # Detect explicit unavailable state.
            # -----------------------------------------------------

            unavailable_phrases = [
                "sold out",
                "out of stock",
            ]

            unavailable = any(
                phrase in product_text_lower
                for phrase in unavailable_phrases
            )

            if unavailable:
                return {
                    "status": "OUT_OF_STOCK",
                    "price": price,
                    "pincode_applied": pincode_applied,
                    "reason": (
                        "Product section explicitly reports "
                        "Sold Out / Out of Stock."
                    ),
                }

            # -----------------------------------------------------
            # Look for an enabled Add to Cart button.
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

                        if not disabled:
                            return {
                                "status": "IN_STOCK",
                                "price": price,
                                "pincode_applied": pincode_applied,
                                "reason": (
                                    "Enabled Add to Cart "
                                    "button found."
                                ),
                            }

                except Exception:
                    continue

            # -----------------------------------------------------
            # Product exists but availability is ambiguous.
            # Stay conservative.
            # -----------------------------------------------------

            return {
                "status": "UNKNOWN",
                "price": price,
                "pincode_applied": pincode_applied,
                "reason": (
                    "Product was found, but availability "
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
    # Only send Telegram notification when the product becomes
    # available.
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
