import base64,csv,hashlib,io,json,re,zipfile
from pathlib import PurePosixPath
from contextlib import nullcontext
from store import connect,event,now
from normalization import LISTS
from ai import score,VERSION
MAX_BYTES=20*1024*1024

def parse_zip(data):
    if len(data)>MAX_BYTES:raise ValueError('Archive exceeds 20 MB prototype limit')
    parsed=[]; seen=set()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        infos=z.infolist()
        if len(infos)>100 or sum(i.file_size for i in infos)>MAX_BYTES:raise ValueError('Archive expands beyond prototype limits')
        for info in infos:
            if info.is_dir():continue
            fname=info.filename
            if PurePosixPath(fname).name!=fname or '\\' in fname:raise ValueError('Unsafe archive path')
            m=re.fullmatch(r'\d{8}_\d{4}_(Email|Phone|NDZ|NameVIN|MAID|CTVID|Removed)(?:_[A-Za-z0-9]{1,10})?\.csv',fname)
            if not m:raise ValueError('Unexpected DROP filename: '+fname)
            kind=m[1]; reader=csv.DictReader(io.StringIO(z.read(info).decode('utf-8-sig')))
            expected=['Id','Hash','ListType'] if kind=='Removed' else ['Id','Hash']
            if reader.fieldnames!=expected:raise ValueError('Invalid CSV header in '+fname)
            for row in reader:
                if None in row or any(v is None for v in row.values()):raise ValueError('Invalid CSV row width')
                rid=row['Id']; h=row['Hash']
                if not re.fullmatch('[A-Za-z0-9]{12}',rid):raise ValueError('Work item ID must be 12 case-sensitive alphanumerics')
                try: valid=len(base64.b64decode(h,validate=True))==32
                except Exception:valid=False
                if not valid:raise ValueError('Invalid SHA-256 Base64 identifier')
                k=row.get('ListType',kind)
                k=next((v for v in LISTS if v.lower()==k.lower()),None)
                if k is None:raise ValueError('Unknown list type')
                if rid in seen:raise ValueError('Duplicate work item ID within archive')
                seen.add(rid); parsed.append((rid,k,h,fname,kind=='Removed'))
    return parsed

def ingest(path,data,source):
    if source not in ('demo','sandbox'):raise ValueError('Invalid source')
    records=parse_zip(data);digest=hashlib.sha256(data).hexdigest()
    with connect(path) as c:
        found=c.execute('SELECT id FROM batches WHERE digest=?',(digest,)).fetchone()
        if found:return {'batch_id':found[0],'duplicate':True,'count':0}
        bid=c.execute('INSERT INTO batches(digest,source,received) VALUES(?,?,?)',(digest,source,now())).lastrowid
        for rid,k,h,fname,removed in records:
            old=c.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
            if old and (old['kind']!=k or old['hash']!=h):raise ValueError('Work item ID conflicts with earlier identifier')
            if removed:
                c.execute('UPDATE requests SET active=0 WHERE id=?',(rid,));c.execute('UPDATE suppression SET active=0 WHERE request_id=?',(rid,))
                event(c,'request_removed',rid,{'batch':bid});continue
            if old:continue
            c.execute('INSERT INTO requests(id,kind,hash,file_name,batch_id) VALUES(?,?,?,?,?)',(rid,k,h,fname,bid))
            c.execute('INSERT INTO suppression VALUES(?,?,?,1)',(rid,k,h))
        event(c,'batch_imported',None,{'batch':bid,'source':source,'rows':len(records)})
    return {'batch_id':bid,'duplicate':False,'count':len(records)}

def match(path):
    with connect(path) as c:
        customers={r['id']:dict(r) for r in c.execute('SELECT * FROM customers')}
        payloads={i:json.loads(r['payload']) for i,r in customers.items()}
        count=0
        for req in c.execute("SELECT * FROM requests WHERE state='new' AND active=1").fetchall():
            direct=[r[0] for r in c.execute('SELECT customer_id FROM hash_index WHERE kind=? AND hash=?',(req['kind'],req['hash']))]
            person_ids={customers[i]['person_id'] for i in direct}
            exact=sorted(i for i,r in customers.items() if r['person_id'] in person_ids)
            suggested=[]
            if len(person_ids)==1:
                for i,p in payloads.items():
                    if i in exact:continue
                    # Blocking avoids all-pairs comparisons; broad review heuristic is explicit.
                    if not any(p.get('LastName')==payloads[j].get('LastName') and p.get('ZIP')==payloads[j].get('ZIP') for j in exact):continue
                    value=max(score(payloads[j],p) for j in exact)
                    if value>=.6:suggested.append({'id':i,'score':value})
            shared=len(person_ids)>1;exempt=any(customers[i]['exempt'] for i in exact)
            if not exact:state='complete';status=5;why='No exact hash match in this synthetic database.'
            elif shared:state='review';status=None;why='Shared identifier links different internal people. Review opt-out.'
            elif exempt:state='review';status=None;why='Synthetic exemption flag requires reviewer rationale.'
            elif suggested:state='review';status=None;why='Experimental ML found an internal duplicate candidate. Review identity evidence.'
            else:state='ready';status=None;why='Exact hash match; ready to delete synthetic records.'
            details=dict(exact=exact,suggested=suggested,shared=shared,exempt=exempt)
            c.execute('UPDATE requests SET state=?,status=?,candidates=?,explanation=? WHERE id=?',(state,status,json.dumps(details),why,req['id']))
            event(c,'matched',req['id'],{'state':state,'exact':exact,'suggested':suggested,'model':VERSION});count+=1
    return {'processed':count}

