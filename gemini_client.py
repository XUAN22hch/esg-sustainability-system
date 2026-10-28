"""Gemini REST JSON client with model discovery and actionable errors."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import threading
import random

class GeminiError(RuntimeError):
    pass

BASE = 'https://generativelanguage.googleapis.com/v1beta/'
_model_cache = {}
_cache_lock = threading.Lock()


def _call(url, key, payload=None, timeout=30):
    headers = {'x-goog-api-key': key, 'Content-Type': 'application/json'}
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode('utf-8')
    req = urllib.request.Request(url, data=data, headers=headers, method='GET' if data is None else 'POST')
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def _error(e, model=''):
    body = e.read(3000).decode('utf-8', 'replace')
    try:
        detail = json.loads(body).get('error', {}).get('message', body)
    except (ValueError, AttributeError):
        detail = body
    detail = str(detail)[:600]
    if e.code == 429:
        return GeminiError('Gemini 免費額度或呼叫頻率已達上限（429），已完成章節保留。請稍後再試。' + (' Google：' + detail if detail else ''))
    if e.code in (401, 403):
        return GeminiError('Gemini API 金鑰無效、權限不足，或此模型不允許使用（HTTP %s）。Google：%s' % (e.code, detail))
    if e.code == 404:
        return GeminiError('Gemini 找不到模型 %s（404）。Google：%s' % (model, detail))
    return GeminiError('Gemini API HTTP %s：%s' % (e.code, detail))


def _available_models(key):
    """Only models explicitly advertising generateContent are eligible."""
    names = []
    page = ''
    for _ in range(5):
        url = BASE + 'models?pageSize=100'
        if page:
            url += '&pageToken=' + urllib.parse.quote(page, safe='')
        obj = _call(url, key, timeout=25)
        for item in obj.get('models', []):
            if 'generateContent' in item.get('supportedGenerationMethods', []):
                names.append(item['name'].removeprefix('models/'))
        page = obj.get('nextPageToken', '')
        if not page:
            break
    return names


def _choose_model(models, configured):
    configured = configured.removeprefix('models/')
    if configured and configured in models:
        return configured
    # Prefer stable, economical Flash models; never select Pro by accident.
    for preferred in ('gemini-2.5-flash', 'gemini-2.5-flash-lite', 'gemini-2.0-flash', 'gemini-2.0-flash-lite'):
        if preferred in models:
            return preferred
    flash = [m for m in models if 'flash' in m.lower() and 'preview' not in m.lower() and 'image' not in m.lower()]
    if flash:
        return sorted(flash, reverse=True)[0]
    raise GeminiError('此 API Key 的可用模型清單沒有適合文字生成的 Flash 模型。請在 Google AI Studio 確認免費模型權限。')


def generate_json(prompt, *, timeout=120):
    key = os.environ.get('GEMINI_API_KEY', '').strip()
    if not key:
        raise GeminiError('尚未設定 GEMINI_API_KEY，請到 Render Environment 新增。')
    configured = os.environ.get('GEMINI_MODEL', 'gemini-2.5-flash').strip()
    cache_key = key[-12:]
    with _cache_lock:
        cached = _model_cache.get(cache_key)
    try:
        models = cached if cached is not None else _available_models(key)
        if cached is None:
            with _cache_lock:
                _model_cache[cache_key] = models
    except urllib.error.HTTPError as e:
        raise _error(e, '模型清單') from e
    except (TimeoutError, urllib.error.URLError) as e:
        raise GeminiError('無法取得 Gemini 可用模型清單，請檢查 Render 網路或稍後重試。') from e
    model = _choose_model(models, configured)
    url = BASE + 'models/' + urllib.parse.quote(model, safe='') + ':generateContent'
    payload = {'contents': [{'role': 'user', 'parts': [{'text': prompt}]}],
               'generationConfig': {'responseMimeType': 'application/json', 'temperature': 0.2}}
    # Transient server overload: exponential backoff with jitter.
    # Never retry authentication, model, or quota errors automatically.
    max_retries = 4
    for attempt in range(max_retries + 1):
        try:
            obj = _call(url, key, payload, timeout=timeout)
            candidates = obj.get('candidates') or []
            parts = candidates[0].get('content', {}).get('parts', []) if candidates else []
            answer = ''.join(p.get('text', '') for p in parts).strip()
            if not answer:
                reason = candidates[0].get('finishReason', 'unknown') if candidates else obj.get('promptFeedback', 'unknown')
                raise GeminiError('Gemini 沒有回傳文字（%s）。請縮小單章證據量後重試。' % reason)
            try:
                return json.loads(answer)
            except json.JSONDecodeError as e:
                raise GeminiError('Gemini 回傳內容不是完整 JSON，可能輸出長度不足。請減少單章資料量後重試。') from e
        except urllib.error.HTTPError as e:
            if e.code in (500, 502, 503, 504):
                if attempt < max_retries:
                    # 10, 20, 40, 80 sec, plus a small jitter; honor Retry-After when provided.
                    delay = min(80, 10 * (2 ** attempt)) + random.uniform(0, 2)
                    retry_after = e.headers.get('Retry-After') if e.headers else None
                    if retry_after:
                        try: delay = min(120, max(delay, float(retry_after)))
                        except ValueError: pass
                    time.sleep(delay)
                    continue
                raise GeminiError('Gemini 伺服器持續忙碌（HTTP %s），已自動重試 %s 次。已儲存的小節不會刪除，請稍後按整本生成以接續未完成部分。' % (e.code, max_retries)) from e
            raise _error(e, model) from e
        except (TimeoutError, urllib.error.URLError) as e:
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            raise GeminiError('Gemini 連線逾時。已儲存的草稿仍會保留，請稍後接續生成。') from e
