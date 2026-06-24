"""
SRF Tippspiel Scraper — runs as a GitHub Action.
URL: https://wmtippspiel.srf.ch/
Scrapes: all rounds, all members' tips, standings, bonus questions
"""

import asyncio
import json
import logging
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from playwright.async_api import async_playwright

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

BASE = "https://wmtippspiel.srf.ch"
MEMBERS = ["Sandro S", "Alice B", "Karin S", "Adi S"]

# German→English team name map (SRF uses German names)
TEAM_EN = {
    "Schweiz": "Switzerland", "Deutschland": "Germany", "Frankreich": "France",
    "Spanien": "Spain", "Portugal": "Portugal", "Brasilien": "Brazil",
    "Argentinien": "Argentina", "England": "England", "Italien": "Italy",
    "Niederlande": "Netherlands", "Belgien": "Belgium", "Kroatien": "Croatia",
    "USA": "USA", "Kanada": "Canada", "Mexiko": "Mexico",
    "Marokko": "Morocco", "Japan": "Japan", "Südkorea": "South Korea",
    "Australien": "Australia", "Bosnien-Herzeg.": "Bosnia-Herzeg.", "Katar": "Qatar",
    "Schottland": "Scotland", "Tschechien": "Czechia", "Südafrika": "South Africa",
    "Uruguay": "Uruguay", "Ecuador": "Ecuador", "Kamerun": "Cameroon",
    "Nigeria": "Nigeria", "Saudi-Arabien": "Saudi Arabia", "Iran": "Iran",
    "Polen": "Poland", "Ukraine": "Ukraine", "Türkei": "Turkey",
    "Österreich": "Austria", "Serbien": "Serbia", "Dänemark": "Denmark",
    "Schweden": "Sweden", "Peru": "Peru", "Kolumbien": "Colombia",
    "Chile": "Chile", "Ghana": "Ghana", "Tunesien": "Tunisia", "Senegal": "Senegal",
    "Norwegen": "Norway", "Haiti": "Haiti", "Curaçao": "Curaçao",
    "Elfenbeinküste": "Ivory Coast", "Rumänien": "Romania", "Albanien": "Albania",
    "Neuseeland": "New Zealand", "Irland": "Ireland", "Kenia": "Kenya",
    "Costa Rica": "Costa Rica", "Panama": "Panama", "DR Kongo": "DR Congo",
    "Usbekistan": "Uzbekistan", "Paraguay": "Paraguay",
}

FLAGS = {
    "Switzerland": "🇨🇭", "Germany": "🇩🇪", "France": "🇫🇷",
    "Spain": "🇪🇸", "Portugal": "🇵🇹", "Brazil": "🇧🇷",
    "Argentina": "🇦🇷", "England": "🏴󠁧󠁢󠁥󠁮󠁧󠁿", "Italy": "🇮🇹",
    "Netherlands": "🇳🇱", "Belgium": "🇧🇪", "Croatia": "🇭🇷",
    "USA": "🇺🇸", "Canada": "🇨🇦", "Mexico": "🇲🇽",
    "Morocco": "🇲🇦", "Japan": "🇯🇵", "South Korea": "🇰🇷",
    "Australia": "🇦🇺", "Bosnia-Herzeg.": "🇧🇦", "Qatar": "🇶🇦",
    "Scotland": "🏴󠁧󠁢󠁳󠁣󠁴󠁿", "Czechia": "🇨🇿", "South Africa": "🇿🇦",
    "Uruguay": "🇺🇾", "Ecuador": "🇪🇨", "Cameroon": "🇨🇲",
    "Nigeria": "🇳🇬", "Saudi Arabia": "🇸🇦", "Iran": "🇮🇷",
    "Poland": "🇵🇱", "Ukraine": "🇺🇦", "Turkey": "🇹🇷",
    "Austria": "🇦🇹", "Serbia": "🇷🇸", "Denmark": "🇩🇰",
    "Sweden": "🇸🇪", "Peru": "🇵🇪", "Colombia": "🇨🇴",
    "Chile": "🇨🇱", "Ghana": "🇬🇭", "Tunisia": "🇹🇳", "Senegal": "🇸🇳",
    "Norway": "🇳🇴", "Haiti": "🇭🇹", "Curaçao": "🇨🇼",
    "Ivory Coast": "🇨🇮", "Romania": "🇷🇴", "Albania": "🇦🇱",
    "New Zealand": "🇳🇿", "Ireland": "🇮🇪", "Kenya": "🇰🇪",
    "Costa Rica": "🇨🇷", "Panama": "🇵🇦", "DR Congo": "🇨🇩",
    "Uzbekistan": "🇺🇿", "Paraguay": "🇵🇾",
}

