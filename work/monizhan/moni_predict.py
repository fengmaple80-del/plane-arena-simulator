import collections,copy
from audit_all_sss import load_catalog, OPTIONS
from battle_runner import run_battle
from combat import Unit
from runtime import PlaneSkillSession
def prepare(data, catalog):
    if data.get('mode') != 'monizhan_candidate':
        raise ValueError('输入不是模拟战候选')
    records = data['units']
    if len(records) != 12 or {(r['camp'], r['position']) for r in records} != {(c, p) for c in (1, 2) for p in range(6)}:
        raise ValueError('必须提供双方各六个独立站位')
    units = []
    differences = []
    for record in records:
        if type(record.get('awakened')) is not bool:
            raise ValueError('每个单位必须明确普通或觉醒假设')
        tid = record['template_id']
        row = catalog.tables['Hero'][tid]
        panel = copy.deepcopy(record['panel'])
        unit = Unit(record['uid'], record['camp'], record['position'], tid,
                    int(row['种族']), int(row['技能类型']), panel['max_hp'], panel['max_hp'],
                    panel['physical_attack'], panel['magical_attack'], panel['physical_defence'],
                    panel['magical_defence'], speed=panel['speed'], panel=panel, awakened=record['awakened'])
        hero = catalog.hero_for(unit)
        selected = list((*hero.skill_ids, *hero.artifact_ids))
        static = [int(skill['id']) for skill in record['static_skills']]
        differences.append({'uid': unit.uid, 'static_skill_ids': static, 'selected_engine_skill_ids': selected,
                            'static_only': sorted(set(static) - set(selected)),
                            'engine_only': sorted(set(selected) - set(static))})
        units.append(unit)
    if len({unit.uid for unit in units}) != 12:
        raise ValueError('单位编号重复')
    return units, differences


def summarize(rows):
    counts = collections.Counter()
    for row in rows:
        winner = row.get('winner')
        if row.get('status') == 'finished' and winner in (0, 1, 2):
            counts[{0: 'draw', 1: 'left', 2: 'right'}[winner]] += 1
        else:
            counts['unknown'] += 1
    counts = {k: counts[k] for k in ('left', 'right', 'draw', 'unknown')}
    return {'samples': len(rows), 'counts': counts,
            'completed': len(rows) - counts['unknown'],
            'left_fraction_of_all': counts['left'] / len(rows) if rows and not counts['unknown'] else None,
            'right_fraction_of_all': counts['right'] / len(rows) if rows and not counts['unknown'] else None}


