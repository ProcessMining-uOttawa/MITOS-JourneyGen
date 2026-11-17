# -*- coding: utf-8 -*-
"""The repository's social preview (1280 x 640), from the logo and a screenshot.

    python assets/social/make_social.py

Needs playwright with Edge. Upload the result under Settings > Social preview.
"""
import base64
import os

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))


def data(path, kind):
    with open(os.path.join(ROOT, path), "rb") as f:
        return f"data:{kind};base64," + base64.b64encode(f.read()).decode()


logo = data("assets/logo_light.svg", "image/svg+xml")
shot = data("docs/images/app_opportunities.png", "image/png")
html = f"""<html><body style="margin:0;width:1280px;height:640px;font-family:Segoe UI,Arial,sans-serif;
background:linear-gradient(135deg,#F3F7FC 0%,#E3EDF8 100%);overflow:hidden;position:relative">
<img src="{logo}" style="position:absolute;left:64px;top:70px;width:560px">
<div style="position:absolute;left:68px;top:200px;width:520px;color:#0B2A4A;font-size:34px;line-height:1.3;font-weight:600">
Which public services do citizens use together?</div>
<div style="position:absolute;left:68px;top:330px;width:500px;color:#35506B;font-size:24px;line-height:1.45">
Synthetic event logs built from the 4,416 procedures of the Greek MITOS register, journey discovery with ground truth, and ranked integration opportunities.</div>
<div style="position:absolute;left:68px;top:540px;color:#0D5EAF;font-size:22px;font-weight:600">
ProcessMining-uOttawa · University of Ottawa</div>
<img src="{shot}" style="position:absolute;left:660px;top:90px;width:760px;border-radius:14px;
box-shadow:0 12px 40px rgba(11,42,74,.25);border:1px solid #C9D6E3">
</body></html>"""
with sync_playwright() as p:
    b = p.chromium.launch(channel="msedge", headless=True)
    page = b.new_page(viewport={"width": 1280, "height": 640})
    page.set_content(html)
    page.wait_for_timeout(500)
    page.screenshot(path=os.path.join(HERE, "social_preview.png"))
    b.close()
print("wrote assets/social/social_preview.png")
