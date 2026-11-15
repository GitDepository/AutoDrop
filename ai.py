"""Small experimental logistic entity-link ranker. No dependencies or remote PII calls.
Trained only on artificial examples; probabilities are NOT calibrated real-world certainty.
Never grants deletion authority. Features exclude DROP hashes and internal person IDs.
"""
import math, random
from difflib import SequenceMatcher
from normalization import standardize, name
VERSION='synthetic-logistic-v1'
def features(a,b):
    sim=lambda x,y: SequenceMatcher(None,x,y).ratio() if x and y else 0.
    eq=lambda k: float(bool(a.get(k)) and a.get(k)==b.get(k))
    return [1.,sim(name(a.get('FirstName','')),name(b.get('FirstName',''))),sim(name(a.get('LastName','')),name(b.get('LastName',''))),eq('DOB'),eq('ZIP'),float(bool(a.get('Phone')) and standardize('Phone',a['Phone'])==standardize('Phone',b.get('Phone','')))]
def sigmoid(x): return 1/(1+math.exp(-max(-30,min(30,x))))
def train():
    rng=random.Random(71); examples=[]
    first=['Anna','Jordan','Mira','Noah','Sofia','Daniel','Avery','Kai','Elena','Ravi']
    last=['Smith','Jones','Patel','Reed','Garcia','Kim','Rivera','Chen','Brooks','Singh']
    def person(): return dict(FirstName=rng.choice(first),LastName=rng.choice(last),DOB=f'{rng.randrange(1960,2005)}-05-12',ZIP=str(rng.randrange(90000,96000)),Phone=f'202555{rng.randrange(100,200):04}')
    for i in range(1600):
        a=person(); same=i%2==0; b=dict(a) if same else person()
        if same:
            if rng.random()<.7: b['FirstName']=b['FirstName'][:-1]
            if rng.random()<.35: b['Phone']=''
            if rng.random()<.2: b['ZIP']=''
            if rng.random()<.1: b['DOB']=''
        elif rng.random()<.4: # difficult nonmatches sharing household traits
            b['LastName']=a['LastName']; b['ZIP']=a['ZIP']; b['Phone']=a['Phone']
        examples.append((features(a,b),float(same)))
    weights=[0.]*6
    for _ in range(140):
        grad=[0.]*6
        for x,y in examples:
            err=sigmoid(sum(w*v for w,v in zip(weights,x)))-y
            for j in range(6): grad[j]+=err*x[j]
        weights=[w-.4*(g/len(examples)+(.002*w if j else 0)) for j,(w,g) in enumerate(zip(weights,grad))]
    return weights
WEIGHTS=train()
def score(a,b): return round(sigmoid(sum(w*x for w,x in zip(WEIGHTS,features(a,b)))),4)
