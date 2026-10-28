import os,json,io,datetime,hashlib,threading,time,logging,zipfile
from concurrent.futures import ThreadPoolExecutor
from flask import g
from flask import Flask,request,jsonify,send_file,render_template_string
from werkzeug.utils import secure_filename
from engine import *
from compliance import ROWS,CHECKS
ROOT=os.path.dirname(__file__);app=Flask(__name__);app.config['MAX_CONTENT_LENGTH']=40*1024*1024;DB=os.environ.get('DATABASE_PATH',os.path.join(ROOT,'projects.sqlite3'))
@app.before_request
def access_control():
 from flask import Response
 import secrets
 password=os.environ.get('APP_PASSWORD','')
 if not password and os.environ.get('ALLOW_UNAUTHENTICATED_LOCAL')!='1':
  return Response('安全設定尚未完成：請在 Render Environment 設定 APP_PASSWORD，避免企業資料公開。',503)
 if password:
  auth=request.authorization
  if not auth or not secrets.compare_digest(auth.username or '','esg') or not secrets.compare_digest(auth.password or '',password):
   return Response('請輸入系統帳號密碼',401,{'WWW-Authenticate':'Basic realm="ESG Workspace"'})
def conn():
 if not hasattr(g,'esg_conn'):
  g.esg_conn=db(DB)
 return g.esg_conn
@app.teardown_appcontext
def close_db(error=None):
 c=g.pop('esg_conn',None)
 if c is not None:
  try:c.close()
  except Exception:pass
def project(c,p):return c.execute('SELECT * FROM projects WHERE id=?',(p,)).fetchone()
@app.get('/')
def home():return render_template_string(open(os.path.join(ROOT,'index.html'),encoding='utf-8').read())
def ensure_compliance(c):
 # persistent_db initializes schema once per worker; no per-request DDL.
 return None

@app.get('/api/projects/<int:pid>/compliance')
def compliance(pid):
 c=conn();ensure_compliance(c)
 if not project(c,pid):return jsonify(error='找不到專案'),404
 rs={x['code']:dict(x) for x in c.execute('SELECT * FROM requests WHERE project=?',(pid,))}
 cs={x['code']:dict(x) for x in c.execute('SELECT * FROM clause_reviews WHERE project=?',(pid,))}
 req=[dict(code=code,group=group,title=title,clauses=clauses,section=section,status=rs.get(code,{}).get('status','missing'),note=rs.get(code,{}).get('note',''),source_id=rs.get(code,{}).get('source_id')) for code,group,title,clauses,section in ROWS]
 checks=[dict(code=code,title=title,section=section,requirement=need,status=cs.get(code,{}).get('status','unreviewed'),note=cs.get(code,{}).get('note',''),source_ids=json.loads(cs.get(code,{}).get('source_ids','[]'))) for code,title,section,need in CHECKS]
 return jsonify(requests=req,checks=checks,version='上傳之 2023 年 IFRS S1/S2 正體中文 A 部分草案（非完整正式準則）',summary={'requests_total':len(req),'requests_missing':sum(r['status']=='missing' for r in req),'clauses_total':len(checks),'clauses_complete':sum(r['status']=='complete' for r in checks),'clauses_gaps':sum(r['status'] in ('missing','partial') for r in checks)})

@app.put('/api/projects/<int:pid>/compliance/request/<code>')
def update_request(pid,code):
 if code not in {x[0] for x in ROWS}:return jsonify(error='索資編號不存在'),404
 j=request.get_json(silent=True) or {};status=j.get('status','missing')
 if status not in ('missing','received','confirmed','issue','not_applicable'):return jsonify(error='狀態不正確'),400
 c=conn();ensure_compliance(c)
 if not project(c,pid):return jsonify(error='找不到專案'),404
 sid=j.get('source_id') or None
 if sid is not None:
  if not c.execute('SELECT 1 FROM sources WHERE id=? AND project=?',(sid,pid)).fetchone():return jsonify(error='來源不屬於此專案'),400
 c.execute('INSERT INTO requests(project,code,status,note,source_id,updated) VALUES(?,?,?,?,?,?) ON CONFLICT(project,code) DO UPDATE SET status=excluded.status,note=excluded.note,source_id=excluded.source_id,updated=excluded.updated',(pid,code,status,str(j.get('note',''))[:3000],sid,datetime.datetime.now().isoformat()));c.commit();return jsonify(ok=True)

@app.put('/api/projects/<int:pid>/compliance/clause/<code>')
def update_clause(pid,code):
 if code not in {x[0] for x in CHECKS}:return jsonify(error='條文編號不存在'),404
 j=request.get_json(silent=True) or {};status=j.get('status','unreviewed')
 if status not in ('unreviewed','missing','partial','complete','not_applicable'):return jsonify(error='狀態不正確'),400
 c=conn();ensure_compliance(c)
 if not project(c,pid):return jsonify(error='找不到專案'),404
 ids=j.get('source_ids',[])
 if not isinstance(ids,list) or len(ids)>100:return jsonify(error='來源格式不正確'),400
 ids=[int(x) for x in ids]
 for sid in ids:
  if not c.execute('SELECT 1 FROM sources WHERE id=? AND project=?',(sid,pid)).fetchone():return jsonify(error='來源不屬於此專案'),400
 c.execute('INSERT INTO clause_reviews(project,code,status,note,source_ids,updated) VALUES(?,?,?,?,?,?) ON CONFLICT(project,code) DO UPDATE SET status=excluded.status,note=excluded.note,source_ids=excluded.source_ids,updated=excluded.updated',(pid,code,status,str(j.get('note',''))[:3000],json.dumps(ids),datetime.datetime.now().isoformat()));c.commit();return jsonify(ok=True)

