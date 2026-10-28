"""Register (or remove) Eva's browser bridge for this Windows user.

    install_browser_bridge.bat            register the native host for Chrome, Edge, Brave, Opera
    install_browser_bridge.bat --uninstall

What it does: writes browser_bridge\\com.eva.browser_bridge.json (the host manifest: which program
to start, and that ONLY the Eva extension may start it) and points each browser's per-user
registry key (HKCU, no administrator rights) at it. Then it tells you how to load the extension
once: open the browser's extensions page, turn on Developer mode, Load unpacked, choose
browser_bridge\\extension.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

HOST = "com.eva.browser_bridge"
MANIFEST = os.path.join(HERE, HOST + ".json")
# Opera and Vivaldi read Chrome's key.
KEYS = {"Chrome / Opera / Vivaldi": r"Software\Google\Chrome\NativeMessagingHosts\\" + HOST,
        "Edge": r"Software\Microsoft\Edge\NativeMessagingHosts\\" + HOST,
        "Brave": r"Software\BraveSoftware\Brave-Browser\NativeMessagingHosts\\" + HOST,
        "Chromium": r"Software\Chromium\NativeMessagingHosts\\" + HOST}
PAGES = {"Chrome": "chrome://extensions", "Edge": "edge://extensions", "Brave": "brave://extensions",
         "Opera": "opera://extensions"}


def host_manifest(ext_id, host_path):
    return {"name": HOST, "description": "Eva Browser Bridge", "path": host_path, "type": "stdio",
            "allowed_origins": [f"chrome-extension://{ext_id}/"]}


def install():
    from browser_link import EXTENSION_ID
    host_bat = os.path.join(HERE, "native_host.bat")
    with open(MANIFEST, "w", encoding="utf-8") as f:
        json.dump(host_manifest(EXTENSION_ID, host_bat), f, indent=2)
    import winreg
    for name, key in KEYS.items():
        k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, key.replace("\\\\", "\\"))
        winreg.SetValueEx(k, "", 0, winreg.REG_SZ, MANIFEST)
        winreg.CloseKey(k)
        print(f"  registered for {name}")
    print("\nNow load the extension once in the browser you use:")
    for b, page in PAGES.items():
        print(f"  {b:7s} open {page}")
    print("  turn on 'Developer mode', click 'Load unpacked' and choose this folder:")
    print(f"     {os.path.join(HERE, 'extension')}")
    print(f"\nThe extension's ID must read {EXTENSION_ID}. Eva connects to it whenever she is running.")
    try:
        os.startfile(os.path.join(HERE, "extension"))
    except Exception:
        pass


def uninstall():
    import winreg
    for name, key in KEYS.items():
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key.replace("\\\\", "\\"))
            print(f"  removed for {name}")
        except OSError:
            pass
    try:
        os.remove(MANIFEST)
    except OSError:
        pass
    print("Done. Remove the extension from the browser's extensions page too.")


if __name__ == "__main__":
    uninstall() if "--uninstall" in sys.argv else install()
