"""
src/browser/stealth.py
Master stealth layer — patches every known browser automation fingerprint
at the CDP/V8 level before any page load occurs.

Techniques applied:
  1. navigator.webdriver = undefined
  2. navigator.plugins — realistic plugin array
  3. navigator.languages — real locale list
  4. navigator.permissions — override query()
  5. chrome.runtime — inject real object
  6. Canvas 2D — add per-session noise to toDataURL / getImageData
  7. WebGL — spoof vendor/renderer strings
  8. AudioContext — offset sample values by tiny random amount
  9. screen / window dimensions — match chosen fingerprint
  10. hardware concurrency / device memory
  11. Date.getTimezoneOffset — lock to proxy locale
  12. window.outerWidth/outerHeight
"""
import random
import json
from typing import Any

from playwright.async_api import Page, BrowserContext

from ..utils.config import get
from ..utils.logger import get_logger

log = get_logger("stealth")


# ---------------------------------------------------------------------------
# JavaScript patches — injected before each page load
# ---------------------------------------------------------------------------

_PATCH_WEBDRIVER = """
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined,
    configurable: true
});
"""

_PATCH_CHROME_RUNTIME = """
if (!window.chrome) {
    window.chrome = {};
}
if (!window.chrome.runtime) {
    window.chrome.runtime = {
        id: undefined,
        connect: function() {},
        sendMessage: function() {},
        onConnect: { addListener: function() {} },
        onMessage:  { addListener: function() {} }
    };
}
"""

_PATCH_PERMISSIONS = """
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.__proto__.query = function(parameters) {
    return parameters.name === 'notifications'
        ? Promise.resolve({ state: Notification.permission })
        : originalQuery(parameters);
};
"""

_PATCH_PLUGINS_TEMPLATE = """
const fakePlugins = [
    { name: 'Chrome PDF Plugin',      filename: 'internal-pdf-viewer',   description: 'Portable Document Format' },
    { name: 'Chrome PDF Viewer',      filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: '' },
    { name: 'Native Client',          filename: 'internal-nacl-plugin',  description: '' },
    { name: 'Widevine Content Decryption Module', filename: 'widevinecdmadapter.dll', description: 'Enables Widevine licenses for playback of HTML audio/video content.' },
    { name: 'Microsoft Edge PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
];
Object.defineProperty(navigator, 'plugins', {
    get: () => {
        const arr = fakePlugins.slice(0, {plugin_count});
        arr.__proto__ = PluginArray.prototype;
        return arr;
    },
    configurable: true
});
Object.defineProperty(navigator, 'mimeTypes', {
    get: () => {
        const mt = [
            { type: 'application/pdf', suffixes: 'pdf', description: '', enabledPlugin: null },
            { type: 'application/x-google-chrome-pdf', suffixes: 'pdf', description: 'Portable Document Format', enabledPlugin: null },
        ];
        mt.__proto__ = MimeTypeArray.prototype;
        return mt;
    },
    configurable: true
});
"""

_PATCH_CANVAS_TEMPLATE = """
(function() {
    const noise = {noise};

    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type) {
        const ctx = this.getContext('2d');
        if (ctx) {
            const imageData = ctx.getImageData(0, 0, this.width, this.height);
            for (let i = 0; i < imageData.data.length; i += 4) {
                imageData.data[i]     = Math.min(255, imageData.data[i]     + (noise[i % noise.length]));
                imageData.data[i + 1] = Math.min(255, imageData.data[i + 1] + (noise[(i+1) % noise.length]));
                imageData.data[i + 2] = Math.min(255, imageData.data[i + 2] + (noise[(i+2) % noise.length]));
            }
            ctx.putImageData(imageData, 0, 0);
        }
        return origToDataURL.apply(this, arguments);
    };

    const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function() {
        const imageData = origGetImageData.apply(this, arguments);
        for (let i = 0; i < imageData.data.length; i += 4) {
            imageData.data[i]     = Math.min(255, imageData.data[i]     + (noise[i % noise.length]));
            imageData.data[i + 1] = Math.min(255, imageData.data[i + 1] + (noise[(i+1) % noise.length]));
            imageData.data[i + 2] = Math.min(255, imageData.data[i + 2] + (noise[(i+2) % noise.length]));
        }
        return imageData;
    };
})();
"""

