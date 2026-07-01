# 클라우드 배포 (공개 URL)

GitHub 저장소를 **Render**에 연결하면 `https://xxxx.onrender.com` 형태의 주소로 누구나 접속할 수 있습니다.

## 1. Render에서 배포 (권장)

### Blueprint (자동)

1. [Render](https://render.com) 가입 → **GitHub 연동**
2. [New Blueprint](https://dashboard.render.com/blueprint/new) → `zanmang-lab/audit-income-expense`
3. **Apply** → 배포 로그에서 **Live** 될 때까지 대기 (5~10분)
4. 대시보드에 표시된 URL로 접속 (예: `https://audit-income-expense.onrender.com`)

### 수동 생성 (Blueprint가 안 될 때)

1. **New +** → **Web Service**
2. 저장소 `audit-income-expense` 연결
3. 설정:
   - **Language**: Python 3
   - **Build Command**: `pip install --upgrade pip && pip install -r requirements-cloud.txt`
   - **Start Command**: `python -m uvicorn web.app:app --host 0.0.0.0 --port $PORT`
   - **Health Check Path**: `/api/health`
4. **Create Web Service**

### "Not Found" 가 뜰 때

| 원인 | 확인 방법 |
|------|-----------|
| 배포 실패·진행 중 | Render 대시보드 → **Logs** 탭에서 빨간 오류 확인 |
| 잘못된 URL | 대시보드 상단 **실제 URL** 복사 (이름이 다를 수 있음) |
| 서비스 없음 | Web Service가 생성됐는지 확인 (Static Site 아님) |
| 슬립 해제 중 | 무료 플랜 첫 접속 30초~1분 대기 후 새로고침 |

정상이면 https://<서비스명>.onrender.com/api/health 에 JSON이 보입니다.

```json
{"status":"ok","parser_build":"table-format-v2","ocr_package":true,"hwp_supported":false,...}
```

`ocr_package`이 `true`이면 OCR 패키지는 설치된 상태입니다. (모델은 첫 이미지 업로드 시 로드)

### HTTP 502

| 원인 | 대응 |
|------|------|
| 메모리 부족 (무료 512MB) | Render **Starter** 플랜($7/월)으로 업그레이드 |
| 배포 진행 중 | Logs에서 **Live** 될 때까지 5~10분 대기 |
| Docker 빌드 실패 | Logs의 빨간 오류 확인 후 GitHub 최신 push 반영 |

502가 계속되면 Render 대시보드 → **Logs** 탭 내용을 확인하세요.

### 무료 플랜 참고
- 15분 미사용 시 슬립 → 첫 접속 시 30초~1분 정도 걸릴 수 있음
- **HWP 변환 불가** (Linux 서버) → **hwpx 양식 업로드**, 결과도 **hwpx**로 zip에 포함
- RAM이 부족하면 Render **Starter** 플랜으로 올리기

## 2. 로컬 Windows (HWP 지원)

한글·pyhwpx가 필요하면 각 PC에서 `run_web.bat` 실행 → `http://127.0.0.1:8000`

## 3. 환경 변수 (선택)

| 변수 | 설명 |
|------|------|
| `OPENAI_API_KEY` | 연체료 이미지 OCR 실패 시 Vision API |

Render 대시보드 → Service → **Environment** 에서 추가.

## 4. 수동 Docker 실행

```bash
docker build -t audit-income-expense .
docker run -p 8000:8000 -e PORT=8000 audit-income-expense
```

## HWP가 꼭 필요할 때

- **방법 A**: Windows PC에서 `run_web.bat` (로컬)
- **방법 B**: Windows VPS + 한글 설치 (고급)
- **방법 C**: hwpx로 받아 한글에서 hwp로 저장

온라인 서버(`/api/health`의 `hwp_supported: false`)에서는 hwpx만 지원합니다.
