# 클라우드 배포 (공개 URL)

GitHub 저장소를 **Render**에 연결하면 `https://xxxx.onrender.com` 형태의 주소로 누구나 접속할 수 있습니다.

## 1. Render에서 배포 (권장, 무료 플랜 가능)

1. [Render](https://render.com) 가입 → **GitHub 연동**
2. **New +** → **Blueprint** 선택
3. 저장소 `zanmang-lab/audit-income-expense` 연결
4. `render.yaml`이 자동 인식됨 → **Apply**
5. 배포 완료 후 표시되는 URL 접속 (예: `https://audit-income-expense.onrender.com`)

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
