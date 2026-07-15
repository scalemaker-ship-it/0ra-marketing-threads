# 0ra_marketing 스레드 자동 게시

**병원마케팅·의료광고법** 채널의 스레드(Threads) 글을 매일 오후 9시(KST, 월~토)에 자동 생성·게시한다.

- 글쓰기 규칙: [`스레드_자동화_가이드.md`](스레드_자동화_가이드.md) (훅 6종·5블록 구조·가독성·의료광고법·표절 금지)
- 오산디에스치과 스레드 자동화와는 **완전히 별개**의 프로젝트(다른 계정·다른 컨셉).

## 동작

1. 요일별 소재 선택 (가이드 §9 캘린더)
   | 월 | 화 | 수 | 목 | 금 | 토 |
   |---|---|---|---|---|---|
   | 의료광고법 실수 TOP5 | 블로그·플레이스 상위노출 | 대행사 고르는 법 | 사전심의 기준·사례집 | 환자가 안 오는 이유 | 마케팅 비유·인사이트 |
2. Claude(`claude-opus-4-8`)로 글 생성 → JSON(`main` / `thread_chain` / `first_comment`)
3. 의료광고법 금지어 필터(§6) → 걸리면 재생성 (최대 4회)
4. 게시: `main` 발행 → `thread_chain` 순차 이어쓰기(자기 답글 체인) → `first_comment` 답글

## 스케줄

- GitHub Actions 크론: `0 12 * * 1-6` (= 21:00 KST, 월~토). 일요일 휴무.
- `workflow_dispatch`로 수동 실행(테스트) 가능.

## 필요한 GitHub Secrets

| 이름 | 설명 |
|---|---|
| `ANTHROPIC_API_KEY` | Claude API 키 |
| `THREADS_USER_ID` | Threads 사용자 ID (0ra_marketing 계정) |
| `THREADS_ACCESS_TOKEN` | Threads 장기 액세스 토큰 (0ra_marketing 계정) |

설정:

```bash
gh secret set ANTHROPIC_API_KEY -R <owner>/<repo>
gh secret set THREADS_USER_ID -R <owner>/<repo>
gh secret set THREADS_ACCESS_TOKEN -R <owner>/<repo>
```

> Threads(Meta Graph API) 장기 토큰은 약 60일마다 만료되므로 주기적 갱신이 필요하다.

## 로컬 실행

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=...
export THREADS_USER_ID=...
export THREADS_ACCESS_TOKEN=...
python threads_post.py
```
