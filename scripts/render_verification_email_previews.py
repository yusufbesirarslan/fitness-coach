"""Render synthetic verification emails; never send mail or load app/AWS config.

Uses the project's existing development dependency, Playwright. Supply a local
copy of the existing public BRAND_MARK_URL artwork for deterministic rendering.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import types

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASELINE = "bfe132a0009f99b808d21fc9bc15e8cc365ee8b7"
TEMPLATE = "app/services/email_templates.py"


def module(source):
    result = types.ModuleType("preview_email_templates")
    exec(compile(source, TEMPLATE, "exec"), result.__dict__)
    result.APP_BASE_URL = "https://www.axisaiapp.com"
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brand-mark", required=True, type=Path)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "docs/design/verification-email/previews")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    before = module(subprocess.check_output(
        ["git", "show", BASELINE + ":" + TEMPLATE], cwd=ROOT).decode())
    after = module((ROOT / TEMPLATE).read_text())
    mark = args.brand_mark.read_bytes()
    report = {"baseline": BASELINE, "code": "012345", "name": "onboarding",
              "brand_mark_sha256": hashlib.sha256(mark).hexdigest(),
              "renderer": "Chromium (browser preview, not an email-client certification)",
              "unrelated_templates_byte_identical": [], "checks": []}

    # Guard scope using actual rendered output in both existing languages.
    for name in ("reset_code_email", "welcome_email", "password_changed_email"):
        for language in ("tr", "en"):
            values = ("İpek", "012345") if name == "reset_code_email" else ("İpek",)
            assert getattr(before, name)(*values, language=language) == \
                getattr(after, name)(*values, language=language), (name, language)
            report["unrelated_templates_byte_identical"].append([name, language])

    with sync_playwright() as p:
        browser = p.chromium.launch()
        report["browser_version"] = browser.version
        page = browser.new_page(device_scale_factor=1)

        def resource(route):
            if route.request.url == after.BRAND_MARK_URL:
                route.fulfill(body=mark, content_type="image/png")
            else:
                route.abort()  # no network, tracking or live verification requests

        page.route("**/*", resource)
        for version, templates in (("before", before), ("after", after)):
            subject, html, text = templates.verification_code_email("onboarding", "012345")
            (args.output / (version + ".html")).write_text(html)
            (args.output / (version + ".txt")).write_text(subject + "\n\n" + text)
            for scheme in ("light", "dark"):
                page.emulate_media(color_scheme=scheme)
                for size, width, height in (("desktop", 800, 800), ("mobile", 390, 844)):
                    page.set_viewport_size({"width": width, "height": height})
                    page.set_content(html, wait_until="networkidle")
                    page.screenshot(path=str(args.output / f"{version}-{size}-{scheme}.png"),
                                    full_page=True)

        # Mobile stress and the inline-only, images-blocked fallback. No changes
        # to production HTML: stripped CSS is used only for this preview check.
        for language in ("tr", "en"):
            _, html, _ = after.verification_code_email("onboarding", "012345", language)
            for scheme in ("light", "dark"):
                page.emulate_media(color_scheme=scheme)
                for width in (320, 375, 390, 430, 800):
                    for inline_only in (False, True):
                        source = html
                        if inline_only:
                            source = re.sub(r"<style>.*?</style>", "", source, flags=re.S)
                            source = re.sub(r"<img\b[^>]*>", "", source)
                        page.set_viewport_size({"width": width, "height": 844})
                        page.set_content(source, wait_until="load")
                        metrics = page.evaluate("""() => {
                            const code = document.querySelector('.ax-digits');
                            const cta = document.querySelector('a[href$="/verify"]');
                            const range = document.createRange();
                            range.selectNodeContents(code);
                            const digits = range.getBoundingClientRect();
                            const box = code.getBoundingClientRect();
                            return {
                                scrollWidth: document.documentElement.scrollWidth,
                                code: code.textContent, cta: cta.getAttribute('href'),
                                ctaHeight: cta.getBoundingClientRect().height,
                                digitsFit: digits.left >= box.left && digits.right <= box.right,
                                card: getComputedStyle(document.querySelector('.ax-card')).backgroundColor,
                                codeColor: getComputedStyle(code).color
                            };
                        }""")
                        assert metrics["scrollWidth"] <= width, metrics
                        assert metrics["code"] == "012345" and metrics["digitsFit"], metrics
                        assert metrics["cta"] == "https://www.axisaiapp.com/verify", metrics
                        assert metrics["ctaHeight"] >= 48, metrics
                        assert metrics["card"] == "rgb(15, 22, 40)", metrics
                        assert metrics["codeColor"] == "rgb(241, 244, 251)", metrics
                        assert "onboarding" not in page.locator("body").inner_text()
                        report["checks"].append({"language": language, "scheme": scheme,
                                                 "width": width, "inline_only": inline_only,
                                                 **metrics})
                        if width == 320 and (inline_only or language == "en"):
                            suffix = "inline-only" if inline_only else "full"
                            page.screenshot(path=str(args.output / f"after-320-{language}-{scheme}-{suffix}.png"),
                                            full_page=True)
        browser.close()
    (args.output / "checks.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(f"Rendered previews; {len(report['checks'])} layout checks and six unchanged-template comparisons passed.")


if __name__ == "__main__":
    main()
