#!/usr/bin/env python3
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from github import Auth, Github, GithubException

ME = "ajoshua2004"
ORGS = ["podman-container-tools", "containers"]
DATA = Path("data/items")
STAMP = Path("data/timestamp.txt")
TZ = ZoneInfo("America/New_York")
QUERIES = [
    f"is:pr author:{ME}",
    f"is:pr reviewed-by:{ME}",
    f"is:pr review-requested:{ME}",
    f"is:pr commenter:{ME}",
    f"is:issue author:{ME}",
]

g = Github(auth=Auth.Token(os.environ["GITHUB_TOKEN"]))


def iso(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def fmt(ts):
    return datetime.fromisoformat(ts).astimezone(TZ).strftime("%b %-d, %Y %-I:%M %p")


def search(since):
    scope = " ".join(f"org:{o}" for o in ORGS)
    hits = {}
    for q in QUERIES:
        for item in g.search_issues(f"{q} {scope} updated:>={since}", sort="updated", order="asc"):
            hits[item.html_url] = item
    return sorted(hits.values(), key=lambda i: i.updated_at)


def latest_reviews(reviews):
    # Mirror GitHub: a COMMENTED review doesn't override APPROVED/CHANGES_REQUESTED
    latest = {}
    for r in reviews:
        if not r.user or r.state == "PENDING":
            continue
        if r.state == "COMMENTED" and r.user.login in latest:
            continue
        latest[r.user.login] = r.state
    return latest


def my_last_response(reviews=(), *comment_lists):
    times = [r.submitted_at for r in reviews if r.user and r.user.login == ME and r.submitted_at]
    for comments in comment_lists:
        times += [c.created_at for c in comments if c.user and c.user.login == ME]
    return iso(max(times)) if times else None


def base_fields(item, org, repo):
    return {
        "organization": org,
        "repository": repo,
        "number": item.number,
        "title": item.title.strip(),
        "url": item.html_url,
        "author": item.user.login,
        "labels": [l.name for l in item.labels],
        "created_at": iso(item.created_at),
        "updated_at": iso(item.updated_at),
        "closed_at": iso(item.closed_at),
    }


def process_pr(item):
    pr = item.as_pull_request()
    org, repo = pr.base.repo.full_name.split("/")
    all_reviews = list(pr.get_reviews())
    reviews = latest_reviews(all_reviews)
    requested_users, _ = pr.get_review_requests()
    requested = [u.login for u in requested_users]

    roles = []
    if pr.user.login == ME:
        roles.append("author")
    if ME in reviews:
        roles.append("reviewer")
    if ME in requested:
        roles.append("review_requested")
    if not roles:
        roles.append("commenter")

    return {
        "type": "pull_request",
        **base_fields(item, org, repo),
        "state": "merged" if pr.merged else pr.state,
        "draft": pr.draft,
        "roles": roles,
        "my_latest_review": reviews.get(ME),
        "my_last_response": my_last_response(all_reviews, item.get_comments(), pr.get_review_comments()),
        "reviews": reviews,
        "requested_reviewers": requested,
        "merged_at": iso(pr.merged_at),
    }


def process_issue(item):
    org, repo = item.repository.full_name.split("/")
    return {
        "type": "issue",
        **base_fields(item, org, repo),
        "state": item.state,
        "state_reason": item.state_reason,  # completed / not_planned / reopened / None
        "my_last_response": my_last_response((), item.get_comments()),
        "comments": item.comments,
    }


def write_pages():
    items = [json.loads(p.read_text()) for p in DATA.glob("*.json")]
    items.sort(key=lambda i: i["updated_at"], reverse=True)
    prs = [i for i in items if i["type"] == "pull_request"]
    issues = [i for i in items if i["type"] == "issue"]

    mine = [p for p in prs if "author" in p["roles"]]
    reviewing = [p for p in prs if "author" not in p["roles"]]
    is_open = lambda i: i["state"] == "open"

    def by_closed(xs):
        return sorted(xs, key=lambda i: i["closed_at"] or "", reverse=True)

    def others_reviews(p):
        others = {u: s.replace("_", " ").title() for u, s in p["reviews"].items() if u != ME}
        return ", ".join(f"{u}: {s}" for u, s in others.items()) if others else "No reviews yet"

    def my_involvement(p):
        if p["my_latest_review"]:
            return p["my_latest_review"].replace("_", " ").title()
        if "review_requested" in p["roles"]:
            return "**Review requested**"
        return "Commented"

    def row(i, status, by=False):
        title = i["title"].replace("|", "\\|")
        author = f" by {i['author']}" if by else ""
        resp = fmt(i["my_last_response"]) if i.get("my_last_response") else "—"
        return f"| [{title}]({i['url']}){author} | `{i['repository']}#{i['number']}` | {status} | {resp} |"

    def table(kind, rows):
        if not rows:
            return ["_None_", ""]
        return [f"| {kind} | Repo | Status | Last response |",
                "|---|---|---|---|", *rows, ""]

    def badge(label, count, color):
        return f"![{label}](https://img.shields.io/badge/{label.replace(' ', '_')}-{count}-{color})"

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def page(path, title, badges, nav, subsections):
        out = [
            f"# {title}",
            " ".join(badges),
            "",
            "<br/>",
            "",
            f"PRs and issues I've opened or reviewed across `{'`, `'.join(ORGS)}`. "
            f"Updated daily by GitHub Actions. Last updated **{fmt(now)}** (Eastern).",
            "",
            nav,
            "",
        ]
        for sub_title, kind, rows in subsections:
            out += [f"## {sub_title} ({len(rows)})", "", *table(kind, rows)]
        Path(path).write_text("\n".join(out))

    # Open -> README.md
    active = [p for p in mine if is_open(p) and not p["draft"]]
    drafts = [p for p in mine if is_open(p) and p["draft"]]
    rev_open = [p for p in reviewing if is_open(p)]
    iss_open = [i for i in issues if is_open(i)]

    page(
        "README.md",
        f"GitHub tracker for {ME}",
        [badge("My PRs", len(active), "brightgreen"),
         badge("Drafts", len(drafts), "lightgrey"),
         badge("Reviewing", len(rev_open), "orange"),
         badge("Open issues", len(iss_open), "blue")],
        "**Open** · [Closed →](CLOSED.md)",
        [("My PRs", "PR", [row(p, others_reviews(p)) for p in active]),
         ("My drafts", "PR", [row(p, "Draft") for p in drafts]),
         ("Reviewing", "PR", [row(p, my_involvement(p), by=True) for p in rev_open]),
         ("My issues", "Issue", [row(i, "Open") for i in iss_open])],
    )

    # Closed -> CLOSED.md
    my_closed = [p for p in by_closed(mine) if not is_open(p)]
    rev_closed = [p for p in by_closed(reviewing) if not is_open(p)]
    iss_closed = [i for i in by_closed(issues) if not is_open(i)]

    page(
        "CLOSED.md",
        f"GitHub tracker for {ME}: closed",
        [badge("Merged", sum(p["state"] == "merged" for p in my_closed), "purple"),
         badge("Reviewed", len(rev_closed), "orange"),
         badge("Closed issues", len(iss_closed), "blue")],
        "[← Open](README.md) · **Closed**",
        [("My PRs", "PR", [row(p, p["state"].title()) for p in my_closed]),
         ("Reviewed", "PR", [row(p, p["state"].title(), by=True) for p in rev_closed]),
         ("My issues", "Issue", [row(i, (i["state_reason"] or "closed").replace("_", " ").title())
                                 for i in iss_closed])],
    )


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    since = STAMP.read_text().strip() if STAMP.exists() else "2020-01-01T00:00:00Z"
    try:
        for item in search(since):
            data = process_pr(item) if item.pull_request else process_issue(item)
            path = DATA / f"{data['organization']}_{data['repository']}_{data['number']}.json"
            path.write_text(json.dumps(data, indent=4, ensure_ascii=False))
            STAMP.write_text(iso(item.updated_at))
            print(f"{data['type']}\t{data['repository']}#{data['number']}\t{data['state']}")
    except GithubException as e:
        print(e)
    write_pages()


if __name__ == "__main__":
    main()
