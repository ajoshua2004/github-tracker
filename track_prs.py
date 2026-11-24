#!/usr/bin/env python3
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from github import Auth, Github, GithubException

ME = "ajoshua2004"
ORGS = ["podman-container-tools", "containers"]
DATA = Path("data/items")
STAMP = Path("data/timestamp.txt")
TZ = ZoneInfo("America/New_York")
MEETING_WEEKDAY = 2  # Mon=0 ... Wed=2; weeks run Thu -> meeting Wed
QUERIES = [
    f"is:pr author:{ME}",
    f"is:pr reviewed-by:{ME}",
    f"is:pr review-requested:{ME}",
    f"is:pr commenter:{ME}",
    f"is:issue author:{ME}",
    f"is:issue commenter:{ME}",
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


def is_bot(user):
    return user is None or user.type == "Bot" or user.login.endswith("[bot]")


def responses(reviews, *comment_lists):
    """All (time, login) pairs for human reviews and comments."""
    out = [(r.submitted_at, r.user.login) for r in reviews
           if not is_bot(r.user) and r.submitted_at and r.state != "PENDING"]
    for comments in comment_lists:
        out += [(c.created_at, c.user.login) for c in comments if not is_bot(c.user)]
    return out


def my_last_response(resps):
    times = [t for t, who in resps if who == ME]
    return iso(max(times)) if times else None


def last_response(resps):
    if not resps:
        return None
    t, who = max(resps)
    return {"at": iso(t), "by": who}


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
    resps = responses(all_reviews, item.get_comments(), pr.get_review_comments())

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
        "my_last_response": my_last_response(resps),
        "last_response": last_response(resps),
        "reviews": reviews,
        "requested_reviewers": requested,
        "merged_at": iso(pr.merged_at),
    }


