"""Deterministic memory-snapshot import and standalone plane simulation CLI.

No image recognition or model service. Combat uses the same scheduler as the
report corpus. Missing/blocked samples remain unknown, never become losses.
"""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import shutil
import time

from audit_all_sss import ROOT, OPTIONS, load_catalog
from corpus_panels import compile_panel
import corpus_simulation as simulator


CURRENT_INPUT_RULES = dict(
    level=150, artifact_skill_level=10, panel_growth_mode='native_float32',
    star='observed', advancement='observed', panel_basis='knowledge_base_formula',
    evidence_manifest='outputs/simulations/位面战/未定区/20261117-large-score-followup-50/native-panel-cross-report-verification.json')


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def compile_snapshot(snapshot,catalog):
    if snapshot.get('kind')!='plane_runtime_memory' or snapshot.get('missing'):
        raise ValueError('A complete runtime plane snapshot is required')
    matches=snapshot['matches']
    if not matches or len({m['rank'] for m in matches})!=len(matches):
        raise ValueError('Missing or duplicate match ranks')
    games=[]
    for match in matches:
        slots=match['slots']
        expected={(c,t,p) for c in (1,2) for t in (1,2,3) for p in range(6)}
        if len(slots)!=36 or {(s['camp'],s['team'],s['position']) for s in slots}!=expected:
            raise ValueError('Missing or duplicate camp/team/position')
        for team in (1,2,3):
            units=[]
            for s in sorted((s for s in slots if s['team']==team),key=lambda s:(s['camp'],s['position'])):
                tid=s['template_id'];star=s['star'];adv=s['advancement']
                if (type(star) is not int or not 1<=star<=6 or type(adv) is not int or not 0<=adv<=50
                        or s.get('level')!=150 or tid not in catalog.tables['Hero']):
                    raise ValueError(f'Invalid or unknown hero input: {s}')
                panel=compile_panel(catalog,tid,star,adv,battle=True,
                    growth_mode=CURRENT_INPUT_RULES['panel_growth_mode'],
                    artifact_level=CURRENT_INPUT_RULES['artifact_skill_level'])
                units.append(dict(uid=f"{s['camp']}:{s['position']}",camp=s['camp'],position=s['position'],
                    template_id=tid,name=catalog.hero(tid).name,star=star,advancement=adv,panel=panel,
                    input_source='runtime_plane_arrays',hp_source='calculated_from_knowledge_rules',
                    panel_source=f"knowledge_formula_{CURRENT_INPUT_RULES['panel_growth_mode']}"))
            games.append(dict(match_id=f"live-rank-{match['rank']}",rank=match['rank'],game=team,
                players=match['players'],units=units,input_ready=True,actual_winner=None))
    return dict(schema=1,observed_at=snapshot['observed_at'],roster_sha256=digest(matches),games=games,
        catalog_sources=dict(catalog.sources),
        input_rules=dict(CURRENT_INPUT_RULES),
        assumptions=['Wing magic-to-HP bonus-plus-base mechanism; historical residual up to6 HP',
            'Hela copy-source threshold50 hypothesis; observed42 propagates and55+ does not',
            'independent team games for BO3 aggregation'])


def initialize(path,scenario):
    data=json.loads(Path(path).read_text(encoding='utf8'))
    simulator.C=load_catalog(data.get('catalog_sources'))
    simulator.C.hero=lru_cache(None)(simulator.C.hero)
    simulator.C.skill=lru_cache(None)(simulator.C.skill)
    simulator.GAMES={(g['match_id'],g['game']):g for g in data['games']}
    simulator.OPTIONS=dict(OPTIONS)
    if scenario=='text48_fractional59':
        simulator.OPTIONS.update(hela_defence_cap_percent=48,fish59_integer_percent=False)


def bo3(p1, p2, p3):
    split_first_two = p1 * (1 - p2) + (1 - p1) * p2
    return min(1.0, p1 * p2 + split_first_two * p3)


def summarize_game(rows):
    n=len(rows)
    wins=Counter(r.get('winner') for r in rows if r['status']=='finished' and r.get('winner') in (1,2))
    unknown=n-wins[1]-wins[2]
    return dict(samples=n,left_wins=wins[1],right_wins=wins[2],unknown=unknown,
        left_probability=wins[1]/n if n and not unknown else None,
        left_sample_bounds=[wins[1]/n,(wins[1]+unknown)/n] if n else [0,1],
        statuses=dict(Counter(r['status'] for r in rows)),
        errors=dict(Counter(r.get('error','undecided') for r in rows if r['status']!='finished')))


def summarize(compiled,rows):
    games=[];matches=[]
    for g in compiled['games']:
        samples=[r for r in rows if (r['match_id'],r['game'])==(g['match_id'],g['game'])]
        games.append(dict(rank=g['rank'],game=g['game'],players=g['players'],**summarize_game(samples)))
    for rank in sorted({g['rank'] for g in games},reverse=True):
        gs=sorted((g for g in games if g['rank']==rank),key=lambda g:g['game'])
        lo=bo3(*(g['left_sample_bounds'][0] for g in gs))
        hi=bo3(*(g['left_sample_bounds'][1] for g in gs))
        p=bo3(*(g['left_probability'] for g in gs)) if all(g['left_probability'] is not None for g in gs) else None
        pick=1 if lo>.5 else 2 if hi<.5 else None
        matches.append(dict(rank=rank,players=gs[0]['players'],left_probability=p,
            left_sample_bounds=[lo,hi],prediction_camp=pick,
            prediction=gs[0]['players'][pick-1] if pick else None))
    return dict(matches=matches,games=games,statuses=dict(Counter(r['status'] for r in rows)),
        sample_bounds_meaning='unknown samples assigned to either camp; not statistical confidence intervals')


