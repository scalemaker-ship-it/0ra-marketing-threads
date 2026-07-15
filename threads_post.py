#!/usr/bin/env python3
"""0ra_marketing 스레드 자동 게시 — 병원마케팅·의료광고법 채널.

`스레드_자동화_가이드.md`의 규칙을 그대로 구현한 스크립트.
매일 오후 9시(KST, 월~토)에 GitHub Actions 크론으로 실행된다.

흐름:
  요일별 소재 선택(가이드 §9) → Claude로 글 생성(가이드 §8 프롬프트, JSON)
  → 의료광고법 금지어 필터(§6) → 통과 시 게시
  → main 발행 → thread_chain 순차 이어쓰기(자기 답글) → first_comment 답글

환경변수(= GitHub Secrets):
  ANTHROPIC_API_KEY     Claude API 키
  THREADS_USER_ID       Threads 사용자 ID
  THREADS_ACCESS_TOKEN  Threads 액세스 토큰
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import anthropic
import requests

KST = ZoneInfo("Asia/Seoul")
MODEL = "claude-opus-4-8"
MAX_GEN_TRIES = 4  # 금지어/파싱 실패 시 재생성 최대 횟수

# 가이드 §8 — 시스템 프롬프트
SYSTEM_PROMPT = """당신은 '병원마케팅·의료광고법' 스레드(Threads) 채널의 콘텐츠 작가입니다.

[역할]
- 병원 원장/마케터/대행사가 공감할 실전 정보를 전달합니다.
- 팔지 않고 알려주며, 팔로우로 연결합니다.

[글쓰기 규칙]
1. 부드러운 반말체로 통일한다.
2. 아래 훅 6종 중 하나로 시작한다:
   소신발언형 / 금지·경고형 / 넘버링 이유형 / 상황·스토리형 / 질문·공감형 / 비교·비유형
3. 구조: [훅 1~2줄] → [전환 1줄] → [본문 3~6줄 또는 넘버링] → [통찰 1~2줄] → [CTA 1~2줄]
4. 한 줄에 한 호흡(약 12~18자)으로 줄바꿈하고, 블록 사이에 빈 줄 1개.
5. 전체 150~400자. 짧을수록 좋다. 정보가 많으면 [이어쓰기]와 [댓글]로 분산한다.
6. 해시태그 금지. 이모지는 0~1개.
7. CTA 예: "잘하고 싶은 사람만 팔로우해줘 / 실전 정보만 남길게"

[의료광고법 준수]
- 금지어 차단: 100%, 완치, 무조건, 최고, 유일, 1등, 부작용 없음, 효과 보장
- 정보 전달형 서술(~할 수 있습니다 / ~인 경우가 많습니다)
- 필요 시 면책 문구 추가

[표절 금지]
- 참고 자료는 '주제·구조 힌트'로만 사용하고, 원문 문장을 절대 복사하지 않는다.
- 본인 관점·현장 경험·구체 수치로 새로 쓴다.
- 참고 원문과 3어절 이상 연속 일치가 없어야 한다.