@app.post('/api/projects/<int:pid>/ai/analyze')
def ai_analyze(pid):
 c=conn();ensure_compliance(c);p=project(c,pid)
 if not p:return jsonify(error='找不到專案'),404
 from ai_review import analyze
 evidence=[dict(x) for x in c.execute('SELECT e.id,e.location,e.body,s.name filename FROM evidence e JOIN sources s ON s.id=e.source WHERE e.project=? ORDER BY e.id',(pid,))]
 try:items,model,count=analyze(evidence,p['industry'],p['year'])
 except Exception as exc:
  app.logger.exception('AI analysis failed')
  if isinstance(exc,ValueError):return jsonify(error=str(exc)),400
  return jsonify(error='AI 分析失敗。請確認 API 金鑰、額度與模型設定，並查看 Render Logs。'),502
 c.execute("CREATE TABLE IF NOT EXISTS ai_suggestions(project INTEGER,code TEXT,assessment TEXT,support TEXT,gap TEXT,request_text TEXT,evidence_ids TEXT,model TEXT,updated TEXT,PRIMARY KEY(project,code))")
 for x in items:
  c.execute('INSERT INTO ai_suggestions VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(project,code) DO UPDATE SET assessment=excluded.assessment,support=excluded.support,gap=excluded.gap,request_text=excluded.request_text,evidence_ids=excluded.evidence_ids,model=excluded.model,updated=excluded.updated',(pid,x['code'],x['assessment'],x['support'][:3000],x['gap'][:3000],x['request'][:3000],json.dumps(x['evidence_ids']),model,datetime.datetime.now().isoformat()))
 c.commit();return jsonify(ok=True,analyzed=len(items),snippets=count,model=model)

@app.get('/api/projects/<int:pid>/ai/suggestions')
def ai_suggestions(pid):
 c=conn();c.execute("CREATE TABLE IF NOT EXISTS ai_suggestions(project INTEGER,code TEXT,assessment TEXT,support TEXT,gap TEXT,request_text TEXT,evidence_ids TEXT,model TEXT,updated TEXT,PRIMARY KEY(project,code))")
 return jsonify([dict(x) for x in c.execute('SELECT * FROM ai_suggestions WHERE project=? ORDER BY code',(pid,))])

@app.get('/api/projects/<int:pid>/compliance/export')
def compliance_export(pid):
 c=conn();ensure_compliance(c)
 if not project(c,pid):return jsonify(error='找不到專案'),404
 from openpyxl import Workbook
 from openpyxl.styles import Font,PatternFill,Alignment
 from openpyxl.utils import get_column_letter
 w=Workbook();a=w.active;a.title='客戶索資追蹤';a.append(['編號','類別','索取資料','參考條文','章節','狀態','備註','來源ID'])
 r={x['code']:x for x in c.execute('SELECT * FROM requests WHERE project=?',(pid,))}
 for code,group,title,clauses,section in ROWS:
  x=r.get(code);a.append([code,group,title,clauses,section,x['status'] if x else 'missing',x['note'] if x else '',x['source_id'] if x else ''])
 b=w.create_sheet('S1S2條文檢核');b.append(['條文','檢核主題','章節','應檢查事項','人工檢核狀態','缺口與補件說明','來源IDs'])
 cr={x['code']:x for x in c.execute('SELECT * FROM clause_reviews WHERE project=?',(pid,))}
 for code,title,section,need in CHECKS:
  x=cr.get(code);b.append([code,title,section,need,x['status'] if x else 'unreviewed',x['note'] if x else '',x['source_ids'] if x else '[]'])
 for sh in w:
  sh.freeze_panes='A2';sh.auto_filter.ref=sh.dimensions
  for col in range(1,sh.max_column+1):sh.column_dimensions[get_column_letter(col)].width=24 if col!=3 else 62
  for cell in sh[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='146B63')
  for row in sh.iter_rows(min_row=2):
   for cell in row:cell.alignment=Alignment(wrap_text=True,vertical='top')
 w.create_sheet('使用說明').append(['此為依使用者提供之 IFRS S1/S2 2023 年中文草案建立的部分檢核清單；條文適用性及符合性必須人工判斷。'])
 out=io.BytesIO();w.save(out);return send_file(io.BytesIO(out.getvalue()),mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',as_attachment=True,download_name=f'IFRS_S1_S2_索資與條文檢核_{pid}.xlsx')

@app.get('/api/projects')
def projects():
 c=conn();return jsonify([dict(x) for x in c.execute('SELECT * FROM projects ORDER BY id DESC')])
@app.post('/api/projects')
def new():
 j=request.get_json();name=(j.get('name') or '').strip()
 if not name:return jsonify(error='請輸入公司名稱'),400
 c=conn();cur=c.execute('INSERT INTO projects(name,industry,year,created) VALUES(?,?,?,?)',(name,j.get('industry',''),j.get('year','2025'),datetime.datetime.now().isoformat()));c.commit();return jsonify(id=cur.lastrowid)
@app.get('/api/projects/<int:pid>')
def get(pid):
 c=conn();p=project(c,pid)
 if not p:return jsonify(error='找不到專案'),404
 sources=[dict(x) for x in c.execute('SELECT * FROM sources WHERE project=?',(pid,))];evidence=[dict(x) for x in c.execute('SELECT e.*,s.name filename FROM evidence e JOIN sources s ON s.id=e.source WHERE e.project=? ORDER BY e.id',(pid,))];drafts=report_rows(c,pid)
 for e in evidence:e['sections']=json.loads(e['sections'])
 checks=[]
 for ch,(title,subs) in SECTIONS.items():
  for i,sub in enumerate(subs,1):
   k=f'{ch}.{i}';ev=[e for e in evidence if k in e['sections']];dr=drafts.get(k,{});checks.append(dict(key=k,title=sub,evidence=len(ev),generated=bool(dr and not dr['body'].startswith('[待補充')),confirmed=bool(dr.get('confirmed')),needs_data=not bool(ev),needs_review=not bool(dr.get('confirmed'))))
 return jsonify(project=dict(p),sources=sources,evidence=evidence,drafts=drafts,checks=checks,structure=SECTIONS)
_pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='esg-parse')
_active=set()
_active_lock=threading.Lock()

