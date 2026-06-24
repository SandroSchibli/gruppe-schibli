"""
SRF Tippspiel Scraper — runs as a GitHub Action.
URL: https://wmtippspiel.srf.ch/
"""

import asyncio
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE = "https://wmtippspiel.srf.ch"
MEMBERS = ["Sandro S", "Alice B", "Karin S", "Adi S"]

FLAGS = {
    "Schweiz": "🇨🇭", "Deutschland": "🇩🇪", "Frankreich": "🇫🇷",
    "Spanien": "🇪🇸", "Portugal": "🇵🇹", "Brasilien": "🇧🇷",
    "Argentinien": "🇦🇷", "England": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "Italien": "🇮🇹",
    "Niederlande": "🇳🇱", "Belgien": "🇧🇪", "Kroatien": "🇭🇷",
    "USA": "🇺🇸", "Kanada": "🇨🇦", "Mexiko": "🇲🇽",
    "Marokko": "🇲🇦", "Japan": "🇯🇵", "Südkorea": "🇰🇷",
    "Australien": "🇦🇺", "Bosnien-Herzeg.": "🇧🇦", "Katar": "🇶🇦",
    "Schottland": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "Tschechien": "🇨🇿", "Südafrika": "🇿🇦",
    "Uruguay": "🇺🇾", "Ecuador": "🇪🇨", "Kamerun": "🇨🇲",
    "Nigeria": "🇳🇬", "Saudi-Arabien": "🇸🇦", "Iran": "🇮🇷",
    "Polen": "🇵🇱", "Ukraine": "🇺🇦", "Türkei": "🇹🇷",
    "Österreich": "🇦🇹", "Serbien": "🇷🇸", "Dänemark": "🇩🇰",
    "Schweden": "🇸🇪", "Peru": "🇵🇪", "Kolumbien": "🇨🇴",
    "Chile": "🇨🇱", "Ghana": "🇬🇭", "Tunesien": "🇹🇳", "Senegal": "🇸🇳",
}


async def dismiss_cookie_banner(page):
    """Accept cookies if the banner appears."""
    try:
        btn = page.locator("text=Alle akzeptieren").first
        if await btn.is_visible(timeout=5000):
            await btn.click()
            log.info("Dismissed cookie banner")
            await page.wait_for_timeout(1000)
    except Exception:
        pass  # No banner, continue


