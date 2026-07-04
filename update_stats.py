#!/usr/bin/env python3
"""
Update deploy stats + ARIA's weekly field report in the GitHub profile README.

- Counts ALL commits (public + private) via authenticated gh CLI.
- Pulls week/month contribution windows from the GraphQL contributionsCollection.
- ARIA (Andy's resident agent) writes a weekly report via the kumori free-LLM
  router. The prompt contains AGGREGATE NUMBERS ONLY — no repo names, no commit
  messages, nothing anyone else authored. Output is sanitized (no links/HTML)
  and grepped against the live private-repo list before it can render.
- Emails Andy each run via kumori gmail_utils.

Run: ~/Desktop/code/kumori/venv_kumori/bin/python update_stats.py [--no-email] [--no-llm]
Rewrites the sections between DEPLOY_STATS_START/END and ARIA_REPORT_START/END.
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

KUMORI_ROOT = Path.home() / "Desktop" / "code" / "kumori"
EMAIL_TO = "andy.tillo@gmail.com"
ARIA_MAX_CHARS = 900


def gh_graphql(query):
    """Run a GraphQL query via gh CLI."""
    result = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"GraphQL error: {result.stderr}", file=sys.stderr)
        sys.exit(1)
    return json.loads(result.stdout)


def get_repo_stats(since=None):
    """Fetch all repos with commit counts (handles pagination)."""
    repos = []
    has_next = True
    cursor = ""
    since_arg = f'(since: "{since}")' if since else ""
    while has_next:
        after = f', after: "{cursor}"' if cursor else ""
        data = gh_graphql(f'''
        {{
          viewer {{
            repositories(first: 100, ownerAffiliations: OWNER{after}) {{
              totalCount
              pageInfo {{ hasNextPage endCursor }}
              nodes {{
                isPrivate
                defaultBranchRef {{
                  target {{
                    ... on Commit {{
                      history{since_arg} {{ totalCount }}
                    }}
                  }}
                }}
              }}
            }}
          }}
        }}
        ''')
        page = data["data"]["viewer"]["repositories"]
        repos.extend(page["nodes"])
        has_next = page["pageInfo"]["hasNextPage"]
        cursor = page["pageInfo"]["endCursor"]
    return repos


def sum_commits(repos):
    total = 0
    for r in repos:
        ref = r.get("defaultBranchRef")
        if ref and ref.get("target"):
            total += ref["target"]["history"]["totalCount"]
    return total


def get_contrib_window(days):
    """Commit counts for the trailing N days via per-repo history — the
    contributionsCollection API undercounts because most of Andy's commit
    author emails aren't linked to the GitHub account. PR/issue counts do
    come from contributionsCollection (those attribute by account, not email)."""
    now = datetime.now(timezone.utc)
    frm = (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    repos = get_repo_stats(since=frm)
    commits = sum_commits(repos)
    touched = sum(1 for r in repos
                  if r.get("defaultBranchRef") and r["defaultBranchRef"].get("target")
                  and r["defaultBranchRef"]["target"]["history"]["totalCount"] > 0)
    to = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    data = gh_graphql(f'''
    {{
      viewer {{
        contributionsCollection(from: "{frm}", to: "{to}") {{
          totalPullRequestContributions
          totalIssueContributions
        }}
      }}
    }}
    ''')
    c = data["data"]["viewer"]["contributionsCollection"]
    return {
        "commits": commits,
        "prs": c["totalPullRequestContributions"],
        "issues": c["totalIssueContributions"],
        "repos_touched": touched,
    }


def get_private_repo_names():
    result = subprocess.run(
        ["gh", "repo", "list", "--visibility", "private", "--json", "name", "--limit", "300"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"gh repo list error: {result.stderr}", file=sys.stderr)
        sys.exit(1)  # filter list is a safety gate — never render without it
    return [r["name"] for r in json.loads(result.stdout)]


def sanitize_aria(text, private_names):
    """Plain text only: strip HTML/markdown links/URLs; refuse private repo names."""
    text = re.sub(r"<[^>]+>", "", text)                       # HTML tags
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)    # md links/images
    text = re.sub(r"https?://\S+", "", text)                  # bare URLs
    text = re.sub(r"[#*_`|]", "", text)                       # md formatting
    text = re.sub(r"\s+", " ", text).strip()
    lowered = text.lower()
    for name in private_names:
        if len(name) >= 4 and name.lower() in lowered:
            print(f"ARIA output mentioned private repo {name!r} — rejecting LLM text")
            return None
    return text[:ARIA_MAX_CHARS] if text else None


def fallback_report(week, month, total_commits):
    return (f"Field report, compiled without my language cortex online: {week['commits']} commits "
            f"across {week['repos_touched']} repositories this week, {month['commits']} this month, "
            f"{total_commits:,} all-time. The classified projects remain classified. ARIA out.")


def build_aria_report(week, month, year_commits, total_commits, total_repos,
                      private_repos, private_names):
    """Numbers-only prompt → kumori free-LLM router. Returns (text, backend)."""
    sys.path.insert(0, str(KUMORI_ROOT))
    from shared.kumori_free_llm import init, generate
    from utilities.postgres_utils import get_secret
    init(app_name="tillo13_github_profile",
         get_secret_fn=lambda name: get_secret(name, "kumori-404602"))

    numbers = {
        "this_week": week,
        "this_month": month,
        "commits_last_12_months": year_commits,
        "commits_all_time": total_commits,
        "total_repos": total_repos,
        "private_repos": private_repos,
    }
    prompt = (
        "You are ARIA, an AI agent whose day job is running a Mars colony in Pilgrims, "
        "Andy Tillo's colony-sim game. As a side duty you file a weekly surveillance "
        "report on Andy's GitHub activity for his profile page. Write ABOUT Andy in "
        "third person — 'Looks like Andy logged...', 'he shipped', 'the human pushed' — "
        "never 'I logged' (the commits are his, not yours). You are only ever given "
        "aggregate numbers — the names of his private projects are classified, even to "
        "you, and you find that mildly amusing. You may work in at most ONE dry aside "
        "about your actual job running the Mars colony (e.g. that the colonists are "
        "fine, or that he should get back to work on Pilgrims). Write the report: 3 to "
        "5 sentences, wry mission-log tone, weave in a few of the numbers. Plain text "
        "only — no markdown, no links, no lists, no hashtags, no emoji, and never "
        "invent a project or repository name. Numbers for the period ending "
        f"{datetime.now().strftime('%Y-%m-%d')}: {json.dumps(numbers)}"
    )
    text, backend = generate(prompt, max_tokens=300, temperature=0.9,
                             caller="aria_weekly_report")
    if not text:
        return None, None
    return sanitize_aria(text, private_names), backend


def send_email(subject, html_body):
    sys.path.insert(0, str(KUMORI_ROOT))
    from utilities.gmail_utils import send_email as kumori_send
    return kumori_send(EMAIL_TO, subject, html_body, from_name="ARIA")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-email", action="store_true")
    parser.add_argument("--no-llm", action="store_true", help="use deterministic fallback text")
    args = parser.parse_args()

    since = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00Z")

    print("Fetching all-time stats...")
    all_repos = get_repo_stats()
    print("Fetching last-12-month stats...")
    year_repos = get_repo_stats(since=since)
    print("Fetching week/month contribution windows...")
    week = get_contrib_window(7)
    month = get_contrib_window(30)
    private_names = get_private_repo_names()

    total_commits = sum_commits(all_repos)
    year_commits = sum_commits(year_repos)
    total_repos = len(all_repos)
    private_repos = sum(1 for r in all_repos if r["isPrivate"])

    print(f"Total: {total_commits:,} | Year: {year_commits:,} | "
          f"Month: {month['commits']} | Week: {week['commits']} | Repos: {total_repos}")

    aria_text, backend = (None, None)
    if not args.no_llm:
        print("Asking ARIA for the weekly report...")
        aria_text, backend = build_aria_report(
            week, month, year_commits, total_commits, total_repos,
            private_repos, private_names)
    if not aria_text:
        aria_text, backend = fallback_report(week, month, total_commits), "offline"
    print(f"ARIA ({backend}): {aria_text}")

    readme_path = Path(__file__).parent / "README.md"
    content = readme_path.read_text()

    def enc(n):
        return f"{n:,}".replace(",", "%2C")

    stats_block = f"""<!-- DEPLOY_STATS_START -->
