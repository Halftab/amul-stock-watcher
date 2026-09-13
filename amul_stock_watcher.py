import asyncio
import json
import os
import re
from datetime import datetime, timezone

import requests
from playwright.async_api import async_playwright


# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------

SEARCH_URL = (
    "https://shop.amul.com/en/search/high%20protein"
)

PRODUCT_NAME = (
    "Amul High Protein Plain Lassi, 200 mL | Pack of 30"
)

PINCODE = "110044"

STATE_FILE = "state.json"

TELEGRAM_BOT_TOKEN = os.environ.get(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = os.environ.get(
    "TELEGRAM_CHAT_ID"
)

PRODUCT_URL = (
    "https://shop.amul.com/product/"
    "amul-high-protein-plain-lassi-200-ml-or-pack-of-30"
)


# ---------------------------------------------------------
# STATE
# ---------------------------------------------------------

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"status": None}

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8",
        ) as f:
            return json.load(f)

    except Exception:
        return {"status": None}


def save_state(status):
    with open(
        STATE_FILE,
        "w",
        encoding="utf-8",
    ) as f:
        json.dump(
            {
                "status": status,
                "checked_at": (
                    datetime.now(
                        timezone.utc
                    ).isoformat()
                ),
            },
            f,
            indent=2,
        )


# ---------------------------------------------------------
# TELEGRAM
# ---------------------------------------------------------

