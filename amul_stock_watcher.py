import asyncio
import json
import os
import re
import sys

import requests
from playwright.async_api import async_playwright


# ---------------- CONFIG ----------------

SEARCH_URL = "https://shop.amul.com/en/search/high%20protein"
PRODUCT_NAME = "Amul High Protein Plain Lassi, 200 mL | Pack of 30"
PINCODE = "110044"
STATE_FILE = "state.json"

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ---------------- STATE ----------------

def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


# ---------------- TELEGRAM ----------------

def send_telegram(message):
    if not BOT_TOKEN or not CHAT_ID:
        print("Telegram secrets missing.")
        return

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        data={"chat_id": CHAT_ID, "text": message},
        timeout=20,
    )

    if response.ok:
        print("Telegram alert sent.")
    else:
        print(f"Telegram error: {response.text[:300]}")


# ---------------- PINCODE ----------------

async def apply_pincode(page):
    print("Looking for delivery pincode dialog...")

    try:
        await page.get_by_text(
            "Select Delivery Pincode",
            exact=True
        ).wait_for(state="visible", timeout=10000)
    except Exception:
        print("Pincode dialog not found.")
        return True

    print("Pincode dialog visible: True")

    box = page.locator(
        'input[placeholder*="ENTER YOUR PINCODE" i]'
    ).first

    if await box.count() == 0:
        print("Pincode input not found.")
        return False

    await box.fill(PINCODE)
    print(f"Entered pincode: {PINCODE}")

    # Amul's search/magnifying-glass control is on
    # the right side of the pincode field.
    bounds = await box.bounding_box()

    if bounds:
        await page.mouse.click(
            bounds["x"] + bounds["width"] - 18,
            bounds["y"] + bounds["height"] / 2,
        )

    await page.wait_for_timeout(3000)

    # If still open, Enter is the fallback.
    try:
        title = page.get_by_text(
            "Select Delivery Pincode",
            exact=True
        ).first

        if await title.is_visible():
            await box.press("Enter")
            await page.wait_for_timeout(3000)

    except Exception:
        pass

    try:
        title = page.get_by_text(
            "Select Delivery Pincode",
            exact=True
        ).first

        visible = await title.is_visible()

    except Exception:
        visible = False

    print(f"Pincode dialog visible: {visible}")

    return not visible


# ---------------- STOCK CHECK ----------------

async def check_stock():
    async with async_playwright() as p:

        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page(
            viewport={"width": 1365, "height": 900},
            locale="en-IN",
        )

        try:
            print(f"Opening: {SEARCH_URL}")

            await page.goto(
                SEARCH_URL,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await page.wait_for_timeout(3000)

            if not await apply_pincode(page):
                return "UNKNOWN", "₹unknown"

            await page.wait_for_timeout(3000)

            # Make sure the exact product is present.
            product = page.get_by_text(
                PRODUCT_NAME,
                exact=True
            ).first

            if await product.count() == 0:
                print("Product not found.")
                return "UNKNOWN", "₹unknown"

            print("Product found.")

            # Get the product's surrounding card.
            card = product.locator("xpath=ancestor::div[1]")

            # Walk upward until we find the stock information.
            for _ in range(6):
                text = await card.inner_text()

                if any(
                    word in text.lower()
                    for word in [
                        "sold out",
                        "notify me",
                        "add to cart",
                        "out of stock",
                    ]
                ):
                    break

                card = card.locator("xpath=..")

            text = await card.inner_text()

            print("Product information:")
            print(text)

            # Price
            match = re.search(r"₹\s*[\d,]+", text)
            price = match.group(0) if match else "₹unknown"

            # Stock
            lower = text.lower()

            if (
                "sold out" in lower
                or "out of stock" in lower
                or "notify me" in lower
            ):
                status = "OUT_OF_STOCK"

            elif (
                "add to cart" in lower
                or "buy now" in lower
                or "in stock" in lower
            ):
                status = "IN_STOCK"

            else:
                status = "UNKNOWN"

            return status, price

        except Exception as e:
            print(f"Check failed: {e}")
            return "UNKNOWN", "₹unknown"

        finally:
            await browser.close()


# ---------------- MAIN ----------------

async def main():

    if len(sys.argv) < 2 or sys.argv[1] != "check":
        print("Usage: python amul_stock_watcher.py check")
        return

    previous = load_state()
    previous_status = previous.get("status")

    print(f"Previous status: {previous_status}")

    status, price = await check_stock()

    print("--------------------------------")
    print(f"Product: {PRODUCT_NAME}")
    print(f"Pincode: {PINCODE}")
    print(f"Price: {price}")
    print(f"Status: {status}")
    print("--------------------------------")

    # Alert only when it becomes available.
    if status == "IN_STOCK" and previous_status != "IN_STOCK":

        send_telegram(
            "🚨 AMUL STOCK ALERT 🚨\n\n"
            f"{PRODUCT_NAME}\n\n"
            f"Price: {price}\n"
            "Status: IN STOCK ✅\n"
            f"Delivery Pincode: {PINCODE}"
        )

    else:
        print("No Telegram alert needed.")

    save_state({
        "status": status,
        "price": price,
    })

    print("State saved.")


if __name__ == "__main__":
    asyncio.run(main())