def finished_identical_run(roster_sha256,source_hashes,args):
    """A completed run with identical inputs, source, options and seeds, if any."""
    outputs=ROOT/'outputs'
    for manifest_path in sorted([*outputs.glob('*/manifest.json'),*outputs.glob('*/*/manifest.json')]):
        try:manifest=json.loads(manifest_path.read_text(encoding='utf8'))
        except (OSError,ValueError):continue
        if (manifest.get('roster_sha256')==roster_sha256 and manifest.get('source_sha256')==source_hashes
                and manifest.get('options')==OPTIONS and manifest.get('seed_start')==args.seed_start
                and manifest.get('samples_per_game')==args.samples and manifest.get('scenarios')==args.scenarios
                and (manifest_path.parent/'summary.json').exists()):
            return manifest_path.parent
    return None


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--snapshot',type=Path,help='Saved plane_memory JSON; omit to read the current loaded cache')
    p.add_argument('--ranks',nargs='+',type=int,default=[15,14,13,12,11])
    p.add_argument('--out',type=Path,required=True)
    p.add_argument('--samples',type=int,default=500)
    p.add_argument('--workers',type=int,default=6)
    p.add_argument('--seed-start',type=int,default=2026102300)
    p.add_argument('--scenarios',nargs='+',choices=['candidate54_integer59','text48_fractional59'],
                   default=['candidate54_integer59'])
    p.add_argument('--force-rerun',action='store_true',
                   help='Run even if a finished run has the same roster, source, options and seeds')
    args=p.parse_args();dest=args.out.resolve()
    if not dest.is_relative_to(ROOT) or args.samples<1 or args.workers<1:
        raise ValueError('Use project output folder and positive samples/workers')
    if dest.exists():raise FileExistsError(dest)
    if args.snapshot:
        snapshot=json.loads(args.snapshot.read_text(encoding='utf8'))
    else:
        from plane_memory import PlaneMemoryReader
        reader=PlaneMemoryReader()
        try:
            start=time.perf_counter();reader.start();bound=time.perf_counter()
            snapshot=reader.read(args.ranks);snapshot['bind_seconds']=bound-start
        finally:reader.stop()
    files=list((ROOT/'work/plane').glob('*.py'))+[ROOT/'work/plane/prediction_panel.html']+[ROOT/'work/app'/name for name in
        ('runtime_source.py','runtime_memory.py','runtime_layout.json')]
    source_hashes={str(source.relative_to(ROOT)):hashlib.sha256(source.read_bytes()).hexdigest() for source in files}
    previous=None if args.force_rerun else finished_identical_run(digest(snapshot['matches']),source_hashes,args)
    if previous:
        print(f'Identical finished run already exists (same roster, source, options, seeds): {previous}',flush=True)
        print('Its results would be reproduced exactly; pass --force-rerun to run again.',flush=True)
        return
    dest.mkdir(parents=True,exist_ok=False)
    save=lambda name,value:(dest/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf8')
    save('memory-snapshot.json',snapshot)
    compiled=compile_snapshot(snapshot,load_catalog());save('compiled-inputs.json',compiled)
    source_dir=dest/'source';source_dir.mkdir()
    for source in files:shutil.copy2(source,source_dir/source.name)
    save('manifest.json',dict(created_at=datetime.now(timezone(timedelta(hours=8))).isoformat(),
        samples_per_game=args.samples,seed_start=args.seed_start,scenarios=args.scenarios,
        roster_sha256=digest(snapshot['matches']),source_sha256=source_hashes,
        options=OPTIONS,
        sensitivity_overrides=(dict(hela_defence_cap_percent=48,fish59_integer_percent=False)
            if 'text48_fractional59' in args.scenarios else None),
        actual_results_used=False,ocr_required=False,language_model_required=False,
        simulation_round_budget=simulator.SIMULATION_ROUND_BUDGET,event_budget=30000))
    reports={};started=time.monotonic()
    jobs=[(g['match_id'],g['game'],args.seed_start+seed) for seed in range(args.samples) for g in compiled['games']]
    for scenario in args.scenarios:
        rows=[]
        with (dest/f'{scenario}-samples.jsonl').open('x',encoding='utf8') as f,ProcessPoolExecutor(
                args.workers,initializer=initialize,initargs=(str(dest/'compiled-inputs.json'),scenario)) as pool:
            pending=[pool.submit(simulator.one,job) for job in jobs]
            for future in as_completed(pending):
                row=future.result();rows.append(row)
                f.write(json.dumps(row,ensure_ascii=False)+'\n');f.flush()
                if len(rows)%150==0 or len(rows)==len(jobs):
                    print(json.dumps(dict(scenario=scenario,done=len(rows),total=len(jobs),
                        seconds=round(time.monotonic()-started),statuses=dict(Counter(r['status'] for r in rows)))),flush=True)
        reports[scenario]=summarize(compiled,rows);save(f'{scenario}-summary.json',reports[scenario])
    changed=[str(path.relative_to(ROOT)) for path in files if hashlib.sha256(path.read_bytes()).hexdigest()!=source_hashes[str(path.relative_to(ROOT))]]
    if changed:raise RuntimeError(f'Source changed during simulation: {changed}')
    save('summary.json',dict(finished_at=datetime.now(timezone(timedelta(hours=8))).isoformat(),
        seconds=time.monotonic()-started,scenarios=reports,source_unchanged=True))
    from prediction_panel import write_panel
    panel_path=write_panel(dest)
    print(f'Prediction panel: {panel_path}',flush=True)
    print(json.dumps({key:value['matches'] for key,value in reports.items()},ensure_ascii=False),flush=True)


if __name__=='__main__':main()
