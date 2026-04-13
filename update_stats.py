#!/usr/bin/env python3
"""
Generate a stats SVG for the GitHub profile README.
Uses authenticated gh CLI to count ALL commits (public + private).
Run: python update_stats.py
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta


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


def get_all_repos():
    """Fetch all repos with commit counts (handles pagination)."""
    repos = []
    has_next = True
    cursor = ""
    while has_next:
        after = f', after: "{cursor}"' if cursor else ""
        data = gh_graphql(f'''
        {{
          viewer {{
            repositories(first: 100, ownerAffiliations: OWNER{after}) {{
              totalCount
              pageInfo {{ hasNextPage endCursor }}
              nodes {{
                name
                isPrivate
                defaultBranchRef {{
                  target {{
                    ... on Commit {{
                      history {{ totalCount }}
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


def get_year_commits():
    """Count commits in the last 12 months across all repos."""
    since = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00Z")
    repos = []
    has_next = True
    cursor = ""
    while has_next:
        after = f', after: "{cursor}"' if cursor else ""
        data = gh_graphql(f'''
        {{
          viewer {{
            repositories(first: 100, ownerAffiliations: OWNER{after}) {{
              pageInfo {{ hasNextPage endCursor }}
              nodes {{
                name
                isPrivate
                defaultBranchRef {{
                  target {{
                    ... on Commit {{
                      history(since: "{since}") {{ totalCount }}
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


def generate_svg(total_commits, year_commits, total_repos, private_repos, public_repos):
    """Generate a compact dark-themed stats SVG — numbers only, no repo names."""
    card_height = 105

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="495" height="{card_height}" viewBox="0 0 495 {card_height}">
  <rect width="495" height="{card_height}" rx="6" fill="#0d1117" stroke="#30363d" stroke-width="1"/>

  <!-- Title -->
  <text x="25" y="30" fill="#58a6ff" font-size="16" font-weight="600" font-family="Segoe UI, sans-serif">Deploy Stats</text>
  <text x="25" y="48" fill="#8b949e" font-size="11" font-family="Segoe UI, sans-serif">all repos (public + private) · updated {datetime.now().strftime("%b %d, %Y")}</text>

  <!-- Stats row -->
  <g transform="translate(25, 80)">
    <g>
      <text x="0" y="0" fill="#c9d1d9" font-size="26" font-weight="700" font-family="Segoe UI, sans-serif">{total_commits:,}</text>
      <text x="0" y="16" fill="#8b949e" font-size="11" font-family="Segoe UI, sans-serif">total commits</text>
    </g>
    <g transform="translate(150, 0)">
      <text x="0" y="0" fill="#c9d1d9" font-size="26" font-weight="700" font-family="Segoe UI, sans-serif">{year_commits:,}</text>
      <text x="0" y="16" fill="#8b949e" font-size="11" font-family="Segoe UI, sans-serif">last 12 months</text>
    </g>
    <g transform="translate(310, 0)">
      <text x="0" y="0" fill="#c9d1d9" font-size="26" font-weight="700" font-family="Segoe UI, sans-serif">{total_repos}</text>
      <text x="0" y="16" fill="#8b949e" font-size="11" font-family="Segoe UI, sans-serif">repos ({public_repos} public · {private_repos} private)</text>
    </g>
  </g>
</svg>'''
    return svg


def main():
    print("Fetching all-time repo data...")
    all_repos = get_all_repos()

    print("Fetching last-12-month commits...")
    year_repos = get_year_commits()

    # All-time totals
    total_commits = 0
    total_repos = len(all_repos)
    private_repos = 0
    public_repos = 0
    for r in all_repos:
        ref = r.get("defaultBranchRef")
        if ref and ref.get("target"):
            total_commits += ref["target"]["history"]["totalCount"]
        if r["isPrivate"]:
            private_repos += 1
        else:
            public_repos += 1

    # Year commits + top repos
    year_commits = 0
    top = []
    for r in year_repos:
        ref = r.get("defaultBranchRef")
        if ref and ref.get("target"):
            count = ref["target"]["history"]["totalCount"]
            year_commits += count
            if count > 0:
                top.append((r["name"], count, r["isPrivate"]))
    top.sort(key=lambda x: x[1], reverse=True)

    print(f"Total commits (all time): {total_commits:,}")
    print(f"Commits (last 12 months): {year_commits:,}")
    print(f"Repos: {total_repos} ({public_repos} public, {private_repos} private)")
    print(f"Top repos: {', '.join(f'{n}({c})' for n, c, _ in top[:6])}")

    svg = generate_svg(total_commits, year_commits, total_repos, private_repos, public_repos)

    out_path = "stats.svg"
    with open(out_path, "w") as f:
        f.write(svg)
    print(f"Wrote {out_path}")


if __name__ == "__main__":
    main()