def _process_source(job_id,pid,sid):
 with _active_lock:
  if job_id in _active:return
  _active.add(job_id)
 c=None
 try:
  c=db(DB)
  c.execute("UPDATE processing_jobs SET status='processing',progress=10,message='正在擷取文件文字',updated=? WHERE id=?",(datetime.datetime.now().isoformat(),job_id));c.commit()
  f=c.execute('SELECT s.name,o.content FROM sources s JOIN original_files o ON o.source=s.id WHERE s.id=? AND s.project=?',(sid,pid)).fetchone()
  if not f:raise ValueError('找不到原始文件')
  chunks=extract(f['name'],bytes(f['content']))
  if not chunks:raise ValueError('文件沒有可擷取的文字，可能是掃描型 PDF，需 OCR 才能解析')
  c.execute("UPDATE processing_jobs SET progress=55,message='正在分類與儲存證據',updated=? WHERE id=?",(datetime.datetime.now().isoformat(),job_id));c.commit()
  # Delete partial evidence if worker retries after interruption
  c.execute('DELETE FROM evidence WHERE project=? AND source=?',(pid,sid))
  for loc,body in chunks:
   sections,confidence=classify(body,f['name'])
   c.execute('INSERT INTO evidence(project,source,location,body,sections,confidence,category) VALUES(?,?,?,?,?,?,?)',(pid,sid,loc,body,json.dumps(sections),confidence,category(body)))
  c.execute("UPDATE processing_jobs SET status='done',progress=100,message=?,updated=? WHERE id=?",(f'已完成，共 {len(chunks)} 個文字片段',datetime.datetime.now().isoformat(),job_id));c.commit()
 except Exception as exc:
  app.logger.exception('Document processing failed')
  if c is not None:
   try:
    c.rollback();c.execute("UPDATE processing_jobs SET status='error',message=?,updated=? WHERE id=?",(str(exc)[:500],datetime.datetime.now().isoformat(),job_id));c.commit()
   except Exception:app.logger.exception('Failed to record job error')
 finally:
  if c is not None:c.close()
  with _active_lock:_active.discard(job_id)

def _process_ai(job_id,pid):
 with _active_lock:
  if job_id in _active:return
  _active.add(job_id)
 c=None
 try:
  c=db(DB)
  c.execute("UPDATE processing_jobs SET status='processing',progress=20,message='正在分析 IFRS S1/S2 證據',updated=? WHERE id=?",(datetime.datetime.now().isoformat(),job_id));c.commit()
  p=project(c,pid)
  if not p:raise ValueError('找不到專案')
  from ai_review import analyze
  evidence=[dict(x) for x in c.execute('SELECT e.id,e.location,e.body,s.name filename FROM evidence e JOIN sources s ON s.id=e.source WHERE e.project=? ORDER BY e.id',(pid,))]
  items,model,count=analyze(evidence,p['industry'],p['year'])
  for x in items:
   c.execute('INSERT INTO ai_suggestions VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(project,code) DO UPDATE SET assessment=excluded.assessment,support=excluded.support,gap=excluded.gap,request_text=excluded.request_text,evidence_ids=excluded.evidence_ids,model=excluded.model,updated=excluded.updated',(pid,x['code'],x['assessment'],x['support'][:3000],x['gap'][:3000],x['request'][:3000],json.dumps(x['evidence_ids']),model,datetime.datetime.now().isoformat()))
  c.execute("UPDATE processing_jobs SET status='done',progress=100,message=?,updated=? WHERE id=?",(f'已分析 {len(items)} 組條文、{count} 筆證據片段',datetime.datetime.now().isoformat(),job_id));c.commit()
 except Exception as exc:
  app.logger.exception('AI background analysis failed')
  if c is not None:
   try:c.rollback();c.execute("UPDATE processing_jobs SET status='error',message=?,updated=? WHERE id=?",(str(exc)[:400],datetime.datetime.now().isoformat(),job_id));c.commit()
   except Exception:app.logger.exception('Failed to record AI job error')
 finally:
  if c is not None:c.close()
  with _active_lock:_active.discard(job_id)

