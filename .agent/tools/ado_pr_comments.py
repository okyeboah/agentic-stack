#!/usr/bin/env python3
"""Azure DevOps PR Comment Manager tool for portable brain.

Commands:
    list    List comments/threads on a PR:
            python3 ~/.agent/tools/ado_pr_comments.py list 109 [--file <path>]

    post    Post a new comment thread on a PR:
            python3 ~/.agent/tools/ado_pr_comments.py post 109 --content "..." [--file <path>]

    reply   Reply to an existing thread:
            python3 ~/.agent/tools/ado_pr_comments.py reply 109 --thread-id 387 --content "..."

    update  Update an existing comment in a thread:
            python3 ~/.agent/tools/ado_pr_comments.py update 109 --thread-id 387 --comment-id 1 --content "..."

    delete  Delete an unnecessary comment from a thread:
            python3 ~/.agent/tools/ado_pr_comments.py delete 109 --thread-id 387 --comment-id 2

    resolve Mark a corrected thread as fixed:
            python3 ~/.agent/tools/ado_pr_comments.py resolve 109 --thread-id 387

    triage List comments with local intent triage (change-request / question /
           approval / noise, scored by the laya-mlx local model; read-only):
            python3 ~/.agent/tools/ado_pr_comments.py triage 109 [--file <path>]

PAT resolution order (ado_config):
    1. --pat argument
    2. ADO_PAT environment variable
    3. ADO_PAT_FILE environment variable (path to a token file)
    4. "patFile" in .agents/config/ado.json
    5. conventional token files under the workspace root:
       api/docs/tools/.ado_pat, docs/tools/.ado_pat, .ado_pat
"""
import argparse
import base64
import json
import os
import sys
import urllib.request
import urllib.error

import ado_config

BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

def resolve_pat(custom_pat=None):
    try:
        return ado_config.resolve_pat(custom_pat)
    except ado_config.AdoConfigError as error:
        print(f"Error: {error}", file=sys.stderr)
        sys.exit(1)

def get_auth_header(pat):
    return "Basic " + base64.b64encode(f":{pat}".encode("utf-8")).decode("utf-8")

def make_request(url, auth_header, method="GET", data=None):
    payload = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=payload, method=method)
    req.add_header("Authorization", auth_header)
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8")
        print(f"HTTP Error {e.code}: {e.reason}\n{err_body}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Request Error: {e}", file=sys.stderr)
        sys.exit(1)

def build_threads_url(args, extra_path=""):
    url = f"https://dev.azure.com/{args.org}/{args.project}/_apis/git/repositories/{args.repo}/pullRequests/{args.pr_id}/threads"
    if extra_path:
        url = f"{url}/{extra_path}"
    return f"{url}?api-version=7.0"

def cmd_list(args):
    pat = resolve_pat(args.pat)
    auth = get_auth_header(pat)
    url = build_threads_url(args)
    
    res = make_request(url, auth)
    threads = res.get("value", [])
    
    if args.file:
        target_file = args.file.strip().lower()
        threads = [
            t for t in threads
            if ((t.get("threadContext") or {}).get("filePath") or "").lower() == target_file
        ]
    
    print(f"Found {len(threads)} thread(s) for PR #{args.pr_id}:")
    for t in threads:
        t_id = t.get("id")
        context = t.get("threadContext") or {}
        file_path = context.get("filePath", "GENERAL/TOP_LEVEL")
        comments = t.get("comments") or []
        print(f"\nThread #{t_id} | File: {file_path} | Status: {t.get('status', 'Unknown')}")
        for c in comments:
            c_id = c.get("id")
            author = (c.get("author") or {}).get("displayName", "Unknown")
            content = (c.get("content") or "").strip()
            print(f"  Comment #{c_id} by {author}:")
            for line in content.splitlines():
                print(f"    {line}")

