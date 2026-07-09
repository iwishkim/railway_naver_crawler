import datetime
import os
import random
import re
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote

import pandas as pd
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.common.exceptions import TimeoutException, WebDriverException
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By


DEFAULT_TARGET = "석촌동"
DEFAULT_DATA_DIR = "/data"

KEYWORDS = [
    "한식",
    "백반",
    "국밥",
    "찌개",
    "김치찌개",
    "된장찌개",
    "분식",
    "김밥",
    "라면",
    "떡볶이",
    "돈가스",
    "고기집",
    "삼겹살",
    "갈비",
    "곱창",
    "막창",
    "족발",
    "보쌈",
    "치킨",
    "피자",
    "버거",
    "중식",
    "짜장면",
    "짬뽕",
    "마라탕",
    "일식",
    "초밥",
    "라멘",
    "우동",
    "돈부리",
    "양식",
    "파스타",
    "스테이크",
    "샐러드",
    "브런치",
    "카페",
    "디저트",
    "베이커리",
    "술집",
    "포차",
    "이자카야",
    "호프",
    "와인바",
    "뷔페",
    "샤브샤브",
    "쌀국수",
    "태국음식",
    "베트남음식",
    "멕시코음식",
    "인도음식",
    "해산물",
    "횟집",
    "조개구이",
    "칼국수",
    "냉면",
]

COLUMNS = ["검색어", "키워드", "키워드순서", "id", "상호명", "주업종", "리뷰내용", "링크"]

print_lock = threading.Lock()


def log(message: str) -> None:
    with print_lock:
        print(message, flush=True)


def env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = int(raw_value)
    except ValueError:
        log(f"[WARN] {name}={raw_value!r} is invalid. Using {default}.")
        value = default

    if minimum is not None and value < minimum:
        log(f"[WARN] {name}={value} is below {minimum}. Using {minimum}.")
        return minimum

    return value


def env_float(name: str, default: float, minimum: float | None = None) -> float:
    raw_value = os.getenv(name, str(default)).strip()
    try:
        value = float(raw_value)
    except ValueError:
        log(f"[WARN] {name}={raw_value!r} is invalid. Using {default}.")
        value = default

    if minimum is not None and value < minimum:
        log(f"[WARN] {name}={value} is below {minimum}. Using {minimum}.")
        return minimum

    return value


def env_bool(name: str, default: bool) -> bool:
    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() not in {"0", "false", "no", "n", "off"}


def parse_regions(target: str) -> list[str]:
    raw_regions = os.getenv("REGIONS", target)
    regions = [region.strip() for region in raw_regions.split(",") if region.strip()]
    return regions or [target]


TARGET = os.getenv("TARGET", DEFAULT_TARGET).strip() or DEFAULT_TARGET
REGIONS = parse_regions(TARGET)
KEYWORD_LIMIT = env_int("KEYWORD_LIMIT", 0, minimum=0)
MAX_WORKERS = env_int("MAX_WORKERS", 1, minimum=1)
MAX_SCROLL = env_int("MAX_SCROLL", 18, minimum=1)
HEADLESS = env_bool("HEADLESS", True)
DATA_DIR = os.getenv("DATA_DIR", DEFAULT_DATA_DIR).strip() or DEFAULT_DATA_DIR

SCROLL_PAUSE_MIN = env_float("SCROLL_PAUSE_MIN", 0.6, minimum=0)
SCROLL_PAUSE_MAX = env_float("SCROLL_PAUSE_MAX", 1.2, minimum=0)
NO_CHANGE_LIMIT = env_int("NO_CHANGE_LIMIT", 3, minimum=1)
PAGE_LOAD_WAIT_MIN = env_float("PAGE_LOAD_WAIT_MIN", 2.5, minimum=0)
PAGE_LOAD_WAIT_MAX = env_float("PAGE_LOAD_WAIT_MAX", 4.0, minimum=0)
PAGE_LOAD_TIMEOUT = env_int("PAGE_LOAD_TIMEOUT", 30, minimum=1)

