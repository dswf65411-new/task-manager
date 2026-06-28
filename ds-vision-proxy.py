#!/usr/bin/env python3
"""
ds-vision-proxy — 給 claude-ds（DeepSeek 後端）加上看圖能力的本地視覺預處理代理。

原理：DeepSeek-v4-pro 是純文字模型。本代理夾在 Claude Code 與 DeepSeek 之間，
攔截每個 Anthropic Messages 請求，若內含 image block，先用 Gemini 3.1-flash-lite
把圖 OCR + 描述成文字、替換掉 image block，再把純文字版請求轉發給 DeepSeek。
回應以 raw socket 原樣串流回傳，保留 SSE（Claude Code 的逐字輸出不受影響）。
無圖請求原封不動直接轉發，零額外延遲。

- 監聽：127.0.0.1:8799
- DeepSeek 金鑰：不在本檔，由 Claude Code 以 x-api-key/authorization header 帶入、代理透傳
- Gemini 金鑰：讀環境變數 GEMINI_API_KEY

2026-06-26：對 5xx（含"temporarily unavailable"）自動重試 2 次，避開 DeepSeek 間歇性不穩。
"""
import os, sys, json, ssl, socket, urllib.request, urllib.error, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = 8799
UPSTREAM_HOST = "api.deepseek.com"
UPSTREAM_PREFIX = "/anthropic"
GEMINI_MODEL = "gemini-3.1-flash-lite"
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

VISION_PROMPT = (
    "你是一個視覺轉述器，把圖片完整轉成文字給看不到圖的下游模型使用。請做到：\n"
    "1. 逐字讀出圖中所有文字（OCR），原文照抄、不要翻譯。\n"
    "2. 描述版面結構、區塊位置、顏色。\n"
    "3. 若有圖表，說明圖表類型並讀出每個數據點的數值與標籤。\n"
    "4. 若有 UI（按鈕、輸入框、選單、錯誤訊息、程式碼），逐一列出其文字與狀態。\n"
    "5. 若有流程圖/示意圖，描述各節點與箭頭關係。\n"
    "用繁體中文輸出，但圖中原文（含英文、數字、程式碼）必須照抄。力求完整，寧詳勿略。"
)

LOG = open("/tmp/ds-vision-proxy.log", "a")


def log(*a):
    print(*a, file=LOG, flush=True)


