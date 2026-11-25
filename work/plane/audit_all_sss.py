"""All captured SSS: source coverage and explicit-input integrated smoke trials.

This is not a full mechanism certificate or replay validation. Synthetic level10
artifacts and chosen candidate branches are test fixtures, never plane defaults.
"""
from collections import Counter
from contextlib import closing
from dataclasses import asdict
import json
from pathlib import Path
import sqlite3

from catalog import PlaneCatalog, load_current_catalog
from combat import Unit, UnsupportedMechanism
from runtime import PlaneSkillSession

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'outputs/plane-replay-20261022'
OPTIONS=dict(wolf_1550_policy='ignored',effect99_chance_policy='config',
             effect55_donor_cap_policy='recipient_cap',effect58_initial_hit=1,
             # Explicit working hypothesis: target extension has no attack to
             # extend for a pure heal. Existing corpus lacks the4-mage healer
             # condition; keep the choice visible in every simulation export.
             blessing218_healer_policy='no_extra_target',
             # Retain observed control exceptions; otherwise cancel a blocked
             # reaction instead of abandoning the whole simulated battle.
             blocked_reaction_policy='skip',compact_stat_debuffs=True,
             # Joint, report-supported hypothesis; keep48%/fractional59 as
             # an explicit sensitivity scenario rather than rewriting source.
             hela_defence_cap_percent=54,fish59_integer_percent=True,
             #42 instances propagate;55/59/60 do not. Exact cutoff unresolved.
             hela_copy_source_limit=50,
             # Scoped report-backed branches. Fish prose says stacking, but
             # its Buff46 row and observed hit bounds favor largest value.
             fish_artifact_defence_policy='largest_active',
             hades_normal_artifact_order='after_defence',
             # Corrected Bear219 state supports source67 bypass of131;
             # one423 residual remains, so preserve an explicit baseline switch.
             effect67_exempt131=True,
             wing_conversion_policy='bonus_plus_base')


def load_catalog(sources=None):
    return load_current_catalog(sources)


def make_unit(catalog,tid,uid,camp,pos,*,hp=8000000):
    row=catalog.tables['Hero'][tid]
    return Unit(uid,camp,pos,tid,int(row['种族']),int(row['技能类型']),
        10000000,hp,10000,12000,2000,2000,speed=1000,
        is_male=row['性别']=='0',panel=dict(intellect=500,critical_chance=30,
            critical_resist=5,critical_multiplier=2,critical_damage_resist=0,
            includes_static_passives=True))


def fixture(catalog,tid,*,faction=False,seed=7):
    # Low-rarity helpers carry real catalogue identities and race/profession;
    # no fabricated race overrides on unrelated hero templates.
    race=int(catalog.tables['Hero'][tid]['种族'])
    pool=[i for i,r in catalog.tables['Hero'].items() if int(r['品质'])<6
          and (not faction or int(r['种族'])==race)]
    if not pool:pool=[i for i,r in catalog.tables['Hero'].items() if int(r['种族'])==race]
    enemy_pool=[i for i,r in catalog.tables['Hero'].items() if int(r['品质'])<6 and r['技能类型']=='2']
    units=[make_unit(catalog,tid,'a',0,0)]
    units.extend(make_unit(catalog,pool[i%len(pool)],f'f{i}',0,i+1) for i in range(5))
    units.extend(make_unit(catalog,enemy_pool[i%len(enemy_pool)],f'e{i}',1,i) for i in range(6))
    return PlaneSkillSession(catalog,units,artifact_levels={u.uid:10 for u in units},
                             mechanism_options=OPTIONS,seed=seed,strict_inputs=True)


def trial(catalog,tid,sid,*,faction=False,lifecycle=False):
    session=None
    try:
        session=fixture(catalog,tid,faction=faction)
        hero=catalog.hero(tid)
        if lifecycle:
            for artifact in hero.artifact_ids:
                if catalog.skill(artifact).activation_type==5:session.execute('a',artifact)
            session.run_turn('a')
            session.end_round()
        else:
            session.execute('a',sid)
            session.drain_followups()
        return dict(status='executed',events=len(session.combat.events),
            event_kinds=dict(Counter(e.kind for e in session.combat.events)),
            queued_followups=len(session.combat.followups))
    except (UnsupportedMechanism,ValueError,KeyError,TypeError,IndexError,RecursionError) as error:
        return dict(status='blocked',error_type=type(error).__name__,reason=str(error),
                    events_before_error=len(session.combat.events) if session else 0)


