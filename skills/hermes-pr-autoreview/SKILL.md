---
name: hermes-pr-autoreview
description: "Hermes 자동 PR 리뷰 전용. gh/REST 호출 규약만 제공한다."
version: 1.0.0
author: WIGTN
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [GitHub, Code-Review, Pull-Requests]
---

# Hermes 자동 PR 리뷰 — 호출 규약

이 문서는 **API 사용법만** 담는다.

**무엇을 차단으로 볼지, 요약 코멘트를 어떤 형식으로 쓸지, 어떤 순서로 볼지는
태스크 지시문에 있다. 그쪽이 유일한 기준이다.** 이 문서와 태스크 지시문이
어긋나 보이면 태스크 지시문을 따른다.

여기에 없는 것: 리뷰 체크리스트, 출력 템플릿, 승인/차단 판정 기준.
일부러 뺐다. 태스크 지시문이 갖고 있다.

## 전제

- 워크트리 안에서 실행된다. 이미 대상 브랜치가 체크아웃되어 있다.
- `gh` 는 인증되어 있다 (`wigtn-contact`, `wigtn` org 읽기·쓰기).
- **브랜치를 바꾸거나 지우지 않는다.** `git checkout`, `git branch -D`,
  `git clean`, `git reset --hard` 를 쓰지 않는다. 워크트리는 회수 잡이 관리한다.

```bash
OWNER=wigtn
REPO=<태스크 지시문에 있는 레포>
PR=<태스크 지시문에 있는 번호>
```

## 1. PR 상태와 변경 내용

```bash
# 상태 한 번에
gh api repos/$OWNER/$REPO/pulls/$PR \
  --jq '{sha: .head.sha, state: .state, merged: .merged, draft: .draft,
         base: .base.ref, additions, deletions, changed_files}'

# 바뀐 파일 목록
gh api repos/$OWNER/$REPO/pulls/$PR/files --paginate --jq '.[].filename'

# diff 전문
gh pr diff $PR --repo $OWNER/$REPO
```

diff 만으로 판단하지 않는다. 이 PR 이 바꾸지 않은 파일은 diff 에 없을 뿐
레포에는 있다. 파일·디렉터리 존재 여부는 워크트리에서 직접 확인한다.

## 2. CI 결과 읽기

로컬에서 돌리기 전에 **먼저 이걸 본다.** 대부분의 레포는 PR 에 CI 가 붙어 있고,
CI 결과가 로컬 실행보다 정확하다(그 레포 전용 환경에서 돌기 때문이다).

```bash
# 체크 요약
gh pr checks $PR --repo $OWNER/$REPO

# 실패한 잡의 로그 (실패했을 때만)
gh run view <run-id> --repo $OWNER/$REPO --log-failed | tail -80
```

`no checks reported` 가 나오면 이 레포·이 브랜치에는 PR CI 가 없다는 뜻이다.
그때만 로컬 실행으로 넘어간다.

## 3. 요약 코멘트 — 하나만 유지한다

**새로 만들기 전에 반드시 기존 것을 찾는다.** `gh pr comment` 는 항상 새로
만들기 때문에 그대로 쓰면 코멘트가 쌓인다.

```bash
# 1) 기존 요약 코멘트 id 찾기
CID=$(gh api repos/$OWNER/$REPO/issues/$PR/comments --paginate \
  --jq '[.[] | select(.user.login=="wigtn-contact")
             | select(.body | startswith("<!-- hermes-review -->")) | .id] | last // empty')

# 2) 있으면 수정
if [ -n "$CID" ]; then
  gh api repos/$OWNER/$REPO/issues/comments/$CID --method PATCH -F body=@summary.md
else
  gh api repos/$OWNER/$REPO/issues/$PR/comments --method POST -F body=@summary.md
fi
```

본문 파일은 `-F body=@파일` 로 넘긴다. 본문에 백틱·따옴표가 많아
셸 인용으로는 자주 깨진다.

## 4. 인라인 코멘트와 리뷰 제출

한 번의 호출로 인라인 코멘트와 판정을 함께 낸다. 따로 내면 알림이 두 번 간다.

```bash
SHA=$(gh api repos/$OWNER/$REPO/pulls/$PR --jq '.head.sha')

gh api repos/$OWNER/$REPO/pulls/$PR/reviews --method POST --input - <<JSON
{
  "commit_id": "$SHA",
  "event": "COMMENT",
  "body": "요약 코멘트를 참고해 주세요.",
  "comments": [
    {"path": "src/auth.py", "line": 45, "body": "..."}
  ]
}
JSON
```

- `event` 는 `APPROVE` · `REQUEST_CHANGES` · `COMMENT` 중 하나.
  **어느 것을 낼지는 태스크 지시문의 기준을 따른다.**
- `line` 은 **diff 에 포함된 라인**이어야 한다. 아니면 422 가 난다.
  삭제된 줄을 가리키려면 `"side": "LEFT"`.
- `commit_id` 는 방금 조회한 head SHA.
- 리뷰 `body` 는 한 줄로 둔다. 상세는 요약 코멘트에 있다.

## 5. 자주 나오는 실패

| 증상 | 원인과 대처 |
|---|---|
| `422 line must be part of the diff` | diff 밖의 줄을 가리켰다. 파일 단위 코멘트로 낮추거나 diff 안의 줄로 옮긴다 |
| 자기 PR 에 `APPROVE` 거부 | 작성자는 자기 PR 을 승인할 수 없다. `COMMENT` 로 내리고 사유를 요약에 적는다 |
| `no checks reported` | 이 레포에 PR CI 가 없다. 로컬 실행으로 넘어간다 |
| 닫히거나 머지된 PR 에 리뷰 거부 | 정식 리뷰 대신 요약 코멘트만 남기고, 왜 판정을 못 냈는지 적는다 |
| `gh: not found` | 로그인 셸로 재시도: `bash -lc "gh ..."` |

## 6. 하지 않는 것

- 코드를 고치지 않는다. 커밋·푸시하지 않는다.
- 새 PR·새 브랜치를 만들지 않는다.
- `gh pr ready` 로 draft 를 풀지 않는다.
- 리뷰어를 지정하지 않는다.
- 워크트리를 정리하지 않는다.
