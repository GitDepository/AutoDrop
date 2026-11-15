"""DROP v1.2 normalization; exact matching only, never fuzzy hash comparison."""
import base64, hashlib, re, unicodedata
from datetime import datetime
LISTS = ('Email','Phone','NDZ','NameVIN','MAID','CTVID')
SPECIAL = dict(zip('ßæœøłðþđħŋıĳŀ', ['ss','ae','oe','o','l','d','th','d','h','n','i','ij','l']))
GREEK = dict(zip('αβγδεζηθικλμνξοπρσςτυφχψω', ['a','v','g','d','e','z','i','th','i','k','l','m','n','x','o','p','r','s','s','t','y','f','ch','ps','o']))
CYR = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюяєіїґў', ['a','b','v','g','d','e','yo','zh','z','i','y','k','l','m','n','o','p','r','s','t','u','f','kh','ts','ch','sh','shch','','y','','e','yu','ya','ye','i','yi','g','u']))
def name(value):
    value = ''.join(SPECIAL.get(c, CYR.get(c,c)) for c in value.lower())
    value = ''.join(''.join(x for x in unicodedata.normalize('NFD',c) if not unicodedata.combining(x)) if ('LATIN' in unicodedata.name(c,'') or 'GREEK' in unicodedata.name(c,'')) else c for c in value)
    return ''.join(GREEK.get(c,c) for c in value if c.isalnum())
def standardize(kind,value):
    value = str(value or '').strip()
    if kind == 'Email': return ''.join(value.lower().split())
    if kind == 'Phone': return re.sub(r'[^0-9]','',value)[-10:]
    if kind in ('FirstName','LastName'): return name(value)
    if kind == 'DOB':
        for fmt in ('%Y-%m-%d','%Y%m%d','%m/%d/%Y'):
            try: return datetime.strptime(value,fmt).strftime('%Y%m%d')
            except ValueError: pass
        return ''
    if kind == 'ZIP':
        if re.fullmatch(r'\d{5}-\d{4}',value): value=value[:5]
        return re.sub(r'[^a-z0-9]','',value.lower()).lstrip('0')[:5]
    if kind == 'MAID':
        result=re.sub('[^a-f0-9]','',value.lower())
        return result if len(result)==32 else ''
    if kind in ('VIN','CTVID'):
        result=re.sub('[^a-z0-9]','',value.lower())
        return result if (len(result)==17 if kind=='VIN' else 8<=len(result)<=32) else ''
    raise ValueError('Unsupported identifier')
def sha(value): return base64.b64encode(hashlib.sha256(value.encode('utf-8')).digest()).decode()
def identifier(kind,row):
    fields={'NDZ':('FirstName','LastName','DOB','ZIP'),'NameVIN':('FirstName','LastName','VIN')}.get(kind,(kind,))
    vals=[standardize(f,row.get(f,'')) for f in fields]
    if any(not v for v in vals): return None
    return sha(''.join(sha(v) for v in vals)) if len(vals)>1 else sha(vals[0])
