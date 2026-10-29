"""AI provider router: Gemini, optional Groq, optional remotely reachable Ollama.
No API key or enterprise document is logged. Never silently transfer to fallback.
"""
import json, os, urllib.request, urllib.error, re
from gemini_client import generate_json as gemini_generate, GeminiError

class ProviderError(RuntimeError): pass

def _safe_error_detail(raw):
    """Show provider error metadata without exposing credentials or document text."""
    try:
        data = json.loads(raw)
        err = data.get('error', data) if isinstance(data, dict) else {}
        if isinstance(err, dict):
            code = str(err.get('code') or err.get('type') or '')
            message = str(err.get('message') or '')
            detail = ('[%s] ' % code if code else '') + message
        else:
            detail = ''
    except (ValueError, TypeError):
        detail = ''
    # Never display raw HTML/proxy responses or echo request data.
    if not detail:
        return '服務端未提供可辨識的 JSON 錯誤訊息'
    detail = re.sub(r'gsk_[A-Za-z0-9_-]+', '[已隱藏金鑰]', detail)
    detail = re.sub(r'(?i)(bearer\s+)\S+', r'\1[已隱藏]', detail)
    return detail[:300]


def _safe_header(value, max_length=100):
    """Allow only simple header identifiers; never include arbitrary response text."""
    return re.sub(r'[^A-Za-z0-9._:/-]', '', str(value or ''))[:max_length]


def _response_kind(raw, content_type):
    """Classify a rejected response without logging potentially sensitive HTML."""
    sample = raw.lstrip().lower()
    if sample.startswith(('<!doctype html', '<html')) or 'text/html' in content_type.lower():
        return 'HTML（可能為網路閘道或安全攔截頁）'
    if not raw.strip():
        return '空白回應'
    try:
        json.loads(raw)
        return 'JSON'
    except (ValueError, TypeError):
        return '非 JSON 回應'


def _json_reply(url, payload, headers, timeout):
    req = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'Accept': 'application/json', **headers},
        method='POST'
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            raw = e.read(4096).decode('utf-8', errors='replace')
        except Exception:
            raw = ''
        response_headers = e.headers
        content_type = str(response_headers.get('Content-Type', '')).split(';', 1)[0]
        request_id = _safe_header(response_headers.get('x-request-id', ''))
        cf_ray = _safe_header(response_headers.get('cf-ray', ''))
        server = _safe_header(response_headers.get('server', ''), 50)
        kind = _response_kind(raw, content_type)
        detail = _safe_error_detail(raw) if kind == 'JSON' else '無可辨識的 JSON 錯誤訊息'
        provider = 'Groq' if 'api.groq.com' in url else 'AI'
        model = _safe_header(payload.get('model', ''), 90)
        metadata = ['回應類型：' + kind]
        if content_type:
            metadata.append('Content-Type=' + _safe_header(content_type, 60))
        if server:
            metadata.append('Server=' + server)
        if request_id:
            metadata.append('Request-ID=' + request_id)
        if cf_ray:
            metadata.append('CF-Ray=' + cf_ray)
        raise ProviderError('%s HTTP %s（模型：%s）：%s；%s' % (
            provider, e.code, model, detail, '；'.join(metadata)
        )) from None
    except (urllib.error.URLError, TimeoutError):
        raise ProviderError('AI 連線失敗，請稍後重試；已完成的草稿不受影響。') from None

def _parse(raw):
    raw=raw.strip()
    try:return json.loads(raw)
    except json.JSONDecodeError:
        if raw.startswith('```'):
            raw=raw.split('\n',1)[-1].rsplit('```',1)[0].strip()
            return json.loads(raw)
        raise ProviderError('備援 AI 未回傳有效 JSON，請縮短證據後重試。')

def generate_json(prompt, *, timeout=120):
    mode=os.getenv('AI_PROVIDER','gemini').strip().lower()
    fallback=os.getenv('AI_FALLBACK_PROVIDER','').strip().lower()
    def run(provider):
        if provider=='gemini':return gemini_generate(prompt,timeout=timeout)
        if provider=='groq':
            key=os.getenv('GROQ_API_KEY','').strip()
            if not key:raise ProviderError('未設定 GROQ_API_KEY，無法使用 Groq 備援。')
            obj=_json_reply('https://api.groq.com/openai/v1/chat/completions',{'model':os.getenv('GROQ_MODEL','llama-3.3-70b-versatile'),'messages':[{'role':'user','content':prompt}],'temperature':0.2,'response_format':{'type':'json_object'}}, {'Authorization':'Bearer '+key},timeout)
            return _parse(obj['choices'][0]['message']['content'])
        if provider=='ollama':
            url=os.getenv('OLLAMA_BASE_URL','').rstrip('/')
            if not url or not url.startswith('https://'):
                raise ProviderError('Render 無法直接連接你電腦的 localhost。請先設定安全 HTTPS Ollama 服務網址 OLLAMA_BASE_URL。')
            # Security: credentials are never sent in URL; use a private authenticated proxy.
            token=os.getenv('OLLAMA_PROXY_TOKEN','')
            if not token:raise ProviderError('使用 Ollama 遠端連線須設定 OLLAMA_PROXY_TOKEN，避免公開模型端點。')
            obj=_json_reply(url+'/api/generate',{'model':os.getenv('OLLAMA_MODEL','qwen2.5:7b'),'prompt':prompt,'stream':False,'format':'json','options':{'temperature':0.2}}, {'Authorization':'Bearer '+token},timeout)
            return _parse(obj['response'])
        raise ProviderError('不支援的 AI_PROVIDER：'+provider)
    try:return run(mode)
    except (GeminiError,ProviderError) as err:
        # Only fallback for quota errors, not authentication or arbitrary model failures.
        if mode=='gemini' and fallback in ('groq','ollama') and ('429' in str(err) or 'quota' in str(err).lower()):
            try:return run(fallback)
            except (ProviderError,KeyError,ValueError) as second:
                raise ProviderError('Gemini 已達免費額度；備援 '+fallback+' 亦失敗：'+str(second)) from second
        raise
