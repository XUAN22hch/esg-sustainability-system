"""Minimal Gemini REST client using Python standard library. No paid OpenAI dependency."""
import os,json,time,urllib.request,urllib.error

class GeminiError(RuntimeError):pass

def generate_json(prompt, *, timeout=120):
    key=os.environ.get('GEMINI_API_KEY','').strip()
    if not key:raise GeminiError('尚未設定 GEMINI_API_KEY，請到 Render Environment 新增。')
    model=os.environ.get('GEMINI_MODEL','gemini-2.5-flash').strip()
    url='https://generativelanguage.googleapis.com/v1beta/models/'+model+':generateContent'
    payload={'contents':[{'role':'user','parts':[{'text':prompt}]}],
             'generationConfig':{'responseMimeType':'application/json','temperature':0.2}}
    data=json.dumps(payload,ensure_ascii=False).encode('utf-8')
    for attempt in range(2):
        req=urllib.request.Request(url,data=data,headers={'Content-Type':'application/json','x-goog-api-key':key},method='POST')
        try:
            with urllib.request.urlopen(req,timeout=timeout) as resp:
                obj=json.load(resp)
            parts=obj.get('candidates',[{}])[0].get('content',{}).get('parts',[])
            text=''.join(p.get('text','') for p in parts).strip()
            if not text:raise GeminiError('Gemini 沒有回傳文字，請稍後再試或減少資料量。')
            try:return json.loads(text)
            except json.JSONDecodeError as e:raise GeminiError('Gemini 回傳格式無法解析，請稍後重試。') from e
        except urllib.error.HTTPError as e:
            body=e.read(2500).decode('utf-8','replace')
            if e.code==429:
                raise GeminiError('Gemini 免費額度或速率限制已達上限（429）。已完成的草稿會保留，請稍後再接續生成。') from e
            if e.code in (401,403):raise GeminiError('Gemini 金鑰無效或沒有模型權限（401/403），請確認 Render 的 GEMINI_API_KEY。') from e
            if e.code==404:raise GeminiError('找不到 Gemini 模型，請檢查 GEMINI_MODEL 是否可用。') from e
            if e.code in (500,502,503,504) and attempt==0:
                time.sleep(3);continue
            raise GeminiError(f'Gemini API 錯誤 HTTP {e.code}：{body[:350]}') from e
        except (TimeoutError,urllib.error.URLError) as e:
            if attempt==0:time.sleep(2);continue
            raise GeminiError('Gemini 連線逾時，已完成章節仍會保留，請稍後接續。') from e