def act(path,rid,action,note='',include_suggestions=False,_connection=None,selected_ids=None):
    if action not in ('delete','optout','exempt'):raise ValueError('Unknown action')
    with (nullcontext(_connection) if _connection is not None else connect(path)) as c:
        if _connection is None:c.execute('BEGIN IMMEDIATE')
        c.execute('CREATE TABLE IF NOT EXISTS deleted_records(id INTEGER PRIMARY KEY, request_id TEXT)')
        r=c.execute('SELECT * FROM requests WHERE id=?',(rid,)).fetchone()
        if not r or not r['active']:raise ValueError('No active request')
        if r['state']=='complete':return {'id':rid,'already_complete':True}
        if r['state'] not in ('ready','review'):raise ValueError('Match this request first')
        d=json.loads(r['candidates'])
        if r['state']=='review' and len(note.strip())<8:raise ValueError('Enter a meaningful reviewer rationale (8+ characters)')
        if d['shared'] and action!='optout':raise ValueError('Shared identifiers require opt-out in this prototype')
        if action=='optout' and not d['shared']:raise ValueError('Opt-out is reserved for shared identifiers')
        if d['exempt'] and action!='exempt':raise ValueError('Exempt synthetic records cannot be deleted')
        if action=='exempt' and not d['exempt']:raise ValueError('No exemption evidence recorded')
        if action=='exempt' and any(not c.execute('SELECT exempt FROM customers WHERE id=?',(i,)).fetchone()[0] for i in d['exact']):raise ValueError('Mixed exemption scope requires specialist resolution')
        ids=d['exact']+([i['id'] for i in d['suggested']] if include_suggestions else [])
        if selected_ids is not None:ids=d['exact']+list(selected_ids)
        ids=sorted(set(ids))
        if include_suggestions and len(note.strip())<8:raise ValueError('Identity-link approval requires rationale')
        for i in ids:
            current=c.execute('SELECT * FROM customers WHERE id=?',(i,)).fetchone()
            if action=='delete':
                if current and current['exempt']:raise ValueError('Linked record has an exemption; manual resolution required')
                if not current and not c.execute('SELECT 1 FROM deleted_records WHERE id=?',(i,)).fetchone():raise ValueError('Record disappeared without deletion evidence')
                c.execute('INSERT OR IGNORE INTO deleted_records VALUES(?,?)',(i,rid));c.execute('DELETE FROM customers WHERE id=?',(i,))
            elif action=='optout':c.execute('UPDATE customers SET opted_out=1 WHERE id=?',(i,))
        status={'delete':3,'optout':4,'exempt':2}[action]
        c.execute("UPDATE requests SET state='complete',status=? WHERE id=?",(status,rid))
        event(c,action,rid,{'record_ids':ids,'reviewer_note':note,'include_suggestions':include_suggestions,'scope':'synthetic local database'})
    return {'id':rid,'status':status,'affected':len(ids)}

def execute_ready(path):
    with connect(path) as c: ids=[r[0] for r in c.execute("SELECT id FROM requests WHERE state='ready' AND active=1")]
    return {'completed':[act(path,r,'delete') for r in ids]}

def exports(path,batch_id,suffix=''):
    if suffix and not re.fullmatch('[A-Za-z0-9]{1,10}',suffix):raise ValueError('Invalid filename suffix')
    with connect(path) as c:
        rows=c.execute('SELECT * FROM requests WHERE batch_id=? AND active=1',(batch_id,)).fetchall()
    if not rows:raise ValueError('No active work items in this batch')
    if any(r['state']!='complete' for r in rows):raise ValueError('Resolve all active work items in this batch before exporting')
    grouped={}
    for r in rows:grouped.setdefault(r['file_name'],[]).append((r['id'],r['status']))
    files={}
    for fname,items in grouped.items():
        s=io.StringIO(newline='');w=csv.writer(s);w.writerow(['Id','Status']);w.writerows(items)
        base=re.match(r'(\d{8}_\d{4}_(?:Email|Phone|NDZ|NameVIN|MAID|CTVID))',fname)[1]
        out=base+('_'+suffix if suffix else '')+'.csv';files[out]=s.getvalue().encode()
    return files