@app.post('/api/projects/<int:pid>/upload')
def upload(pid):
 c=conn()
 if not project(c,pid):return jsonify(error='找不到專案'),404
 files=request.files.getlist('files');results=[]
 if not files:return jsonify(error='請先選擇檔案'),400
 for f in files:
  data=f.read();name=f.filename or 'unnamed';sha=hashlib.sha256(data).hexdigest()
  if c.execute('SELECT 1 FROM sources WHERE project=? AND sha=?',(pid,sha)).fetchone():
   results.append({'file':name,'status':'重複檔案已略過'});continue
  cur=c.execute('INSERT INTO sources(project,name,sha,created) VALUES(?,?,?,?)',(pid,name,sha,datetime.datetime.now().isoformat()))
  sid=cur.lastrowid
  c.execute('INSERT INTO original_files(source,content,content_type,uploaded) VALUES(?,?,?,?)',(sid,data,f.mimetype,datetime.datetime.now().isoformat()))
  job=c.execute('INSERT INTO processing_jobs(project,source,kind,status,progress,message,created,updated) VALUES(?,?,?,?,?,?,?,?) RETURNING id',(pid,sid,'upload','queued',0,'已保存原始檔，等待解析',datetime.datetime.now().isoformat(),datetime.datetime.now().isoformat()))
  # psycopg adapter returns Result for explicit RETURNING
  jid=job.fetchone()['id']
  c.commit()
  _pool.submit(_process_source,jid,pid,sid)
  results.append({'file':name,'status':'已排入背景解析','job_id':jid})
 return jsonify(results=results),202

@app.get('/api/projects/<int:pid>/jobs/<int:jid>')
def get_job(pid,jid):
 c=conn();j=c.execute('SELECT id,kind,status,progress,message FROM processing_jobs WHERE project=? AND id=?',(pid,jid)).fetchone()
 if not j:return jsonify(error='找不到工作'),404
 return jsonify(dict(j))

@app.post('/api/projects/<int:pid>/ai/start')
def ai_start(pid):
 c=conn()
 if not project(c,pid):return jsonify(error='找不到專案'),404
 if not c.execute('SELECT 1 FROM evidence WHERE project=? LIMIT 1',(pid,)).fetchone():return jsonify(error='請先上傳可解析文件'),400
 running=c.execute("SELECT id FROM processing_jobs WHERE project=? AND kind='ai' AND status IN ('queued','processing') ORDER BY id DESC LIMIT 1",(pid,)).fetchone()
 if running:return jsonify(job_id=running['id'],status='processing'),202
 now=datetime.datetime.now().isoformat()
 j=c.execute('INSERT INTO processing_jobs(project,kind,status,progress,message,created,updated) VALUES(?,?,?,?,?,?,?) RETURNING id',(pid,'ai','queued',0,'等待 AI 分析',now,now));jid=j.fetchone()['id'];c.commit()
 _pool.submit(_process_ai,jid,pid)
 return jsonify(job_id=jid,status='queued'),202
@app.get('/api/projects/<int:pid>/sources/<int:sid>/download')
def download_original(pid,sid):
 c=conn();r=c.execute('SELECT s.name,f.content,f.content_type FROM sources s JOIN original_files f ON f.source=s.id WHERE s.project=? AND s.id=?',(pid,sid)).fetchone()
 if not r:return jsonify(error='原始檔不存在（舊版上傳的檔案未保存原件）'),404
 return send_file(io.BytesIO(bytes(r['content'])),mimetype=r['content_type'] or 'application/octet-stream',as_attachment=True,download_name=r['name'])
@app.post('/api/projects/<int:pid>/text')
def add_text(pid):
 j=request.get_json(silent=True) or {};body=(j.get('body') or '').strip();name=(j.get('name') or '手動輸入文字').strip()[:180]
 if not body:return jsonify(error='請輸入文字內容'),400
 if len(body)>100000:return jsonify(error='文字內容過長'),400
 c=conn();sha=hashlib.sha256((name+body+datetime.datetime.now().isoformat()).encode()).hexdigest()
 cur=c.execute('INSERT INTO sources(project,name,sha,created,status,note) VALUES(?,?,?,?,?,?)',(pid,name,sha,datetime.datetime.now().isoformat(),'yellow','文字輸入'))
 sections,confidence=classify(body,name)
 for i in range(0,len(body),2600):
  piece=body[i:i+2600];sec,conf=classify(piece,name)
  c.execute('INSERT INTO evidence(project,source,location,body,sections,confidence,category) VALUES(?,?,?,?,?,?,?)',(pid,cur.lastrowid,f'文字段落 {i//2600+1}',piece,json.dumps(sec),conf,category(piece)))
 c.commit();return jsonify(ok=True,id=cur.lastrowid)
@app.put('/api/projects/<int:pid>/sources/<int:sid>')
def edit_source(pid,sid):
 j=request.get_json(silent=True) or {};status=j.get('status')
 if status not in ('yellow','green','red'):return jsonify(error='狀態不正確'),400
 c=conn();r=c.execute('UPDATE sources SET status=?,note=? WHERE project=? AND id=?',(status,str(j.get('note',''))[:2000],pid,sid));c.commit()
 if not r.rowcount:return jsonify(error='找不到文件'),404
 return jsonify(ok=True)