BONUS_Q_MAP = {
    "Weltmeister": "🏆 World Champion",
    "Wie weit kommt die Schweiz": "🇨🇭 How far does Switzerland go?",
    "Tore erzielt die Schweiz": "⚽ Switzerland total goals (excl. shootouts)",
    "Tore erzielt der Torschützenkönig": "👟 Top scorer total goals (excl. shootouts)",
    "Partien im gesamten Turnier enden 0:0": "0️⃣ Matches ending 0-0 in tournament",
}

ROUND_NAME_EN = {
    "1. Runde": "Round 1", "2. Runde": "Round 2", "3. Runde": "Round 3",
    "1/16-Finals": "Round of 32", "Achtelfinals": "Round of 16",
    "Viertelfinals": "Quarter-finals", "Halbfinals": "Semi-finals",
    "Kleiner Final": "Third Place", "Final": "Final",
}

BONUS_VAL_MAP = {
    "Achtelfinal": "Round of 16",
    "Viertelfinal": "Quarter-final",
    "Halbfinal": "Semi-final",
    "Final": "Final",
    "Spanien": "Spain", "Argentinien": "Argentina", "Frankreich": "France",
    "Brasilien": "Brazil", "Deutschland": "Germany", "England": "England",
    "Portugal": "Portugal", "Niederlande": "Netherlands",
}


async def dismiss_cookie_banner(page):
    try:
        btn = page.locator("text=Alle akzeptieren").first
        if await btn.is_visible(timeout=4000):
            await btn.click()
            await page.wait_for_timeout(800)
    except Exception:
        pass