def send_telegram(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN and "
            "TELEGRAM_CHAT_ID must be configured."
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


# ---------------------------------------------------------
# PINCODE MODAL
# ---------------------------------------------------------

async def apply_pincode(page):

    print("Looking for delivery pincode dialog...")

    try:

        # Wait briefly for the modal to appear.
        await page.wait_for_timeout(2000)

        # Look for the modal.
        modal_text = page.get_by_text(
            "Select Delivery Pincode",
            exact=True,
        ).first

        modal_visible = False

        try:
            modal_visible = await modal_text.is_visible()
        except Exception:
            pass

        print(
            f"Pincode dialog visible: "
            f"{modal_visible}"
        )

        # Find the actual pincode input.
        pincode_input = page.locator(
            'input[placeholder*="ENTER YOUR PINCODE" i]'
        ).first

        if await pincode_input.count() == 0:

            pincode_input = page.locator(
                'input[placeholder*="Pincode" i]'
            ).first

        if await pincode_input.count() == 0:

            print(
                "Could not find the pincode input."
            )

            return False

        print(
            "Pincode input found."
        )

        # Fill pincode.
        await pincode_input.fill(PINCODE)

        print(
            f"Entered pincode: {PINCODE}"
        )

        # Submit using Enter.
        await pincode_input.press("Enter")

        print(
            "Submitted pincode with Enter."
        )

        # Give Amul time to process it.
        await page.wait_for_timeout(4000)

        # -------------------------------------------------
        # Determine whether the dialog disappeared.
        # -------------------------------------------------

        try:

            still_visible = await modal_text.is_visible()

            print(
                f"Pincode dialog still visible: "
                f"{still_visible}"
            )

            if not still_visible:
                print(
                    "Pincode appears to have been accepted."
                )

                return True

        except Exception:
            pass

        # -------------------------------------------------
        # Sometimes the modal stays in the DOM but the
        # input gets cleared/re-rendered.
        # Check whether the input still contains our value.
        # -------------------------------------------------

        try:

            current_value = (
                await pincode_input.input_value()
            )

            print(
                f"Pincode input after submission: "
                f"{current_value}"
            )

            if PINCODE in current_value:
                print(
                    "Pincode value was retained."
                )

                return True

        except Exception:
            pass

        print(
            "Could not confirm pincode acceptance."
        )

        return False

    except Exception as exc:

        print(
            f"Pincode handling error: {exc}"
        )

        return False


# ---------------------------------------------------------
# FIND PRODUCT CARD
# ---------------------------------------------------------

async def find_product_card(page):

    print(
        "Looking for exact lassi product..."
    )

    # First try exact text.
    product_locator = page.get_by_text(
        PRODUCT_NAME,
        exact=True,
    ).first

    if await product_locator.count() == 0:

        # Fallback to partial product name.
        product_locator = page.get_by_text(
            re.compile(
                r"Amul\s+High\s+Protein\s+Plain\s+Lassi",
                re.IGNORECASE,
            )
        ).first

    count = await product_locator.count()

    print(
        f"Matching product elements found: {count}"
    )

    if count == 0:
        return None

    try:

        # Walk upward through the DOM until we find the
        # product-card container.
        card_text = await product_locator.evaluate(
            """
            (el) => {

                let current = el;

                for (let i = 0; i < 10; i++) {

                    if (!current) {
                        break;
                    }

                    const text =
                        current.innerText || "";

                    const lower =
                        text.toLowerCase();

                    const hasProduct =
                        lower.includes(
                            "amul high protein plain lassi"
                        );

                    const hasPack =
                        lower.includes(
                            "pack of 30"
                        );

                    const hasPrice =
                        lower.includes(
                            "mrp"
                        );

                    const hasAction =
                        lower.includes(
                            "add"
                        )
                        ||
                        lower.includes(
                            "notify me"
                        )
                        ||
                        lower.includes(
                            "sold out"
                        );

                    if (
                        hasProduct
                        &&
                        hasPack
                        &&
                        hasPrice
                        &&
                        hasAction
                        &&
                        text.length < 5000
                    ) {
                        return text;
                    }

                    current =
                        current.parentElement;
                }

                return el.innerText || "";
            }
            """
        )

        return card_text

    except Exception as exc:

        print(
            f"Could not extract product card: "
            f"{exc}"
        )

        return None


# ---------------------------------------------------------
# STOCK CHECK
# ---------------------------------------------------------

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

            print(
                f"Opening search page: {SEARCH_URL}"
            )

            await page.goto(
                SEARCH_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            # Allow storefront JavaScript to render.
            await page.wait_for_timeout(7000)

            print(
                f"Initial URL: {page.url}"
            )

            # -------------------------------------------------
            # Apply delivery pincode.
            # -------------------------------------------------

            pincode_applied = (
                await apply_pincode(page)
            )

            # Allow results to refresh after pincode.
            await page.wait_for_timeout(4000)

            print(
                f"Pincode applied: "
                f"{pincode_applied}"
            )

            print(
                f"URL after pincode: "
                f"{page.url}"
            )

            # -------------------------------------------------
            # Read whole page for diagnostics.
            # -------------------------------------------------

            body_text = await page.locator(
                "body"
            ).inner_text()

            print(
                f"Page text length: "
                f"{len(body_text)} characters"
            )

            # -------------------------------------------------
            # Find exact product card.
            # -------------------------------------------------

            card_text = (
                await find_product_card(page)
            )

            if not card_text:

                return {
                    "status": "UNKNOWN",
                    "price": None,
                    "pincode_applied": (
                        pincode_applied
                    ),
                    "reason": (
                        "Exact Amul lassi product "
                        "could not be found in "
                        "search results."
                    ),
                }

            # Normalize whitespace.
            card_text = re.sub(
                r"\s+",
                " ",
                card_text.replace(
                    "\xa0",
                    " ",
                ),
            ).strip()

            card_lower = card_text.lower()

            print(
                "Product card:"
            )

            print(
                card_text[:1500]
            )

            # -------------------------------------------------
            # Extract price.
            # -------------------------------------------------

            price = None

            price_patterns = [
                r"MRP\s*[:\-]?\s*₹\s*([\d,]+)",
                r"₹\s*([\d,]+)",
            ]

            for pattern in price_patterns:

                match = re.search(
                    pattern,
                    card_text,
                    re.IGNORECASE,
                )

                if match:

                    price = int(
                        match.group(1)
                        .replace(",", "")
                    )

                    break

            # -------------------------------------------------
            # Detect stock status.
            # -------------------------------------------------

            sold_out = (
                "sold out" in card_lower
            )

            out_of_stock = (
                "out of stock" in card_lower
            )

            notify_me = (
                "notify me" in card_lower
            )

            add_button = (
                re.search(
                    r"\badd\b",
                    card_lower,
                )
                is not None
            )

            print(
                f"Sold Out: {sold_out}"
            )

            print(
                f"Out of Stock: {out_of_stock}"
            )

            print(
                f"Notify Me: {notify_me}"
            )

            print(
                f"ADD detected: {add_button}"
            )

            # -------------------------------------------------
            # OUT OF STOCK
            # -------------------------------------------------

            if (
                sold_out
                or out_of_stock
                or notify_me
            ):

                return {
                    "status": "OUT_OF_STOCK",
                    "price": price,
                    "pincode_applied": (
                        pincode_applied
                    ),
                    "reason": (
                        "Product card shows "
                        "Sold Out / Notify Me."
                    ),
                }

            # -------------------------------------------------
            # IN STOCK
            # -------------------------------------------------

            if add_button:

                return {
                    "status": "IN_STOCK",
                    "price": price,
                    "pincode_applied": (
                        pincode_applied
                    ),
                    "reason": (
                        "Product card shows "
                        "ADD."
                    ),
                }

            # -------------------------------------------------
            # UNKNOWN
            # -------------------------------------------------

            return {
                "status": "UNKNOWN",
                "price": price,
                "pincode_applied": (
                    pincode_applied
                ),
                "reason": (
                    "Product was found, but "
                    "availability could not "
                    "be determined."
                ),
            }

        finally:

            await browser.close()


# ---------------------------------------------------------
# MAIN
# ---------------------------------------------------------

async def main():

    print("=" * 60)
    print("Amul Stock Watcher")
    print("=" * 60)

    result = await check_stock()

    status = result["status"]
    price = result["price"]
    pincode_applied = result[
        "pincode_applied"
    ]
    reason = result["reason"]

    print(
        f"Product: {PRODUCT_NAME}"
    )

    print(
        f"Pincode: {PINCODE}"
    )

    print(
        f"Pincode applied: "
        f"{pincode_applied}"
    )

    print(
        f"Price: ₹"
        f"{price if price is not None else 'unknown'}"
    )

    print(
        f"Status: {status}"
    )

    print(
        f"Reason: {reason}"
    )

    # -----------------------------------------------------
    # Previous state
    # -----------------------------------------------------

    previous_state = load_state()

    previous_status = (
        previous_state.get("status")
    )

    print(
        f"Previous status: "
        f"{previous_status}"
    )

    save_state(status)

    # -----------------------------------------------------
    # Telegram alert ONLY when becoming available.
    # -----------------------------------------------------

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
            f"Price: ₹"
            f"{price if price is not None else 'Unknown'}\n"
            f"Delivery pincode: {PINCODE}\n\n"
            f"🛒 Buy now:\n"
            f"{PRODUCT_URL}"
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
