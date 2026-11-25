"""All-report conditional win screening; unfinished samples never become losses."""
import argparse,collections,copy,hashlib,json,time,sys
from concurrent.futures import ProcessPoolExecutor,as_completed
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from audit_all_sss import load_catalog,ROOT,OPTIONS
from corpus_inputs import OUT
from combat import Unit,UnsupportedMechanism
from runtime import PlaneSkillSession
from battle_runner import run_battle

C=None;GAMES=None
# Diagnostic safety ceiling, NOT an asserted game rule. Actual0e25df/game3
# lasts32 rounds, so the previous30-round budget truncated a known valid path.
SIMULATION_ROUND_BUDGET=100
def default_input_path():
    index=ROOT/'work/plane/current_corpus_inputs.json'
    if not index.exists():return OUT/'compiled-inputs.json'
    path=(ROOT/json.loads(index.read_text(encoding='utf8'))['path']).resolve()
    if not path.is_relative_to(ROOT) or not path.is_file():raise ValueError('Invalid current corpus input path')
    return path

def initialize(input_path=None):
    global C,GAMES
    data=json.loads(Path(input_path or default_input_path()).read_text(encoding='utf8'))
    sources=data.get('catalog_sources')
    if sources is None:
        sources=json.loads((ROOT/'outputs/plane-replay-20261022/corpus-summary.json').read_text(encoding='utf8'))['catalog_sources']
    C=load_catalog(sources);C.hero=lru_cache(None)(C.hero);C.skill=lru_cache(None)(C.skill)
    GAMES={(g['match_id'],g['game']):g for g in data['games']}

def units_for(g):
    units=[]
    for x in g['units']:
        p=copy.deepcopy(x['panel']);h=C.tables['Hero'][x['template_id']]
        units.append(Unit(x['uid'],x['camp'],x['position'],x['template_id'],int(h['种族']),int(h['技能类型']),
            p['max_hp'],p['max_hp'],p['physical_attack'],p['magical_attack'],p['physical_defence'],p['magical_defence'],
            speed=p['speed'],panel=p))
    return units

def catalog_for(g):
    disabled=g.get('disabled_awakenings',[])
    if disabled and not g.get('historical_version_evidence'):
        raise ValueError('Historical awakening profile requires evidence')
    return C.without_awakenings(disabled) if disabled else C

def one(job,trace=False):
    mid,gi,seed=job;g=GAMES[(mid,gi)]
    def session_factory(seed):
        if not g['input_ready']:raise UnsupportedMechanism('missing panel inputs')
        units=units_for(g)
        return PlaneSkillSession(catalog_for(g),units,artifact_levels={u.uid:10 for u in units},seed=seed,
                                 mechanism_options=OPTIONS,strict_inputs=True)
    result=run_battle(session_factory,seed,round_budget=SIMULATION_ROUND_BUDGET,event_budget=30000,trace=trace)
    return dict(match_id=mid,game=gi,actual_winner=g['actual_winner'],**result)

def summarize(rows):
    grouped=collections.defaultdict(list)
    for row in rows:grouped[(row['match_id'],row['game'])].append(row)
    reports=[];hero_rows=collections.defaultdict(list)
    for key,samples in grouped.items():
        g=GAMES[key];finished=[r for r in samples if r['status']=='finished'];counts=collections.Counter(r.get('winner') for r in finished)
        # Jeffreys smoothing only ranks surprises; raw counts remain primary.
        p_actual=(counts[g['actual_winner']]+.5)/(len(finished)+1) if finished else None
        row=dict(match_id=key[0],game=key[1],actual_winner=g['actual_winner'],samples=len(samples),finished=len(finished),
            winner_counts=dict(counts),statuses=dict(collections.Counter(r['status'] for r in samples)),
            actual_winner_frequency=counts[g['actual_winner']]/len(finished) if finished else None,
            smoothed_actual_probability=p_actual,errors=dict(collections.Counter(r.get('error') for r in samples if r['status']=='blocked')),
            heroes=[dict(template_id=u['template_id'],name=u['name'],camp=u['camp']) for u in g['units']])
        reports.append(row)
        for tid in {u['template_id'] for u in g['units']}:hero_rows[tid].append(row)
    report=dict(scope='all existing reports are development data; no validation accuracy claim',
        sample_count=len(rows),game_count=len(reports),statuses=dict(collections.Counter(r['status'] for r in rows)),
        games=sorted(reports,key=lambda r:(r['actual_winner_frequency'] is None,r['actual_winner_frequency'] or 0)),
        heroes=[dict(template_id=t,name=C.hero(t).name,games=len(rs),
            high_confidence_wrong=sum(r['finished']==r['samples'] and r['finished']>=5 and r['actual_winner_frequency']<=.1 for r in rs),
            blocked_games=sum(r['finished']<r['samples'] for r in rs)) for t,rs in hero_rows.items()])
    return report

def main():
    p=argparse.ArgumentParser();p.add_argument('--samples',type=int,default=10);p.add_argument('--workers',type=int,default=6);p.add_argument('--label',required=True)
    p.add_argument('--case-file',type=Path);p.add_argument('--seed-start',type=int,default=0)
    p.add_argument('--inputs',type=Path,help='Explicit newly compiled inputs; historical defaults remain unchanged')
    args=p.parse_args();initialize(args.inputs);dest=OUT/f'{args.label}-samples.jsonl'
    if dest.exists():raise FileExistsError(dest)
    keys=list(GAMES)
    if args.case_file:
        selected=json.loads(args.case_file.read_text(encoding='utf8'))
        keys=[(x['match_id'],x['game']) for x in selected]
        if len(keys)!=len(set(keys)) or any(k not in GAMES for k in keys):raise ValueError('Invalid case selection')
    inputs=args.inputs or default_input_path()
    sources={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'work/plane').glob('*.py') if not p.name.startswith('test_')}
    manifest=dict(label=args.label,input_sha256=hashlib.sha256(inputs.read_bytes()).hexdigest(),source_sha256=sources,
        samples_per_game=args.samples,seed_start=args.seed_start,games=len(keys),workers=args.workers,artifact_level=10,options=OPTIONS,
        simulation_round_budget=SIMULATION_ROUND_BUDGET,event_budget=30000)
    (OUT/f'{args.label}-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    jobs=[(m,g,s) for s in range(args.seed_start,args.seed_start+args.samples) for m,g in keys];rows=[];start=time.monotonic()
    with dest.open('w',encoding='utf8') as f,ProcessPoolExecutor(args.workers,initializer=initialize,initargs=(str(inputs),)) as pool:
        pending=[pool.submit(one,j) for j in jobs]
        for task in as_completed(pending):
            row=task.result();rows.append(row);f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush()
            if len(rows)%50==0 or len(rows)==len(jobs):print(json.dumps(dict(done=len(rows),total=len(jobs),seconds=round(time.monotonic()-start),status=dict(collections.Counter(r['status'] for r in rows)))),flush=True)
    report=summarize(rows);(OUT/f'{args.label}-summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('games','heroes')},ensure_ascii=False))

if __name__=='__main__':main()
