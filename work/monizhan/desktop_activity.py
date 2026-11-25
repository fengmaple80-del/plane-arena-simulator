"""Desktop entry to the activity's fixed Enemy records and original battle runner."""
import json
import sys
from local_battle import POS, SAMPLES, fixed_unit, load_enemy, simulate
from moni_attribute import FIELDS


def activity(request):
    enemy = load_enemy()
    records = {str(r['怪物ID']): r for r in enemy.values()}
    if request['action'] == 'forms':
        return [dict(id=k, hero_id=r['heroId'], name=r['名称'], awakened=r['是否觉醒']=='1',
                     panel={label:int(r[label]) for label in FIELDS.values()}) for k,r in records.items()]
    selected = request.get('positions', {})
    if not isinstance(selected, dict) or set(selected) != set(POS):
        raise ValueError('请补齐双方各六个站位。')
    units, slots = [], []
    for slot in POS:
        row = records.get(str(selected[slot]))
        if row is None:
            raise ValueError('请选择活动中已有的角色形态。')
        form = 'awakened' if row['是否觉醒']=='1' else 'normal'
        unit = fixed_unit(slot, row['heroId'], form, row['名称'], enemy)
        units.append(unit)
        slots.append(dict(slot=slot, name=row['名称'], hero_id=row['heroId'], form=form))
    res = simulate({'mode':'monizhan_candidate','units':units})
    return dict(status='ok' if res['summary']['completed']==SAMPLES else 'incomplete', slots=slots, summary=res['summary'])


if __name__ == '__main__':
    print(json.dumps(activity(json.load(sys.stdin)), ensure_ascii=True))
