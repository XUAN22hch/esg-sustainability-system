"""Safe, fixed-prompt Groq diagnostic. Does not transmit enterprise data or reveal credentials."""
import json, os, urllib.request, urllib.error, socket

URL='https://api.groq.com/openai/v1/chat/completions'

def test_groq():
    key=os.getenv('GROQ_API_KEY','').strip()
    model=os.getenv('GROQ_MODEL','openai/gpt-oss-120b').strip()
    result={'diagnostic_version':'connection-test-1','provider':'groq','model':model,
            'configured':bool(key),'sends_enterprise_data':False}
    if not key:
        return {**result,'ok':False,'message':'Render 尚未設定 GROQ_API_KEY'}
    # Do not include user documents, project information or secrets.
    payload={'model':model,'messages':[{'role':'user','content':'Reply with exactly this JSON object: {"ok":true}'}],
             'temperature':0,'max_completion_tokens':80,'response_format':{'type':'json_object'}}
    req=urllib.request.Request(URL,data=json.dumps(payload).encode('utf-8'),
        headers={'Authorization':'Bearer '+key,'Content-Type':'application/json','Accept':'application/json','User-Agent':'ESG-Workspace-Diagnostic/1.0'},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=25) as resp:
            raw=resp.read(2048)
            return {**result,'ok':True,'http_status':resp.status,'content_type':resp.headers.get('Content-Type',''),
                    'message':'Groq 固定短文字請求成功；完整專章仍可能受額度或輸入長度影響。'}
    except urllib.error.HTTPError as exc:
        body=exc.read(4096)
        headers=exc.headers
        info={**result,'ok':False,'http_status':exc.code,
              'content_type':headers.get('Content-Type',''),
              'server':headers.get('Server',''),
              'cf_ray':headers.get('CF-Ray',''),
              'request_id':headers.get('x-request-id','') or headers.get('request-id',''),
              'body_kind':'html' if b'<html' in body.lower() or b'<!doctype html' in body.lower() else ('json' if body.lstrip().startswith(b'{') else 'other_or_empty'),
              'message':'Groq 回傳 HTTP %s；請提供這份診斷結果（不要提供 API Key）。'%exc.code}
        # Never display arbitrary raw server responses, which may echo private content.
        try:
            obj=json.loads(body)
            err=obj.get('error',{}) if isinstance(obj,dict) else {}
            if isinstance(err,dict):
                info['error_code']=str(err.get('code') or err.get('type') or '')[:80]
                # Don't display raw message; only safe categories.
                msg=str(err.get('message','')).lower()
                info['error_category']=('rate_limit' if 'rate limit' in msg or 'quota' in msg else
                    'invalid_key' if 'invalid api key' in msg or 'invalid_api_key' in msg else
                    'permission_or_policy' if 'permission' in msg or 'forbidden' in msg or 'blocked' in msg else 'unspecified')
        except (ValueError,TypeError):pass
        return info
    except (urllib.error.URLError,TimeoutError,socket.timeout) as exc:
        return {**result,'ok':False,'error_type':type(exc).__name__,'message':'連線逾時或網路錯誤，請查看 Render 網路狀態。'}