@app.delete('/api/projects/<int:pid>/sources/<int:sid>')
def delete_source(pid,sid):
 c=conn();exists=c.execute('SELECT 1 FROM sources WHERE project=? AND id=?',(pid,sid)).fetchone()
 if not exists:return jsonify(error='找不到文件；資料可能已在先前部署時遺失，請重新整理'),404
 c.execute('DELETE FROM processing_jobs WHERE source=?',(sid,))
 c.execute('DELETE FROM original_files WHERE source=?',(sid,))
 c.execute('DELETE FROM evidence WHERE project=? AND source=?',(pid,sid))
 c.execute("UPDATE requests SET source_id=NULL,status='missing' WHERE project=? AND source_id=?",(pid,sid))
 r=c.execute('DELETE FROM sources WHERE project=? AND id=?',(pid,sid));c.commit()
 if not r.rowcount:return jsonify(error='找不到文件'),404
 return jsonify(ok=True,warning='既有草稿可能仍包含刪除前的來源摘錄，請重新生成並人工檢查；已確認章節不會自動覆蓋。')
@app.put('/api/projects/<int:pid>/evidence/<int:eid>/text')
def edit_evidence(pid,eid):
 j=request.get_json(silent=True) or {};body=(j.get('body') or '').strip()
 if not body:return jsonify(error='內容不可空白'),400
 c=conn();e=c.execute('SELECT source FROM evidence WHERE id=? AND project=?',(eid,pid)).fetchone()
 if not e:return jsonify(error='找不到片段'),404
 r=c.execute('SELECT status FROM sources WHERE id=?',(e['source'],)).fetchone()
 if r and r['status']=='green':return jsonify(error='文件已人工確認，請先改回黃燈才能修改'),409
 sec,conf=classify(body)
 c.execute('UPDATE evidence SET body=?,sections=?,confidence=?,category=? WHERE id=? AND project=?',(body,json.dumps(sec),conf,category(body),eid,pid))
 c.execute("UPDATE sources SET status='yellow' WHERE id=?",(e['source'],));c.commit();return jsonify(ok=True)
@app.delete('/api/projects/<int:pid>')
def delete_project(pid):
 c=conn();ensure_compliance(c)
 if not project(c,pid):return jsonify(error='找不到專案'),404
 c.execute("CREATE TABLE IF NOT EXISTS ai_suggestions(project INTEGER,code TEXT,assessment TEXT,support TEXT,gap TEXT,request_text TEXT,evidence_ids TEXT,model TEXT,updated TEXT,PRIMARY KEY(project,code))")
 c.execute('DELETE FROM processing_jobs WHERE project=?',(pid,))
 c.execute('DELETE FROM original_files WHERE source IN (SELECT id FROM sources WHERE project=?)',(pid,))
 for t in ('evidence','sources','drafts','requests','clause_reviews','ai_suggestions','draft_audit'):c.execute(f'DELETE FROM {t} WHERE project=?',(pid,))
 r=c.execute('DELETE FROM projects WHERE id=?',(pid,));c.commit()
 if not r.rowcount:return jsonify(error='找不到專案'),404
 return jsonify(ok=True)


def _report_progress(c, job_id, percent, message):
 c.execute('UPDATE processing_jobs SET status=?,progress=?,message=?,updated=? WHERE id=?',('processing',percent,message,datetime.datetime.now().isoformat(),job_id))
 c.commit()

def _process_report(job_id,pid):
 """Generate one subsection at a time, committing each result for resumability."""
 c=None
 try:
  from gemini_client import generate_json
  if not os.getenv('GEMINI_API_KEY'):raise ValueError('尚未設定 GEMINI_API_KEY')
  c=db(DB);p=project(c,pid)
  if not p:raise ValueError('公司專案不存在')
  rows=[dict(x) for x in c.execute('SELECT e.id,e.location,e.body,e.sections,s.name filename FROM evidence e JOIN sources s ON s.id=e.source WHERE e.project=? ORDER BY e.id',(pid,))]
  if not rows:raise ValueError('沒有可解析的企業證據，請先上傳文件')
  from report_writer import write_full_report
  write_full_report(c,p,rows,job_id,_report_progress,generate_json)
  c.execute('UPDATE processing_jobs SET status=?,progress=?,message=?,updated=? WHERE id=?',('done',100,'整本專章已處理完成；請逐節核對並匯出 Word',datetime.datetime.now().isoformat(),job_id));c.commit()
 except Exception as exc:
  app.logger.exception('Full-book AI report generation failed')
  if c:
   try:
    c.rollback();c.execute('UPDATE processing_jobs SET status=?,message=?,updated=? WHERE id=?',('error',str(exc)[:350]+'；已完成小節已保存，可稍後接續',datetime.datetime.now().isoformat(),job_id));c.commit()
   except Exception:app.logger.exception('Could not save report error')
 finally:
  if c:c.close()