def cmd_post(args):
    pat = resolve_pat(args.pat)
    auth = get_auth_header(pat)
    url = build_threads_url(args)
    
    body = {
        "comments": [
            {
                "parentCommentId": 0,
                "content": args.content,
                "commentType": 1
            }
        ],
        "status": 1
    }
    if args.file:
        ctx = {"filePath": args.file}
        if args.start_line:
            end_l = args.end_line or args.start_line
            ctx["rightFileStart"] = {"line": args.start_line, "offset": 1}
            ctx["rightFileEnd"] = {"line": end_l, "offset": 1}
        body["threadContext"] = ctx
        
    res = make_request(url, auth, method="POST", data=body)
    print(f"Successfully created thread #{res.get('id')} on PR #{args.pr_id}")

def cmd_reply(args):
    pat = resolve_pat(args.pat)
    auth = get_auth_header(pat)
    url = build_threads_url(args, f"{args.thread_id}/comments")
    
    body = {
        "content": args.content,
        "commentType": 1
    }
    res = make_request(url, auth, method="POST", data=body)
    print(f"Successfully added reply #{res.get('id')} to thread #{args.thread_id}")

def cmd_update(args):
    pat = resolve_pat(args.pat)
    auth = get_auth_header(pat)
    url = build_threads_url(args, f"{args.thread_id}/comments/{args.comment_id}")
    
    body = {
        "content": args.content
    }
    res = make_request(url, auth, method="PATCH", data=body)
    print(f"Successfully updated comment #{args.comment_id} on thread #{args.thread_id}")

def cmd_delete(args):
    pat = resolve_pat(args.pat)
    auth = get_auth_header(pat)
    url = build_threads_url(args, f"{args.thread_id}/comments/{args.comment_id}")

    make_request(url, auth, method="DELETE")
    print(f"Successfully deleted comment #{args.comment_id} from thread #{args.thread_id}")

def cmd_resolve(args):
    pat = resolve_pat(args.pat)
    auth = get_auth_header(pat)
    url = build_threads_url(args, str(args.thread_id))

    res = make_request(url, auth, method="PATCH", data={"status": 2})
    print(f"Successfully marked thread #{res.get('id')} as fixed on PR #{args.pr_id}")