def process_issue(item):
    org, repo = item.repository.full_name.split("/")
    resps = responses((), item.get_comments())
    return {
        "type": "issue",
        **base_fields(item, org, repo),
        "state": item.state,
        "state_reason": item.state_reason,  # completed / not_planned / reopened / None
        "my_last_response": my_last_response(resps),
        "last_response": last_response(resps),
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

    def others_reviews(p):
        others = {u: s.replace("_", " ").title() for u, s in p["reviews"].items() if u != ME}
        return ", ".join(f"{u}: {s}" for u, s in others.items()) if others else "No reviews yet"

    def my_involvement(p):
        if p["my_latest_review"]:
            return p["my_latest_review"].replace("_", " ").title()
        if "review_requested" in p["roles"]:
            return "**Review requested**"
        return "Commented"

    def issue_status(i):
        return (i["state_reason"] or "closed").replace("_", " ").title()

    def row(i, status, by=False, kind=None):
        title = i["title"].replace("|", "\\|")
        author = f" by {i['author']}" if by else ""
        mine_resp = fmt(i["my_last_response"]) if i.get("my_last_response") else "—"
        last = i.get("last_response")
        last_resp = f"{fmt(last['at'])} by {last['by']}" if last else "—"
        prefix = f"| {kind} " if kind else ""
        return (f"{prefix}| [{title}]({i['url']}){author} | `{i['repository']}#{i['number']}` "
                f"| {status} | {mine_resp} | {last_resp} |")

    def table(kind, rows):
        if not rows:
            return ["_None_", ""]
        return [f"| {kind} | Repo | Status | My last response | Last response |",
                "|---|---|---|---|---|", *rows, ""]

    def badge(label, count, color):
        return f"![{label}](https://img.shields.io/badge/{label.replace(' ', '_')}-{count}-{color})"

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    def header(title, badges, nav):
        return [
            f"# {title}",
            " ".join(badges),
            "",
            "<br/>",
            "",
            f"PRs and issues I've opened or reviewed across `{'`, `'.join(ORGS)}`. "
            f"Updated hourly on weekdays by GitHub Actions. Last updated **{fmt(now)}** (Eastern).",
            "",
            nav,
            "",
        ]

    # ---- Open -> README.md
    active = [p for p in mine if is_open(p) and not p["draft"]]
    drafts = [p for p in mine if is_open(p) and p["draft"]]
    rev_open = [p for p in reviewing if is_open(p)]
    iss_open = [i for i in issues if is_open(i)]

    out = header(
        f"GitHub tracker for {ME}",
        [badge("My PRs", len(active), "brightgreen"),
         badge("Drafts", len(drafts), "lightgrey"),
         badge("Reviewing", len(rev_open), "orange"),
         badge("Open issues", len(iss_open), "blue")],
        "**Open** · [Closed →](CLOSED.md)",
    )
    for sub_title, kind, rows in [
        ("My Pull Request", "PR", [row(p, others_reviews(p)) for p in active]),
        ("My Draft Pull Request", "PR", [row(p, "Draft") for p in drafts]),
        ("Reviewing Pull Request", "PR", [row(p, my_involvement(p), by=True) for p in rev_open]),
        ("Issue", "Issue", [row(i, "Open", by=i["author"] != ME) for i in iss_open]),
    ]:
        out += [f"## {sub_title} ({len(rows)})", "", *table(kind, rows)]
    Path("README.md").write_text("\n".join(out))

    # ---- Closed -> CLOSED.md, grouped by month -> meeting week, one table per week
    my_closed = [p for p in mine if not is_open(p)]
    rev_closed = [p for p in reviewing if not is_open(p)]
    iss_closed = [i for i in issues if not is_open(i)]

    def week_end(d):
        return d + timedelta(days=(MEETING_WEEKDAY - d.weekday()) % 7)

    def week_month(end):
        days = [end - timedelta(days=n) for n in range(7)]
        y, m = Counter((d.year, d.month) for d in days).most_common(1)[0][0]
        return datetime(y, m, 1).strftime("%B %Y")

    def anchor(i):
        # Week is decided by my last response; fall back to when it closed
        return i.get("my_last_response") or i["closed_at"]

    def local_date(ts):
        return datetime.fromisoformat(ts).astimezone(TZ).date()

    cats = [
        (my_closed, lambda p: row(p, p["state"].title(), kind="My Pull Request")),
        (rev_closed, lambda p: row(p, p["state"].title(), by=True, kind="Reviewing Pull Request")),
        (iss_closed, lambda i: row(i, issue_status(i), by=i["author"] != ME, kind="Issue")),
    ]

    weeks = defaultdict(list)
    for rank, (group, make_row) in enumerate(cats):
        for i in group:
            weeks[week_end(local_date(anchor(i)))].append((rank, i, make_row))

    this_week = week_end(datetime.now(TZ).date())

    out = header(
        f"GitHub tracker for {ME}: closed",
        [badge("Merged", sum(p["state"] == "merged" for p in my_closed), "purple"),
         badge("Reviewed", len(rev_closed), "orange"),
         badge("Closed issues", len(iss_closed), "blue")],
        "[← Open](README.md) · **Closed**",
    )

    shown_month = None
    for end in sorted(weeks, reverse=True):
        month = week_month(end)
        if month != shown_month:
            out += [f"## {month}", ""]
            shown_month = month

        start = end - timedelta(days=6)
        label = f"{start:%b %-d} – {end:%b %-d}" + (" (this week)" if end == this_week else "")

        entries = sorted(weeks[end], key=lambda e: anchor(e[1]), reverse=True)  # newest first
        entries.sort(key=lambda e: e[0])                                       # then group by type
        out += [
            f"### {label}",
            "",
            "| Type | Title | Repo | Status | My last response | Last response |",
            "|---|---|---|---|---|---|",
            *[make_row(i) for _, i, make_row in entries],
            "",
        ]

    Path("CLOSED.md").write_text("\n".join(out))


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