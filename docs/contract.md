# Local contract 1.0

## 입력

실행 요청과 모든 참조 JSON은 요청 파일이 있는 폴더 아래에 둔다.
절대 참조 경로와 폴더 밖 참조는 거부한다. JSON 키 중복·비유한 수·알 수 없는 필드는 거부한다.
모든 예시는 `examples/synthetic/`의 완성본을 사용한다.

| 요청 필드 | 조건 |
| --- | --- |
| `schema_version` | `1.0` |
| `customer_id`, `project_id`, `batch_id`, `request_id` | 영숫자로 시작하는 영숫자·밑줄·하이픈, 최대 64자; Windows 예약어 제외 |
| `metadata`, `dictionary`, `business_definition` | 상대 `path`와 `version`; 문서 내부 판본과 일치 필수 |
| `target` | `provided`, `confirmed`, `path`, `version`; 미제공이면 false, false, null, null |

메타데이터 컬럼은 `system_id + table_id + column_id`로 식별한다. 이름 단독으로 연결하지 않는다.
컬럼에는 `name`, `data_type`이 필요하다. 업무 정의는 같은 식별자에 `concept_id`와 `description`을 연결한다.
개념 ID는 사전과 고객 정의가 명시적으로 공유하는 식별자이며, 이 구현이 자유서술에서 추출하지 않는다.
설명이 있어도 개념 ID가 없으면 해당 용어 추천은 보류한다.

합성 사전은 `origin: synthetic`과 `words`, `terms`, `domains`를 가진다.
단어·용어에는 ID·이름·별칭·개념 ID가 필요하고, 용어는 단어 ID 목록과 도메인 ID를 참조한다.
끊어진 연결·중복 ID는 거부한다. 도메인은 자료형과 제공된 `allowed_values`를 갖는다.
현재 코드값은 문자열 목록만 허용한다. 코드 매핑이나 신규 코드 생성은 하지 않는다.

외부 참조 선택본은 `origin: external_reference`와 같은 배열을 가지며
`binding_version`, `business_bindings`에 명시적 개념 연결 규칙의 판본과 전체 내용을 보존한다.
참조사전에 없는 개념 ID·도메인 자료형은 null일 수 있으며 적용을 보류한다.
원문에 없는 용어–단어 연결은 빈 배열로 두고 추정해 채우지 않는다.
외부 선택본의 등록 상태는 `existing_external_reference`로 표시한다.
이 상태는 최신 공식 배포본 확인이나 업무 승인을 뜻하지 않는다.

Target은 판본과 테이블 목록을 갖는다. 각 테이블의 컬럼은 ID·이름·자료형·제공된 사전 용어 ID를 갖는다.
Target을 제공했어도 미확정이면 매핑 후보를 생성하지 않는다.
동일 용어·자료형에 여러 Target이 있거나 여러 Source가 같은 Target에 연결되면 매핑을 보류한다.

## 출력

저장 JSON 최상위는 `schema_version`, `input_fingerprint`, `input`, `result`이다.
`input`은 요청과 참조 문서 전체 스냅샷이다. 지문은 정렬된 키·고정 구분자를 사용한 JSON의 SHA-256이다.
원본 요청의 파일 참조 경로도 입력 지문에 포함되며, JSON 들여쓰기·객체 키 순서 변경은 지문에 영향을 주지 않는다.
배열 순서나 파일 참조 이름 변경은 다른 입력으로 취급한다.

`result`는 요청 식별정보, 처리 완료 상태, 결과 판본, provenance와 컬럼 결과 목록을 가진다.
provenance에는 입력·사전·규칙 사용 여부, 메타데이터·사전·정의·Target·규칙·구현 판본,
모델 사용 여부와 모델 판본이 들어간다. 모의 응답도 사용하지 않으므로 `mock_used`는 false다.

| 컬럼 결과 | 의미 |
| --- | --- |
| `source` | 원천 식별자·이름·자료형 |
| `word_candidates` | 기존 합성 단어 검색 후보·일치 별칭·개념 검증 근거·보류 사유 |
| `term_candidates` | 기존 합성 용어 후보·연결 도메인·단어 연결·근거·보류 사유 |
| `mapping_candidates` | 확정 Target 판본의 컬럼 후보·동일 용어/자료형 근거 |
| `hold_reasons` | 표준용어 판단 보류 사유 |
| `mapping_hold_reasons` | 표준용어 판단과 별개인 매핑 보류 사유 |
| `scores` | `public_standard`, `business_concept`; 모두 null |
| `review_status` | 항상 `not_reviewed` |
| `approval_eligible`, `execution_eligible` | 항상 false |
| `code_processing` | `reference_only`, 추천 목록은 빈 배열 |

후보별 점수도 null이다. 사전 검색 결과를 LLM 출력으로 표시하지 않는다.
출력 검증은 현재 결정적 규칙으로 결과를 재구성하고 필드·값·타입 전체를 대조한다.
꾸며낸 도메인·Target·점수·모델 사용·승인 상태는 저장 전에 거부한다.
모델 어댑터를 추가할 때는 모델 응답 전용 구조와 참조·의미 검증을 별도로 구현해야 한다.

## 상태·재시도

`recommendation_completed`는 추천 처리가 완료됐다는 뜻이며 후보 확정·승인·데이터 변환 완료를 뜻하지 않는다.
CLI는 성공 시 `saved` 또는 `reused`, 입력·출력 검증/파일 오류 시 `validation_or_storage_error`,
동일 ID 충돌 시 `request_conflict`를 출력한다. 종료 코드는 각각 0, 2, 3이다.
CLI 로그는 원본 입력·개인 경로·인증정보를 출력하지 않는다.

동일 ID 결과가 이미 존재하면 입력 지문과 출력 계약을 검증한 뒤 재사용한다.
입력 변경·처리 판본 변경은 새 요청이 필요하다. 저장된 결과가 훼손됐으면 덮어쓰지 않고 거부한다.
구현 판본 변경 후 과거 출력의 마이그레이션·검증 호환성은 제공하지 않는다.
로컬 결과 발행 후 Backend 수신·SQL 저장·승인 발행의 상태는 이 CLI가 주장하지 않는다.