CHROME_BIN = os.getenv("CHROME_BIN", "/usr/bin/chromium").strip() or "/usr/bin/chromium"
CHROMEDRIVER_PATH = (
    os.getenv("CHROMEDRIVER_PATH", "/usr/bin/chromedriver").strip()
    or "/usr/bin/chromedriver"
)
SAVE_DIR = os.path.join(DATA_DIR, f"{TARGET}_naver_restaurants")


def build_search_keywords() -> list[str]:
    search_keywords = [f"{region} {keyword}" for region in REGIONS for keyword in KEYWORDS]
    if KEYWORD_LIMIT > 0:
        return search_keywords[:KEYWORD_LIMIT]
    return search_keywords


SEARCH_KEYWORDS = build_search_keywords()


def normalize_text(text: object) -> str:
    if text is None or pd.isna(text):
        return ""
    return re.sub(r"\s+", " ", str(text)).strip()


def normalize_link(link: object) -> str:
    text = normalize_text(link)
    if not text:
        return ""
    return text.split("?")[0].rstrip("/")


def safe_text(node) -> str:
    return node.get_text(" ", strip=True) if node else ""


def extract_place_id(href: str) -> str:
    normalized = normalize_link(href)
    if not normalized:
        return ""

    match = re.search(r"/(?:restaurant|place)/(\d+)", normalized)
    if match:
        return match.group(1)

    for part in reversed(normalized.split("/")):
        if part.isdigit():
            return part

    return ""


def make_unique_key(row: dict) -> str:
    place_id = normalize_text(row.get("id"))
    if place_id:
        return f"id::{place_id}"

    link = normalize_link(row.get("링크"))
    name = normalize_text(row.get("상호명"))
    sector = normalize_text(row.get("주업종"))
    return f"fallback::{link}::{name}::{sector}"


def check_chrome_files() -> None:
    missing = []
    if not os.path.exists(CHROME_BIN):
        missing.append(f"Chromium binary not found: {CHROME_BIN}")
    if not os.path.exists(CHROMEDRIVER_PATH):
        missing.append(f"ChromeDriver not found: {CHROMEDRIVER_PATH}")
    if missing:
        raise FileNotFoundError("; ".join(missing))


def create_driver():
    check_chrome_files()

    user_data_dir = tempfile.mkdtemp(prefix="chrome-user-data-")
    options = webdriver.ChromeOptions()
    options.binary_location = CHROME_BIN

    if HEADLESS:
        options.add_argument("--headless=new")

    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("--remote-allow-origins=*")
    options.add_argument("--window-size=1400,2200")
    options.add_argument("--lang=ko-KR")
    options.add_argument("--log-level=3")
    options.add_argument(f"--user-data-dir={user_data_dir}")
    options.add_argument(
        "--user-agent=Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
    )
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)

    driver = webdriver.Chrome(service=Service(CHROMEDRIVER_PATH), options=options)
    driver._user_data_dir = user_data_dir
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT)

    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"},
        )
    except Exception as exc:
        log(f"[WARN] CDP anti-automation script was not applied: {exc}")

    return driver


def cleanup_driver(driver) -> None:
    if not driver:
        return

    service_process = getattr(getattr(driver, "service", None), "process", None)
    user_data_dir = getattr(driver, "_user_data_dir", None)

    try:
        driver.quit()
    except Exception as exc:
        log(f"[WARN] driver.quit() failed: {exc}")

    if service_process and service_process.poll() is None:
        try:
            service_process.terminate()
            service_process.wait(timeout=5)
        except Exception:
            try:
                service_process.kill()
            except Exception as exc:
                log(f"[WARN] ChromeDriver process kill failed: {exc}")

    if user_data_dir:
        shutil.rmtree(user_data_dir, ignore_errors=True)


def find_first_text(item, selectors: list[str]) -> str:
    for selector in selectors:
        text = safe_text(item.select_one(selector))
        if text:
            return text
    return ""