def build():
    catalog=load_catalog()
    scope=json.loads((OUT/'all-sss-scope.json').read_text(encoding='utf8'))
    with sqlite3.connect(f'file:{ROOT / "outputs/ancient_war.sqlite"}?mode=ro',uri=True) as db:
        roster_ids={unit['template_id'] for (raw,) in db.execute('select roster_json from plane_game')
                    for unit in json.loads(raw)}
    live_sss={tid for tid in roster_ids if catalog.tables['Hero'][tid]['品质']=='6'}
    if live_sss!={r['template_id'] for r in scope['heroes']}:
        raise ValueError('Saved scope differs from all SSS in the current database; refresh scope first')
    rows=[]
    for item in scope['heroes']:
        hero=catalog.hero(item['template_id'])
        session=fixture(catalog,hero.template_id)
        skills=[]
        for sid in (*hero.skill_ids,*hero.artifact_ids):
            skill=catalog.skill(sid)
            executable=bool(skill.enemy_effects or skill.ally_effects)
            chosen=session.combat.mechanism_options
            session.combat.mechanism_options={}
            candidate_requirements=list(session.combat.requirements(sid))
            session.combat.mechanism_options=chosen
            skills.append(dict(skill_id=sid,name=skill.name,activation_type=skill.activation_type,
                passives=[p[0] for p in skill.passives],
                unconfigured_requirements=candidate_requirements,
                requirements=list(session.combat.requirements(sid)),
                trial=trial(catalog,hero.template_id,sid) if executable else
                    dict(status='passive_registered_not_individually_certified')))
        lifecycle={label:trial(catalog,hero.template_id,None,faction=faction,lifecycle=True)
                   for label,faction in [('mixed',False),('same_race',True)]}
        rows.append(dict(template_id=hero.template_id,name=hero.name,appearances=item['appearances'],
            skills=skills,supplemental_tooltip_ids=list(hero.supplemental_ids),
            lifecycle=lifecycle,full_hero_verified=False))
    trials=[s['trial'] for r in rows for s in r['skills'] if s['trial']['status']!='passive_registered_not_individually_certified']
    life=[v for r in rows for v in r['lifecycle'].values()]
    return dict(scope='all_sss_in_captured_plane_reports',total_heroes=len(rows),
        scope_matches_current_database=True,
        effective_skill_rows=sum(len(r['skills']) for r in rows),
        effect_trials=dict(Counter(t['status'] for t in trials)),
        lifecycle_trials=dict(Counter(t['status'] for t in life)),
        static_requirement_count=sum(len(s['requirements']) for r in rows for s in r['skills']),
        unresolved_behavior_branches=[dict(template_id=80600102,buff_id=218,
            condition='blessed undead healer active and at least four living enemy mages',
            missing='extra attack skill/coefficient; no eligible first-round sample in existing corpus')],
        full_prediction_ready=False,full_hero_verified_count=0,
        fixture=dict(artifact_level=10,artifact_level_is_synthetic=True,candidate_options=OPTIONS,
            panel='explicit synthetic; static passives already included',
            helper_roster='real catalogue low-rarity templates; mixed and same-race'),
        limitations=['Registered callbacks are not individual behavior certification.',
          'These are synthetic action/lifecycle trials, not continuous replay comparisons.',
          'Critical probabilities, initial panels, artifact levels and reaction ordering still need evidence.'],
        source_manifest=catalog.sources,heroes=rows)


if __name__=='__main__':
    result=build()
    (OUT/'all-sss-implementation-coverage.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({k:v for k,v in result.items() if k not in ('heroes','source_manifest')},ensure_ascii=False))
    for row in result['heroes']:
        for s in row['skills']:
            if s['requirements'] or s['trial']['status']=='blocked':print(row['name'],s)
        for mode,t in row['lifecycle'].items():
            if t['status']=='blocked':print(row['name'],mode,t)
