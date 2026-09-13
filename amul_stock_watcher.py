import asyncio
import json
import os
import re
import sys

import requests
from playwright.async_api import async_playwright


# ============================================================
# CONFIGURATION
# ============================================================

SEARCH_URL = "https://shop.amul.com/en/search/high%20protein"

PRODUCT_NAME = "Amul High Protein Plain Lassi, 200 mL | Pack of 30"
PINCODE = "110044"

STATE_FILE = "state.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# STATE
# ============================================================

def load_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram secrets are missing.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:
        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
            },
            timeout=20,
        )

        if response.ok:
            print("Telegram alert sent.")
            return True

        print(
            f"Telegram error: {response.status_code} "
            f"{response.text[:300]}"
        )
        return False

    except Exception as e:
        print(f"Telegram request failed: {e}")
        return False


# ============================================================
# PINCODE MODAL
# ============================================================

async def find_pincode_input(page):
    selectors = [
        'input[placeholder*="ENTER YOUR PINCODE" i]',
        'input[placeholder*="YOUR PINCODE" i]',
        'input[placeholder*="PINCODE" i]',
        'input[placeholder*="Pincode" i]',
        'input[type="number"]',
        'input[inputmode="numeric"]',
    ]

    for selector in selectors:
        locator = page.locator(selector).first

        try:
            if await locator.count() > 0 and await locator.is_visible():
                return locator
        except Exception:
            pass

    return None


async def pincode_modal_visible(page):
    try:
        title = page.get_by_text(
            "Select Delivery Pincode",
            exact=True
        ).first

        if await title.count() > 0:
            return await title.is_visible()

    except Exception:
        pass

    # Fallback: look for the pincode input itself.
    try:
        pincode_input = await find_pincode_input(page)
        return pincode_input is not None
    except Exception:
        return False


async def click_pincode_submit(page, pincode_input):
    """
    Amul's pincode popup has a magnifying-glass submit control
    beside the input.

    We try several methods because the site's HTML can change.
    """

    # --------------------------------------------------------
    # Attempt 1: buttons close to the input
    # --------------------------------------------------------

    try:
        parent = pincode_input.locator("xpath=..")

        buttons = parent.locator("button")

        if await buttons.count() > 0:
            for i in range(await buttons.count()):
                button = buttons.nth(i)

                try:
                    if await button.is_visible():
                        await button.click()
                        print("Submitted pincode using nearby button.")
                        return True
                except Exception:
                    pass

    except Exception:
        pass

    # --------------------------------------------------------
    # Attempt 2: clickable elements in the input container
    # --------------------------------------------------------

    try:
        parent = pincode_input.locator("xpath=..")

        candidates = parent.locator(
            "button, [role='button'], svg, "
            "[onclick], a"
        )

        if await candidates.count() > 0:
            for i in range(await candidates.count()):
                candidate = candidates.nth(i)

                try:
                    if await candidate.is_visible():
                        await candidate.click(force=True)
                        print(
                            "Submitted pincode using nearby "
                            "clickable element."
                        )
                        return True
                except Exception:
                    pass

    except Exception:
        pass

    # --------------------------------------------------------
    # Attempt 3: click the right side of the input
    #
    # This matches the magnifying-glass UI shown by Amul.
    # --------------------------------------------------------

    try:
        box = await pincode_input.bounding_box()

        if box:
            x = box["x"] + box["width"] - 18
            y = box["y"] + box["height"] / 2

            await page.mouse.click(x, y)

            print(
                "Submitted pincode by clicking the "
                "right side of the pincode field."
            )

            return True

    except Exception as e:
        print(f"Could not click pincode submit area: {e}")

    return False


