"""
SRF Tippspiel Scraper — runs as a GitHub Action.
Logs in as Sandro, fetches standings + predictions for Gruppe Schibli,
saves results to data/data.json which GitHub Pages serves to the frontend.
"""

import asyncio
import json
import logging
import os
import re
import sys
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
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="de-CH",
        )
        page = await ctx.new_page()

        # ── Login ──────────────────────────────────────────────────────────
        log.info("Loading Tippspiel...")
        await page.goto(BASE, wait_until="networkidle", timeout=30000)
        await page.screenshot(path="/tmp/01_loaded.png")

        # Check if already logged in
        if not await page.locator("text=Willkommen").count():
            log.info("Logging in...")
            # Find and click login — SRF sites use various patterns
            for sel in ["text=Anmelden", "[data-testid='login-button']", "a[href*='login']"]:
                if await page.locator(sel).count():
                    await page.click(sel)
                    break

            await page.wait_for_load_state("networkidle", timeout=20000)
            await page.screenshot(path="/tmp/02_login_page.png")

            await page.fill("input[type='email'], input[name='email']", username)
            await page.fill("input[type='password']", password)
            await page.screenshot(path="/tmp/03_filled.png")
            await page.click("button[type='submit'], input[type='submit']")
            await page.wait_for_load_state("networkidle", timeout=20000)
            await page.screenshot(path="/tmp/04_after_login.png")

        log.info("Logged in, collecting data...")

        # ── Standings ──────────────────────────────────────────────────────
        standings = await get_standings(page)

        # ── Tips per member ────────────────────────────────────────────────
        rounds = await get_all_rounds(page)

        # ── Save ───────────────────────────────────────────────────────────
        data = {
            "standings": standings,
            "rounds": rounds,
            "updated_at": datetime.utcnow().strftime("%d.%m.%Y %H:%M") + " UTC",
        }

        Path("data").mkdir(exist_ok=True)
        Path("data/data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
        log.info("Saved data/data.json")

        await browser.close()
    return data


async def get_standings(page) -> list:
    """Navigate to group page and extract leaderboard."""
    log.info("Getting standings...")
    try:
        await page.click("text=Tippgruppen", timeout=8000)
        await page.wait_for_load_state("networkidle")
        await page.click("text=Gruppe Schibli", timeout=8000)
        await page.wait_for_load_state("networkidle")
        await page.screenshot(path="/tmp/05_group.png")
    except Exception as e:
        log.warning(f"Could not navigate to group via clicks: {e}")
        await page.screenshot(path="/tmp/05_group_fail.png")

    # Extract from page text — look for known names + points
    body = await page.inner_text("body")
    log.info(f"Page text snippet: {body[:500]}")

    standings = []
    seen = set()
    for name in MEMBERS:
        if name in body and name not in seen:
            idx = body.find(name)
            snippet = body[idx: idx + 150]
            pts = re.search(r"(\d{2,3})\s*Pkt", snippet)
            if pts:
                standings.append({"name": name, "points": int(pts.group(1))})
                seen.add(name)

    # Sort by points descending and assign ranks
    standings.sort(key=lambda x: x["points"], reverse=True)
    for i, s in enumerate(standings):
        s["rank"] = i + 1

    log.info(f"Standings: {standings}")
    return standings


async def get_all_rounds(page) -> list:
    """
    Visit each member's tip page and collect all rounds.
    Returns list of round dicts, each with a list of match dicts.
    """
    # Collect tips per member
    member_data = {}
    for member in MEMBERS:
        tips = await get_member_tips(page, member)
        member_data[member] = tips

    # Merge into rounds structure
    # Use Sandro's data as the base (he's always accessible)
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
            # Add predictions from all members
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
    """Navigate to a member's profile and extract their tips for all visible rounds."""
    log.info(f"Getting tips for {member}...")

    # Navigate: Tippgruppen → Gruppe Schibli → click member name
    try:
        await page.goto(BASE, wait_until="networkidle", timeout=20000)
        await page.click("text=Tippgruppen", timeout=8000)
        await page.wait_for_load_state("networkidle")
        await page.click("text=Gruppe Schibli", timeout=8000)
        await page.wait_for_load_state("networkidle")

        # Click the member name in the leaderboard
        await page.click(f"text={member}", timeout=8000)
        await page.wait_for_load_state("networkidle")
        await page.screenshot(path=f"/tmp/member_{member.replace(' ', '_')}.png")
    except Exception as e:
        log.warning(f"Could not navigate to {member}: {e}")
        return []

    rounds = []
    # Collect current round + navigate backwards through available rounds
    for attempt in range(5):  # Up to 5 rounds
        round_data = await extract_round_from_page(page)
        if round_data and round_data.get("matches"):
            rounds.insert(0, round_data)  # Insert at front (chronological order)

        # Try to go to previous round
        try:
            prev_btn = page.locator("button:has-text('‹'), [aria-label*='vorig'], [aria-label*='prev']").first
            if await prev_btn.count():
                await prev_btn.click()
                await page.wait_for_load_state("networkidle")
            else:
                break
        except Exception:
            break

    return rounds


async def extract_round_from_page(page) -> dict:
    """Extract all match tips from the currently displayed round page."""
    await page.wait_for_timeout(800)

    result = await page.evaluate("""
    () => {
        const out = { name: '', matches: [] };

        // Round name — look in selects, h2, h3 or specific round header elements
        const roundEl = document.querySelector('select option:checked, [class*="round-header"], h2, h3');
        if (roundEl) out.name = roundEl.innerText.trim().replace(/\\s+/g, ' ');

        // Find all match/tip blocks — they contain two teams, a predicted score, and a result
        // SRF renders these as card-like components
        const allText = document.body.innerText;

        // Heuristic: find date lines like "18. Juni | 18:00"
        const datePattern = /(\\d{1,2})\\.\\s*(\\w+)\\s*\\|?\\s*(\\d{2}:\\d{2})/g;
        const dates = [...allText.matchAll(datePattern)].map(m => m[0]);

        // Find team blocks — pairs of team names
        // Look for containers that have two country names side by side
        const containers = document.querySelectorAll(
            '[class*="match"], [class*="game"], [class*="spiel"], [class*="tip-item"], [class*="tipitem"], article'
        );

        containers.forEach((c, idx) => {
            const text = c.innerText || '';
            if (!text.trim()) return;

            // Extract tip score like "2 : 1" or "2:1"
            const tipMatch = text.match(/(\\d+)\\s*:\\s*(\\d+)/g);
            if (!tipMatch || tipMatch.length === 0) return;

            // Extract result — often shown below as "Ergebnis X:Y"
            const resultIdx = text.indexOf('Ergebnis');
            let result = null;
            if (resultIdx >= 0) {
                const afterResult = text.slice(resultIdx, resultIdx + 30);
                const rm = afterResult.match(/(\\d+)\\s*:\\s*(\\d+)/);
                if (rm) result = { home: parseInt(rm[1]), away: parseInt(rm[2]) };
            }

            // Extract points earned for this match
            const ptsMatch = text.match(/Gesamtpunkte[:\\s]*(\\d+)/);
            const points = ptsMatch ? parseInt(ptsMatch[1]) : null;

            // First score = tip
            const tipScores = tipMatch[0].split(':').map(s => parseInt(s.trim()));

            // Extract team names — usually strong/span elements or just text
            const teamEls = c.querySelectorAll('[class*="team-name"], [class*="teamname"], strong, b, [class*="club"]');
            const teams = Array.from(teamEls)
                .map(el => el.innerText.trim())
                .filter(t => t.length > 2 && !/^\\d/.test(t) && !t.includes(':'));

            if (teams.length >= 2) {
                out.matches.push({
                    home: teams[0],
                    away: teams[1],
                    tip_home: tipScores[0] ?? null,
                    tip_away: tipScores[1] ?? null,
                    result: result,
                    points: points,
                    date: dates[idx] || '',
                });
            }
        });

        return out;
    }
    """)

    log.info(f"Round '{result.get('name')}': {len(result.get('matches', []))} matches")
    return result


if __name__ == "__main__":
    data = asyncio.run(run())
    print(f"\n✓ Done — {len(data.get('rounds', []))} rounds, {len(data.get('standings', []))} standings")