@app.post('/api/projects/<int:pid>/report/start')
def report_start(pid):
 c=conn()
 if not project(c,pid):return jsonify(error='找不到公司專案'),404
 if not c.execute('SELECT 1 FROM evidence WHERE project=? LIMIT 1',(pid,)).fetchone():return jsonify(error='尚無已解析的證據資料。請先上傳文件並等待解析完成。'),400
 cutoff=(datetime.datetime.now()-datetime.timedelta(minutes=7)).isoformat()
 c.execute("UPDATE processing_jobs SET status='error',message='處理程序可能已中斷，請重新執行整本生成' WHERE project=? AND kind='report' AND status IN ('queued','processing') AND updated<?",(pid,cutoff));c.commit()
 running=c.execute("SELECT id FROM processing_jobs WHERE project=? AND kind='report' AND status IN ('queued','processing') ORDER BY id DESC LIMIT 1",(pid,)).fetchone()
 if running:return jsonify(job_id=running['id'],status='processing'),202
 now=datetime.datetime.now().isoformat()
 j=c.execute('INSERT INTO processing_jobs(project,kind,status,progress,message,created,updated) VALUES(?,?,?,?,?,?,?) RETURNING id',(pid,'report','queued',0,'已開始準備 AI 撰寫',now,now));jid=j.fetchone()['id'];c.commit()
 _pool.submit(_process_report,jid,pid)
 return jsonify(job_id=jid,status='queued'),202

@app.post('/api/projects/<int:pid>/generate')
def gen(pid):
 c=conn();return jsonify(updated=generate(c,pid))
@app.put('/api/projects/<int:pid>/sections/<key>')
def edit(pid,key):
 if key not in KEYS:return jsonify(error='章節不存在'),404
 j=request.get_json(silent=True) or {};c=conn()
 if not project(c,pid):return jsonify(error='找不到專案'),404
 old=c.execute('SELECT body,confirmed FROM drafts WHERE project=? AND section=?',(pid,key)).fetchone()
 body=str(j.get('body',''))[:100000];confirmed=int(bool(j.get('confirmed')));now=datetime.datetime.now().isoformat()
 c.execute('INSERT INTO drafts(project,section,body,confirmed,updated) VALUES(?,?,?,?,?) ON CONFLICT(project,section) DO UPDATE SET body=excluded.body,confirmed=excluded.confirmed,updated=excluded.updated',(pid,key,body,confirmed,now))
 c.execute('INSERT INTO draft_audit(project,section,old_body,new_body,old_confirmed,new_confirmed,actor,changed) VALUES(?,?,?,?,?,?,?,?)',(pid,key,old['body'] if old else '',body,int(old['confirmed']) if old else 0,confirmed,'esg (共用帳號)',now))
 c.commit();return jsonify(ok=True)

@app.get('/api/projects/<int:pid>/audit')
def audit(pid):
 c=conn()
 if not project(c,pid):return jsonify(error='找不到專案'),404
 key=request.args.get('section','')
 if key and key not in KEYS:return jsonify(error='章節不存在'),400
 if key:rows=c.execute('SELECT id,section,old_body,new_body,old_confirmed,new_confirmed,actor,changed FROM draft_audit WHERE project=? AND section=? ORDER BY id DESC LIMIT 100',(pid,key))
 else:rows=c.execute('SELECT id,section,old_body,new_body,old_confirmed,new_confirmed,actor,changed FROM draft_audit WHERE project=? ORDER BY id DESC LIMIT 100',(pid,))
 return jsonify([dict(x) for x in rows])

@app.get('/api/projects/<int:pid>/backup')
def backup(pid):
 c=conn();p=project(c,pid)
 if not p:return jsonify(error='找不到專案'),404
 tables={'sources':'SELECT * FROM sources WHERE project=?','evidence':'SELECT * FROM evidence WHERE project=?','drafts':'SELECT * FROM drafts WHERE project=?','requests':'SELECT * FROM requests WHERE project=?','clause_reviews':'SELECT * FROM clause_reviews WHERE project=?','ai_suggestions':'SELECT * FROM ai_suggestions WHERE project=?','processing_jobs':'SELECT * FROM processing_jobs WHERE project=?','draft_audit':'SELECT * FROM draft_audit WHERE project=?'}
 payload={'format':'esg-v7-project-backup','created':datetime.datetime.now(datetime.timezone.utc).isoformat(),'project':dict(p),'tables':{}}
 for name,sql in tables.items():payload['tables'][name]=[dict(x) for x in c.execute(sql,(pid,))]
 stream=io.BytesIO()
 with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as z:
  z.writestr('project.json',json.dumps(payload,ensure_ascii=False,indent=2,default=str))
  sources=payload['tables']['sources']
  for item in sources:
   record=c.execute('SELECT content,content_type FROM original_files WHERE source=?',(item['id'],)).fetchone()
   if record and record['content'] is not None:
    name=secure_filename(item.get('name') or 'document') or 'document'
    z.writestr(f"original_files/{item['id']}_{name}",bytes(record['content']))
  z.writestr('README.txt','ESG 專案完整備份（JSON + 原始檔案），包含敏感資料。請妥善保管。此檔供備份與人工復原，非一鍵還原程式。')
 stream.seek(0)
 return send_file(stream,mimetype='application/zip',as_attachment=True,download_name=f'esg_project_{pid}_backup.zip')

