import base64
import io
import time
from pathlib import Path
from typing import Optional

from PIL import Image
from playwright.sync_api import Page


def wait_for_stable_result(page: Page, selector: str, timeout_seconds: float = 20.0) -> None:
    page.wait_for_function("document.readyState === 'complete'", timeout=15_000)
    page.wait_for_function(
        "!document.fonts || document.fonts.status === 'loaded'", timeout=15_000
    )
    page.wait_for_function(
        "Array.from(document.images).every(image => image.complete)", timeout=15_000
    )
    page.locator(selector).wait_for(state="attached", timeout=15_000)

    deadline = time.monotonic() + timeout_seconds
    stable_since: Optional[float] = None
    previous = None
    while time.monotonic() < deadline:
        snapshot = page.evaluate(
            """
            selector => {
                const element = document.querySelector(selector);
                if (!element) return null;
                const rect = element.getBoundingClientRect();
                const style = getComputedStyle(element);
                return [
                    Math.round(rect.x), Math.round(rect.y),
                    Math.round(rect.width), Math.round(rect.height),
                    element.innerHTML,
                    element.innerText.trim().length,
                    style.display, style.visibility,
                    document.documentElement.scrollWidth,
                    document.documentElement.scrollHeight,
                ];
            }
            """,
            selector,
        )
        now = time.monotonic()
        if (
            snapshot
            and snapshot[2] > 0
            and snapshot[3] > 0
            and snapshot[5] > 0
            and snapshot[6] != "none"
            and snapshot[7] not in {"hidden", "collapse"}
        ):
            if snapshot == previous:
                stable_since = stable_since or now
                if now - stable_since >= 2.5:
                    return
            else:
                previous = snapshot
                stable_since = now
        else:
            stable_since = None
        time.sleep(0.25)
    raise RuntimeError("Sonuç alanı stabil render durumuna gelmedi.")


def capture_result_element(
    page: Page,
    selector: str,
    output: Path,
    crop_before_text: Optional[str] = None,
    max_width: int = 1280,
) -> Path:
    wait_for_stable_result(page, selector)
    clip = page.evaluate(
        """
        ({selector, cropBeforeText}) => {
            const root = document.querySelector(selector);
            if (!root) throw new Error(`Sonuç elementi bulunamadı: ${selector}`);
            const rootRect = root.getBoundingClientRect();
            let bottom = rootRect.bottom;
            if (cropBeforeText) {
                const candidates = Array.from(root.querySelectorAll('*')).filter(
                    element => element.innerText?.includes(cropBeforeText)
                );
                candidates.sort((a, b) =>
                    a.getBoundingClientRect().height - b.getBoundingClientRect().height
                );
                if (candidates.length) {
                    bottom = Math.min(bottom, candidates[0].getBoundingClientRect().top);
                }
            }
            const height = Math.max(1, bottom - rootRect.top);
            return {
                x: rootRect.left + window.scrollX,
                y: rootRect.top + window.scrollY,
                width: rootRect.width,
                height,
                scale: 1,
            };
        }
        """,
        {"selector": selector, "cropBeforeText": crop_before_text},
    )
    return _save_cdp_clip(page, clip, output, max_width)


def capture_btk_result(page: Page, output: Path, max_width: int = 1280) -> Path:
    wait_for_stable_result(page, "#sonuc")
    clip = page.evaluate(
        """
        () => {
            const root = document.querySelector('#sonuc');
            if (!root) throw new Error('BTK sonuç elementi bulunamadı: #sonuc');
            const rootRect = root.getBoundingClientRect();

            const siteContainer = document.querySelector('#sorgu_sonuc');
            const decisionContainer = document.querySelector('#sorgu_mahkeme');
            if (siteContainer && decisionContainer) {
                const siteRect = siteContainer.getBoundingClientRect();
                const decisionRect = decisionContainer.getBoundingClientRect();
                const left = Math.min(siteRect.left, decisionRect.left);
                const top = Math.min(siteRect.top, decisionRect.top);
                const right = Math.max(siteRect.right, decisionRect.right);
                const bottom = Math.max(siteRect.bottom, decisionRect.bottom);
                return {
                    x: left + window.scrollX,
                    y: top + window.scrollY,
                    width: right - left,
                    height: bottom - top,
                    scale: 1,
                };
            }

            const exactTextElement = text => {
                const normalized = text.replace(/\s+/g, ' ').trim();
                const matches = Array.from(root.querySelectorAll('*')).filter(element =>
                    (element.textContent || '').replace(/\s+/g, ' ').trim().startsWith(normalized)
                );
                matches.sort((a, b) => {
                    const ar = a.getBoundingClientRect();
                    const br = b.getBoundingClientRect();
                    return ar.width * ar.height - br.width * br.height;
                });
                return matches[0] || null;
            };

            const sectionFor = heading => {
                const headingRect = heading.getBoundingClientRect();
                const candidates = [];
                for (let element = heading; element && element !== root; element = element.parentElement) {
                    const rect = element.getBoundingClientRect();
                    if (
                        rect.width >= rootRect.width * 0.25 &&
                        rect.width <= rootRect.width * 0.65 &&
                        rect.height >= headingRect.height * 2
                    ) {
                        candidates.push({element, rect, area: rect.width * rect.height});
                    }
                }
                candidates.sort((a, b) => b.area - a.area);
                return candidates[0]?.rect || null;
            };

            const siteHeading = exactTextElement('Site Bilgileri');
            const decisionHeading = exactTextElement('İlgili Kararlar');
            if (!siteHeading || !decisionHeading) {
                throw new Error('BTK sonuç başlıkları DOM içinde bulunamadı.');
            }
            const siteRect = sectionFor(siteHeading);
            const decisionRect = sectionFor(decisionHeading);
            if (!siteRect || !decisionRect) {
                throw new Error('BTK sonuç kolonlarının sınırları belirlenemedi.');
            }

            const left = Math.min(siteRect.left, decisionRect.left);
            const top = Math.min(siteRect.top, decisionRect.top);
            const right = Math.max(siteRect.right, decisionRect.right);
            const bottom = Math.max(siteRect.bottom, decisionRect.bottom);
            return {
                x: left + window.scrollX,
                y: top + window.scrollY,
                width: right - left,
                height: bottom - top,
                scale: 1,
            };
        }
        """
    )
    return _save_cdp_clip(page, clip, output, max_width)


def _save_cdp_clip(page: Page, clip: dict, output: Path, max_width: int) -> Path:
    if clip["width"] <= 0 or clip["height"] <= 0:
        raise RuntimeError("Sonuç alanı screenshot ölçüsü geçersiz.")

    session = page.context.new_cdp_session(page)
    try:
        session.send("Emulation.setEmulatedMedia", {"media": "screen"})
        payload = session.send(
            "Page.captureScreenshot",
            {
                "format": "png",
                "fromSurface": True,
                "captureBeyondViewport": True,
                "clip": clip,
            },
        )
    finally:
        session.detach()

    image = Image.open(io.BytesIO(base64.b64decode(payload["data"]))).convert("RGB")
    if image.width > max_width:
        height = max(1, round(image.height * max_width / image.width))
        image = image.resize((max_width, height), Image.Resampling.LANCZOS)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, "PNG", optimize=True)
    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("Sonuç screenshot dosyası oluşturulamadı.")
    return output