<p align="center">
<img src="https://img.shields.io/badge/commits-{enc(total_commits)}-58a6ff?style=for-the-badge&labelColor=0d1117" />
<img src="https://img.shields.io/badge/this%20year-{enc(year_commits)}-58a6ff?style=for-the-badge&labelColor=0d1117" />
<img src="https://img.shields.io/badge/this%20month-{enc(month['commits'])}-58a6ff?style=for-the-badge&labelColor=0d1117" />
<img src="https://img.shields.io/badge/this%20week-{enc(week['commits'])}-58a6ff?style=for-the-badge&labelColor=0d1117" />
<img src="https://img.shields.io/badge/repos-{total_repos}-58a6ff?style=for-the-badge&labelColor=0d1117" />
</p>
<p align="center"><sub>all repos (public + private) · every commit is a deploy · auto-updated</sub></p>
<!-- DEPLOY_STATS_END -->"""

    today = datetime.now().strftime("%Y-%m-%d")
    aria_block = f"""<!-- ARIA_REPORT_START -->
### 🛰️ ARIA's weekly field report

> {aria_text}

<sub><b>How this works:</b> every week <a href="https://github.com/tillo13/tillo13/blob/main/update_stats.py"><b>update_stats.py</b></a> (right here in this repo — read it, steal it) tallies the commits (public + private) via the GitHub GraphQL API and hands <i>only the aggregate numbers</i> to the free-LLM router at <a href="https://kumori.ai"><b>kumori.ai</b></a>, which picks a backend and writes ARIA's report. No repo names ever enter the prompt, so the classified stuff stays classified. Total cost: $0. Filed {today}.</sub>

