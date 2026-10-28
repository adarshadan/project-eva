"""The person's own browser, through the Eva Browser Bridge extension (browser_link.py).
Declared once each; see core/registry.py. Each click and keystroke is judged inside the tool by
the page's own labels (money and can't-be-undone steps ask); page text taints the turn."""
import browser_link as bl
from core.effects import Effect as E, Surface as S
from core.registry import BOOL, INT, STR, register

_TAB = INT("Optional tab_id from page_tabs; default: the tab in front.")
_BROWSER = STR("Optional: chrome, edge, brave or opera when more than one has the extension.")

register("page_tabs", "The tabs open in the person's OWN browser (needs the Eva extension). Use it first when "
         "they talk about a page they have open.",
         {"browser": _BROWSER}, [],
         surface=S.BROWSER, effect=E.OBSERVE, fn=bl.page_tabs, verb="read", web=True,
         describe=lambda a: "Looking at your tabs")
register("page_activate", "Bring one of the person's tabs to the front (tab_id from page_tabs).",
         {"tab_id": INT(), "browser": _BROWSER}, ["tab_id"],
         surface=S.BROWSER, effect=E.INTERACT, fn=bl.page_activate, verb="work",
         describe=lambda a: "Switching tab")
register("page_go", "Open a web address in the person's OWN browser (signed in as them), in a new tab unless "
         "new_tab=false. Prefer this over navigate_browser when the extension is connected.",
         {"url": STR(), "new_tab": BOOL(), "browser": _BROWSER}, ["url"],
         surface=S.BROWSER, effect=E.INTERACT, fn=bl.page_go, verb="work",
         describe=lambda a: "Opening a page")
register("page_elements", "The clickable and typeable elements of the person's page, with their real labels and "
         "ids for page_click / page_type; in view first. contains='words' lists only matching ones.",
         {"tab_id": _TAB, "contains": STR("Optional words the element's label must contain."), "browser": _BROWSER},
         [], surface=S.BROWSER, effect=E.OBSERVE, fn=bl.page_elements, verb="read", web=True,
         describe=lambda a: "Reading the page")
register("page_read", "The text of the person's page as they see it, with its title and address.",
         {"tab_id": _TAB, "browser": _BROWSER}, [],
         surface=S.BROWSER, effect=E.OBSERVE, fn=bl.page_read, verb="read", web=True,
         describe=lambda a: "Reading the page")
register("page_click", "Click an element of the person's page by its id from page_elements. Asks first when it "
         "spends money or cannot be undone.",
         {"element_id": STR(), "tab_id": _TAB, "browser": _BROWSER}, ["element_id"],
         surface=S.BROWSER, effect=E.INTERACT, fn=bl.page_click, verb="work",
         describe=lambda a: "Clicking on the page")
register("page_type", "Type into a field of the person's page by its id from page_elements (mode replace or "
         "append; submit=true presses Enter / submits the form). Never a password field.",
         {"element_id": STR(), "text": STR(), "submit": BOOL(), "mode": STR(enum=["replace", "append"]),
          "tab_id": _TAB, "browser": _BROWSER}, ["element_id", "text"],
         surface=S.BROWSER, effect=E.INTERACT, fn=bl.page_type, verb="work",
         describe=lambda a: "Typing on the page")
register("page_scroll", "Scroll the person's page: down, up, top or bottom.",
         {"direction": STR(enum=["down", "up", "top", "bottom"]), "tab_id": _TAB, "browser": _BROWSER}, [],
         surface=S.BROWSER, effect=E.INTERACT, fn=bl.page_scroll, verb="work",
         describe=lambda a: "Scrolling")