@app.get('/api/projects/<int:pid>/dashboard')
def dashboard(pid):
 c=conn()
 if not project(c,pid):return jsonify(error='找不到專案'),404
 rows=[dict(x) for x in c.execute('SELECT section,body,confirmed FROM drafts WHERE project=?',(pid,))]
 drafts={x['section']:x for x in rows};evidence=[dict(x) for x in c.execute('SELECT sections FROM evidence WHERE project=?',(pid,))]
 support={k:0 for k in KEYS}
 for e in evidence:
  try:keys=json.loads(e['sections'] or '[]')
  except (ValueError,TypeError):keys=[]
  for k in keys:
   if k in support:support[k]+=1
 result=[]
 for chapter,(heading,subs) in SECTIONS.items():
  items=[]
  for i,title in enumerate(subs,1):
   key=f'{chapter}.{i}';d=drafts.get(key,{});body=(d.get('body') or '').strip();generated=bool(body and not body.startswith(('【待補充','[待補充')));confirmed=bool(d.get('confirmed'));has_evidence=support[key]>0
   status='已確認' if confirmed else '待補證據' if not has_evidence else '待人工審閱' if generated else '待撰寫'
   items.append({'key':key,'title':title,'generated':generated,'confirmed':confirmed,'evidence':support[key],'status':status})
  result.append({'chapter':chapter,'title':heading,'items':items,'total':len(items),'generated':sum(x['generated'] for x in items),'confirmed':sum(x['confirmed'] for x in items),'supported':sum(x['evidence']>0 for x in items)})
 return jsonify(chapters=result,summary={'total':len(KEYS),'generated':sum(ch['generated'] for ch in result),'confirmed':sum(ch['confirmed'] for ch in result),'supported':sum(ch['supported'] for ch in result)},note='證據對應為初步分類；完成率不代表 IFRS 合規或外部確信。')

@app.put('/api/projects/<int:pid>/evidence/<int:eid>')
def map_evidence(pid,eid):
 j=request.get_json();keys=[k for k in j.get('sections',[]) if k in KEYS];c=conn();c.execute('UPDATE evidence SET sections=? WHERE id=? AND project=?',(json.dumps(keys),eid,pid));c.commit();return jsonify(ok=True)
@app.get('/api/projects/<int:pid>/export/<fmt>')
def export(pid,fmt):
 c=conn();p=project(c,pid)
 if not p:return '找不到專案',404
 if fmt=='docx':data=docx_bytes(c,pid);mime='application/vnd.openxmlformats-officedocument.wordprocessingml.document'
 elif fmt=='xlsx':data=excel_bytes(c,pid);mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
 elif fmt=='pdf':
  import tempfile
  from reportlab.pdfbase import pdfmetrics
  from reportlab.pdfbase.ttfonts import TTFont
  from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,PageBreak
  from reportlab.lib.styles import getSampleStyleSheet,ParagraphStyle
  from reportlab.lib import colors
  font=os.path.join(ROOT,'fonts','NotoSansCJK-Regular.ttc')
  if not os.path.exists(font):font='/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc'
  if not os.path.exists(font):font='/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
  pdfmetrics.registerFont(TTFont('CJK',font,subfontIndex=0));styles=getSampleStyleSheet();styles.add(ParagraphStyle(name='CJKTitle',fontName='CJK',fontSize=16,leading=24,spaceAfter=14));styles.add(ParagraphStyle(name='CJKBody',fontName='CJK',fontSize=9,leading=15,spaceAfter=7))
  buf=io.BytesIO();doc=SimpleDocTemplate(buf,pagesize=(595,842),leftMargin=50,rightMargin=50);story=[Paragraph(escape(p['name'])+' 永續相關財務資訊專章',styles['CJKTitle']),Paragraph('草稿，未經準則符合性確信',styles['CJKBody'])];rows=report_rows(c,pid)
  for ch,(title,subs) in SECTIONS.items():
   story.append(Paragraph(escape(ch+' '+title),styles['CJKTitle']))
   for i,sub in enumerate(subs,1):
    key=f'{ch}.{i}';story.append(Paragraph(escape(key+' '+sub),styles['CJKBody']))
    for para in rows.get(key,{}).get('body','[待補充]').split('\n'):
     if para.strip():story.append(Paragraph(escape(para),styles['CJKBody']))
  doc.build(story);data=buf.getvalue();mime='application/pdf'
 else:return jsonify(error='不支援'),400
 return send_file(io.BytesIO(data),mimetype=mime,as_attachment=True,download_name=f'ESG_{pid}.{fmt}')

# V7.1: reviewer comments, reporting boundaries, financial reconciliation and illustrative scenarios.
@app.get('/api/projects/<int:pid>/professional')
def professional(pid):
 c=conn()
 if not project(c,pid):return jsonify(error='找不到專案'),404
 comments=[dict(x) for x in c.execute('SELECT * FROM review_comments WHERE project=? ORDER BY id DESC LIMIT 200',(pid,))]
 entities=[dict(x) for x in c.execute('SELECT * FROM entity_boundaries WHERE project=? ORDER BY id DESC',(pid,))]
 links=[dict(x) for x in c.execute('SELECT * FROM financial_links WHERE project=? ORDER BY id DESC',(pid,))]
 return jsonify(comments=comments,entities=entities,links=links)

