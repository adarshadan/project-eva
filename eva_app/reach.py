"""Reachable from anywhere (ubiquity.py): clicking her, the chat box, the global shortcuts,
asking about part of the screen, and the instant file index."""
import config
import logger

log = logger.get_logger("__main__")


def install(ctx):
    import idle_behavior
    import ubiquity
    from PySide6.QtCore import QPoint as _QPoint
    from PySide6.QtGui import QCursor as _QCursor
    from avatar_ui import ClickForwarder, RegionPicker
    app, avatar, bridge, menu, runtime, show_text = (ctx.app, ctx.avatar, ctx.bridge, ctx.menu, ctx.runtime,
                                                     ctx.show_text)

    # ---- menus and tray ---------------------------------------------------------------
    def on_avatar_click():
        menu.toggle_around(avatar)          # the ring opens around her; clicking her again closes it
    avatar.click_forwarder = ClickForwarder(avatar, bridge.fire, on_avatar_click)
    # ---- reachable from anywhere (ubiquity.py) ----------------------------------------
    ubiquity.CLIPBOARD.start()
    # Instant file search over the person's own folders (file_index.py).
    import file_index
    try:
        file_index.INDEX = file_index.FileIndex().start()
    except Exception:
        log.exception("File index unavailable -- search_files still works")

    def on_chat_message(text):
        ctx_ = avatar.chat_popup.take_context()
        if ctx_ and ctx_[0] == "selection":
            text = ubiquity.with_context(text, ctx_[1])
        elif ctx_ and ctx_[0] == "region":
            text = ubiquity.with_region(text, ctx_[1])
        runtime.submit(text, "chat")
    avatar.chat_popup.message_submitted.connect(on_chat_message)

    def open_chat_here(selection=""):
        if selection:
            preview = " ".join(selection.split())
            preview = preview[:70] + ("..." if len(preview) > 70 else "")
            avatar.chat_popup.set_context("selection", selection,
                                          f"With the text you selected ({len(selection)} characters): \"{preview}\"")
        else:
            avatar.chat_popup.set_context(None)
        pos = _QCursor.pos()
        avatar.look_toward(pos.x(), pos.y(), 2500)          # she turns to you
        avatar.chat_popup.open_at(pos + _QPoint(14, 14))

    def on_region(rect):
        left, top, right, bottom = rect
        cx, cy = (left + right) // 2, (top + bottom) // 2
        # She looks at what you marked and points at it, then asks what you want.
        avatar.look_toward(cx, cy, 4000)
        body = avatar.x() + avatar.width() // 2
        avatar.play("p_point_l" if cx > body else "p_point_r", loop=False)
        avatar.chat_popup.set_context("region", rect, f"About the area you marked ({right - left} x {bottom - top} px)")
        avatar.chat_popup.open_at(_QPoint(left, bottom + 8))
    region_picker = RegionPicker(on_region)

    def hotkey_chat():
        selection = ubiquity.capture_now()           # before focus leaves the app in front
        bridge.fire(lambda: open_chat_here(selection))

    def hotkey_region():
        bridge.fire(region_picker.start)

    app._eva_hotkeys = ubiquity.start_hotkeys({config.HOTKEY_CHAT: hotkey_chat,
                                               config.HOTKEY_REGION: hotkey_region,
                                               getattr(config, "HOTKEY_STOP", ""): lambda: runtime.stop()})
    app._eva_region_picker = region_picker

    def action_scan():
        show_text(idle_behavior.check_for_suggestion() or "Nothing worth flagging right now.", 5000)

    ctx.region_picker, ctx.action_scan, ctx.file_index = region_picker, action_scan, file_index