def cmd_triage(args):
    """cmd_list plus a local intent classification per comment.

    Read-only routing advice: change-requests float to the top, low-
    confidence rows are marked READ-MANUALLY so a wrong triage can never
    hide a comment. Falls back to the untriaged list when the local model
    is unavailable.
    """
    pat = resolve_pat(args.pat)
    auth = get_auth_header(pat)
    res = make_request(build_threads_url(args), auth)
    threads = res.get("value", [])

    if args.file:
        target_file = args.file.strip().lower()
        threads = [
            t for t in threads
            if ((t.get("threadContext") or {}).get("filePath") or "").lower() == target_file
        ]

    texts, metas = [], []
    for t in threads:
        for c in t.get("comments") or []:
            content = (c.get("content") or "").strip()
            if not content:
                continue
            texts.append(content)
            metas.append((t.get("id"), c.get("id"),
                          (c.get("author") or {}).get("displayName", "Unknown")))

    print(f"Found {len(texts)} comment(s) for PR #{args.pr_id}:")
    if not texts:
        return
    try:
        import laya_comment_triage
        rows = laya_comment_triage.classify(texts, args.threshold)
    except RuntimeError as error:
        print(f"(triage unavailable: {error} — showing untriaged)", file=sys.stderr)
        rows = [{"predicted": "?", "confidence": 0.0, "act": False, "text": t}
                for t in texts]

    order = {"change-request": 0, "question": 1, "approval": 2, "noise": 3, "?": 4}
    paired = sorted(zip(rows, metas),
                    key=lambda pair: (order.get(pair[0]["predicted"], 9),
                                      -pair[0]["confidence"]))
    for row, (t_id, c_id, author) in paired:
        mark = "" if row["act"] else "  [READ-MANUALLY]"
        print(f"\n[{row['predicted']} {row['confidence']:.2f}] Thread #{t_id} "
              f"Comment #{c_id} by {author}{mark}")
        for line in row["text"].splitlines():
            print(f"    {line}")
    try:
        import laya_comment_triage as _lct
        _lct.log({"mode": "ado-triage", "pr": args.pr_id, "count": len(rows),
                  "escalated": sum(1 for r in rows if not r["act"])})
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(description="Azure DevOps PR Comment Manager")
    parser.add_argument("--org", default=ado_config.org_setting(), help="Azure DevOps Organization")
    parser.add_argument("--project", default=ado_config.setting("project"), help="Azure DevOps Project")
    parser.add_argument("--repo", default=ado_config.setting("repository", "api"), help="Azure DevOps Repository name")
    parser.add_argument("--pat", help="Azure DevOps Personal Access Token")
    
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # List command
    list_p = subparsers.add_parser("list", help="List PR threads and comments")
    list_p.add_argument("pr_id", type=int, help="Pull Request ID")
    list_p.add_argument("--file", help="Filter by file path (e.g. /src/DDI.Application/User.cs)")
    list_p.set_defaults(func=cmd_list)
    
    # Post command
    post_p = subparsers.add_parser("post", help="Post a new thread/comment on a PR")
    post_p.add_argument("pr_id", type=int, help="Pull Request ID")
    post_p.add_argument("--content", required=True, help="Comment body")
    post_p.add_argument("--file", help="File path to attach comment thread to")
    post_p.add_argument("--start-line", type=int, help="Start line number in the file")
    post_p.add_argument("--end-line", type=int, help="End line number in the file")
    post_p.set_defaults(func=cmd_post)
    
    # Reply command
    reply_p = subparsers.add_parser("reply", help="Reply to an existing thread")
    reply_p.add_argument("pr_id", type=int, help="Pull Request ID")
    reply_p.add_argument("--thread-id", type=int, required=True, help="Thread ID")
    reply_p.add_argument("--content", required=True, help="Reply comment body")
    reply_p.set_defaults(func=cmd_reply)
    
    # Update command
    update_p = subparsers.add_parser("update", help="Update an existing comment")
    update_p.add_argument("pr_id", type=int, help="Pull Request ID")
    update_p.add_argument("--thread-id", type=int, required=True, help="Thread ID")
    update_p.add_argument("--comment-id", type=int, required=True, help="Comment ID")
    update_p.add_argument("--content", required=True, help="New comment content")
    update_p.set_defaults(func=cmd_update)

    delete_p = subparsers.add_parser("delete", help="Delete an unnecessary comment from a thread")
    delete_p.add_argument("pr_id", type=int, help="Pull Request ID")
    delete_p.add_argument("--thread-id", type=int, required=True, help="Thread ID")
    delete_p.add_argument("--comment-id", type=int, required=True, help="Comment ID")
    delete_p.set_defaults(func=cmd_delete)

    resolve_p = subparsers.add_parser("resolve", help="Mark a corrected thread as fixed")
    resolve_p.add_argument("pr_id", type=int, help="Pull Request ID")
    resolve_p.add_argument("--thread-id", type=int, required=True, help="Thread ID")
    resolve_p.set_defaults(func=cmd_resolve)

    # Triage command
    triage_p = subparsers.add_parser("triage", help="List PR comments with local intent triage")
    triage_p.add_argument("pr_id", type=int, help="Pull Request ID")
    triage_p.add_argument("--file", help="Filter by file path (e.g. /src/DDI.Application/User.cs)")
    triage_p.add_argument("--threshold", type=float, default=0.7,
                          help="confidence below which a comment is marked READ-MANUALLY")
    triage_p.set_defaults(func=cmd_triage)

    args = parser.parse_args()
    args.org, args.project = ado_config.require_target(getattr(args, "org", None), getattr(args, "project", None))
    args.func(args)

if __name__ == "__main__":
    main()
