"""Reproducible synthetic data; reserved example.test emails and fictional records."""
import csv, io, json, zipfile
from pathlib import Path
from store import init,connect,add_customer,ROOT
from normalization import identifier
FIELDS=['FirstName','LastName','DOB','ZIP','Email','Phone','VIN','MAID','CTVID']
def fixture_rows(n=1000):
    first=['Anna','Jordan','Mira','Noah','Sofia','Daniel','Avery','Kai','Elena','Ravi']
    last=['Smith','Jones','Patel','Reed','Garcia','Kim','Rivera','Chen','Brooks','Singh']
    rows=[]
    for i in range(n):
        r=dict(FirstName=first[i%10],LastName=last[(i//10)%10],DOB=f'{1960+i%45}-05-{1+i%28:02}',ZIP=str(90000+i%6000),Email=f'person{i:04}@example.test',Phone='',VIN=f'1HGCM82633A{i:06}',MAID=f'{i+1:032x}',CTVID=f'testtv{i:08}')
        if i==0: r['Email']=' Anna.Smith@Example.test '
        if i in (2,3): r['Email']='household@example.test'
        if i==5: r.update(FirstName='Anya',LastName='Stone',DOB='1988-02-12',ZIP='94103',Email='anya.primary@example.test')
        if i==6: r.update(FirstName='Ania',LastName='Stone',DOB='1988-02-12',ZIP='94103',Email='anya.secondary@example.test')
        if i==7: r['Phone']='+1 (202) 555-0107'
        rows.append(r)
    return rows
def csv_bytes(header,rows):
    s=io.StringIO(newline=''); w=csv.writer(s);w.writerow(header);w.writerows(rows);return s.getvalue().encode()
def make_zip(rows):
    entries={'Email':[(f'DEMO{i:08}',identifier('Email',rows[i])) for i in [0,1,2,4,5]+list(range(10,25))], 'Phone':[('PHONE0000001',identifier('Phone',rows[7]))], 'NDZ':[('NDZ000000001',identifier('NDZ',rows[8]))], 'NameVIN':[('VIN000000001',identifier('NameVIN',rows[9]))], 'MAID':[('MAID00000001',identifier('MAID',rows[30]))], 'CTVID':[('CTVID0000001',identifier('CTVID',rows[31]))]}
    entries['Email'].append(('ABSENT000001',identifier('Email',{'Email':'absent@example.test'})))
    b=io.BytesIO()
    with zipfile.ZipFile(b,'w',zipfile.ZIP_DEFLATED) as z:
        for kind,items in entries.items():z.writestr(f'20261114_0000_{kind}.csv',csv_bytes(['Id','Hash'],items))
    return b.getvalue()
def seed(path,n=1000,write_files=False):
    init(path)
    with connect(path) as c:
        if c.execute('SELECT count(*) FROM customers').fetchone()[0] or c.execute('SELECT count(*) FROM batches').fetchone()[0]:return
        rows=fixture_rows(n)
        for i,r in enumerate(rows):
            cid=add_customer(c,r,f'person-{i}',i==4)
            context='Fictional source record; no independent ownership verification.'
            if i in (2,3): context='Shared-email scenario: two deliberately distinct fixture people. Their household relationship and email ownership are not independently verified.'
            if i in (5,6): context='Synthetic scenario DUP-001: seed.py intentionally models two source records for one fictional person. This fixture label is not real-world identity evidence.'
            if i==4: context='EXEMPT-001 is a classroom exemption fixture, not an actual statutory exemption. Production would need an approved basis, scope and retention justification.'
            c.execute('INSERT INTO record_evidence VALUES(?,?,?,?,?)',(cid,'Synthetic generator','seed.py / fixture row '+str(i+1),'Synthetic fixture only',context))
    if write_files:
        d=Path(path).parent
        (d/'sample_customers.csv').write_bytes(csv_bytes(['customer_id','person_id','exempt']+FIELDS,[[i+1,f'person-{i}',int(i==4)]+[r[k] for k in FIELDS] for i,r in enumerate(rows)]))
        (d/'simulated_drop_requests.zip').write_bytes(make_zip(rows))
        (d/'ground_truth.json').write_text(json.dumps({'note':'Synthetic expected decisions before AI review. Internal row 7 is an intentionally ambiguous duplicate candidate for row 6; requires reviewer evidence.', 'request_count':26,'expected_initial_states':{'ready':22,'review':3,'complete':1},'special_cases':{'DEMO00000002':'shared household identifier: opt out','DEMO00000004':'synthetic exemption: reviewer approval','DEMO00000005':'possible internal duplicate: human review','ABSENT000001':'not found'}},indent=2))
if __name__=='__main__':seed(ROOT/'data'/'demo.sqlite3',write_files=True)