def compute_groups(rounds):
    """Derive group standings from match results — no external API needed."""
    # Build adjacency graph: teams that played each other are in same group
    from collections import defaultdict
    adj = defaultdict(set)
    all_matches = [m for r in rounds for m in r.get("matches", [])]
    flags = {}
    for m in all_matches:
        h, a = m["home"], m["away"]
        adj[h].add(a)
        adj[a].add(h)
        flags[h] = m.get("homeFlag", "🏳")
        flags[a] = m.get("awayFlag", "🏳")

    # BFS to find groups of 4
    visited = set()
    group_sets = []
    for team in sorted(adj.keys()):
        if team in visited:
            continue
        group = set()
        q = [team]
        while q:
            t = q.pop()
            if t in group:
                continue
            group.add(t)
            for n in adj[t]:
                if n not in group:
                    q.append(n)
        visited |= group
        group_sets.append(sorted(group))

    # Initialize stats
    team_stats = {}
    for g in group_sets:
        for t in g:
            team_stats[t] = {"P": 0, "W": 0, "D": 0, "L": 0, "GF": 0, "GA": 0, "GD": 0, "Pts": 0}

    # Process finished matches
    for m in all_matches:
        res = m.get("result") or {}
        if res.get("home") is None:
            continue
        h, a = m["home"], m["away"]
        gh, ga = res["home"], res["away"]
        for team, gf, ga2 in [(h, gh, ga), (a, ga, gh)]:
            if team not in team_stats:
                continue
            s = team_stats[team]
            s["P"] += 1
            s["GF"] += gf
            s["GA"] += ga2
            s["GD"] += gf - ga2
            if gf > ga2:
                s["W"] += 1
                s["Pts"] += 3
            elif gf == ga2:
                s["D"] += 1
                s["Pts"] += 1
            else:
                s["L"] += 1

    # Build output sorted by Pts, GD, GF
    groups_out = []
    for i, grp in enumerate(group_sets):
        rows = sorted(
            [{"team": t, **team_stats[t]} for t in grp],
            key=lambda x: (-x["Pts"], -x["GD"], -x["GF"])
        )
        groups_out.append({
            "name": f"Group {chr(65 + i)}",
            "teams": [
                {
                    "name": r["team"],
                    "flag": flags.get(r["team"], "🏳"),
                    "rank": j + 1,
                    "played": r["P"],
                    "won": r["W"],
                    "drawn": r["D"],
                    "lost": r["L"],
                    "gf": r["GF"],
                    "ga": r["GA"],
                    "gd": r["GD"],
                    "points": r["Pts"],
                }
                for j, r in enumerate(rows)
            ],
        })
    return groups_out


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

        # Block Usercentrics consent manager
        await ctx.route("**/*usercentrics*", lambda route: route.abort())
        await ctx.route("**/app.usercentrics.eu/**", lambda route: route.abort())
        await ctx.route("**/aggregator.service.usercentrics.eu/**", lambda route: route.abort())

        page = await ctx.new_page()

        # ── Load & login ───────────────────────────────────────────────────
        # SRF uses SRG SSO via OIDC — redirects to account.srgssr.ch
        # Two-step flow: email → Weiter → password → submit → back to tippspiel
        log.info("Navigating to SRG SSO login...")
        await page.goto(f"{BASE}/auth/oidc?intent=signin", wait_until="domcontentloaded", timeout=30000)
        await page.screenshot(path="/tmp/01_sso_login.png")
        log.info(f"After OIDC redirect: {page.url}")

        if "account.srgssr.ch" in page.url:
            log.info("On SSO page — filling email...")
            # Step 1: enter email
            email_input = page.locator("input[placeholder='E-Mail'], input[type='email'], input[name='email']").first
            await email_input.wait_for(timeout=10000)
            await email_input.fill(username)
            await page.screenshot(path="/tmp/02_email_filled.png")

            # Click "Weiter"
            await page.locator("button:has-text('Weiter'), button[type='submit']").first.click()
            log.info("Clicked Weiter...")

            # Step 2: wait for password field
            try:
                await page.wait_for_selector("input[type='password']", timeout=10000)
                log.info("Password field appeared")
                await page.screenshot(path="/tmp/03_password_form.png")

                await page.locator("input[type='password']").first.fill(password)
                await page.screenshot(path="/tmp/04_password_filled.png")

                # Submit login
                await page.locator("button[type='submit'], button:has-text('Anmelden')").first.click()
                log.info("Submitted login form...")

                # Wait for redirect back to tippspiel
                await page.wait_for_url(f"{BASE}/**", timeout=25000)
                await page.screenshot(path="/tmp/05_after_login.png")
                log.info(f"Redirected back: {page.url}")

            except Exception as e:
                log.error(f"Login step failed: {e}")
                await page.screenshot(path="/tmp/error_login.png")
        else:
            # Already logged in (session cookie still valid)
            log.info(f"Already logged in or unexpected redirect: {page.url}")

        # Make sure we're on the tippspiel homepage
        if BASE not in page.url:
            log.info("Navigating back to tippspiel...")
            await page.goto(BASE, wait_until="domcontentloaded", timeout=20000)

        await dismiss_cookie_banner(page)
        await page.screenshot(path="/tmp/06_tippspiel.png")
        log.info(f"Ready to scrape — URL: {page.url}")

        # ── Standings ──────────────────────────────────────────────────────
        standings = await get_standings(page)

        # ── All rounds with tips per member ────────────────────────────────
        rounds = await get_all_rounds(page)

        # ── Bonus questions ────────────────────────────────────────────────
        bonus = await get_bonus_questions(page)

        # ── Guard: don't overwrite with empty data ─────────────────────────
        existing = {}
        ep = Path("data/data.json")
        if ep.exists():
            try:
                existing = json.loads(ep.read_text())
            except Exception:
                pass

        if not standings:
            log.warning("No standings — keeping existing")
            standings = existing.get("standings", [])
        if not rounds:
            log.warning("No rounds — keeping existing")
            rounds = existing.get("rounds", [])
        if not bonus:
            bonus = existing.get("bonus_questions", [])

        groups = compute_groups(rounds)

        data = {
            "standings": standings,
            "rounds": rounds,
            "bonus_questions": bonus,
            "top_scorers": existing.get("top_scorers", []),
            "tournament_stats": existing.get("tournament_stats", {}),
            "groups": groups,
            "updated": datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        Path("data").mkdir(exist_ok=True)
        ep.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        log.info(f"Saved: {len(standings)} standings, {len(rounds)} rounds")

        # ── Push notifications for games starting now ──────────────────────
        send_kickoff_notifications(data)

        await browser.close()
    return data


# ── Push notifications via ntfy.sh ────────────────────────────────────────
def send_kickoff_notifications(data: dict):
    topic = os.environ.get("NTFY_TOPIC", "")
    if not topic:
        log.info("NTFY_TOPIC not set — skipping notifications")
        return

    now = datetime.now(timezone.utc)
    members = ["Sandro S", "Alice B", "Karin S", "Adi S"]
    first = {"Sandro S": "Sandro", "Alice B": "Alice", "Karin S": "Karin", "Adi S": "Adi"}

    for rnd in data.get("rounds", []):
        for match in rnd.get("matches", []):
            kickoff_str = match.get("kickoff")
            if not kickoff_str:
                continue
            try:
                ko = datetime.fromisoformat(kickoff_str.replace("Z", "+00:00"))
            except Exception:
                continue

            # Send notification if kickoff was within the last 6 minutes
            mins_since = (now - ko).total_seconds() / 60
            if not (0 <= mins_since <= 6):
                continue

            home = match.get("home", "?")
            away = match.get("away", "?")
            hf   = match.get("homeFlag", "")
            af   = match.get("awayFlag", "")
            preds = match.get("predictions", {})

            # Build tip line per member
            tip_parts = []
            for mb in members:
                tip = preds.get(mb, {})
                h, a = tip.get("home"), tip.get("away")
                if h is not None and a is not None:
                    tip_parts.append(f"{first[mb]}: {h}-{a}")
                else:
                    tip_parts.append(f"{first[mb]}: ?")

            title   = f"⚽ {hf} {home} vs {away} {af} — ANSTOSS!"
            message = "  ·  ".join(tip_parts)

            try:
                req = urllib.request.Request(
                    f"https://ntfy.sh/{topic}",
                    data=message.encode("utf-8"),
                    headers={
                        "Title":    title,
                        "Priority": "high",
                        "Tags":     "soccer,world_cup",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    log.info(f"Notification sent for {home} vs {away}: {resp.status}")
            except Exception as e:
                log.warning(f"Notification failed for {home} vs {away}: {e}")


# ── Standings ──────────────────────────────────────────────────────────────
async def get_standings(page) -> list:
    log.info("Getting standings...")
    try:
        await page.click("text=Tippgruppen", timeout=8000)
        await page.wait_for_load_state("domcontentloaded")
        await page.click("text=Gruppe Schibli", timeout=8000)
        await page.wait_for_load_state("domcontentloaded")
        await page.screenshot(path="/tmp/06_gruppe.png")
    except Exception as e:
        log.warning(f"Standings nav failed: {e}")
        return []

    body = await page.inner_text("body")
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


# ── All rounds ─────────────────────────────────────────────────────────────
async def get_all_rounds(page) -> list:
    """Collect tips for all members across all rounds, merging with existing data."""
    # Load existing rounds as base (we merge INTO them)
    existing_rounds = []
    ep = Path("data/data.json")
    if ep.exists():
        try:
            existing_rounds = json.loads(ep.read_text()).get("rounds", [])
        except Exception:
            pass

    member_data = {}
    for member in MEMBERS:
        tips = await get_member_tips(page, member)
        member_data[member] = tips
        log.info(f"{member}: {len(tips)} rounds scraped")

    # Use member with most rounds as base structure
    if not any(member_data.values()):
        log.warning("No member data scraped — keeping existing rounds")
        return existing_rounds

    base_member = max(member_data, key=lambda m: len(member_data[m]))
    scraped_rounds = member_data[base_member]

    if not scraped_rounds:
        return existing_rounds

    # Build round name lookup from existing
    existing_by_name = {r["name"]: r for r in existing_rounds}

    rounds_out = []
    for ri, round_info in enumerate(scraped_rounds):
        rname_de = round_info.get("name", "")
        # Translate round name from German
        rname = ROUND_NAME_EN.get(rname_de, rname_de) or f"Round {ri + 1}"

        # Start from existing round data (preserves known schedule)
        existing_round = existing_by_name.get(rname, {})
        existing_matches = {
            f"{m.get('home')}|{m.get('away')}": m
            for m in existing_round.get("matches", [])
        }

        matches_out = []
        for mi, match in enumerate(round_info.get("matches", [])):
            home_en = TEAM_EN.get(match["home"], match["home"])
            away_en = TEAM_EN.get(match["away"], match["away"])
            mkey = f"{home_en}|{away_en}"

            # Merge: existing match data + scraped tips/result
            existing_m = existing_matches.get(mkey, {})
            match_out = {
                "home": home_en,
                "away": away_en,
                "homeFlag": FLAGS.get(home_en, existing_m.get("homeFlag", "🏳️")),
                "awayFlag": FLAGS.get(away_en, existing_m.get("awayFlag", "🏳️")),
                "date": existing_m.get("date") or match.get("date", ""),
                "kickoff": existing_m.get("kickoff") or match.get("kickoff", ""),
                "result": match.get("result") or existing_m.get("result"),
                "predictions": existing_m.get("predictions", {}),
            }
            # Fill in predictions for each member
            for member in MEMBERS:
                mr = member_data.get(member, [])
                if ri < len(mr):
                    mm = mr[ri].get("matches", [])
                    if mi < len(mm):
                        m = mm[mi]
                        if m.get("tip_home") is not None:
                            match_out["predictions"][member] = {
                                "home": m.get("tip_home"),
                                "away": m.get("tip_away"),
                                "points": m.get("points"),
                            }
            matches_out.append(match_out)
            # Remove from existing_matches so we know what's been accounted for
            existing_matches.pop(mkey, None)

        # Add any existing matches not in scraped data (e.g. kickoff times)
        for mkey, existing_m in existing_matches.items():
            matches_out.append(existing_m)

        rounds_out.append({
            "name": rname,
            "isKO": round_info.get("isKO", False) or existing_round.get("isKO", False),
            "matches": matches_out,
        })
        existing_by_name.pop(rname, None)

    # Append any existing rounds that weren't in scraped data (future KO rounds)
    for rname, existing_round in existing_by_name.items():
        rounds_out.append(existing_round)

    # Sort: group rounds first, then KO
    ko_order = ["Round of 32","Round of 16","Quarter-finals","Semi-finals","Third Place","Final"]
    def round_sort(r):
        n = r["name"]
        if n in ko_order: return (1, ko_order.index(n))
        m = re.search(r'(\d+)', n)
        return (0, int(m.group(1)) if m else 99)
    rounds_out.sort(key=round_sort)

    return rounds_out


# ── Tips for one member — ALL rounds ──────────────────────────────────────
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
        log.warning(f"Nav to {member} failed: {e}")
        return []

    await page.wait_for_timeout(800)

    # First: try the dropdown selector to get all available round names
    # The SRF site shows a round selector with options like "1. Runde", "2. Runde", etc.
    round_names = await page.evaluate(r"""
    () => {
        // Try native <select>
        const sel = document.querySelector('select');
        if (sel && sel.options.length > 1) {
            return Array.from(sel.options).map(o => ({value: o.value, label: o.text.trim()}));
        }
        // Try custom dropdown list items
        const items = document.querySelectorAll('[class*="dropdown"] li, [class*="select"] li, [class*="round"] li, [class*="runde"] li');
        if (items.length > 0) {
            return Array.from(items).map((el, i) => ({value: String(i), label: el.innerText.trim()}));
        }
        return [];
    }
    """)
    log.info(f"{member}: found {len(round_names)} rounds in selector: {[r['label'] for r in round_names]}")

    if round_names:
        # Navigate each round via dropdown
        rounds = []
        for ropt in round_names:
            try:
                # Open dropdown
                dropdown_btn = page.locator('[class*="dropdown"][class*="toggle"], select, [class*="round-selector"], [class*="round-select"]').first
                if await dropdown_btn.count():
                    # Try native select
                    try:
                        sel = page.locator('select').first
                        if await sel.count():
                            await sel.select_option(label=ropt['label'])
                            await page.wait_for_load_state("domcontentloaded")
                            await page.wait_for_timeout(600)
                            round_data = await extract_round(page)
                            if round_data and round_data.get("matches"):
                                rounds.append(round_data)
                            continue
                    except Exception:
                        pass
                    # Try clicking custom dropdown
                    await dropdown_btn.click()
                    await page.wait_for_timeout(400)
                    opt_el = page.locator(f"text={ropt['label']}").first
                    if await opt_el.count():
                        await opt_el.click()
                        await page.wait_for_load_state("domcontentloaded")
                        await page.wait_for_timeout(600)
                        round_data = await extract_round(page)
                        if round_data and round_data.get("matches"):
                            rounds.append(round_data)
            except Exception as e:
                log.warning(f"Dropdown nav to {ropt['label']} failed: {e}")
        if rounds:
            log.info(f"{member}: {len(rounds)} rounds via dropdown")
            return rounds

    # Fallback: navigate from round 1 forward using prev/next buttons
    log.info(f"{member}: falling back to prev/next navigation")

    # Go to first round by clicking prev until disabled
    for _ in range(20):
        try:
            prev = page.locator("button:has-text('‹'), [aria-label*='vorig'], [aria-label*='prev']").first
            if await prev.count() and await prev.is_enabled(timeout=2000):
                await prev.click()
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(400)
            else:
                break
        except Exception:
            break

    # Now go forward collecting ALL rounds
    rounds = []
    visited = set()
    for _ in range(30):  # up to 30 rounds max
        round_data = await extract_round(page)
        rname = round_data.get("name", "")
        if rname and rname in visited:
            log.info(f"{member}: revisited {rname} — stopping")
            break
        if round_data and round_data.get("matches"):
            rounds.append(round_data)
            if rname:
                visited.add(rname)
            log.info(f"{member}: collected {rname} ({len(round_data.get('matches', []))} matches)")
        try:
            nxt = page.locator("button:has-text('›'), [aria-label*='nächst'], [aria-label*='next']").first
            if await nxt.count() and await nxt.is_enabled(timeout=2000):
                await nxt.click()
                await page.wait_for_load_state("domcontentloaded")
                await page.wait_for_timeout(400)
            else:
                log.info(f"{member}: no more next rounds")
                break
        except Exception:
            break

    return rounds


# ── Extract one round from current page ───────────────────────────────────
async def extract_round(page) -> dict:
    await page.wait_for_timeout(600)
    return await page.evaluate(r"""
    () => {
        const out = { name: '', isKO: false, matches: [] };

        // Round name — try various selectors
        const nameSelectors = [
            'select option:checked',
            '[class*="round-title"]', '[class*="runde"]',
            'h2', 'h3', '[class*="heading"]'
        ];
        for (const sel of nameSelectors) {
            const el = document.querySelector(sel);
            if (el && el.innerText && el.innerText.trim().length > 2) {
                out.name = el.innerText.trim().split('\n')[0];
                break;
            }
        }
        // Check if KO round
        out.isKO = /achtel|viertel|halbfinal|final/i.test(out.name);

        // Find all match containers
        const containers = document.querySelectorAll([
            '[class*="match"]',
            '[class*="game"]',
            '[class*="begegnung"]',
            '[class*="tip"]',
            'article',
            'li[class]'
        ].join(','));

        containers.forEach(c => {
            const text = (c.innerText || '').trim();
            if (text.length < 5) return;

            // Extract score pattern (tip or result)
            const scores = text.match(/(\d+)\s*:\s*(\d+)/g);
            if (!scores && !text.match(/\d+\s*:\s*\d+/)) return;

            // Date/time
            let dateStr = '';
            const dateEl = c.querySelector('[class*="date"], [class*="datum"], [class*="time"], [class*="zeit"]');
            if (dateEl) dateStr = dateEl.innerText.trim();
            else {
                const m = text.match(/(\d{1,2}\.\s*\w+\.?\s*\|\s*\d{2}:\d{2})/);
                if (m) dateStr = m[1];
            }

            // Result
            let result = null;
            const resIdx = Math.max(text.indexOf('Ergebnis'), text.indexOf('ergebnis'), text.indexOf('Resultat'));
            if (resIdx >= 0) {
                const rm = text.slice(resIdx, resIdx + 30).match(/(\d+)\s*:\s*(\d+)/);
                if (rm) result = { home: parseInt(rm[1]), away: parseInt(rm[2]) };
            }

            // Tip (first score found)
            let tipHome = null, tipAway = null;
            if (scores && scores[0]) {
                const parts = scores[0].split(':').map(s => parseInt(s.trim()));
                tipHome = parts[0] ?? null;
                tipAway = parts[1] ?? null;
            }

            // Points
            let pts = null;
            const ptsMatch = text.match(/Gesamtpunkte[:\s]*(\d+)|(\d+)\s*Punkt/i);
            if (ptsMatch) pts = parseInt(ptsMatch[1] || ptsMatch[2]);

            // Teams
            const teamEls = c.querySelectorAll('[class*="team"], [class*="mannschaft"], strong, b, [class*="club"]');
            const teams = Array.from(teamEls)
                .map(el => el.innerText.trim())
                .filter(t => t.length > 2 && t.length < 30 && !/^\d/.test(t) && !/Punkt|Pkt|Tipp|Runde/i.test(t));

            if (teams.length >= 2) {
                out.matches.push({
                    home: teams[0], away: teams[1],
                    tip_home: tipHome, tip_away: tipAway,
                    result, points: pts, date: dateStr
                });
            }
        });

        return out;
    }
    """)


# ── Bonus questions ────────────────────────────────────────────────────────
async def get_bonus_questions(page) -> list:
    """Scrape bonus question answers for all members."""
    log.info("Getting bonus questions...")
    member_answers = {}

    for member in MEMBERS:
        try:
            await page.goto(BASE, wait_until="domcontentloaded", timeout=20000)
            await dismiss_cookie_banner(page)
            await page.click("text=Tippgruppen", timeout=8000)
            await page.wait_for_load_state("domcontentloaded")
            await page.click("text=Gruppe Schibli", timeout=8000)
            await page.wait_for_load_state("domcontentloaded")
            await page.click(f"text={member}", timeout=8000)
            await page.wait_for_load_state("domcontentloaded")

            # Navigate to bonus/Zusatzfragen section
            for sel in ["text=Zusatzfragen", "text=Bonus", "[class*='zusatz']"]:
                try:
                    if await page.locator(sel).count() > 0:
                        await page.locator(sel).first.click()
                        await page.wait_for_load_state("domcontentloaded")
                        break
                except Exception:
                    continue

            await page.wait_for_timeout(800)
            answers = await page.evaluate(r"""
            () => {
                const result = {};
                const cards = document.querySelectorAll('[class*="question"], [class*="frage"], [class*="bonus"], [class*="zusatz"], article, .card');
                cards.forEach(card => {
                    const text = card.innerText || '';
                    // Find question text
                    const qEl = card.querySelector('[class*="title"], [class*="heading"], h2, h3, h4, strong');
                    const q = qEl ? qEl.innerText.trim() : '';
                    // Find selected answer
                    const ansEl = card.querySelector('[class*="answer"], [class*="antwort"], [class*="selected"], [class*="chosen"], b, strong + *, .value');
                    let a = ansEl ? ansEl.innerText.trim() : '';
                    // Fallback: look for "Gewählte Antwort" pattern
                    const gaIdx = text.indexOf('Gewählte Antwort');
                    if (!a && gaIdx >= 0) {
                        a = text.slice(gaIdx + 16, gaIdx + 60).trim().split('\n')[0].trim();
                    }
                    if (q && a) result[q] = a;
                });
                return result;
            }
            """)
            member_answers[member] = answers
            log.info(f"{member} bonus: {answers}")
        except Exception as e:
            log.warning(f"Bonus for {member} failed: {e}")
            member_answers[member] = {}

    # Build structured bonus questions list
    # Use question keys to group across members
    all_questions = {}
    for member, answers in member_answers.items():
        for q_de, ans in answers.items():
            q_en = next((v for k, v in BONUS_Q_MAP.items() if k in q_de), q_de)
            if q_en not in all_questions:
                all_questions[q_en] = {}
            ans_en = BONUS_VAL_MAP.get(ans, ans)
            all_questions[q_en][member] = ans_en

    if not all_questions:
        return []

    return [{"q": q, "a": a} for q, a in all_questions.items()]


if __name__ == "__main__":
    data = asyncio.run(run())
    print(f"\n✓ Done — {len(data.get('rounds', []))} rounds, {len(data.get('standings', []))} standings")
