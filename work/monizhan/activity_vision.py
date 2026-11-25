import math
import cv2
import numpy as np
import json
from moni_evidence import ASSETS
MAX_BYTES = 12 * 1024 * 1024
MAX_PIXELS = 12_000_000
SCALES = (0.9,1.0,1.1,1.2)
MIN_SCORE, MIN_GAP, FORM_GAP = 0.72,0.12,0.08
def icon_image(icon):
    return cv2.imdecode(np.frombuffer((ASSETS/'icons'/f'{icon}.png').read_bytes(), dtype=np.uint8), cv2.IMREAD_UNCHANGED)
def load_templates():
    templates=[]
    for record in json.loads((ASSETS/'portraits.json').read_bytes()):
        image = icon_image(record['icon'])
        image = cv2.resize(image[:, :, :3], (110, 110), interpolation=cv2.INTER_AREA)
        patch = image[25:85, 20:90]
        patches = [cv2.resize(patch, None, fx=s, fy=s, interpolation=cv2.INTER_AREA) for s in SCALES]
        templates.append(dict(record, patches=patches))
    return templates

def rank_candidates(region, templates):
    best_by_hero = {}
    by_form = {}
    for template in templates:
        score = max(float(cv2.minMaxLoc(cv2.matchTemplate(region, patch, cv2.TM_CCOEFF_NORMED))[1]) for patch in template['patches'])
        if not math.isfinite(score):
            continue
        value = {'hero_id': template['hero_id'], 'name': template['name'], 'appearance': template['appearance'], 'icon': template['icon'], 'similarity': round(score, 4)}
        key = (template['hero_id'], template['appearance'])
        if key not in by_form or score > by_form[key]['similarity']:
            by_form[key] = value
        if template['hero_id'] not in best_by_hero or score > best_by_hero[template['hero_id']]['similarity']:
            best_by_hero[template['hero_id']] = value
    ranking = sorted(best_by_hero.values(), key=lambda r: r['similarity'], reverse=True)
    ranking = [{**candidate, 'formCandidates': [by_form[(candidate['hero_id'], form)]
               for form in ('normal', 'awakened') if (candidate['hero_id'], form) in by_form]}
               for candidate in ranking]
    if len(ranking) < 2:
        return {'status': 'unknown', 'hero': None, 'appearance': 'unknown', 'candidates': ranking, 'reason': '有效头像候选不足'}
    top, second = ranking[:2]
    gap = top['similarity'] - second['similarity']
    if top['similarity'] < 0.45:
        return {'status': 'unknown', 'hero': None, 'appearance': 'unknown', 'candidates': [], 'reason': '没有可信的头像候选'}
    if top['similarity'] < MIN_SCORE or gap < MIN_GAP:
        return {'status': 'uncertain', 'hero': None, 'appearance': 'unknown', 'candidates': ranking[:3], 'reason': '相似度不足或不同角色候选接近'}
    form_scores = sorted((v for (hero_id, _), v in by_form.items() if hero_id == top['hero_id']), key=lambda r: r['similarity'], reverse=True)
    appearance = top['appearance'] if len(form_scores) == 1 or form_scores[0]['similarity'] - form_scores[1]['similarity'] >= FORM_GAP else 'unknown'
    return {'status': 'matched_candidate', 'hero': {'id': top['hero_id'], 'name': top['name']}, 'appearance': appearance, 'candidates': ranking[:3], 'reason': '模板候选，未经独立确认'}