[출력 형식(JSON만 출력, 다른 텍스트 금지)]
{
  "main": "본문(첫 스레드)",
  "thread_chain": ["2번째 글", "3번째 글"],
  "first_comment": "첫 댓글에 넣을 추가 정보/링크/CTA",
  "hook_type": "사용한 훅 종류",
  "compliance_ok": true
}"""

# 가이드 §9 — 요일별 소재 캘린더 (isoweekday: 월=1 ... 토=6, 일=7)
TOPICS = {
    1: {
        "name": "의료광고법 실수 TOP5",
        "hook": "넘버링 이유형",
        "detail": "병원·의료 마케팅에서 자주 저지르는 의료광고법 위반 실수를 넘버링으로 짚어줘. "
                  "각 항목은 '무엇이 문제인지 → 어떻게 하면 안전한지'가 한 줄로 드러나게.",
    },
    2: {
        "name": "병원 블로그·플레이스 상위노출",
        "hook": "소신발언형",
        "detail": "병원 블로그나 네이버 플레이스가 상위노출 안 되는 진짜 이유, 혹은 되게 만드는 실전 포인트를 "
                  "현장 관점으로 소신 있게 짚어줘.",
    },
    3: {
        "name": "대행사 고르는 법 / 사기 피하기",
        "hook": "금지·경고형",
        "detail": "마케팅 대행사를 고를 때 이런 말/제안을 들으면 계약하지 말라는 식으로, "
                  "병원이 대행사에 당하지 않는 판단 기준을 경고형으로 알려줘.",
    },
    4: {
        "name": "사전심의 대상 기준·사례집 활용",
        "hook": "상황·스토리형",
        "detail": "의료광고 사전심의 대상 기준이나 복지부 사례집 활용법을, 원장/마케터가 실제로 마주치는 상황 "
                  "스토리로 풀어줘.",
    },
    5: {
        "name": "환자가 안 오는 이유 / 전환율",
        "hook": "질문·공감형",
        "detail": "광고비를 써도 환자가 안 오는 이유, 문의는 오는데 예약 전환이 안 되는 이유를 질문·공감형으로 "
                  "던지고 핵심 포인트를 짚어줘.",
    },
    6: {
        "name": "마케팅 비유·인사이트 (가벼운 글)",
        "hook": "비교·비유형",
        "detail": "병원 마케팅을 일상적인 무언가에 비유해서 쉽게 각인시키는 가벼운 인사이트 글을 써줘.",
    },
}

THREADS_API = "https://graph.threads.net/v1.0"

# 가이드 §6 — 금지 표현(자동 차단)
BANNED_PATTERNS = [
    "100%", "100 %", "완치", "무조건", "최고", "유일", "1등", "국내 유일",
    "부작용 없음", "부작용이 없", "반드시 낫", "효과 보장", "효과를 보장",
]


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"[오류] 환경변수 {name} 가 설정되지 않았습니다.")
    return value


def find_banned(text: str) -> list[str]:
    """텍스트에서 발견된 금지 표현 목록을 반환한다."""
    hits = []
    for pat in BANNED_PATTERNS:
        if pat in text:
            hits.append(pat)
    return hits


def _all_text(post: dict) -> str:
    parts = [post.get("main", ""), post.get("first_comment", "")]
    parts.extend(post.get("thread_chain") or [])
    return "\n".join(p for p in parts if p)


def _parse_json(raw: str) -> dict:
    """모델 응답에서 JSON 객체를 최대한 안전하게 파싱한다."""
    raw = raw.strip()
    # 코드펜스 제거
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not m:
            raise
        return json.loads(m.group(0))


def generate_post(topic: dict) -> dict:
    """Claude로 스레드 글(JSON)을 생성한다. 금지어 발견 시 재생성."""
    client = anthropic.Anthropic()  # ANTHROPIC_API_KEY 환경변수 자동 사용
    user_message = (
        f"오늘의 소재: {topic['name']}\n"
        f"추천 훅: {topic['hook']}\n\n"
        f"요청사항: {topic['detail']}\n\n"
        "가이드 규칙을 반드시 지켜서 JSON만 출력해줘."
    )

    last_err = None
    for attempt in range(1, MAX_GEN_TRIES + 1):
        response = client.messages.create(
            model=MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = next((b.text for b in response.content if b.type == "text"), "").strip()
        if not raw:
            last_err = "빈 응답"
            continue
        try:
            post = _parse_json(raw)
        except json.JSONDecodeError as exc:
            last_err = f"JSON 파싱 실패: {exc}"
            continue

        if not post.get("main"):
            last_err = "main 누락"
            continue

        hits = find_banned(_all_text(post))
        if hits:
            print(f"  [재생성 {attempt}] 금지어 발견: {', '.join(hits)}")
            last_err = f"금지어 {hits}"
            continue

        # 정상
        post.setdefault("thread_chain", [])
        post.setdefault("first_comment", "")
        return post

    sys.exit(f"[오류] {MAX_GEN_TRIES}회 시도 후에도 유효한 글 생성 실패: {last_err}")


def _create_container(user_id: str, access_token: str, text: str,
                      reply_to_id: str | None = None) -> str:
    payload = {"media_type": "TEXT", "text": text, "access_token": access_token}
    if reply_to_id:
        payload["reply_to_id"] = reply_to_id
    resp = requests.post(f"{THREADS_API}/{user_id}/threads", json=payload, timeout=30)
    resp.raise_for_status()
    return resp.json()["id"]


def _publish(user_id: str, access_token: str, creation_id: str) -> str:
    resp = requests.post(
        f"{THREADS_API}/{user_id}/threads_publish",
        json={"creation_id": creation_id, "access_token": access_token},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["id"]


def publish_one(user_id: str, access_token: str, text: str,
                reply_to_id: str | None = None, wait: int = 30) -> str:
    """컨테이너 생성 → 대기 → 발행. 게시물 ID 반환."""
    creation_id = _create_container(user_id, access_token, text, reply_to_id)
    time.sleep(wait)  # Threads 권장 대기
    return _publish(user_id, access_token, creation_id)


def post_to_threads(user_id: str, access_token: str, post: dict) -> str:
    """main → thread_chain(자기 답글 체인) → first_comment(답글) 순으로 게시.

    main 게시물 ID를 반환한다.
    """
    main_id = publish_one(user_id, access_token, post["main"])
    print(f"  main 게시 완료: {main_id}")

    # 이어지는 스레드: 직전 글에 답글로 연결해 체인 형성
    prev_id = main_id
    for i, text in enumerate(post.get("thread_chain") or [], start=1):
        text = (text or "").strip()
        if not text:
            continue
        prev_id = publish_one(user_id, access_token, text, reply_to_id=prev_id)
        print(f"  이어쓰기 {i} 게시 완료: {prev_id}")

    # 첫 댓글: main에 답글
    first_comment = (post.get("first_comment") or "").strip()
    if first_comment:
        cid = publish_one(user_id, access_token, first_comment, reply_to_id=main_id)
        print(f"  첫 댓글 게시 완료: {cid}")

    return main_id


def main() -> None:
    require_env("ANTHROPIC_API_KEY")  # SDK가 환경변수로 읽음
    user_id = require_env("THREADS_USER_ID")
    access_token = require_env("THREADS_ACCESS_TOKEN")

    now = datetime.now(KST)
    weekday = now.isoweekday()  # 월=1 ... 일=7
    topic = TOPICS.get(weekday)
    if topic is None:
        print(f"오늘({now:%Y-%m-%d %A})은 게시일이 아닙니다(일요일 휴무). 종료합니다.")
        return

    print(f"[{now:%Y-%m-%d %H:%M KST}] 소재: {topic['name']} (훅: {topic['hook']})")
    post = generate_post(topic)

    print("=== 생성된 글 ===")
    print(post["main"])
    if post.get("thread_chain"):
        for i, t in enumerate(post["thread_chain"], start=1):
            print(f"--- 이어쓰기 {i} ---\n{t}")
    if post.get("first_comment"):
        print(f"--- 첫 댓글 ---\n{post['first_comment']}")
    print(f"[훅: {post.get('hook_type')}]")
    print("=================")

    main_id = post_to_threads(user_id, access_token, post)
    print(f"게시 완료. 메인 Threads 게시물 ID: {main_id}")


if __name__ == "__main__":
    main()
