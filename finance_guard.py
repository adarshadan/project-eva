"""Eva -- recognising when an action involves money. Pure logic, no I/O.

The person's rule is: act freely, and ask only for destructive actions and
for decisions that spend money. "Spends money" cannot be read off a tool
name -- clicking a button is harmless on a search page and a purchase on a
checkout page -- so the framework looks at what is actually being pressed
and where. This module is that judgement, kept separate so it can be tested
against real-looking pages and so a false alarm or a miss has one place to
be fixed.

It leans toward asking. A needless prompt costs a click; a missed purchase
costs money.
"""
import re

_PAYMENT_URL = re.compile(
    r"(checkout|check-out|/pay\b|/pay/|payment|billing|order[-_/]?(confirm|review|summary|place)|"
    r"purchase|razorpay|paytm|phonepe|gpay|bhim|netbanking|stripe\.com|paypal\.com|3dsecure|"
    r"/cart/(pay|checkout))", re.I)
_PAYMENT_TITLE = re.compile(
    r"\b(checkout|check out|payment|pay now|billing|order summary|confirm (your )?order|"
    r"review (your )?order|purchase|secure pay)\b", re.I)

# Always about money, wherever they appear.
_MONEY_BUTTON = re.compile(
    r"\b(buy(\s+now)?|place\s+(your\s+)?order|pay(\s+now|\s+securely|\s+with)?|make\s+(a\s+)?payment|"
    r"proceed\s+to\s+pay(ment)?|complete\s+(the\s+)?(purchase|order|payment|booking)|"
    r"confirm\s+(and\s+pay|order|booking|purchase|payment)|book\s+now|purchase|check\s*out|"
    r"donate|send\s+money|transfer(\s+funds)?|withdraw|add\s+(a\s+)?(payment|card)|"
    r"pay\s*(in|by)\s+(cash|upi|card))\b", re.I)
# Only about money when the page is one (or the label carries a price).
_SOFT_MONEY_BUTTON = re.compile(
    r"\b(subscribe|upgrade(\s+now)?|start\s+(your\s+)?(free\s+)?trial|continue|confirm|submit|next)\b", re.I)
_PRICE = re.compile(r"[₹$€£]\s?\d|\b\d[\d,]*(\.\d{1,2})?\s?(rs\.?|inr|usd|eur)\b", re.I)

_PAYMENT_FIELD = re.compile(
    r"(card.?(number|no)|cc-?(num|number|csc|exp)|cardnumber|cvv|cvc|csc|security.?code|"
    r"expir(y|ation)|exp.?(month|year|date)|\bupi\b|\bvpa\b|iban|routing.?number|"
    r"account.?number|net.?banking)", re.I)

_TOTAL = re.compile(
    r"(?i)(grand\s+total|order\s+total|total\s+(?:amount|payable|price)?|amount\s+payable|"
    r"you\s+pay|to\s+pay|payable|subtotal)\D{0,24}?([₹$€£]|rs\.?\s*|inr\s*|usd\s*|eur\s*)?\s?"
    r"(\d[\d,]*(?:\.\d{1,2})?)")


def is_payment_context(url: str = "", title: str = "") -> bool:
    return bool(_PAYMENT_URL.search(url or "") or _PAYMENT_TITLE.search(title or ""))


def button_reason(label: str, payment_context: bool = False):
    """Why pressing a control labelled `label` involves money, or None."""
    text = " ".join((label or "").split())
    if not text:
        return None
    if _MONEY_BUTTON.search(text):
        return f"“{text[:50]}” looks like a purchase or payment"
    if _PRICE.search(text) and re.search(r"\b(pay|buy|book|order|confirm|purchase|checkout)\b", text, re.I):
        return f"“{text[:50]}” carries a price"
    if payment_context and _SOFT_MONEY_BUTTON.search(text):
        return f"“{text[:50]}” submits a payment page"
    return None


def payment_field_reason(attrs: str):
    """attrs: the element's name/id/autocomplete/placeholder/aria text, joined."""
    if attrs and _PAYMENT_FIELD.search(attrs):
        return "it is a payment or bank-detail field"
    return None


def looks_like_card_number(text: str) -> bool:
    digits = re.sub(r"[\s\-]", "", str(text or ""))
    if not digits.isdigit() or not 13 <= len(digits) <= 19:
        return False
    if len(set(digits)) < 2:            # 0000000000000 is Luhn-valid and not a card
        return False
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d *= 2
            d -= 9 if d > 9 else 0
        total += d
        alt = not alt
    return total % 10 == 0


def extract_total(page_text: str):
    """A best-effort 'what am I about to pay' for the approval dialog."""
    best = None
    for m in _TOTAL.finditer(page_text or ""):
        symbol = (m.group(2) or "").strip()
        if symbol.lower().startswith("rs") or symbol.lower() == "inr":
            symbol = "₹"
        amount = m.group(3)
        if len(amount.replace(",", "")) >= 1:
            best = f"{symbol}{amount}"
            if "total" in m.group(1).lower() or "pay" in m.group(1).lower():
                return best
    return best


def browser_step_reason(action: str, element: dict, url: str = "", title: str = "", typed: str = None):
    """Does this single browser step spend money? Returns the reason, or None."""
    payment_context = is_payment_context(url, title)
    el = element or {}
    if action == "click":
        reason = button_reason(el.get("text", ""), payment_context)
        if reason:
            return reason
        if payment_context and el.get("type") == "submit":
            return "it submits a form on a payment page"
    elif action == "type":
        reason = payment_field_reason(el.get("attrs", ""))
        if reason:
            return f"typing into it is risky: {reason}"
        if typed and looks_like_card_number(typed):
            return "the text looks like a card number"
    elif action == "press":
        if payment_context and str(typed or "").lower() in ("enter", "return"):
            return "pressing Enter on a payment page can submit it"
    return None


def native_reason(kind: str, target_text: str = "", window_title: str = "", typed: str = None):
    """The same judgement for a native desktop app."""
    payment_context = is_payment_context("", window_title)
    if kind == "click":
        return button_reason(target_text, payment_context)
    if kind == "click_blind":                      # coordinates: no label to read
        return "it is a click inside a payment window" if payment_context else None
    if kind == "type" and typed and looks_like_card_number(typed):
        return "the text looks like a card number"
    if kind == "enter" and payment_context:
        return "pressing Enter in a payment window can submit it"
    return None
