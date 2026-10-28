"""PostgreSQL adapter for existing SQLite-style app queries."""
import os,re
import psycopg
from psycopg.rows import dict_row

SCHEMA='''
CREATE TABLE IF NOT EXISTS projects(id BIGSERIAL PRIMARY KEY,name TEXT,industry TEXT,year TEXT,created TEXT);
CREATE TABLE IF NOT EXISTS sources(id BIGSERIAL PRIMARY KEY,project BIGINT,name TEXT,sha TEXT,created TEXT,status TEXT DEFAULT 'yellow',note TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS evidence(id BIGSERIAL PRIMARY KEY,project BIGINT,source BIGINT,location TEXT,body TEXT,sections TEXT,confidence DOUBLE PRECISION,category TEXT);
CREATE TABLE IF NOT EXISTS drafts(project BIGINT,section TEXT,body TEXT,confirmed INTEGER DEFAULT 0,updated TEXT,PRIMARY KEY(project,section));
CREATE TABLE IF NOT EXISTS requests(project BIGINT,code TEXT,status TEXT DEFAULT 'missing',note TEXT DEFAULT '',source_id BIGINT,updated TEXT,PRIMARY KEY(project,code));
CREATE TABLE IF NOT EXISTS clause_reviews(project BIGINT,code TEXT,status TEXT DEFAULT 'unreviewed',note TEXT DEFAULT '',source_ids TEXT DEFAULT '[]',updated TEXT,PRIMARY KEY(project,code));
CREATE TABLE IF NOT EXISTS ai_suggestions(project BIGINT,code TEXT,assessment TEXT,support TEXT,gap TEXT,request_text TEXT,evidence_ids TEXT,model TEXT,updated TEXT,PRIMARY KEY(project,code));
CREATE TABLE IF NOT EXISTS original_files(source BIGINT PRIMARY KEY,content BYTEA NOT NULL,content_type TEXT,uploaded TEXT);
CREATE INDEX IF NOT EXISTS idx_sources_project ON sources(project);
CREATE INDEX IF NOT EXISTS idx_evidence_project ON evidence(project);
'''
class Row(dict):
 def __getitem__(self,k):
  if isinstance(k,int):return list(self.values())[k]
  return super().__getitem__(k)
def row_factory(cursor):
 cols=[x.name for x in cursor.description]
 def make(values):return Row(zip(cols,values))
 return make
class Result:
 def __init__(self,cur,lastrowid=None):self.cur=cur;self.lastrowid=lastrowid;self.rowcount=cur.rowcount
 def fetchone(self):return self.cur.fetchone()
 def fetchall(self):return self.cur.fetchall()
 def __iter__(self):return iter(self.cur)
class Connection:
 def __init__(self):
  url=os.environ['DATABASE_URL']
  self.c=psycopg.connect(url,autocommit=False,row_factory=row_factory,connect_timeout=15)
  self.c.execute('SET statement_timeout = 60000')
  for stmt in SCHEMA.split(';'):
   if stmt.strip():self.c.execute(stmt)
  self.c.commit()
 def execute(self,sql,args=()):
  sql=sql.replace('?', '%s')
  returning=bool(re.match(r'\s*INSERT\s+INTO\s+(projects|sources)\s*\(',sql,re.I)) and 'RETURNING' not in sql.upper()
  if returning:sql+=' RETURNING id'
  cur=self.c.execute(sql,args)
  if returning:
   row=cur.fetchone();return Result(cur,row['id'])
  return Result(cur)
 def executescript(self,sql):
  for stmt in sql.split(';'):
   if stmt.strip():self.c.execute(stmt)
 def commit(self):self.c.commit()
 def rollback(self):self.c.rollback()
 def close(self):self.c.close()
def connect():return Connection()
