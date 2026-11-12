# Windows PowerShell 실행

프로젝트 폴더에서 실행한다. Python 3.13 실행파일의 실제 설치 위치를 확인한 환경용 명령이다.
외부 패키지 설치나 가상환경 활성화는 필요하지 않다.

```powershell
$PythonExecutable = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
if (-not (Test-Path -LiteralPath $PythonExecutable)) {
    throw '확인된 Python 실행파일이 없습니다.'
}
& $PythonExecutable --version
& $PythonExecutable -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) { throw '테스트 실패' }
& $PythonExecutable -m maps_ai_pipeline --request examples/synthetic/request-v1.json --output local-results
if ($LASTEXITCODE -ne 0) { throw '첫 요청 실패' }
& $PythonExecutable -m maps_ai_pipeline --request examples/synthetic/request-v2.json --output local-results
if ($LASTEXITCODE -ne 0) { throw '업무 정의 변경 요청 실패' }
& $PythonExecutable -m maps_ai_pipeline --request examples/synthetic/request-no-target.json --output local-results
if ($LASTEXITCODE -ne 0) { throw 'Target 미제공 요청 실패' }
& $PythonExecutable -m maps_ai_pipeline --request examples/synthetic/request-v1.json --output local-results
if ($LASTEXITCODE -ne 0) { throw '동일 요청 재실행 실패' }
Get-ChildItem -LiteralPath local-results -Recurse -File -Filter '*.json'
```

새 결과 폴더에서는 첫 세 요청이 `saved`, 마지막 동일 요청이 `reused`다.
이미 실행한 폴더에서는 동일한 모든 요청이 `reused`다.
기본 요청은 네 컬럼 중 세 컬럼에 용어·매핑 후보가 생기고 한 컬럼은 보류한다.
업무 정의를 바꾼 요청은 용어 후보 세 컬럼·매핑 후보 두 컬럼이다.
Target 미제공 요청은 용어 후보 세 컬럼·매핑 후보 영 컬럼이다.
이 개수는 합성 흐름의 관찰 결과이며 정확도 지표가 아니다.

결과 파일은 요청 ID별로 보존된다. 합성 참조자료를 수정한 뒤 같은 ID로 실행하면 충돌한다.
정상적인 재추천은 판본·요청 ID를 새로 지정한 별도 요청을 사용한다.
기존 예시·결과를 수정하거나 삭제할 필요 없이 자동 테스트가 임시 폴더에서 충돌·잘못된 입력을 검증한다.
