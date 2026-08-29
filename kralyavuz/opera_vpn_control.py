import sys
import time

from .btk_operator import verify_opera_vpn


def _find_private_opera_window():
    from pywinauto import Desktop

    desktop = Desktop(backend="uia")

    for window in desktop.windows():
        try:
            if window.element_info.class_name != "Chrome_WidgetWin_1":
                continue

            descendants = window.descendants()

            has_vpn = any(
                x.element_info.control_type == "Button"
                and x.window_text() == "VPN"
                for x in descendants
            )
            has_private_badge = any(
                x.element_info.control_type == "Button"
                and x.window_text() == "Özel"
                for x in descendants
            )

            if has_vpn and has_private_badge:
                return window
        except Exception:
            continue

    raise RuntimeError("Opera GX Private Window bulunamadı.")


def _open_vpn_popup(root):
    popup = next(
        (
            x
            for x in root.descendants()
            if x.element_info.control_type == "Window"
            and x.window_text() == "VPN"
        ),
        None,
    )

    if popup is not None:
        return popup

    vpn_button = next(
        (
            x
            for x in root.descendants()
            if x.element_info.control_type == "Button"
            and x.window_text() == "VPN"
        ),
        None,
    )

    if vpn_button is None:
        raise RuntimeError("Opera GX VPN butonu bulunamadı.")

    vpn_button.invoke()
    time.sleep(0.8)

    popup = next(
        (
            x
            for x in root.descendants()
            if x.element_info.control_type == "Window"
            and x.window_text() == "VPN"
        ),
        None,
    )

    if popup is None:
        raise RuntimeError("Opera GX VPN penceresi açılamadı.")

    return popup


def _activate_vpn(root) -> None:
    popup = _open_vpn_popup(root)

    deadline = time.monotonic() + 4.0
    status = None

    while time.monotonic() < deadline:
        for element in popup.descendants():
            text = element.window_text()

            if text in {"KORUNMASIZ", "KORUNUYOR", "BAĞLANIYOR"}:
                status = element
                break

        if status is not None:
            break

        time.sleep(0.2)

    if status is None:
        raise RuntimeError("Opera GX VPN durumu okunamadı.")

    if status.window_text() in {"KORUNUYOR", "BAĞLANIYOR"}:
        return

    status_rect = status.rectangle()
    candidates = []

    for element in popup.descendants():
        try:
            if element.element_info.control_type != "Group":
                continue

            if element.window_text():
                continue

            element.iface_invoke
            rect = element.rectangle()

            if (
                rect.bottom <= status_rect.top
                and rect.width() > 150
                and rect.height() > 80
            ):
                candidates.append(
                    (status_rect.top - rect.bottom, element)
                )
        except Exception:
            continue

    if not candidates:
        raise RuntimeError("Opera GX VPN açma kontrolü bulunamadı.")

    control = min(candidates, key=lambda item: item[0])[1]
    control.invoke()


def _close_vpn_popup(root) -> None:
    try:
        popup_open = any(
            x.element_info.control_type == "Window"
            and x.window_text() == "VPN"
            for x in root.descendants()
        )

        if not popup_open:
            return

        vpn_button = next(
            x
            for x in root.descendants()
            if x.element_info.control_type == "Button"
            and x.window_text() == "VPN"
        )
        vpn_button.invoke()
    except Exception:
        pass


def ensure_opera_vpn(page, timeout: float = 25.0) -> str:
    """
    Opera VPN'i gerçekten bağlı hale getirir.

    VPN zaten aktifse hiçbir UI kontrolüne dokunmaz.
    Windows'ta Opera GX Private Window içindeki gerçek VPN
    kontrolünü UI Automation ile çalıştırır.
    """

    try:
        return verify_opera_vpn(page)
    except Exception:
        pass

    if sys.platform != "win32":
        raise RuntimeError(
            "Opera GX VPN otomatik bağlantısı yalnızca Windows'ta destekleniyor."
        )

    root = _find_private_opera_window()
    _activate_vpn(root)

    deadline = time.monotonic() + timeout
    last_error = None

    while time.monotonic() < deadline:
        try:
            ip = verify_opera_vpn(page)
            _close_vpn_popup(root)
            return ip
        except Exception as exc:
            last_error = exc
            time.sleep(1.0)

    _close_vpn_popup(root)

    raise RuntimeError(
        f"Opera GX VPN bağlanamadı: {last_error}"
    )