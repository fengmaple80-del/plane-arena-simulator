"""Local worker: a complete Simulation Battle screenshot in, ten battles out."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

import screenshot
from moni_attribute import lookup
from moni_evidence import ASSET, load_enemy, sha256
from moni_predict import OPTIONS, PlaneSkillSession, load_catalog, prepare as prepare_candidate, run_battle, summarize


SAMPLES = 10
POS = {f'{side}{row}{lane}': (camp, offset + lane - 1)
       for camp, side in enumerate('LR', 1)
       for row, offset in [('F', 0), ('B', 3)] for lane in (1, 2, 3)}


class SlotError(ValueError):
    def __init__(self, slot, reason):
        self.issue = {'slot': slot, 'reason': reason}
        super().__init__(f'{slot}: {reason}')


def fixed_unit(slot, hero, form, name, enemy):
    awakened = form == 'awakened'
    try:
        static = lookup(hero, awakened, enemy=enemy)
    except ValueError as exc:
        raise SlotError(slot, 'panel_ambiguous') from exc
    camp, position = POS[slot]
    panel = dict(static['panel'], critical_multiplier=2, critical_damage_resist=0,
                 cast_chance=100, includes_static_passives=True)
    return {'uid': f'{camp}:{position}', 'camp': camp, 'position': position, 'template_id': int(hero),
                  'name': name, 'awakened': awakened, 'artifact_level': 10, 'panel': panel,
                  'static_enemy_id': static['enemy_id'], 'static_skills': static['skills']}


def build_input(lineup, enemy):
    slots = lineup.get('slots', [])
    if lineup['status'] != 'ok' or len(slots) != len(POS) or {s['slot'] for s in slots} != set(POS):
        raise ValueError('Incomplete Simulation Battle lineup')
    forms = {}
    for row in enemy.values():
        forms.setdefault(row['heroId'], set()).add(('normal', 'awakened')[int(row['是否觉醒'])])
    units = []
    for item in slots:
        hero = item['hero_id']
        form = item['form']
        if form not in forms.get(hero, ()):
            raise SlotError(item['slot'], 'panel_missing')
        if not forms[hero].issubset(item['compared_forms']):
            raise SlotError(item['slot'], 'form_template_missing')
        units.append(fixed_unit(item['slot'], hero, form, item['name'], enemy))
    return {'mode': 'monizhan_candidate', 'image_sha256': lineup['image_sha256'], 'asset_sha256': sha256(ASSET),
            'units': units}


def prepare(data, catalog):
    units, differences = prepare_candidate(data, catalog)
    for unit in units:
        gender = catalog.tables['Hero'][unit.template_id]['性别']
        if gender not in ('0', '1'):
            raise ValueError(f'Unrecognized hero gender for {unit.template_id}')
        unit.is_male = gender == '0'
    return units, differences


def simulate(data):
    catalog = load_catalog()
    _, differences = prepare(data, catalog)
    artifact_levels = {u['uid']: u['artifact_level'] for u in data['units']}
    def factory(seed):
        units, _ = prepare(data, catalog)
        return PlaneSkillSession(catalog, units, artifact_levels=artifact_levels, seed=seed,
                                 mechanism_options=OPTIONS, strict_inputs=True,
                                 battle_rules={'mode': 'monizhan_candidate', 'level': 150,
                                               'artifact_level': 10, 'awakening': 'screen_enemy_form'})

    # Bind the ten distinct, reproducible seeds to the ordered lineup rather than image compression.
    identity = [(u['camp'], u['position'], u['static_enemy_id']) for u in data['units']]
    digest = hashlib.sha256(json.dumps(identity).encode('ascii')).hexdigest()
    seed_start = int(digest[:12], 16)
    rows = [run_battle(factory, seed) for seed in range(seed_start, seed_start + SAMPLES)]
    keys = ('seed', 'status', 'winner', 'round', 'event_count', 'phase', 'error_type', 'error', 'seconds')
    blocked_heroes = []
    if all(row['status'] == 'blocked' and row.get('phase') == 'input' for row in rows):
        try:
            problems = json.loads(rows[0]['error'])
        except (KeyError, ValueError):
            problems = None
        if isinstance(problems, dict):
            blocked_heroes = [dict(uid=u['uid'], name=u['name'], awakened=u['awakened'], missing=problems[u['uid']])
                              for u in data['units'] if u['uid'] in problems]
    return {'runs': [{k: row[k] for k in keys if k in row} for row in rows], 'summary': summarize(rows),
            'blocked_heroes': blocked_heroes, 'skill_differences': [r for r in differences if r['static_only'] or r['engine_only']]}


def run_image(data):
    started = time.monotonic()
    lineup = screenshot.recognize(data)
    if lineup['status'] != 'ok':
        return lineup
    try:
        battle_input = build_input(lineup, load_enemy())
    except SlotError as exc:
        return {**lineup, 'status': 'unreadable', 'issues': [exc.issue]}
    recognition = time.monotonic()
    result = simulate(battle_input)
    return {**lineup, 'status': 'ok' if result['summary']['completed'] == SAMPLES else 'incomplete',
            'input': battle_input, **result,
            'recognition_seconds': round(recognition - started, 3),
            'simulation_seconds': round(time.monotonic() - recognition, 3)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', nargs='?', type=Path, help='Screenshot path. Omit to read image bytes from stdin.')
    args = parser.parse_args()
    if args.image:
        with args.image.open('rb') as stream:
            data = stream.read(screenshot.core.MAX_BYTES + 1)
    else:
        data = sys.stdin.buffer.read(screenshot.core.MAX_BYTES + 1)
    print(json.dumps(run_image(data), ensure_ascii=False))


if __name__ == '__main__':
    main()