def parse_items_from_html(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    seen_ids = set()

    anchors = soup.select("a[href*='/restaurant/'], a[href*='/place/']")
    for anchor in anchors:
        href = anchor.get("href", "")
        place_id = extract_place_id(href)
        if place_id and place_id in seen_ids:
            continue
        if place_id:
            seen_ids.add(place_id)

        item = anchor
        for parent in anchor.parents:
            if getattr(parent, "name", None) in {"li", "div"}:
                item = parent
                break

        name = (
            normalize_text(anchor.get("aria-label"))
            or normalize_text(anchor.get("title"))
            or find_first_text(item, ["span.TYaxT", "span.place_bluelink", "strong"])
            or safe_text(anchor)
        )
        sector = find_first_text(item, ["span.KCMnt", "span[class*='category']"])
        review = find_first_text(item, ["div.Dr_06", "span[class*='review']", "span[class*='Review']"])

        row = {
            "id": place_id,
            "상호명": name,
            "주업종": sector,
            "리뷰내용": review,
            "링크": normalize_link(href),
        }

        if row["id"] or row["상호명"] or row["링크"]:
            rows.append(row)

    return rows


def enrich_row(row: dict, search_keyword: str, sequence: int) -> dict:
    keyword = search_keyword.split(" ", 1)[1] if " " in search_keyword else search_keyword
    enriched = dict(row)
    enriched["검색어"] = search_keyword
    enriched["키워드"] = keyword
    enriched["키워드순서"] = sequence
    return enriched


def collect_one_keyword(search_keyword: str) -> list[dict]:
    driver = None
    collected_rows = []
    local_seen = set()

    try:
        driver = create_driver()
        url = f"https://m.place.naver.com/restaurant/list?query={quote(search_keyword)}"
        log(f"[START] keyword={search_keyword}")
        driver.get(url)
        time.sleep(random.uniform(PAGE_LOAD_WAIT_MIN, PAGE_LOAD_WAIT_MAX))

        try:
            driver.find_element(By.TAG_NAME, "body").click()
        except Exception:
            pass

        previous_count = 0
        no_change_count = 0

        for scroll_index in range(MAX_SCROLL):
            rows = parse_items_from_html(driver.page_source)
            before_count = len(local_seen)

            for row in rows:
                unique_key = make_unique_key(row)
                if unique_key in local_seen:
                    continue
                local_seen.add(unique_key)
                collected_rows.append(enrich_row(row, search_keyword, len(collected_rows) + 1))

            after_count = len(local_seen)
            new_count = after_count - before_count
            log(
                f"[SCROLL] keyword={search_keyword} "
                f"scroll={scroll_index + 1}/{MAX_SCROLL} new={new_count} total={after_count}"
            )

            if after_count == previous_count:
                no_change_count += 1
            else:
                no_change_count = 0

            if no_change_count >= NO_CHANGE_LIMIT:
                log(f"[STOP] keyword={search_keyword} reason=no-new-items")
                break

            previous_count = after_count
            driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
            time.sleep(random.uniform(SCROLL_PAUSE_MIN, SCROLL_PAUSE_MAX))

        log(f"[DONE] keyword={search_keyword} rows={len(collected_rows)}")
        return collected_rows

    except (TimeoutException, WebDriverException) as exc:
        log(f"[ERROR] keyword={search_keyword} selenium={exc}")
        return []
    except Exception as exc:
        log(f"[ERROR] keyword={search_keyword} unexpected={exc}")
        return []
    finally:
        cleanup_driver(driver)
        log(f"[CLEANUP] keyword={search_keyword} chrome=closed")


def parallel_collect(search_keywords: list[str], max_workers: int) -> list[dict]:
    all_rows = []
    if not search_keywords:
        return all_rows

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(collect_one_keyword, keyword): keyword for keyword in search_keywords}
        for future in as_completed(futures):
            keyword = futures[future]
            try:
                rows = future.result()
                all_rows.extend(rows)
                log(f"[MERGE] keyword={keyword} rows={len(rows)} accumulated={len(all_rows)}")
            except Exception as exc:
                log(f"[ERROR] keyword={keyword} future={exc}")

    return all_rows