def describe_image(b64data, media_type):
    if not GEMINI_KEY:
        return None
    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}")
    payload = {"contents": [{"parts": [
        {"inline_data": {"mime_type": media_type or "image/png", "data": b64data}},
        {"text": VISION_PROMPT},
    ]}]}
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            d = json.loads(r.read())
        return d["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        log("Gemini 失敗:", repr(e))
        return None


def fetch_url_image(u):
    import base64
    try:
        with urllib.request.urlopen(u, timeout=30) as r:
            data = r.read()
            ct = r.headers.get("content-type", "image/png")
        return base64.b64encode(data).decode(), ct
    except Exception as e:
        log("抓 url 圖失敗:", repr(e))
        return None, None


def transform_body(raw):
    try:
        body = json.loads(raw)
    except Exception:
        return raw, 0
    if not isinstance(body.get("messages"), list):
        return raw, 0
    replaced = 0
    for msg in body["messages"]:
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for i, block in enumerate(content):
            if not (isinstance(block, dict) and block.get("type") == "image"):
                continue
            src = block.get("source", {})
            b64, mtype = None, None
            if src.get("type") == "base64":
                b64, mtype = src.get("data"), src.get("media_type")
            elif src.get("type") == "url":
                b64, mtype = fetch_url_image(src.get("url"))
            desc = describe_image(b64, mtype) if b64 else None
            if desc:
                content[i] = {"type": "text",
                              "text": f"[以下是一張圖片的視覺轉述（原圖未傳給你，由 Gemini 看圖轉成文字）：\n{desc}\n]"}
                replaced += 1
                log(f"已轉述 1 張圖（{len(desc)} 字）")
            else:
                content[i] = {"type": "text",
                              "text": "[此處原有一張圖片，但視覺轉述失敗，無法取得內容。]"}
                replaced += 1
                log("圖轉述失敗，插入佔位文字")
    if replaced:
        return json.dumps(body).encode(), replaced
    return raw, 0


def try_forward_retry(headers_list, raw_body, up_path, max_retries=4):
    """raw TLS socket 連 deepseek，5xx 時遞增間隔重試。回傳 (200 bytes, True) 或 (error_body, False)。"""
    fwd = "\r\n".join(headers_list)
    for attempt in range(max_retries + 1):
        try:
            ctx = ssl.create_default_context()
            s = ctx.wrap_socket(socket.create_connection((UPSTREAM_HOST, 443), timeout=120),
                                server_hostname=UPSTREAM_HOST)
            req_bytes = (f"POST {up_path} HTTP/1.1\r\n" + fwd +
                         "\r\n\r\n").encode() + raw_body
            s.sendall(req_bytes)
            # 讀 HTTP status line
            buf = bytearray()
            while b"\r\n" not in buf:
                chunk = s.recv(1)
                if not chunk:
                    break
                buf.extend(chunk)
            status_line = buf.decode(errors="replace").strip()
            # 5xx / 429（rate limit）/ unavailable → 重試（分類器爆量打 DeepSeek 易撞 rate limit）
            retryable = any(s in status_line for s in [" 5", " 429", "unavailable", "rate", "overload"])
            if retryable and attempt < max_retries:
                log(f"← {status_line} 可重試, 第 {attempt+1}/{max_retries} 次, 退避 {0.6*(attempt+1):.1f}s")
                s.close()
                time.sleep(0.6 * (attempt + 1))
                continue
            # 回傳 rest
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf.extend(chunk)
            s.close()
            return bytes(buf), True
        except Exception as e:
            if attempt < max_retries:
                log(f"轉發 deepseek 異常（{e}），重試 {attempt+1}/{max_retries}")
                time.sleep(0.5 * (attempt + 1))
                continue
            err = {"type": "error",
                   "error": {"type": "api_error", "message": f"proxy upstream error ({attempt+1}次): {e}"}}
            body = json.dumps(err).encode()
            return body, False
    return b"", False


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
        else:
            self.send_response(404);
            self.send_header("Content-Length", "0");
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        new_body, n = transform_body(raw)
        if n:
            log(f"本請求轉述 {n} 張圖，body {len(raw)}→{len(new_body)} bytes")

        # === 全請求 log（診斷分類器）===
        try:
            _b = json.loads(new_body)
            _model = _b.get("model", "?")
            _msgs = _b.get("messages", [])
            _sys = _b.get("system", "")
            _mt = _b.get("max_tokens", "?")
            _stream = _b.get("stream", False)
            _think = _b.get("thinking", "none")
            _firstmsg = ""
            if _msgs and isinstance(_msgs[0].get("content"), str):
                _firstmsg = _msgs[0]["content"][:120]
            elif _msgs:
                _firstmsg = str(_msgs[0].get("content"))[:120]
            log(f"[REQ] path={self.path} model={_model} max_tok={_mt} stream={_stream} "
                f"think={_think} nmsg={len(_msgs)} sys={str(_sys)[:60]!r} msg0={_firstmsg!r}")
        except Exception as _e:
            log(f"[REQ] path={self.path} body={len(new_body)}b (non-json: {_e})")

        # 安全分類器修復：非串流請求（stream!=true）= 分類器型快速判斷請求。
        # DeepSeek thinking 開啟時 content[0] 是 thinking block，分類器讀 content[0] 期望 text→解析失敗→"cannot determine safety"。
        # 對非串流請求強制 thinking:disabled，讓 content[0]=text、且更快。對話用 stream:true 不受影響。
        try:
            _cb = json.loads(new_body)
            if _cb.get("stream") is not True and _cb.get("thinking", {}).get("type") != "disabled":
                _cb["thinking"] = {"type": "disabled"}
                new_body = json.dumps(_cb).encode()
                log(f"[CLS] 非串流請求→強制 thinking disabled (max_tok={_cb.get('max_tokens')})")
        except Exception:
            pass

        # 剝除 model 的 [1m] 後綴（分類器可能不剝→DeepSeek 回 503）
        try:
            _bd = json.loads(new_body)
            if isinstance(_bd.get("model"), str) and "[" in _bd["model"]:
                import re as _re
                _clean = _re.sub(r"\[[^\]]*\]", "", _bd["model"]).strip()
                if _clean != _bd["model"]:
                    log(f"[FIX] model {_bd['model']} -> {_clean}")
                    _bd["model"] = _clean
                    new_body = json.dumps(_bd).encode()
        except Exception:
            pass

        # 組轉發 header（去掉 hop-by-hop 與會變動的）
        skip = {"host", "content-length", "connection"}
        fwd = []
        for k, v in self.headers.items():
            if k.lower() in skip:
                continue
            fwd.append(f"{k}: {v}")
        fwd.append(f"Host: {UPSTREAM_HOST}")
        fwd.append(f"Content-Length: {len(new_body)}")
        fwd.append("Connection: close")
        up_path = UPSTREAM_PREFIX + self.path

        # 5xx/429 自動重試，應對分類器爆量 / DeepSeek 間歇性不穩
        resp_bytes, ok = try_forward_retry(fwd, new_body, up_path, max_retries=4)

        # raw 直通：把 DeepSeek 完整 HTTP 回應（status line + headers + chunked body）原樣寫回，
        # 不剝不重組（剝掉會破壞 chunked 框架，導致 16d 之類 chunk marker 洩漏進 body）。
        self.close_connection = True
        if ok:
            self.wfile.write(resp_bytes)
        else:
            # 連線失敗：自組 502 + JSON error body
            self.wfile.write(b"HTTP/1.1 502 Bad Gateway\r\n")
            self.wfile.write(f"Content-Type: application/json\r\nContent-Length: {len(resp_bytes)}\r\nConnection: close\r\n\r\n".encode())
            self.wfile.write(resp_bytes)


if __name__ == "__main__":
    if not GEMINI_KEY:
        log("警告：GEMINI_API_KEY 未設，看圖會回 fallback 佔位文字")
    log(f"=== ds-vision-proxy 啟動於 127.0.0.1:{PORT}，視覺層 {GEMINI_MODEL}（5xx 自動重試 2 次）===")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
