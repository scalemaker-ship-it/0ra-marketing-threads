"""Zernio API 로 @0ra_marketing 스레드 글(본문+이어쓰기+첫댓글)을 예약/즉시 발행한다.

사용:
  ZERNIO_API_KEY=sk_... python zernio_schedule.py --list                 # 연결된 계정 확인
  ZERNIO_API_KEY=sk_... python zernio_schedule.py --pinned --at 2026-10-01T20:30   # KST 예약
  ZERNIO_API_KEY=sk_... python zernio_schedule.py --queue-index 3 --at ...
  --now 로 즉시 발행. --dry-run 은 페이로드만 출력.
"""
import argparse, json, os, sys
import requests

BASE = "https://zernio.com/api/v1"
HERE = os.path.dirname(os.path.abspath(__file__))


def headers():
    key = os.environ.get("ZERNIO_API_KEY")
    if not key:
        sys.exit("ZERNIO_API_KEY 환경변수가 필요합니다.")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def threads_account(username="0ra_marketing"):
    r = requests.get(f"{BASE}/accounts", headers=headers(), timeout=30)
    r.raise_for_status()
    for a in r.json().get("accounts", []):
        if a.get("platform") == "threads" and a.get("username") == username:
            return a["_id"]
    sys.exit(f"Zernio 에 Threads 계정 @{username} 이 연결돼 있지 않습니다.")


def build_payload(post, account_id, at=None):
    items = [{"content": post["main"]}] + [{"content": c} for c in post.get("thread_chain", [])]
    data = {"threadItems": items}
    if post.get("first_comment"):
        data["firstComment"] = post["first_comment"]
    body = {
        "content": post["main"],
        "platforms": [{"platform": "threads", "accountId": account_id, "platformSpecificData": data}],
    }
    if at:
        body.update({"scheduledFor": at, "timezone": "Asia/Seoul"})
    else:
        body["publishNow"] = True
    return body


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--pinned", action="store_true")
    ap.add_argument("--queue-index", type=int)
    ap.add_argument("--at", help="KST 예약 시각 예: 2026-10-01T20:30:00")
    ap.add_argument("--now", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    if a.list:
        r = requests.get(f"{BASE}/accounts", headers=headers(), timeout=30)
        for x in r.json().get("accounts", []):
            print(x.get("platform"), x.get("username"), x.get("_id"))
        return

    if a.pinned:
        post = json.load(open(os.path.join(HERE, "pinned_post.json"), encoding="utf-8"))
    elif a.queue_index is not None:
        post = json.load(open(os.path.join(HERE, "posts_queue.json"), encoding="utf-8"))["posts"][a.queue_index]
    else:
        sys.exit("--pinned 또는 --queue-index 를 지정하세요.")
    if not (a.at or a.now):
        sys.exit("--at 또는 --now 를 지정하세요.")

    body = build_payload(post, threads_account(), None if a.now else a.at)
    if a.dry_run:
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return
    r = requests.post(f"{BASE}/posts", headers=headers(), json=body, timeout=60)
    print(r.status_code, r.text[:500])
    r.raise_for_status()


if __name__ == "__main__":
    main()
