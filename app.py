import os,json,io,datetime
from flask import Flask,request,jsonify,send_file,render_template_string
from werkzeug.utils import secure_filename
from engine import *
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
