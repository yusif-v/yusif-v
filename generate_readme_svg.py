#!/usr/bin/env python3
"""Fetches live GitHub stats for yusif-v and renders dark_mode.svg / light_mode.svg.

Same neofetch-card pattern as github.com/Andrew6rant/Andrew6rant: ASCII art
block on the left, a dotted key/value readout on the right, same column
positions and row height. Static, no animation.

Content (the ASCII art and every row of text) lives in card_template.txt,
not in this file — edit that instead. This script only fetches live GitHub
data and fills it into the template's {placeholders}.

Run manually with GH_TOKEN set (needs a PAT with `repo` scope to see private
repos/commits — `read:user` alone only gets public data), or via
.github/workflows/update-profile.yml on a schedule.
"""
import json
import os
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import date
from pathlib import Path
from xml.sax.saxutils import escape

USERNAME = "yusif-v"
TOKEN = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
BIRTHDATE = date(2003, 11, 12)
TEMPLATE_PATH = Path(__file__).parent / "card_template.txt"
REQUEST_TIMEOUT = 10  # seconds, per HTTP call

PROGRAMMING_LANGS = {
    "Python", "Go", "JavaScript", "TypeScript", "Rust", "Java", "C", "C++",
    "C#", "Shell", "Ruby", "PHP", "Swift", "Kotlin", "Scala", "Perl", "Lua",
    "Dart", "R", "Objective-C", "Elixir", "Haskell",
}


# ---------------------------------------------------------------- template

def parse_template(path):
    text = path.read_text()
    art_block = text.split("<<<ART", 1)[1].split(">>>", 1)[0].strip("\n")
    rows_block = text.split("<<<ROWS", 1)[1].split(">>>", 1)[0].strip("\n")

    art = art_block.split("\n")

    rows = []
    for line in rows_block.split("\n"):
        line = line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if line == "BLANK":
            rows.append(("blank",))
        elif line == "STATS_REPOS":
            rows.append(("stats_repos",))
        elif line == "STATS_COMMITS":
            rows.append(("stats_commits",))
        elif line == "STATS_LOC":
            rows.append(("stats_loc",))
        elif line.startswith("HEADER:"):
            rows.append(("header", line.split(":", 1)[1].strip()))
        elif line.startswith("SECTION:"):
            rows.append(("section", line.split(":", 1)[1].strip()))
        elif line.startswith("SITE:"):
            rows.append(("site", line.split(":", 1)[1].strip()))
        elif line.startswith("KV:"):
            key, _, value = line[3:].partition("|")
            rows.append(("kv", key.strip(), value.strip()))
        else:
            raise ValueError(f"card_template.txt: unrecognized line: {line!r}")
    return art, rows


# -------------------------------------------------------------- GitHub API

