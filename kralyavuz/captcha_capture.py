import base64
import binascii

from playwright.sync_api import Error as PlaywrightError, Locator


def capture_captcha_image(
    locator: Locator, allow_element_screenshot: bool = True
) -> bytes:
    """Read the loaded CAPTCHA resource through the page's CDP session."""
    try:
        data_url = locator.evaluate(
            """
            async image => {
                if (!image.complete) {
                    await image.decode();
                }
                if (!image.naturalWidth || !image.naturalHeight) {
                    throw new Error('CAPTCHA image has no dimensions');
                }

                try {
                    const canvas = document.createElement('canvas');
                    canvas.width = image.naturalWidth;
                    canvas.height = image.naturalHeight;
                    canvas.getContext('2d').drawImage(image, 0, 0);
                    return canvas.toDataURL('image/png');
                } catch (canvasError) {
                    const source = image.currentSrc || image.src;
                    const response = await fetch(source, {
                        credentials: 'include',
                        cache: 'force-cache',
                    });
                    if (!response.ok) {
                        throw new Error(`CAPTCHA HTTP ${response.status}`);
                    }
                    const blob = await response.blob();
                    return await new Promise((resolve, reject) => {
                        const reader = new FileReader();
                        reader.onload = () => resolve(reader.result);
                        reader.onerror = () => reject(reader.error);
                        reader.readAsDataURL(blob);
                    });
                }
            }
            """
        )
        if not isinstance(data_url, str) or "," not in data_url:
            raise RuntimeError("CAPTCHA DOM verisi geçersiz.")
        encoded = data_url.split(",", 1)[1]
        image = base64.b64decode(encoded, validate=True)
        if not image:
            raise RuntimeError("CAPTCHA DOM verisi boş.")
        return image
    except (PlaywrightError, RuntimeError, ValueError, binascii.Error) as exc:
        if not allow_element_screenshot:
            raise RuntimeError("CAPTCHA görseli DOM üzerinden alınamadı.") from exc
        image = locator.screenshot(type="png")
        if not image:
            raise RuntimeError("CAPTCHA görseli CDP üzerinden alınamadı.")
        return image
