# Railway Naver Restaurant Crawler

Selenium으로 네이버 음식점 검색 결과를 수집하고 CSV 파일을 저장한 뒤 종료되는 Railway Cron Job용 배치 프로그램입니다.

## 현재 구조

- `app.py`: 환경변수를 읽고 검색 키워드를 생성한 뒤 Selenium/Chromium으로 수집, 중복 제거, CSV 저장까지 수행합니다.
- `Dockerfile`: Railway Linux Docker 환경에서 Python 3.12, Chromium, ChromeDriver, 한글 폰트를 설치하고 `python app.py`를 실행합니다.
- `requirements.txt`: Python 의존성입니다. `webdriver_manager`는 사용하지 않습니다.
- `.dockerignore`: Git/캐시/CSV/env 파일이 Docker 이미지에 들어가지 않도록 제외합니다.
- `.env.example`: Railway Variables에 넣을 첫 테스트용 값입니다.

## Railway Variables

처음 Railway에서 시험할 때는 아래 값으로 시작하세요.

```env
TARGET=석촌동
REGIONS=석촌동
KEYWORD_LIMIT=3
MAX_WORKERS=1
MAX_SCROLL=5
HEADLESS=true
DATA_DIR=/data
```

`KEYWORD_LIMIT=0`이면 전체 키워드를 수집합니다. 첫 테스트가 성공하면 `KEYWORD_LIMIT=0`, `MAX_SCROLL=18` 정도로 늘려서 실행하세요. `MAX_WORKERS`는 Railway 메모리 사용량을 확인하며 1부터 시작하는 것을 권장합니다.

## CSV 저장

Railway Volume을 추가하고 Mount Path를 `/data`로 설정하세요.

CSV는 기본적으로 `/data/{TARGET}_naver_restaurants` 아래에 저장됩니다. 같은 날 여러 번 실행해도 덮어쓰지 않도록 파일명에는 `YYYYMMDD_HHMMSS` 시간이 포함됩니다.

## Railway 배포 순서

1. GitHub 저장소 루트에 이 폴더의 파일을 올립니다.
2. Railway에서 `New Project` > `Deploy from GitHub Repo`를 선택합니다.
3. Service에 Volume을 추가하고 Mount Path를 `/data`로 설정합니다.
4. Variables에 위 환경변수를 입력합니다.
5. Dockerfile의 기본 `CMD ["python", "app.py"]`를 사용합니다.
6. Cron Job으로 실행하려면 Railway Service Settings에서 Cron Schedule을 설정합니다.

한국시간 매일 오전 9시에 실행하려면 UTC 기준 cron은 `0 0 * * *`입니다.

## 로컬 점검

네이버 크롤링을 실행하지 않고 문법만 확인:

```bash
python -m py_compile app.py
```

Docker가 설치되어 있다면 빌드 확인:

```bash
docker build -t railway-naver-crawler .
```

## GitHub 업로드 명령

```bash
git init
git add app.py Dockerfile requirements.txt .dockerignore .env.example README.md
git commit -m "Prepare Railway cron crawler"
git branch -M main
git remote add origin <YOUR_GITHUB_REPO_URL>
git push -u origin main
```
