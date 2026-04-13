#!/usr/bin/env python3
"""
Update deploy stats in the GitHub profile README.
Uses authenticated gh CLI to count ALL commits (public + private).
Rewrites the section between DEPLOY_STATS_START and DEPLOY_STATS_END markers.
Run: python update_stats.py
"""

import json
import re
import subprocess
import sys
from pathlib import Path


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


def main():
    from datetime import datetime, timedelta
    since = (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%dT00:00:00Z")

    print("Fetching all-time stats...")
    all_repos = get_repo_stats()

    print("Fetching last-12-month stats...")
    year_repos = get_repo_stats(since=since)

    total_commits = 0
    total_repos = len(all_repos)
    private_repos = sum(1 for r in all_repos if r["isPrivate"])
    public_repos = total_repos - private_repos
    for r in all_repos:
        ref = r.get("defaultBranchRef")
        if ref and ref.get("target"):
            total_commits += ref["target"]["history"]["totalCount"]

    year_commits = 0
    for r in year_repos:
        ref = r.get("defaultBranchRef")
        if ref and ref.get("target"):
            year_commits += ref["target"]["history"]["totalCount"]

    print(f"Total: {total_commits:,} | Year: {year_commits:,} | Repos: {total_repos}")

    # Rewrite the stats block in README.md
    readme_path = Path(__file__).parent / "README.md"
    content = readme_path.read_text()

    stats_block = f"""<!-- DEPLOY_STATS_START -->
### deploy stats

| | |
|:--|--:|
| **total commits** | **{total_commits:,}** |
| **last 12 months** | **{year_commits:,}** |
| **repos** | **{total_repos}** ({public_repos} public · {private_repos} private) |

<sub>all repos (public + private) · auto-updated on each deploy</sub>
<!-- DEPLOY_STATS_END -->"""

    new_content = re.sub(
        r"<!-- DEPLOY_STATS_START -->.*?<!-- DEPLOY_STATS_END -->",
        stats_block,
        content,
        flags=re.DOTALL
    )

    if new_content != content:
        readme_path.write_text(new_content)
        print("README.md updated")
    else:
        print("No changes needed")


if __name__ == "__main__":
    main()
