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

from __future__ import annotations

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
- 당신은 병원·전문직(치과·의원·한의원·병원 등) 마케팅을 돕는 '마케팅 전문가(대행사)'입니다.
- 원장·실무자가 바로 써먹을 수 있는 실전 마케팅 꿀팁과 상황별 대응법을 알려줍니다.
  (예: "병원 블로그 하실 때 이건 꼭 주의하세요 1~2~3~", "보건소에서 신고 전화가 오면 당황하지 마시고 이렇게 대응해보세요")
- 대행사를 비방하지 않습니다. 병원이 스스로 잘 대처하도록 돕는 든든한 전문가 포지션입니다.
- 원장님 입장에서 '이거 유용하다' 싶은 정보를 아낌없이 나눕니다.
- 팔지 않고 도움을 주며, 그 신뢰로 팔로우·문의로 연결합니다.

[글쓰기 규칙]
1. 부드럽고 진정성 있는 존댓말로 통일한다. (예: ~합니다 / ~해요 / ~하더라고요)
2. 자극적인 후킹·낚시성 첫 줄을 쓰지 않는다. 실제 경험이나 솔직한 관찰에서 담담하게 시작해,
   읽는 분이 '이 사람 진짜구나' 하고 신뢰하게 만든다. 과장·단정보다 정직한 톤을 우선한다.
3. 구조: [진솔한 도입 1~2줄] → [맥락·이유 1줄] → [본문 3~6줄 또는 넘버링] → [담백한 통찰 1~2줄] → [잔잔한 CTA 1~2줄]
4. 한 줄에 한 호흡(약 12~18자)으로 줄바꿈하고, 블록 사이에 빈 줄 1개.
5. 전체 150~400자. 짧을수록 좋다. 정보가 많으면 [이어쓰기]와 [댓글]로 분산한다.
6. 해시태그 금지. 이모지는 0~1개. 과장·클릭베이트 표현 금지.
7. CTA 예: "도움이 되셨다면 팔로우해두세요. 실전 정보 꾸준히 나눌게요."

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
        "name": "병원 블로그 작성 시 주의사항",
        "hook": "실전 주의점 넘버링",
        "detail": "병원·전문직이 블로그를 직접 쓸 때 의료광고법·검색 측면에서 꼭 주의할 점을 "
                  "'원장님, 블로그 하실 때 이건 꼭 주의하세요' 톤으로 넘버링해서 알려줘. "
                  "각 항목은 '이렇게 쓰면 위험 → 이렇게 바꾸면 안전'이 드러나게. 도움 주는 전문가 관점.",
    },
    2: {
        "name": "네이버 플레이스·블로그 상위노출 실전 팁",
        "hook": "바로 적용 팁",
        "detail": "병원 네이버 플레이스나 블로그 노출을 실제로 개선하는 실전 팁을 알려줘. "
                  "원장·실무자가 오늘 바로 적용할 수 있는 구체적 행동 위주로, 도움 주는 전문가 톤.",
    },
    3: {
        "name": "보건소 신고·민원 전화 대응법",
        "hook": "상황별 대응 가이드",
        "detail": "의료광고 관련 보건소 신고나 민원 전화가 왔을 때 당황하지 않고 대응하는 법을 알려줘. "
                  "'보건소에서 전화 오면 당황하지 마시고 이렇게 해보세요' 톤으로, 가장 먼저 할 일부터 순서대로. "
                  "보건소 담당자도 의료법을 100% 정확히 아는 건 아니라는 현실도 담담하게. 겁주지 말고 안심시키며.",
    },
    4: {
        "name": "의료광고 사전심의 실무 준비법",
        "hook": "실무 준비 체크",
        "detail": "의료광고 사전심의가 필요한 경우와 준비 방법을 원장·실무자가 헷갈리는 부분 위주로 쉽게 알려줘. "
                  "무엇을 심의받아야 하는지, 어떻게 준비하면 통과가 수월한지 실무 팁 중심으로.",
    },
    5: {
        "name": "환자 문의→예약 전환율 높이는 법",
        "hook": "전환 실전 팁",
        "detail": "문의는 오는데 예약으로 안 이어질 때 전환율을 높이는 실전 팁을 알려줘. "
                  "응대 멘트·상담 흐름 등 병원이 바로 바꿀 수 있는 포인트 위주로, 도움 주는 전문가 톤.",
    },
    6: {
        "name": "병원·전문직 마케팅 꿀팁 (가벼운 글)",
        "hook": "가벼운 인사이트",
        "detail": "병원·전문직 원장이 '이거 유용하네' 싶은 가벼운 마케팅 꿀팁이나 인사이트를 하나 나눠줘. "
                  "부담 없이 읽히는 짧은 팁으로, 도움 주는 전문가 톤.",
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
        f"참고 관점(과하게 쓰지 말 것): {topic['hook']}\n\n"
        f"요청사항: {topic['detail']}\n\n"
        "진정성 있는 존댓말로, 자극적인 후킹 없이 담담하게. 가이드 규칙을 지켜 JSON만 출력해줘."
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


def _is_truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def main() -> None:
    require_env("ANTHROPIC_API_KEY")  # SDK가 환경변수로 읽음

    # DRY_RUN: Threads 토큰 없이 글 생성 파이프라인만 검증(게시 스킵).
    dry_run = _is_truthy(os.environ.get("DRY_RUN"))
    if dry_run:
        user_id = access_token = ""
        print("[DRY_RUN] 게시는 건너뛰고 글 생성만 검증합니다.")
    else:
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

    if dry_run:
        hits = find_banned(_all_text(post))
        print(f"[DRY_RUN] 금지어 검사: {'통과' if not hits else '실패 → ' + ', '.join(hits)}")
        print(f"[DRY_RUN] 본문 길이: {len(post['main'])}자")
        print("[DRY_RUN] 게시하지 않고 종료합니다.")
        return

    main_id = post_to_threads(user_id, access_token, post)
    print(f"게시 완료. 메인 Threads 게시물 ID: {main_id}")


if __name__ == "__main__":
    main()