def gh_rest(path):
    req = urllib.request.Request(f"https://api.github.com{path}")
    req.add_header("Accept", "application/vnd.github+json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read())


def gh_rest_status(path):
    """Like gh_rest but also returns the HTTP status (some endpoints reply
    202 while GitHub computes stats in the background)."""
    req = urllib.request.Request(f"https://api.github.com{path}")
    req.add_header("Accept", "application/vnd.github+json")
    if TOKEN:
        req.add_header("Authorization", f"Bearer {TOKEN}")
    try:
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
            body = resp.read()
            return resp.status, (json.loads(body) if body else None)
    except urllib.error.HTTPError as e:
        return e.code, None
    except urllib.error.URLError:
        return None, None


def fetch_owned_repos():
    """Uses the authenticated /user/repos endpoint (not /users/{username}/repos)
    so private repos are included too — requires GH_TOKEN to carry `repo` scope
    (a fine-grained PAT with Contents: read, or classic PAT with `repo`), not
    just `read:user`. Falls back to public-only listing if no token is set."""
    repos = []
    page = 1
    path_base = "/user/repos?affiliation=owner" if TOKEN else f"/users/{USERNAME}/repos"
    sep = "&" if "?" in path_base else "?"
    while True:
        batch = gh_rest(f"{path_base}{sep}per_page=100&page={page}")
        if not batch:
            break
        repos.extend(batch)
        page += 1
    return [r for r in repos if not r["fork"]]


def fetch_language_breakdown(owned):
    bytes_by_lang = Counter()
    for repo in owned:
        try:
            langs = gh_rest(f"/repos/{USERNAME}/{repo['name']}/languages")
        except Exception:
            continue
        for lang, n in langs.items():
            bytes_by_lang[lang] += n

    programming = [l for l in bytes_by_lang if l in PROGRAMMING_LANGS]
    computer = [l for l in bytes_by_lang if l not in PROGRAMMING_LANGS]
    rank = lambda langs: sorted(langs, key=lambda l: -bytes_by_lang[l])
    return {
        "programming": ", ".join(rank(programming)[:4]) or "Python, Go, Shell",
        "computer": ", ".join(rank(computer)[:4]) or "HTML, YAML, JSON",
    }


def fetch_commit_and_loc_stats(owned, max_seconds=60):
    """Sums commits/additions/deletions attributed to USERNAME via the
    per-repo contributor-stats endpoint (GitHub aggregates this server-side,
    so no manual commit walking / diff caching needed). This is used instead
    of GraphQL's contributionsCollection because that field silently drops
    private-repo activity into `restrictedContributionsCount` whenever the
    account has "include private contributions" turned off in profile
    settings — even for the owner's own authenticated query. Iterating
    /repos/{owner}/{repo}/stats/contributors isn't gated by that setting.
    That endpoint computes stats lazily and can return 202 while it works —
    poll briefly, and bail out entirely once max_seconds elapses so a slow
    repo can't hang the run."""
    total_commits = total_add = total_del = 0
    contributed_repos = 0
    start = time.monotonic()
    for repo in owned:
        if time.monotonic() - start > max_seconds:
            break
        path = f"/repos/{USERNAME}/{repo['name']}/stats/contributors"
        data = None
        for _ in range(3):
            status, data = gh_rest_status(path)
            if status == 202:
                time.sleep(1.5)
                continue
            break
        if not data:
            continue
        for entry in data:
            if entry.get("author", {}).get("login") != USERNAME:
                continue
            repo_commits = sum(w.get("c", 0) for w in entry.get("weeks", []))
            if repo_commits:
                contributed_repos += 1
            total_commits += repo_commits
            for week in entry.get("weeks", []):
                total_add += week.get("a", 0)
                total_del += week.get("d", 0)
    return total_commits, total_add, total_del, contributed_repos


def age_string(birth, today):
    years = today.year - birth.year
    months = today.month - birth.month
    days = today.day - birth.day
    if days < 0:
        months -= 1
        prev_month = today.month - 1 or 12
        prev_year = today.year if today.month > 1 else today.year - 1
        days_in_prev = (date(prev_year, prev_month % 12 + 1, 1) - date(prev_year, prev_month, 1)).days
        days += days_in_prev
    if months < 0:
        years -= 1
        months += 12
    return f"{years} years, {months} months, {days} days"


def fmt_num(n):
    return f"{n:,}" if isinstance(n, int) else n


def fetch_stats():
    user = gh_rest(f"/users/{USERNAME}")
    owned = fetch_owned_repos()
    stars = sum(r["stargazers_count"] for r in owned)
    langs = fetch_language_breakdown(owned)
    if TOKEN:
        commits, loc_add, loc_del, contributed_repos = fetch_commit_and_loc_stats(owned)
    else:
        commits, loc_add, loc_del, contributed_repos = ("n/a", 0, 0, "n/a")

    return {
        "repos": fmt_num(len(owned)),
        "stars": fmt_num(stars),
        "followers": fmt_num(user["followers"]),
        "langs_programming": langs["programming"],
        "langs_computer": langs["computer"],
        "commits": fmt_num(commits),
        "contributed_repos": fmt_num(contributed_repos),
        "uptime": age_string(BIRTHDATE, date.today()),
        "loc_total": fmt_num(loc_add - loc_del),
        "loc_add": fmt_num(loc_add),
        "loc_del": fmt_num(loc_del),
    }


# -------------------------------------------------------------------- SVG

def dots(key, target=30):
    return "." * max(3, target - len(key))


DIVIDER_TOTAL = 69  # header/section divider lines all end at this column


def divider_dashes(prefix):
    return "-" * max(3, DIVIDER_TOTAL - len(prefix))


THEMES = {
    "dark": {
        "bg": "#161b22", "text": "#c9d1d9", "key": "#ffa657",
        "value": "#a5d6ff", "cc": "#616e7f", "art": "#c9d1d9",
        "add": "#3fb950", "del": "#f85149",
    },
    "light": {
        "bg": "#ffffff", "text": "#24292f", "key": "#953800",
        "value": "#0969da", "cc": "#8c959f", "art": "#24292f",
        "add": "#1a7f37", "del": "#cf222e",
    },
}

ART_X = 15
ART_MARGIN_RIGHT = 20  # gap kept clear before the text column starts
COL_X, TXT_Y0, ROW_H = 390, 30, 20
FONT = "ConsolasFallback,Consolas,monospace"
FONT_SIZE = 16
CHAR_W = FONT_SIZE * 0.6  # approx monospace advance width


def stats_repos_text(values):
    return (
        f". Repos:{dots('Repos', 10)} {values['repos']} "
        f"(Contributed: {values['contributed_repos']}) | "
        f"Stars:{dots('Stars', 16)} {values['stars']}"
    )


def stats_commits_text(values):
    return (
        f". Commits:{dots('Commits', 25)} {values['commits']} | "
        f"Followers:{dots('Followers', 16)} {values['followers']}"
    )


def stats_loc_text(values):
    return (
        f". Lines of Code on GitHub:{'..'} "
        f"{values['loc_total']} ( {values['loc_add']}++, {values['loc_del']}-- )"
    )


def row_plain_text(row, values):
    kind = row[0]
    if kind == "header":
        prefix = f"{row[1]} "
        return prefix + divider_dashes(prefix)
    if kind == "section":
        prefix = f"- {row[1]} "
        return prefix + divider_dashes(prefix)
    if kind == "site":
        return row[1]
    if kind == "kv":
        _, key, template = row
        value = template.format(**values) if "{" in template else template
        return f". {key}: {dots(key)} {value}"
    if kind == "stats_repos":
        return stats_repos_text(values)
    if kind == "stats_commits":
        return stats_commits_text(values)
    if kind == "stats_loc":
        return stats_loc_text(values)
    return ""


def render(theme_name, art, rows, values):
    t = THEMES[theme_name]
    text_content_height = len(rows) * ROW_H
    height = TXT_Y0 + text_content_height + 20
    longest_line = max((len(row_plain_text(r, values)) for r in rows), default=0)
    width = max(985, int(COL_X + longest_line * CHAR_W) + 30)

    art_font_size = FONT_SIZE  # same size as the text column, just repositioned
    art_row_h = art_font_size * 1.15
    art_char_w = art_font_size * 0.6
    art_longest = max((len(line) for line in art), default=1)
    art_col_w = COL_X - ART_X - ART_MARGIN_RIGHT

    art_content_height = len(art) * art_row_h
    art_y0 = max(TXT_Y0, (height - art_content_height) / 2)
    art_x0 = ART_X + max(0, (art_col_w - art_longest * art_char_w) / 2)

    body = [f'<rect width="{width}px" height="{height}px" fill="{t["bg"]}"/>']

    body.append(f'<text x="{art_x0}" y="{art_y0}" fill="{t["art"]}" font-size="{art_font_size}px">')
    for i, line in enumerate(art):
        body.append(f'<tspan x="{art_x0}" y="{art_y0 + i*art_row_h:.2f}" xml:space="preserve">{escape(line)}</tspan>')
    body.append("</text>")

    body.append(f'<text x="{COL_X}" y="{TXT_Y0}" fill="{t["text"]}">')
    for i, row in enumerate(rows):
        y = TXT_Y0 + i * ROW_H
        kind = row[0]
        if kind == "blank":
            continue
        if kind == "header":
            prefix = f"{row[1]} "
            body.append(f'<tspan x="{COL_X}" y="{y}">{escape(row[1])}</tspan> ' + divider_dashes(prefix))
        elif kind == "section":
            prefix = f"- {row[1]} "
            body.append(f'<tspan x="{COL_X}" y="{y}">- {escape(row[1])}</tspan> ' + divider_dashes(prefix))
        elif kind == "site":
            body.append(f'<tspan x="{COL_X}" y="{y}" fill="{t["value"]}">{escape(row[1])}</tspan>')
        elif kind == "kv":
            _, key, template = row
            value = template.format(**values) if "{" in template else template
            body.append(
                f'<tspan x="{COL_X}" y="{y}" fill="{t["cc"]}">. </tspan>'
                f'<tspan fill="{t["key"]}">{escape(key)}</tspan>:'
                f'<tspan fill="{t["cc"]}">{escape(dots(key))} </tspan>'
                f'<tspan fill="{t["value"]}">{escape(value)}</tspan>'
            )
        elif kind == "stats_repos":
            body.append(
                f'<tspan x="{COL_X}" y="{y}" fill="{t["cc"]}">. </tspan>'
                f'<tspan fill="{t["key"]}">Repos</tspan>:'
                f'<tspan fill="{t["cc"]}">{escape(dots("Repos", 10))} </tspan>'
                f'<tspan fill="{t["value"]}">{escape(values["repos"])}</tspan>'
                f'<tspan fill="{t["cc"]}"> (Contributed: </tspan>'
                f'<tspan fill="{t["value"]}">{escape(values["contributed_repos"])}</tspan>'
                f'<tspan fill="{t["cc"]}">) | </tspan>'
                f'<tspan fill="{t["key"]}">Stars</tspan>:'
                f'<tspan fill="{t["cc"]}">{escape(dots("Stars", 16))} </tspan>'
                f'<tspan fill="{t["value"]}">{escape(values["stars"])}</tspan>'
            )
        elif kind == "stats_commits":
            body.append(
                f'<tspan x="{COL_X}" y="{y}" fill="{t["cc"]}">. </tspan>'
                f'<tspan fill="{t["key"]}">Commits</tspan>:'
                f'<tspan fill="{t["cc"]}">{escape(dots("Commits", 25))} </tspan>'
                f'<tspan fill="{t["value"]}">{escape(values["commits"])}</tspan>'
                f'<tspan fill="{t["cc"]}"> | </tspan>'
                f'<tspan fill="{t["key"]}">Followers</tspan>:'
                f'<tspan fill="{t["cc"]}">{escape(dots("Followers", 16))} </tspan>'
                f'<tspan fill="{t["value"]}">{escape(values["followers"])}</tspan>'
            )
        elif kind == "stats_loc":
            label = "Lines of Code on GitHub"
            body.append(
                f'<tspan x="{COL_X}" y="{y}" fill="{t["cc"]}">. </tspan>'
                f'<tspan fill="{t["key"]}">{label}</tspan>:'
                f'<tspan fill="{t["cc"]}">{escape("..")} </tspan>'
                f'<tspan fill="{t["value"]}">{escape(values["loc_total"])}</tspan>'
                f'<tspan fill="{t["cc"]}"> ( </tspan>'
                f'<tspan fill="{t["add"]}">{escape(values["loc_add"])}++</tspan>'
                f'<tspan fill="{t["cc"]}">, </tspan>'
                f'<tspan fill="{t["del"]}">{escape(values["loc_del"])}--</tspan>'
                f'<tspan fill="{t["cc"]}"> )</tspan>'
            )
    body.append("</text>")

    svg = (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        f'<svg xmlns="http://www.w3.org/2000/svg" font-family="{FONT}" '
        f'width="{width}px" height="{height}px" font-size="{FONT_SIZE}px">'
        + "".join(body) + "</svg>"
    )
    return svg


def main():
    art, rows = parse_template(TEMPLATE_PATH)
    values = fetch_stats()
    for theme in THEMES:
        svg = render(theme, art, rows, values)
        with open(f"{theme}_mode.svg", "w") as f:
            f.write(svg)
    print("wrote dark_mode.svg / light_mode.svg with", values)


if __name__ == "__main__":
    main()
