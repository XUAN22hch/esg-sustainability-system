"""Run on your Windows PC. Requires Python 3.10+ and Ollama running locally.
Environment: ESG_SERVER_URL, LOCAL_OLLAMA_TOKEN, OLLAMA_MODEL (default qwen2.5:3b).
Never publish LOCAL_OLLAMA_TOKEN or expose Ollama port 11434.
"""
import os,json,time,urllib.request,urllib.error
BASE=os.environ.get("ESG_SERVER_URL","https://esg-sustainability-workspace.onrender.com").rstrip("/")
TOKEN=os.environ.get("LOCAL_OLLAMA_TOKEN","").strip()
MODEL=os.environ.get("OLLAMA_MODEL","qwen2.5:3b")
OLLAMA=os.environ.get("OLLAMA_URL","http://127.0.0.1:11434").rstrip("/")
if not TOKEN:raise SystemExit("請先設定 LOCAL_OLLAMA_TOKEN，需與 Render 的值相同")
def request_json(url,method="GET",payload=None,auth=False,timeout=60):
 data=None if payload is None else json.dumps(payload,ensure_ascii=False).encode("utf-8")
 headers={"Content-Type":"application/json"}
 if auth:headers["Authorization"]="Bearer "+TOKEN
 req=urllib.request.Request(url,data=data,headers=headers,method=method)
 with urllib.request.urlopen(req,timeout=timeout) as res:return json.load(res)
def _extract_body(answer):
 answer=(answer or "").strip()
 if not answer:
  return None
 try:
  obj=json.loads(answer)
  if isinstance(obj,dict):
   body=obj.get("body")
   if isinstance(body,str) and body.strip():
    return body.strip()
   for key in ("content","text","response"):
    val=obj.get(key)
    if isinstance(val,str) and val.strip():
     return val.strip()
  if isinstance(obj,str) and obj.strip():
   return obj.strip()
 except (json.JSONDecodeError,TypeError):
  pass
 if answer.startswith("```"):
  answer=answer.strip("`").removeprefix("json").strip()
  try:
   obj=json.loads(answer)
   if isinstance(obj,dict) and isinstance(obj.get("body"),str):
    return obj["body"].strip() or None
  except (json.JSONDecodeError,TypeError):
   pass
 # Do not store broken JSON as a draft.
 if answer.startswith("{") or answer.startswith("["):
  return None
 return answer if len(answer)>=25 else None

def generate(prompt):
 # Use a JSON schema rather than unconstrained JSON; 3B models sometimes omit body.
 schema={"type":"object","properties":{"body":{"type":"string"}},"required":["body"]}
 for attempt in range(3):
  options={"temperature":0.1,"num_predict":2400+attempt*800,"num_ctx":4096}
  payload={"model":MODEL,"prompt":prompt,"stream":False,"format":schema,"options":options}
  result=request_json(OLLAMA+"/api/generate","POST",payload,timeout=900)
  answer=result.get("response","")
  body=_extract_body(answer)
  if body and not result.get("done_reason")=="length":
   return body
  print("Ollama response invalid or truncated; retry",attempt+1,"/3",flush=True)
  prompt=prompt+"\\nIMPORTANT: Return exactly one JSON object with a nonempty string field named body. No other fields."
 raise ValueError("Ollama did not return a complete body after 3 attempts")

print("已連接本機 Ollama 工作程式；請保持此視窗開啟。",flush=True)
while True:
 try:
  response=request_json(BASE+"/api/local-worker/task",auth=True)
  task=response.get("task")
  if not task:time.sleep(6);continue
  print("處理章節",task["section"],"進度",task["cursor"]+1,"/",task["total"],flush=True)
  if task.get("skip"):
   body=""
  elif task.get("missing"):
   body="【待補充："+task.get("title","本節")+"。目前未找到足夠企業證據，請補充來源資料。】"
  else:
   body=generate(task["prompt"])
  request_json(BASE+"/api/local-worker/result","POST",{"job_id":task["job_id"],"section":task["section"],"cursor":task["cursor"],"body":body,"skip":bool(task.get("skip"))},auth=True)
  print("已保存",task["section"],flush=True)
 except KeyboardInterrupt:break
 except Exception as e:
  print("暫時失敗：",str(e)[:300],"；稍後重試",flush=True)
  time.sleep(15)
