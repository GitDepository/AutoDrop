import sqlite3, json
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parent
def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def connect(path):
    c=sqlite3.connect(path); c.row_factory=sqlite3.Row; c.execute('PRAGMA foreign_keys=ON'); c.execute('PRAGMA secure_delete=ON'); return c
def init(path):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    with connect(path) as c:
        c.executescript('''
CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY, person_id TEXT NOT NULL, payload TEXT NOT NULL, exempt INTEGER NOT NULL DEFAULT 0, opted_out INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS hash_index(kind TEXT, hash TEXT, customer_id INTEGER REFERENCES customers(id) ON DELETE CASCADE, PRIMARY KEY(kind,hash,customer_id));
CREATE TABLE IF NOT EXISTS batches(id INTEGER PRIMARY KEY, digest TEXT UNIQUE, source TEXT, received TEXT);
CREATE TABLE IF NOT EXISTS requests(id TEXT PRIMARY KEY, kind TEXT, hash TEXT, file_name TEXT, batch_id INTEGER REFERENCES batches(id), state TEXT DEFAULT 'new', status INTEGER, candidates TEXT DEFAULT '[]', explanation TEXT DEFAULT '', active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS suppression(request_id TEXT PRIMARY KEY, kind TEXT, hash TEXT, active INTEGER DEFAULT 1);
CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, time TEXT, event TEXT, request_id TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS review_cases(request_id TEXT PRIMARY KEY, owner TEXT DEFAULT '', due_date TEXT DEFAULT '', updated TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS record_evidence(customer_id INTEGER PRIMARY KEY REFERENCES customers(id) ON DELETE CASCADE, source TEXT, source_ref TEXT, verification TEXT, context TEXT);
CREATE TABLE IF NOT EXISTS submissions(id INTEGER PRIMARY KEY, time TEXT, batch_id INTEGER, operation TEXT, state TEXT, files TEXT, detail TEXT);
''')
def event(c,kind,rid,detail): c.execute('INSERT INTO audit(time,event,request_id,detail) VALUES(?,?,?,?)',(now(),kind,rid,json.dumps(detail,ensure_ascii=False)))
def add_customer(c,row,person_id,exempt=False):
    from normalization import LISTS,identifier
    hashes=[(k,identifier(k,row)) for k in LISTS]; hashes=[(k,h) for k,h in hashes if h]
    for k,h in hashes:
        if c.execute('SELECT 1 FROM suppression WHERE kind=? AND hash=? AND active=1',(k,h)).fetchone():
            event(c,'intake_blocked',None,{'kind':k}); return None
    n=c.execute('INSERT INTO customers(person_id,payload,exempt) VALUES(?,?,?)',(person_id,json.dumps(row),int(exempt))).lastrowid
    c.executemany('INSERT INTO hash_index VALUES(?,?,?)',[(k,h,n) for k,h in hashes]); return n
