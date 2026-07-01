# 수입지출내역서 자동 작성

학생회 감사 자료용 **수입지출내역서**를 엑셀 장부·연체료/환불 목록·연체료 규정 텍스트에서 자동 생성하는 도구입니다.

- **웹 UI**: 브라우저에서 파일 업로드 → 사업별 HWP/HWPX + 검수목록 zip 다운로드
- **CLI**: `input/` 폴더 + `config/학기설정.json`으로 로컬 일괄 처리

## 요구 사항

| 항목 | 웹 | CLI |
|------|----|-----|
| OS | Windows (HWP 변환 시) | Windows 권장 |
| Python | **3.10+ 64비트 권장** | 동일 |
| 한글(아래아한글) | HWP 업로드·변환 시 필요 | HWP 출력 시 필요 |
| HWPX 양식 | 사용자 업로드 | `templates/수입지출내역서_양식.hwpx` 배치 |

> 32비트 Python에서도 동작하지만, OCR·한글 연동은 64비트 환경을 권장합니다.

## 클라우드 배포 (공개 URL)

다른 사람이 **주소만 입력**해 쓰려면 [Render](https://render.com) 등에 배포합니다.  
자세한 단계: **[docs/DEPLOY.md](docs/DEPLOY.md)**

1. Render 가입 → GitHub 연동
2. **New Blueprint** → `zanmang-lab/audit-income-expense` 선택
3. 배포 완료 후 `https://audit-income-expense.onrender.com` 형태 URL 사용

> 클라우드(Linux)에서는 **hwpx만** 지원합니다. HWP 변환은 Windows 로컬(`run_web.bat`)에서만 됩니다.

## 빠른 시작 (로컬 Windows)

```bat
git clone https://github.com/<사용자명>/<저장소명>.git
cd <저장소명>
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
run_web.bat
```

브라우저에서 http://127.0.0.1:8000 을 엽니다.

서버 상태 확인: http://127.0.0.1:8000/api/health  
(`parser_build: table-format-v2` 이면 최신 연체료 파서)

### 업로드 항목

1. **수입지출 양식** — `.hwpx` 권장 (`.hwp`는 한글+pyhwpx로 자동 변환)
2. **사업개요** — `번호. 사업명` 형식 텍스트
3. **연체료 규정** — `1DAY ₩1,000: 물품명` 형식 텍스트
4. **장부** / **연체료목록** / **환불자목록** — `.xlsx`

결과 zip에는 사업별 문서, `검수목록.txt`, `연체료규정_추출.json` 등이 포함됩니다.

## CLI 사용

```bat
pip install -r requirements.txt
```

1. `templates/수입지출내역서_양식.hwpx` — 한글 양식(치환 토큰 포함) 배치
2. `input/장부.xlsx`, `input/연체료목록.xlsx`, `input/환불자목록.xlsx` 배치
3. `config/학기설정.json` — 학기·사업명·연체료 규칙 등 조정
4. 실행:

```bat
python -m src.main
```

출력: `output/` 폴더

## 환경 변수 (선택)

| 변수 | 용도 |
|------|------|
| `OPENAI_API_KEY` | 연체료 규정 이미지 OCR 실패 시 Vision API 폴백 |

`.env.example` 참고. 일반적으로 RapidOCR(한국어 모델)만으로 충분합니다.

## 트러블슈팅

### 연체료 규정 형식 예시

```
1DAY ₩1,000: 보조배터리
1DAY ₩2,000: 그 외 물품
1DAY ₩3,000: 폴라로이드, 앰프, 듀라테이블
```

### `pyhwpx` / HWP 변환 오류
```bat
pip install "numpy<2"
```
한글이 설치된 Windows에서만 HWP 변환이 됩니다. 불가 시 **hwpx 양식을 직접 업로드**하세요.

### HWP 대신 HWPX만 받는 경우
hwp 변환 실패 시 zip에 hwpx가 포함됩니다. 한글에서 열어 hwp로 저장할 수 있습니다.

## 프로젝트 구조

```
src/           엔진 (장부 파싱, 연체료 계산, hwpx 채우기, OCR)
web/           FastAPI 웹 앱 + UI 템플릿
config/        CLI용 학기 설정 (웹은 업로드만 사용)
docs/SPEC.md   상세 규칙 명세
examples/      연체료 규정 예시 SVG 등
scripts/       검증 스크립트
```

## 라이선스

학내·학생회 내부 사용을 전제로 합니다. 배포 시 조직 정책에 맞게 라이선스를 지정하세요.