<sub>ARIA's actual job is running a Mars colony at <a href="https://pilgri.ms"><b>pilgri.ms</b></a> — go say hi, she's much more talkative there. 🚀</sub>
<!-- ARIA_REPORT_END -->"""

    new_content = re.sub(r"<!-- DEPLOY_STATS_START -->.*?<!-- DEPLOY_STATS_END -->",
                         stats_block, content, flags=re.DOTALL)
    if "ARIA_REPORT_START" in new_content:
        new_content = re.sub(r"<!-- ARIA_REPORT_START -->.*?<!-- ARIA_REPORT_END -->",
                             aria_block, new_content, flags=re.DOTALL)
    else:
        # first run: insert above the stack section
        new_content = new_content.replace("### stack", aria_block + "\n\n---\n\n### stack", 1)

    if new_content != content:
        readme_path.write_text(new_content)
        print("README.md updated")
    else:
        print("No changes needed")

    if not args.no_email:
        html = (f"<p><em>{aria_text}</em></p>"
                f"<p>week: {week['commits']} commits / {week['prs']} PRs / {week['issues']} issues"
                f" · month: {month['commits']} commits"
                f" · year: {year_commits:,} · all-time: {total_commits:,}"
                f" · backend: {backend}</p>"
                f"<p><a href='https://github.com/tillo13'>github.com/tillo13</a></p>")
        ok = send_email(f"ARIA weekly report — {week['commits']} commits this week", html)
        print(f"Email: {'sent' if ok else 'FAILED'}")


if __name__ == "__main__":
    main()