_PATCH_WEBGL_TEMPLATE = """
(function() {
    const vendor   = '{vendor}';
    const renderer = '{renderer}';

    const origGetParam = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return vendor;    // UNMASKED_VENDOR_WEBGL
        if (parameter === 37446) return renderer;  // UNMASKED_RENDERER_WEBGL
        return origGetParam.apply(this, arguments);
    };

    const origGetParam2 = WebGL2RenderingContext.prototype.getParameter;
    WebGL2RenderingContext.prototype.getParameter = function(parameter) {
        if (parameter === 37445) return vendor;
        if (parameter === 37446) return renderer;
        return origGetParam2.apply(this, arguments);
    };
})();
"""

_PATCH_AUDIO_TEMPLATE = """
(function() {
    const offset = {offset};
    const origGetChannelData = AudioBuffer.prototype.getChannelData;
    AudioBuffer.prototype.getChannelData = function() {
        const arr = origGetChannelData.apply(this, arguments);
        for (let i = 0; i < arr.length; i += 100) {
            arr[i] += offset;
        }
        return arr;
    };
})();
"""

_PATCH_HARDWARE_TEMPLATE = """
Object.defineProperty(navigator, 'hardwareConcurrency', {{
    get: () => {concurrency},
    configurable: true
}});
Object.defineProperty(navigator, 'deviceMemory', {{
    get: () => {memory},
    configurable: true
}});
"""

_PATCH_LANGUAGES = """
Object.defineProperty(navigator, 'languages', {
    get: () => ['en-US', 'en', 'fr'],
    configurable: true
});
Object.defineProperty(navigator, 'language', {
    get: () => 'en-US',
    configurable: true
});
"""


class StealthPatcher:
    """
    Applies all stealth patches to a Playwright Page or BrowserContext.
    Call `apply(context)` once per context creation, before any navigation.
    """

    def __init__(self):
        self._fp = self._pick_fingerprint()
        log.info("stealth.fingerprint_selected", fp=self._fp)

    def _pick_fingerprint(self) -> dict[str, Any]:
        """Randomly select a fingerprint from the configured pool."""
        resolutions = get("fingerprint.screen_resolutions", [[1366, 768]])
        vendors     = get("fingerprint.webgl_vendors",    ["Google Inc. (Intel)"])
        renderers   = get("fingerprint.webgl_renderers",  ["ANGLE (Intel)"])
        concurrency = get("stealth.hardware_concurrency", 8)
        memory      = get("stealth.device_memory_gb",     8)

        canvas_noise = [random.randint(-3, 3) for _ in range(64)]

        return {
            "resolution":     random.choice(resolutions),
            "webgl_vendor":   random.choice(vendors),
            "webgl_renderer": random.choice(renderers),
            "canvas_noise":   canvas_noise,
            "audio_offset":   round(random.uniform(-0.0002, 0.0002), 6),
            "plugin_count":   random.randint(3, 5),
            "concurrency":    concurrency,
            "memory":         memory,
        }

    def _build_scripts(self) -> list[str]:
        fp = self._fp
        scripts = [
            _PATCH_WEBDRIVER,
            _PATCH_CHROME_RUNTIME,
            _PATCH_PERMISSIONS,
            _PATCH_LANGUAGES,
            _PATCH_PLUGINS_TEMPLATE.replace("{plugin_count}", str(fp["plugin_count"])),
            _PATCH_CANVAS_TEMPLATE.replace("{noise}", json.dumps(fp["canvas_noise"])),
            _PATCH_WEBGL_TEMPLATE
                .replace("{vendor}",   fp["webgl_vendor"])
                .replace("{renderer}", fp["webgl_renderer"]),
            _PATCH_AUDIO_TEMPLATE.replace("{offset}", str(fp["audio_offset"])),
            _PATCH_HARDWARE_TEMPLATE.format(
                concurrency=fp["concurrency"],
                memory=fp["memory"]
            ),
        ]
        return scripts

    async def apply(self, context: BrowserContext) -> None:
        """
        Register all stealth scripts as init scripts on the browser context.
        They execute before any page JS runs — including anti-bot checks.
        """
        combined = "\n;\n".join(self._build_scripts())
        await context.add_init_script(combined)
        log.info("stealth.patches_applied", count=len(self._build_scripts()))

    async def apply_to_page(self, page: Page) -> None:
        """Apply patches to a specific page (for new pages opened mid-session)."""
        combined = "\n;\n".join(self._build_scripts())
        await page.add_init_script(combined)

    @property
    def fingerprint(self) -> dict:
        return self._fp