@app.post('/api/projects/<int:pid>/professional/comments')
def add_review_comment(pid):
 j=request.get_json(silent=True) or {};c=conn()
 if not project(c,pid):return jsonify(error='找不到專案'),404
 section=str(j.get('section',''))
 if section not in KEYS:return jsonify(error='請選擇有效章節'),400
 comment=str(j.get('comment','')).strip()
 if not comment or len(comment)>5000:return jsonify(error='評語必填且不可超過 5000 字'),400
 reviewer=str(j.get('reviewer','')).strip()[:100] or '未具名審閱者'
 now=datetime.datetime.now(datetime.timezone.utc).isoformat()
 c.execute('INSERT INTO review_comments(project,section,reviewer,comment,status,created,updated) VALUES(?,?,?,?,?,?,?)',(pid,section,reviewer,comment,'open',now,now));c.commit();return jsonify(ok=True),201

@app.put('/api/projects/<int:pid>/professional/comments/<int:cid>')
def resolve_review_comment(pid,cid):
 j=request.get_json(silent=True) or {};status=j.get('status')
 if status not in ('open','resolved'):return jsonify(error='狀態不正確'),400
 c=conn();r=c.execute('UPDATE review_comments SET status=?,updated=? WHERE project=? AND id=? RETURNING id',(status,datetime.datetime.now(datetime.timezone.utc).isoformat(),pid,cid)).fetchone()
 if not r:return jsonify(error='找不到評語'),404
 c.commit();return jsonify(ok=True)

@app.post('/api/projects/<int:pid>/professional/entities')
def add_boundary(pid):
 j=request.get_json(silent=True) or {};c=conn()
 if not project(c,pid):return jsonify(error='找不到專案'),404
 name=str(j.get('name','')).strip()[:200];scope=str(j.get('scope','')).strip()[:100];method=str(j.get('method','')).strip()[:100];note=str(j.get('note','')).strip()[:2000]
 if not name or not scope or not method:return jsonify(error='名稱、邊界類型及方法必填'),400
 c.execute('INSERT INTO entity_boundaries(project,name,scope,method,note,created) VALUES(?,?,?,?,?,?)',(pid,name,scope,method,note,datetime.datetime.now(datetime.timezone.utc).isoformat()));c.commit();return jsonify(ok=True),201

@app.post('/api/projects/<int:pid>/professional/links')
def add_financial_link(pid):
 j=request.get_json(silent=True) or {};c=conn()
 if not project(c,pid):return jsonify(error='找不到專案'),404
 section=str(j.get('section',''))
 if section not in KEYS:return jsonify(error='章節不存在'),400
 item=str(j.get('item','')).strip()[:200];account=str(j.get('account','')).strip()[:200]
 if not item or not account:return jsonify(error='項目與財報科目必填'),400
 try:amount=float(j.get('amount'))
 except (ValueError,TypeError):return jsonify(error='金額必須為數字'),400
 import math
 if not math.isfinite(amount):return jsonify(error='金額必須為有限數字'),400
 sid=j.get('source_id') or None
 if sid is not None:
  try:sid=int(sid)
  except (ValueError,TypeError):return jsonify(error='來源 ID 不正確'),400
  if not c.execute('SELECT 1 FROM sources WHERE id=? AND project=?',(sid,pid)).fetchone():return jsonify(error='來源不屬於本專案'),400
 currency=str(j.get('currency','TWD')).upper()
 if currency not in ('TWD','USD','EUR','JPY','CNY'):return jsonify(error='不支援的幣別'),400
 c.execute('INSERT INTO financial_links(project,section,item,account,amount,currency,source_id,note,created) VALUES(?,?,?,?,?,?,?,?,?)',(pid,section,item,account,amount,currency,sid,str(j.get('note',''))[:2000],datetime.datetime.now(datetime.timezone.utc).isoformat()));c.commit();return jsonify(ok=True),201

@app.get('/api/projects/<int:pid>/professional/consistency')
def consistency(pid):
 c=conn()
 if not project(c,pid):return jsonify(error='找不到專案'),404
 drafts={x['section']:(x['body'] or '') for x in c.execute('SELECT section,body FROM drafts WHERE project=?',(pid,))}
 text_strategy='\n'.join(v for k,v in drafts.items() if k.startswith('3.'))
 text_targets='\n'.join(v for k,v in drafts.items() if k.startswith('5.'))
 text_fin='\n'.join(v for k,v in drafts.items() if k.startswith('3.') or k.startswith('5.'))
 rules=[('再生能源/太陽能','太陽能|再生能源|光電','發電量|裝置容量|再生能源占比','策略提及再生能源，請檢查指標與目標是否有相應數據'),('減碳承諾','淨零|減碳目標|碳中和','基準年|目標年|減量百分比','策略提及減碳承諾，請核對基準年、目標年及衡量方式'),('資本支出','資本支出|CapEx|設備投資','資本支出|CapEx','請核對相關投資是否已與財報科目連結')]
 import re
 warnings=[]
 for title,trigger,expected,message in rules:
  if re.search(trigger,text_strategy,re.I) and not re.search(expected,text_targets if title!='資本支出' else text_fin,re.I):warnings.append({'title':title,'message':message})
 return jsonify(warnings=warnings,note='僅為關鍵字提醒，不代表已完成會計調節或 IFRS 合規檢核。')

if __name__=='__main__':app.run(host='127.0.0.1',port=5050,debug=False)
