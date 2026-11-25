"""Read all available plane matches and inventory missing panel provenance."""
import collections,hashlib,json,sqlite3
from pathlib import Path
from audit_all_sss import load_catalog,ROOT
OUT=ROOT/'outputs/plane-corpus-validation-20261022'

def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def load_matches():
    matches={}
    with sqlite3.connect(f'file:{ROOT / "outputs/ancient_war.sqlite"}?mode=ro',uri=True) as db:
        for mid,raw in db.execute('select match_id,payload_json from plane_match'):
            roster={gi:json.loads(r) for gi,r in db.execute('select game_index,roster_json from plane_game where match_id=?',(mid,))}
            matches[mid]=dict(match_id=mid,payload=json.loads(raw),rosters=roster,sources=['formal_sqlite'])
    p=ROOT/'outputs/plane-match17-diagnosis-20261022/replay-0.json'
    raw=json.loads(p.read_text(encoding='utf8'));payload={k:v for k,v in raw.items() if k not in ('pd','rId')}
    mid=hashlib.sha256(canonical(payload).encode()).hexdigest()
    if mid not in matches:
        inp=json.loads((ROOT/'outputs/plane-match17-20261022/match-input-observed.json').read_text(encoding='utf8'))
        rosters={}
        for gi,g in enumerate(('c','e','g'),1):
            rosters[gi]=[]
            for camp,side in ((1,'attackers'),(2,'defencers')):
                for pos,h in enumerate(payload[g][side]):
                    slot=next(s for s in inp['slots'] if s['team']==gi and s['position']==pos and s['side']==('left' if camp==1 else 'right'))
                    assert slot['template_id']==h['b']
                    rosters[gi].append(dict(camp=camp,position=pos,template_id=h['b'],initial_hp=h['c'],star=slot['star'],advancement=slot['advancement'],metadata_matched=True))
        matches[mid]=dict(match_id=mid,payload=payload,rosters=rosters,sources=[str(p.relative_to(ROOT))])
    return list(matches.values())

def inventory():
    catalog=load_catalog();matches=load_matches();entries=collections.defaultdict(list)
    for m in matches:
        for gi,rs in m['rosters'].items():
            for r in rs:
                k=(r['template_id'],r.get('star'),r.get('advancement'),r['initial_hp'])
                entries[k].append(dict(match_id=m['match_id'],game=gi,camp=r['camp'],position=r['position']))
    panels=json.loads((ROOT/'outputs/plane-match17-20261022/observed-panels.json').read_text(encoding='utf8'))
    known={(p['template_id'],p['star'],p['advancement']):p for p in panels}
    rows=[]
    for (tid,star,adv,hp),locations in entries.items():
        panel=known.get((tid,star,adv))
        rows.append(dict(template_id=tid,name=catalog.hero(tid).name,star=star,advancement=adv,initial_hp=hp,
            count=len(locations),known_panel=panel,status='panel_available' if panel else 'metadata_missing' if star is None or adv is None else 'panel_missing',locations=locations))
    result=dict(matches=len(matches),games=sum(len(m['rosters']) for m in matches),slots=sum(r['count'] for r in rows),
        templates=len({r['template_id'] for r in rows}),unique_inputs=len(rows),status_slots=dict(collections.Counter({s:sum(r['count'] for r in rows if r['status']==s) for s in {r['status'] for r in rows}})),rows=rows)
    OUT.mkdir(exist_ok=True,parents=True)
    (OUT/'input-inventory.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},ensure_ascii=False))
    print('missing panels',[(r['name'],r['star'],r['advancement'],r['count']) for r in sorted(rows,key=lambda x:-x['count']) if r['status']=='panel_missing'][:20])

if __name__=='__main__':inventory()
