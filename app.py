import os,json,io,datetime,hashlib
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
 if password:
  auth=request.authorization
  if not auth or not secrets.compare_digest(auth.password or '',password):
   return Response('請輸入系統帳號密碼',401,{'WWW-Authenticate':'Basic realm="ESG Workspace"'})
def conn():
 os.makedirs(os.path.dirname(os.path.abspath(DB)),exist_ok=True)
 return db(DB)
def project(c,p):return c.execute('SELECT * FROM projects WHERE id=?',(p,)).fetchone()
@app.get('/')
def home():return render_template_string(open(os.path.join(ROOT,'index.html'),encoding='utf-8').read())
def ensure_compliance(c):
 c.executescript("""CREATE TABLE IF NOT EXISTS requests(project INTEGER,code TEXT,status TEXT DEFAULT 'missing',note TEXT DEFAULT '',source_id INTEGER,updated TEXT,PRIMARY KEY(project,code));
 CREATE TABLE IF NOT EXISTS clause_reviews(project INTEGER,code TEXT,status TEXT DEFAULT 'unreviewed',note TEXT DEFAULT '',source_ids TEXT DEFAULT '[]',updated TEXT,PRIMARY KEY(project,code));""")
 c.commit()

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
@app.post('/api/projects/<int:pid>/upload')
def upload(pid):
 c=conn();files=request.files.getlist('files');results=[]
 for f in files:
  data=f.read();name=f.filename or 'unnamed';sha=hashlib.sha256(data).hexdigest()
  if c.execute('SELECT 1 FROM sources WHERE project=? AND sha=?',(pid,sha)).fetchone():results.append({'file':name,'status':'重複檔案已略過'});continue
  try:chunks=extract(name,data)
  except Exception as e:results.append({'file':name,'error':str(e)});continue
  cur=c.execute('INSERT INTO sources(project,name,sha,created) VALUES(?,?,?,?)',(pid,name,sha,datetime.datetime.now().isoformat()))
  for loc,body in chunks:
   sections,confidence=classify(body,name)
   c.execute('INSERT INTO evidence(project,source,location,body,sections,confidence,category) VALUES(?,?,?,?,?,?,?)',(pid,cur.lastrowid,loc,body,json.dumps(sections),confidence,category(body)))
  results.append({'file':name,'status':'已解析','chunks':len(chunks),'unclassified':sum(not classify(t,name)[0] for _,t in chunks)})
 c.commit();return jsonify(results=results)
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
 c=conn();c.execute('DELETE FROM evidence WHERE project=? AND source=?',(pid,sid));r=c.execute('DELETE FROM sources WHERE project=? AND id=?',(pid,sid));c.commit()
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
 c.execute("CREATE TABLE IF NOT EXISTS ai_suggestions(project INTEGER,code TEXT,assessment TEXT,support TEXT,gap TEXT,request_text TEXT,evidence_ids TEXT,model TEXT,updated TEXT,PRIMARY KEY(project,code))")
 for t in ('evidence','sources','drafts','requests','clause_reviews','ai_suggestions'):c.execute(f'DELETE FROM {t} WHERE project=?',(pid,))
 c.execute('DELETE FROM projects WHERE id=?',(pid,));c.commit();return jsonify(ok=True)
@app.post('/api/projects/<int:pid>/generate')
def gen(pid):
 c=conn();return jsonify(updated=generate(c,pid))
@app.put('/api/projects/<int:pid>/sections/<key>')
def edit(pid,key):
 if key not in KEYS:return jsonify(error='章節不存在'),404
 j=request.get_json();c=conn();c.execute('INSERT INTO drafts(project,section,body,confirmed,updated) VALUES(?,?,?,?,?) ON CONFLICT(project,section) DO UPDATE SET body=excluded.body,confirmed=excluded.confirmed,updated=excluded.updated',(pid,key,j.get('body',''),int(bool(j.get('confirmed'))),datetime.datetime.now().isoformat()));c.commit();return jsonify(ok=True)
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
if __name__=='__main__':app.run(host='127.0.0.1',port=5050,debug=False)
