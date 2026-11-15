#!/usr/bin/env python3
"""Local-only browser app, Python 3.10+, zero third-party dependencies."""
import argparse,base64,io,json,os,secrets,threading,zipfile
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import urlparse,parse_qs
from store import ROOT,connect,now,event
from seed import seed,make_zip,fixture_rows
from engine import ingest,match,execute_ready,act,exports,MAX_BYTES
from drop_api import DropClient,ApiError
from review import get_case,snapshot,decide,OPEN_STATES
TOKEN=secrets.token_urlsafe(32);LOCK=threading.RLock()
DATA=ROOT/'data';PORT=8765

def db(mode):
    if mode not in ('demo','sandbox'):raise ValueError('Unknown mode')
    return DATA/(mode+'.sqlite3')
def state(mode):
    path=db(mode)
    with connect(path) as c:
        rows=[dict(r) for r in c.execute('SELECT id,kind,file_name,batch_id,state,status,candidates,explanation,active FROM requests ORDER BY id')]
        for r in rows:
            r['candidates']=json.loads(r['candidates'])
            if r['state'] in OPEN_STATES:r['review']=snapshot(c,r['id'])
            if isinstance(r['candidates'],dict):
                for key in ('exact','suggested'):
                    for value in r['candidates'][key]:
                        i=value['id'] if isinstance(value,dict) else value
                        x=c.execute('SELECT payload,exempt,opted_out FROM customers WHERE id=?',(i,)).fetchone()
                        r.setdefault('records',[]).append({'id':i,'role':key,'score':value.get('score') if isinstance(value,dict) else None,'data':json.loads(x['payload']) if x else {'FirstName':'Deleted','LastName':'record'},'exempt':bool(x['exempt']) if x else False})
        batches=[dict(r) for r in c.execute('SELECT * FROM batches ORDER BY id DESC')]
        audit=[dict(r) for r in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 100')]
        submissions=[dict(r) for r in c.execute('SELECT * FROM submissions ORDER BY id DESC')]
        customers=[dict(id=r['id'],person_id=r['person_id'],exempt=r['exempt'],opted_out=r['opted_out'],**json.loads(r['payload'])) for r in c.execute('SELECT * FROM customers ORDER BY id LIMIT 100')]
        total=c.execute('SELECT count(*) FROM customers').fetchone()[0]
        suppressed=c.execute('SELECT count(*) FROM suppression WHERE active=1').fetchone()[0]
    return dict(mode=mode,api_configured=bool(os.environ.get('DROP_API_KEY')),requests=rows,batches=batches,audit=audit,submissions=submissions,customers=customers,total_customers=total,suppression_count=suppressed,api_base='https://api.drop.privacy.ca.gov/sandbox')

def command(action,p):
    mode=p.get('mode','demo');path=db(mode)
    if action=='demo_import':
        if mode!='demo':raise ValueError('Simulated batches are restricted to Demo mode')
        return ingest(path,make_zip(fixture_rows()),'demo')
    if action=='import':
        raw=base64.b64decode(p['data'],validate=True)
        return ingest(path,raw,mode)
    if action=='match':return match(path)
    if action=='execute':return execute_ready(path)
    if action=='review':return decide(path,p)
    if action=='download':
        if mode!='sandbox':raise ValueError('Select Sandbox mode')
        try:r=DropClient().download()
        except ApiError as e:
            with connect(path) as c:event(c,'api_download_failed',None,{'error':str(e)})
            raise
        if 'data' in r:
            raw=r.pop('data');r.update(ingest(path,raw,'sandbox'))
        with connect(path) as c:event(c,'api_download',None,r)
        return r
    if action=='upload':
        if mode!='sandbox':raise ValueError('Demo results cannot be uploaded to DROP')
        if not os.environ.get('DROP_API_KEY'):raise ValueError('Set DROP_API_KEY before uploading')
        if p.get('confirm')!='SANDBOX':raise ValueError('Explicit sandbox submission confirmation required')
        bid=int(p['batch_id']);amend=bool(p.get('amend'))
        with connect(path) as c:
            b=c.execute('SELECT source FROM batches WHERE id=?',(bid,)).fetchone()
            if not b or b['source']!='sandbox':raise ValueError('Batch is not a sandbox import')
            previous=c.execute('SELECT * FROM submissions WHERE batch_id=? ORDER BY id DESC LIMIT 1',(bid,)).fetchone()
            if previous and previous['state'] in ('sending','delivery_unknown','partial_or_rejected','queued_for_validation'):raise ValueError('Reconcile the previous submission in the portal/email first, then record its result below')
            if amend and (not previous or previous['state']!='validated'):raise ValueError('Amend requires a previously validated submission')
            if not amend and previous and previous['state']=='validated':raise ValueError('Already submitted. Use amendment for a correction')
            files=exports(path,bid,secrets.token_hex(4))
            sid=c.execute('INSERT INTO submissions(time,batch_id,operation,state,files,detail) VALUES(?,?,?,?,?,?)',(now(),bid,'amend' if amend else 'upload','sending',json.dumps(list(files)),'{}')).lastrowid
        try:
            result=DropClient().upload(files,amend)
        except ApiError as e:
            with connect(path) as c:
                c.execute('UPDATE submissions SET state=?,detail=? WHERE id=?',('delivery_unknown',json.dumps({'error':str(e)}),sid));event(c,'api_upload_failed',None,{'submission':sid,'error':str(e)})
            raise
        with connect(path) as c:
            c.execute('UPDATE submissions SET state=?,detail=? WHERE id=?',(result['state'],json.dumps(result),sid));event(c,'api_upload',None,{'submission':sid,**result})
        return result
    if action=='reconcile':
        if p.get('result') not in ('validated','retry_allowed'):raise ValueError('Invalid reconciliation result')
        if len(p.get('note','').strip())<8:raise ValueError('Record portal/email evidence before reconciling')
        with connect(path) as c:
            old=c.execute('SELECT * FROM submissions WHERE id=?',(int(p['id']),)).fetchone()
            if not old:raise ValueError('Submission missing')
            c.execute('UPDATE submissions SET state=? WHERE id=?',(p['result'],int(p['id'])))
            event(c,'manual_reconciliation',None,{'submission':p['id'],'result':p['result'],'note':p['note'],'prior_state':old['state']})
        return {'state':p['result']}
    raise ValueError('Unknown action')

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def send(self,code,data,ctype='application/json',filename=None):
        if not isinstance(data,bytes):data=json.dumps(data).encode()
        self.send_response(code);self.send_header('Content-Type',ctype);self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Frame-Options','DENY')
        if filename:self.send_header('Content-Disposition','attachment; filename="'+filename+'"')
        self.end_headers();self.wfile.write(data)
    def valid_host(self):return self.headers.get('Host') in (f'127.0.0.1:{PORT}',f'localhost:{PORT}')
    def do_GET(self):
        if not self.valid_host():return self.send(403,{'error':'Invalid host'})
        u=urlparse(self.path);q=parse_qs(u.query);mode=q.get('mode',['demo'])[0]
        try:
            if u.path=='/':return self.send(200,(ROOT/'static'/'index.html').read_text().replace('__TOKEN__',TOKEN).encode(),'text/html; charset=utf-8')
            if u.path=='/api/state':return self.send(200,state(mode))
            if u.path=='/review.js':return self.send(200,(ROOT/'static'/'review.js').read_bytes(),'text/javascript; charset=utf-8')
            if u.path=='/api/case':return self.send(200,get_case(db(mode),q['id'][0]),filename='review_case.json')
            if u.path=='/api/export':
                files=exports(db(mode),int(q['batch_id'][0]));out=io.BytesIO()
                with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
                    for n,b in files.items():z.writestr(n,b)
                return self.send(200,out.getvalue(),'application/zip','drop_status_responses.zip')
            if u.path=='/api/audit':
                with connect(db(mode)) as c:rows=[dict(r) for r in c.execute('SELECT * FROM audit ORDER BY id')]
                return self.send(200,json.dumps(rows,indent=2).encode(),'application/json','audit.json')
            return self.send(404,{'error':'Not found'})
        except (ValueError,KeyError) as e:return self.send(400,{'error':str(e)})
    def do_POST(self):
        if not self.valid_host() or self.headers.get('X-CSRF-Token')!=TOKEN:return self.send(403,{'error':'Invalid local session; reload the page'})
        try:
            n=int(self.headers.get('Content-Length','0'))
            if n>MAX_BYTES*2:raise ValueError('Request too large')
            p=json.loads(self.rfile.read(n))
            with LOCK:result=command(urlparse(self.path).path.removeprefix('/api/'),p)
            self.send(200,result)
        except (ValueError,KeyError,ApiError,zipfile.BadZipFile) as e:self.send(400,{'error':str(e)})
        except Exception:self.send(500,{'error':'Internal error. See local data; no completion was assumed.'})

def main():
    global PORT,DATA
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=8765);parser.add_argument('--data-dir',type=__import__('pathlib').Path,default=DATA);args=parser.parse_args();PORT=args.port;DATA=args.data_dir
    seed(db('demo'),write_files=True);seed(db('sandbox'))
    print(f'DROP Assistant: http://127.0.0.1:{PORT}',flush=True)
    print('Local synthetic database only. Official sandbox API available when DROP_API_KEY is set.',flush=True)
    ThreadingHTTPServer(('127.0.0.1',PORT),Handler).serve_forever()
if __name__=='__main__':main()
