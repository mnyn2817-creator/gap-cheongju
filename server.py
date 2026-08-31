import html, json, os, pathlib, re, subprocess, sys, urllib.parse, urllib.request, webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

args = sys.argv[1:]
PORT = next((int(x) for x in args if x.isdigit()), 8767)
AUTO_OPEN = "--no-browser" not in args
ROOT = pathlib.Path(__file__).resolve().parent
WEB_ROOT = ROOT
DEFAULT_HTML = "index.html"

def read_settings(path):
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"\'')
    return values

kakao_settings = read_settings(ROOT / ".Renviron.kakao")
naver_settings = read_settings(ROOT / ".Renviron.naver")
KAKAO_KEY = os.environ.get("KAKAO_REST_API_KEY") or kakao_settings.get("KAKAO_REST_API_KEY", "")
NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID") or naver_settings.get("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET") or naver_settings.get("NAVER_CLIENT_SECRET", "")
if not KAKAO_KEY:
    raise RuntimeError("Missing KAKAO_REST_API_KEY")

def get_json(url, kakao=False, headers=None):
    headers = dict(headers or {})
    if kakao:
        headers["Authorization"] = f"KakaoAK {KAKAO_KEY}"
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=15) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as error:
        winerror = getattr(error, "winerror", None) or getattr(getattr(error, "reason", None), "winerror", None)
        socket_blocked = winerror == 10013 or "WinError 10013" in repr(error)
        if os.name != "nt" or not socket_blocked:
            raise
        env = os.environ.copy()
        env["GAP_CHEONGJU_API_URL"] = url
        env["GAP_CHEONGJU_API_HEADERS"] = json.dumps(headers, ensure_ascii=False)
        command = (
            "[Console]::OutputEncoding=[Text.UTF8Encoding]::new();"
            "$h=@{};$o=$env:GAP_CHEONGJU_API_HEADERS|ConvertFrom-Json;"
            "$o.psobject.Properties|ForEach-Object{$h[$_.Name]=$_.Value};"
            "Invoke-RestMethod -Uri $env:GAP_CHEONGJU_API_URL -Headers $h | "
            "ConvertTo-Json -Depth 30 -Compress"
        )
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", command],
            capture_output=True, text=True, encoding="utf-8", env=env, timeout=25, check=True,
        )
        return json.loads(result.stdout)

def clean_naver_title(value):
    return html.unescape(re.sub(r"<[^>]+>", "", value or "")).strip()

def normalized_name(value):
    return re.sub(r"[^0-9A-Za-z가-힣]", "", clean_naver_title(value)).lower()

naver_cache = {}

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEB_ROOT), **kwargs)
    def send_json(self, data, status=200):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status); self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path); query = urllib.parse.parse_qs(parsed.query)
        try:
            if parsed.path == "/api/kakao/search":
                q = query.get("q", [""])[0]
                url = "https://dapi.kakao.com/v2/local/search/keyword.json?" + urllib.parse.urlencode({"query": q, "size": 15})
                data = get_json(url, True)
                docs = [d for d in data.get("documents", []) if "\uccad\uc8fc\uc2dc" in ((d.get("address_name") or "") + (d.get("road_address_name") or ""))]
                if not docs:
                    url = "https://dapi.kakao.com/v2/local/search/address.json?" + urllib.parse.urlencode({"query": q, "size": 15})
                    for d in get_json(url, True).get("documents", []):
                        if "\uccad\uc8fc\uc2dc" in (d.get("address_name") or ""):
                            road = d.get("road_address") or {}
                            docs.append({"place_name": road.get("building_name", ""), "address_name": d.get("address_name", ""), "road_address_name": road.get("address_name", ""), "x": d.get("x"), "y": d.get("y"), "category_name": "address"})
                return self.send_json({"documents": docs})
            if parsed.path == "/api/kakao/coord2address":
                params = {"x": query.get("x", [""])[0], "y": query.get("y", [""])[0]}
                return self.send_json(get_json("https://dapi.kakao.com/v2/local/geo/coord2address.json?" + urllib.parse.urlencode(params), True))
            if parsed.path == "/api/weather":
                params = {"latitude": query.get("lat", [""])[0], "longitude": query.get("lng", [""])[0], "current": "temperature_2m,weather_code,precipitation", "timezone": "Asia/Seoul"}
                return self.send_json(get_json("https://api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params)))
            if parsed.path == "/api/naver/interest":
                name = query.get("name", [""])[0].strip()
                if not NAVER_CLIENT_ID or not NAVER_CLIENT_SECRET:
                    return self.send_json({"configured": False, "reason": "missing_naver_credentials"}, 503)
                if not name:
                    return self.send_json({"configured": True, "error": "missing_name"}, 400)
                if name in naver_cache:
                    return self.send_json(naver_cache[name])
                headers = {
                    "X-Naver-Client-Id": NAVER_CLIENT_ID,
                    "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
                }
                local_params = {"query": f"청주 {name}", "display": 5, "start": 1, "sort": "comment"}
                local_data = get_json(
                    "https://openapi.naver.com/v1/search/local.json?" + urllib.parse.urlencode(local_params),
                    headers=headers,
                )
                target = normalized_name(name)
                ranked = []
                for rank, item in enumerate(local_data.get("items", []), start=1):
                    item_name = clean_naver_title(item.get("title"))
                    candidate = normalized_name(item_name)
                    if target == candidate or target in candidate or candidate in target:
                        ranked.append((rank, item, item_name))
                matched = ranked[0] if ranked else None
                blog_params = {"query": f"청주 {name}", "display": 1, "start": 1, "sort": "sim"}
                blog_data = get_json(
                    "https://openapi.naver.com/v1/search/blog.json?" + urllib.parse.urlencode(blog_params),
                    headers=headers,
                )
                result = {
                    "configured": True,
                    "place_name": matched[2] if matched else name,
                    "local_comment_rank": matched[0] if matched else None,
                    "local_match": bool(matched),
                    "blog_search_total": int(blog_data.get("total", 0)),
                    "naver_place_url": (
                        matched[1].get("link", "") if matched else ""
                    ) or "https://search.naver.com/search.naver?" + urllib.parse.urlencode({"query": f"청주 {name}"}),
                    "measure_label": "네이버 블로그 검색 결과 수",
                    "interpretation": "리뷰 수나 별점이 아닌 온라인 관심도 프록시",
                }
                naver_cache[name] = result
                return self.send_json(result)
            if parsed.path == "/api/status":
                return self.send_json({"kakao": True, "naver": bool(NAVER_CLIENT_ID and NAVER_CLIENT_SECRET)})
            if parsed.path == "/": self.path = "/" + urllib.parse.quote(DEFAULT_HTML)
            return super().do_GET()
        except Exception as e:
            self.send_json({"error": str(e)}, 500)
    def log_message(self, fmt, *args):
        print(fmt % args)

url = f"http://127.0.0.1:{PORT}/"
print("Gap Cheongju is running:", url)
if AUTO_OPEN:
    webbrowser.open(url)
ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