async def apply_pincode(page):
    print("Looking for delivery pincode dialog...")

    # Give the popup a moment to appear.
    try:
        await page.get_by_text(
            "Select Delivery Pincode",
            exact=True
        ).first.wait_for(
            state="visible",
            timeout=10000
        )
    except Exception:
        pass

    visible = await pincode_modal_visible(page)

    print(f"Pincode dialog visible: {visible}")

    if not visible:
        print("Pincode dialog is not visible.")
        return True

    pincode_input = await find_pincode_input(page)

    if pincode_input is None:
        print("Could not find pincode input.")
        return False

    print("Pincode input found.")

    try:
        await pincode_input.fill(PINCODE)

        print(f"Entered pincode: {PINCODE}")

    except Exception as e:
        print(f"Could not enter pincode: {e}")
        return False

    # First try Enter.
    try:
        await pincode_input.press("Enter")
        print("Pressed Enter on pincode field.")
    except Exception:
        pass

    # Give Amul time to process it.
    await page.wait_for_timeout(2500)

    # IMPORTANT:
    # The old code incorrectly treated the pincode as applied
    # just because the text remained in the field.
    #
    # We now require the popup to actually disappear.
    visible = await pincode_modal_visible(page)

    print(
        f"Pincode dialog visible after Enter: {visible}"
    )

    if not visible:
        print("Pincode applied successfully.")
        return True

    # Enter didn't close it, so click the magnifying-glass /
    # submit control.
    print(
        "Pincode dialog is still open. "
        "Clicking the submit/search control..."
    )

    await click_pincode_submit(page, pincode_input)

    # Wait for Amul to process the pincode.
    await page.wait_for_timeout(4000)

    visible = await pincode_modal_visible(page)

    print(
        f"Pincode dialog visible after clicking submit: {visible}"
    )

    if not visible:
        print("Pincode applied successfully.")
        return True

    # One final attempt: press Enter again.
    try:
        await pincode_input.press("Enter")
        await page.wait_for_timeout(3000)
    except Exception:
        pass

    visible = await pincode_modal_visible(page)

    print(
        f"Pincode dialog visible after final attempt: {visible}"
    )

    if not visible:
        print("Pincode applied successfully.")
        return True

    print(
        "ERROR: Amul's pincode dialog did not close."
    )

    return False


# ============================================================
# PRODUCT DETECTION
# ============================================================

def normalize_text(text):
    return re.sub(r"\s+", " ", text).strip().lower()


async def find_product(page):
    """
    Find the exact lassi product in the search results.

    We use several approaches because Amul's product-card HTML
    can change.
    """

    target = normalize_text(PRODUCT_NAME)

    # --------------------------------------------------------
    # Method 1: exact visible text
    # --------------------------------------------------------

    try:
        locator = page.get_by_text(
            PRODUCT_NAME,
            exact=True
        ).first

        if await locator.count() > 0 and await locator.is_visible():
            print("Exact product text found.")
            return locator

    except Exception:
        pass

    # --------------------------------------------------------
    # Method 2: partial text
    # --------------------------------------------------------

    try:
        locator = page.get_by_text(
            re.compile(
                r"Amul High Protein Plain Lassi.*Pack of 30",
                re.I
            )
        ).first

        if await locator.count() > 0 and await locator.is_visible():
            print("Product found using partial text.")
            return locator

    except Exception:
        pass

    # --------------------------------------------------------
    # Method 3: scan text-bearing elements
    # --------------------------------------------------------

    try:
        elements = page.locator(
            "h1, h2, h3, h4, h5, h6, "
            "a, div, span, p"
        )

        count = min(await elements.count(), 1000)

        for i in range(count):
            element = elements.nth(i)

            try:
                if not await element.is_visible():
                    continue

                text = normalize_text(
                    await element.inner_text()
                )

                if (
                    "amul high protein plain lassi" in text
                    and "pack of 30" in text
                ):
                    print(
                        "Product found by scanning page elements."
                    )
                    return element

            except Exception:
                continue

    except Exception:
        pass

    return None


async def get_product_card(product_element):
    """
    Walk upward from the product name and find a useful
    product-card/container containing price and stock text.
    """

    if product_element is None:
        return None

    try:
        return await product_element.evaluate_handle(
            """
            (el) => {
                let current = el;

                for (let i = 0; i < 8 && current; i++) {
                    const text =
                        (current.innerText || '').toLowerCase();

                    if (
                        text.includes('pack of 30') &&
                        (
                            text.includes('₹') ||
                            text.includes('sold out') ||
                            text.includes('notify me') ||
                            text.includes('add to cart') ||
                            text.includes('out of stock')
                        )
                    ) {
                        return current;
                    }

                    current = current.parentElement;
                }

                return el;
            }
            """
        )

    except Exception:
        return None


# ============================================================
# STOCK STATUS
# ============================================================

def extract_price(text):
    patterns = [
        r"₹\s*[\d,]+(?:\.\d+)?",
        r"Rs\.?\s*[\d,]+(?:\.\d+)?",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)

        if match:
            return match.group(0).strip()

    return "₹unknown"


def detect_status(text):
    lower = normalize_text(text)

    # Sold-out checks first.
    if (
        "sold out" in lower
        or "out of stock" in lower
        or "notify me" in lower
    ):
        return "OUT_OF_STOCK"

    # Positive availability checks.
    if (
        "add to cart" in lower
        or "buy now" in lower
        or "in stock" in lower
    ):
        return "IN_STOCK"

    return "UNKNOWN"