def advanced_deduplicate(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)

    for column in COLUMNS:
        if column not in df.columns:
            df[column] = ""
        df[column] = df[column].astype(str).fillna("").map(normalize_text)

    df["링크"] = df["링크"].map(normalize_link)
    df["unique_key"] = df.apply(lambda row: make_unique_key(row.to_dict()), axis=1)
    df["id_exists"] = df["id"].map(lambda value: 1 if normalize_text(value) else 0)
    df["link_exists"] = df["링크"].map(lambda value: 1 if normalize_text(value) else 0)
    df["review_len"] = df["리뷰내용"].map(lambda value: len(normalize_text(value)))

    before = len(df)
    df = df.sort_values(
        by=["id_exists", "link_exists", "review_len"],
        ascending=[False, False, False],
    ).drop_duplicates(subset=["unique_key"], keep="first")

    has_id = df["id"].str.strip() != ""
    df_with_id = df[has_id].drop_duplicates(subset=["id"], keep="first")
    df_no_id = df[~has_id]
    df = pd.concat([df_with_id, df_no_id], ignore_index=True)

    for column in ["unique_key", "id_exists", "link_exists", "review_len"]:
        if column in df.columns:
            df.drop(columns=[column], inplace=True)

    log(f"[DEDUP] rows={before}->{len(df)}")
    return df[COLUMNS].copy()


def save_results(raw_df: pd.DataFrame, dedup_df: pd.DataFrame) -> tuple[str, str]:
    os.makedirs(SAVE_DIR, exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_target = re.sub(r"[^0-9A-Za-z가-힣_-]+", "_", TARGET).strip("_") or "target"

    raw_path = os.path.join(SAVE_DIR, f"{safe_target}_restaurant_ids_full_{timestamp}.csv")
    dedup_path = os.path.join(SAVE_DIR, f"{safe_target}_restaurant_ids_dedup_{timestamp}.csv")

    for column in COLUMNS:
        if column not in raw_df.columns:
            raw_df[column] = ""
        if column not in dedup_df.columns:
            dedup_df[column] = ""

    raw_df[COLUMNS].to_csv(raw_path, index=False, encoding="utf-8-sig")
    dedup_df[COLUMNS].to_csv(dedup_path, index=False, encoding="utf-8-sig")
    log(f"[SAVE] full_csv={raw_path}")
    log(f"[SAVE] dedup_csv={dedup_path}")
    return raw_path, dedup_path


def main() -> None:
    start_time = time.time()
    log("[BOOT] Railway Naver crawler started")
    log(f"[CONFIG] TARGET={TARGET}")
    log(f"[CONFIG] REGIONS={','.join(REGIONS)}")
    log(f"[CONFIG] KEYWORD_LIMIT={KEYWORD_LIMIT} effective_keywords={len(SEARCH_KEYWORDS)}")
    log(f"[CONFIG] MAX_WORKERS={MAX_WORKERS} MAX_SCROLL={MAX_SCROLL} HEADLESS={HEADLESS}")
    log(f"[CONFIG] DATA_DIR={DATA_DIR} SAVE_DIR={SAVE_DIR}")
    log(f"[CONFIG] CHROME_BIN={CHROME_BIN} CHROMEDRIVER_PATH={CHROMEDRIVER_PATH}")

    try:
        check_chrome_files()
    except Exception as exc:
        log(f"[FATAL] Chrome/ChromeDriver check failed: {exc}")
        return

    all_rows = parallel_collect(SEARCH_KEYWORDS, MAX_WORKERS)
    raw_df = pd.DataFrame(all_rows, columns=COLUMNS)
    log(f"[RAW] rows={len(raw_df)}")

    dedup_df = advanced_deduplicate(raw_df)
    log(f"[FINAL] rows={len(dedup_df)}")

    save_results(raw_df, dedup_df)
    elapsed = time.time() - start_time
    log(f"[EXIT] elapsed_seconds={elapsed:.1f}")


if __name__ == "__main__":
    main()