async def run():
    username = os.environ["SRF_USERNAME"]
    password = os.environ["SRF_PASSWORD"]

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="de-CH",
        )

        # Block Usercentrics consent manager — prevents cookie banner entirely
        # Only block Usercentrics domains, NOT generic "consent" (breaks SRF OAuth)
        await ctx.route("**/*usercentrics*", lambda route: route.abort())
        await ctx.route("**/app.usercentrics.eu/**", lambda route: route.abort())
        await ctx.route("**/aggregator.service.usercentrics.eu/**", lambda route: route.abort())

        page = await ctx.new_page()

        # ── Load page ──────────────────────────────────────────────────────
        log.info(f"Loading {BASE} ...")
        await page.goto(BASE, wait_until="domcontentloaded", timeout=30000)
        await dismiss_cookie_banner(page)  # fallback in case banner still appears
        await page.screenshot(path="/tmp/01_loaded.png")

        # ── Login ──────────────────────────────────────────────────────────
        # Check if already logged in
        if await page.locator("text=Willkommen").count() == 0:
            log.info("Not logged in — clicking login button...")

            # The page shows "JETZT ANMELDEN" when logged out
            # Click it to get to the login/registration page
            for sel in [
                "text=JETZT ANMELDEN",
                "text=Anmelden",
                "text=Jetzt anmelden",
                "a[href*='login']",
                "button[class*='login']",
            ]:
                try:
                    if await page.locator(sel).count() > 0:
                        await page.locator(sel).first.click()
                        log.info(f"Clicked login via: {sel}")
                        break
                except Exception:
                    continue

            await page.wait_for_load_state("domcontentloaded", timeout=20000)
            await dismiss_cookie_banner(page)
            await page.screenshot(path="/tmp/02_after_click.png")
            log.info(f"Now at: {page.url}")

            # If we're now on a login/register page, look for "Anmelden" tab
            # SRF often has a toggle between Register and Login
            for sel in [
                "text=Ich habe bereits ein Konto",
                "text=Bereits registriert",
                "text=Einloggen",
                "text=Login",
                "a[href*='login']",
            ]:
                try:
                    if await page.locator(sel).count() > 0:
                        await page.locator(sel).first.click()
                        await page.wait_for_load_state("domcontentloaded", timeout=10000)
                        log.info(f"Switched to login tab via: {sel}")
                        break
                except Exception:
                    continue

            await page.screenshot(path="/tmp/03_login_form.png")

            # Fill credentials
            email_filled = False
            for sel in ["input[type='email']", "input[name='email']", "input[id*='email']", "input[placeholder*='Mail']", "input[placeholder*='mail']"]:
                try:
                    if await page.locator(sel).count() > 0:
                        await page.locator(sel).first.fill(username)
                        email_filled = True
                        log.info(f"Filled email via: {sel}")
                        break
                except Exception:
                    continue

            for sel in ["input[type='password']", "input[name='password']"]:
                try:
                    if await page.locator(sel).count() > 0:
                        await page.locator(sel).first.fill(password)
                        log.info(f"Filled password via: {sel}")
                        break
                except Exception:
                    continue

            await page.screenshot(path="/tmp/04_filled.png")

            # Submit
            for sel in ["button[type='submit']", "input[type='submit']", "text=Anmelden", "text=Login", "text=Einloggen"]:
                try:
                    if await page.locator(sel).count() > 0:
                        await page.locator(sel).first.click()
                        log.info(f"Submitted via: {sel}")
                        break
                except Exception:
                    continue

            await page.wait_for_load_state("domcontentloaded", timeout=20000)
            await page.screenshot(path="/tmp/05_after_login.png")
            log.info(f"After login URL: {page.url}")

        log.info("Login complete — collecting data...")

        # Navigate back to Tippspiel if we got redirected elsewhere
        if BASE not in page.url:
            await page.goto(BASE, wait_until="domcontentloaded", timeout=20000)
            await dismiss_cookie_banner(page)

        await page.screenshot(path="/tmp/06_tippspiel.png")

        # ── Standings ──────────────────────────────────────────────────────
        standings = await get_standings(page)

        # ── Rounds ────────────────────────────────────────────────────────
        rounds = await get_all_rounds(page)

        # ── Save ───────────────────────────────────────────────────────────
        data = {
            "standings": standings,
            "rounds": rounds,
            "updated_at": datetime.utcnow().strftime("%d.%m.%Y %H:%M") + " UTC",
        }

        Path("data").mkdir(exist_ok=True)
        Path("data/data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
        log.info(f"Saved: {len(standings)} standings, {len(rounds)} rounds")

        await browser.close()
    return data


async def get_standings(page) -> list:
    log.info("Getting group standings...")

    try:
        await page.click("text=Tippgruppen", timeout=8000)
        await page.wait_for_load_state("domcontentloaded")
        await page.screenshot(path="/tmp/07_tippgruppen.png")

        await page.click("text=Gruppe Schibli", timeout=8000)
        await page.wait_for_load_state("domcontentloaded")
        await page.screenshot(path="/tmp/08_gruppe.png")
    except Exception as e:
        log.warning(f"Navigation failed: {e}")
        await page.screenshot(path="/tmp/07_nav_fail.png")

    body = await page.inner_text("body")
    log.info(f"Page snippet: {body[:300]}")

    standings = []
    seen = set()
    for name in MEMBERS:
        if name in body and name not in seen:
            idx = body.find(name)
            snippet = body[idx: idx + 200]
            pts = re.search(r"(\d{2,3})\s*Pkt", snippet)
            if pts:
                standings.append({"name": name, "points": int(pts.group(1))})
                seen.add(name)

    standings.sort(key=lambda x: x["points"], reverse=True)
    for i, s in enumerate(standings):
        s["rank"] = i + 1

    log.info(f"Standings: {standings}")
    return standings


async def get_all_rounds(page) -> list:
    member_data = {}
    for member in MEMBERS:
        tips = await get_member_tips(page, member)
        member_data[member] = tips

    base = member_data.get("Sandro S", [])
    rounds_out = []

    for round_idx, round_info in enumerate(base):
        matches_out = []
        for match_idx, match in enumerate(round_info.get("matches", [])):
            match_out = {
                "home": match["home"],
                "away": match["away"],
                "home_flag": FLAGS.get(match["home"], "🏳️"),
                "away_flag": FLAGS.get(match["away"], "🏳️"),
                "date": match.get("date", ""),
                "result": match.get("result"),
                "predictions": {},
            }
            for member in MEMBERS:
                member_rounds = member_data.get(member, [])
                if round_idx < len(member_rounds):
                    member_matches = member_rounds[round_idx].get("matches", [])
                    if match_idx < len(member_matches):
                        m = member_matches[match_idx]
                        match_out["predictions"][member] = {
                            "home": m.get("tip_home"),
                            "away": m.get("tip_away"),
                            "points": m.get("points"),
                        }
            matches_out.append(match_out)

        rounds_out.append({
            "name": round_info.get("name", f"Runde {round_idx + 1}"),
            "matches": matches_out,
        })

    return rounds_out


async def get_member_tips(page, member: str) -> list:
    log.info(f"Getting tips for {member}...")
    try:
        await page.goto(BASE, wait_until="domcontentloaded", timeout=20000)
        await dismiss_cookie_banner(page)
        await page.click("text=Tippgruppen", timeout=8000)
        await page.wait_for_load_state("domcontentloaded")
        await page.click("text=Gruppe Schibli", timeout=8000)
        await page.wait_for_load_state("domcontentloaded")
        await page.click(f"text={member}", timeout=8000)
        await page.wait_for_load_state("domcontentloaded")
    except Exception as e:
        log.warning(f"Could not navigate to {member}: {e}")
        return []

    rounds = []
    for _ in range(5):
        round_data = await extract_round(page)
        if round_data and round_data.get("matches"):
            rounds.insert(0, round_data)
        try:
            prev = page.locator("button:has-text('‹'), [aria-label*='vorig']").first
            if await prev.count():
                await prev.click()
                await page.wait_for_load_state("domcontentloaded")
            else:
                break
        except Exception:
            break

    return rounds


async def extract_round(page) -> dict:
    await page.wait_for_timeout(800)
    return await page.evaluate("""
    () => {
        const out = { name: '', matches: [] };
        const roundEl = document.querySelector('select option:checked, h2, h3, [class*="round"]');
        if (roundEl) out.name = roundEl.innerText.trim();

        const containers = document.querySelectorAll('[class*="match"], [class*="game"], [class*="tip"], article');
        containers.forEach(c => {
            const text = c.innerText || '';
            const tipMatch = text.match(/(\\d+)\\s*:\\s*(\\d+)/g);
            if (!tipMatch) return;

            const resultIdx = text.indexOf('Ergebnis');
            let result = null;
            if (resultIdx >= 0) {
                const rm = text.slice(resultIdx, resultIdx+30).match(/(\\d+)\\s*:\\s*(\\d+)/);
                if (rm) result = { home: parseInt(rm[1]), away: parseInt(rm[2]) };
            }

            const ptsMatch = text.match(/Gesamtpunkte[:\\s]*(\\d+)/);
            const scores = tipMatch[0].split(':').map(s => parseInt(s.trim()));
            const teamEls = c.querySelectorAll('[class*="team"], strong, b');
            const teams = Array.from(teamEls).map(el => el.innerText.trim()).filter(t => t.length > 2 && !/^\\d/.test(t));

            if (teams.length >= 2) {
                out.matches.push({
                    home: teams[0], away: teams[1],
                    tip_home: scores[0] ?? null, tip_away: scores[1] ?? null,
                    result, points: ptsMatch ? parseInt(ptsMatch[1]) : null,
                    date: ''
                });
            }
        });
        return out;
    }
    """)


if __name__ == "__main__":
    data = asyncio.run(run())
    print(f"\n✓ Done — {len(data.get('rounds', []))} rounds, {len(data.get('standings', []))} standings")