# ============================================================
# STOCK CHECK
# ============================================================

async def check_stock():
    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True
        )

        context = await browser.new_context(
            viewport={
                "width": 1365,
                "height": 900,
            },
            locale="en-IN",
        )

        page = await context.new_page()

        try:
            print(f"Opening search page: {SEARCH_URL}")

            await page.goto(
                SEARCH_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            print(f"Initial URL: {page.url}")

            # Let the site finish rendering.
            await page.wait_for_timeout(3000)

            # ------------------------------------------------
            # Apply delivery pincode.
            # ------------------------------------------------

            pincode_applied = await apply_pincode(page)

            print(
                f"Pincode applied: {pincode_applied}"
            )

            if not pincode_applied:
                return {
                    "status": "UNKNOWN",
                    "price": "₹unknown",
                    "reason": "Could not apply delivery pincode.",
                }

            print(f"URL after pincode: {page.url}")

            # ------------------------------------------------
            # Wait for search results.
            # ------------------------------------------------

            await page.wait_for_timeout(3000)

            # ------------------------------------------------
            # Find product.
            # ------------------------------------------------

            print("Looking for exact lassi product...")

            product = await find_product(page)

            if product is None:

                # Save useful diagnostic information in logs.
                body_text = await page.locator(
                    "body"
                ).inner_text()

                print(
                    f"Page text length: {len(body_text)} characters"
                )

                print(
                    "Matching product elements found: 0"
                )

                return {
                    "status": "UNKNOWN",
                    "price": "₹unknown",
                    "reason": (
                        "Exact Amul lassi product could not "
                        "be found in search results."
                    ),
                }

            print("Product found.")

            # ------------------------------------------------
            # Get surrounding product card.
            # ------------------------------------------------

            card = await get_product_card(product)

            if card is not None:
                try:
                    card_text = await card.evaluate(
                        "(el) => el.innerText || ''"
                    )
                except Exception:
                    card_text = await product.inner_text()
            else:
                card_text = await product.inner_text()

            # If card text is too small, use page text as a
            # fallback.
            if len(card_text.strip()) < 30:
                card_text = await page.locator(
                    "body"
                ).inner_text()

            print("Product information:")
            print(card_text[:1500])

            # ------------------------------------------------
            # Extract price and status.
            # ------------------------------------------------

            price = extract_price(card_text)
            status = detect_status(card_text)

            return {
                "status": status,
                "price": price,
                "reason": "Product found and checked.",
            }

        except Exception as e:

            print(
                f"Stock check failed with exception: {e}"
            )

            return {
                "status": "UNKNOWN",
                "price": "₹unknown",
                "reason": f"Exception: {e}",
            }

        finally:
            await browser.close()


# ============================================================
# MAIN
# ============================================================

async def main():

    if len(sys.argv) < 2 or sys.argv[1].lower() != "check":
        print(
            "Usage: python amul_stock_watcher.py check"
        )
        return

    state = load_state()

    previous_status = state.get("status")

    print(
        f"Previous status: {previous_status}"
    )

    result = await check_stock()

    status = result["status"]
    price = result["price"]
    reason = result["reason"]

    print("----------------------------------------")
    print(f"Product: {PRODUCT_NAME}")
    print(f"Pincode: {PINCODE}")
    print(f"Price: {price}")
    print(f"Status: {status}")
    print(f"Reason: {reason}")
    print(f"Previous status: {previous_status}")
    print("----------------------------------------")

    # --------------------------------------------------------
    # Alert only when the product becomes available.
    # --------------------------------------------------------

    if (
        status == "IN_STOCK"
        and previous_status != "IN_STOCK"
    ):

        message = (
            "🚨 AMUL STOCK ALERT 🚨\n\n"
            f"{PRODUCT_NAME}\n\n"
            f"Price: {price}\n"
            f"Status: IN STOCK ✅\n"
            f"Delivery Pincode: {PINCODE}\n\n"
            "Check Amul now."
        )

        send_telegram(message)

    else:
        print("No Telegram alert needed.")

    # --------------------------------------------------------
    # Save current state.
    # --------------------------------------------------------

    state = {
        "status": status,
        "price": price,
        "product": PRODUCT_NAME,
        "pincode": PINCODE,
        "reason": reason,
    }

    save_state(state)

    print("State saved.")


if __name__ == "__main__":
    asyncio.run(main())
