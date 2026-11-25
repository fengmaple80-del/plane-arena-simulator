"""Read the pre-battle Simulation Battle screen, independently of chat or OCR."""
import hashlib
from io import BytesIO
from pathlib import Path
import sqlite3
import sys
from contextlib import closing
from functools import lru_cache


import activity_vision as core
from moni_evidence import ASSETS
ANCHORS = [(ASSETS/'moni-title-anchor.png',None),(ASSETS/'moni-versus-anchor.png',None)]
CENTERS = {'left': {'back':175,'front':325}, 'right':{'front':745,'back':895}}
LANES = (1255,1390,1524)
from PIL import Image, ImageOps


MIN_SCORE = 0.78
MIN_HERO_GAP = 0.12
MIN_FORM_GAP = 0.10
MIN_FOREGROUND_CANDIDATE = 0.62
WIDTH = 1080
VS_REFERENCE_Y = 1397.5


@lru_cache(maxsize=1)
def templates():
    return core.load_templates()


@lru_cache(maxsize=2)
def load_anchor(path):
    with Image.open(path) as image:
        return core.cv2.cvtColor(core.np.asarray(image.convert('RGB')), core.cv2.COLOR_RGB2BGR)


def read_image(data):
    if not 0 < len(data) <= core.MAX_BYTES:
        return None
    try:
        with Image.open(BytesIO(data)) as raw:
            if raw.format not in ('PNG', 'JPEG', 'WEBP') or getattr(raw, 'n_frames', 1) != 1:
                return None
            if raw.width * raw.height > core.MAX_PIXELS:
                return None
            image = ImageOps.exif_transpose(raw)
            width, height = image.size
            if width < 360 or not 1.6 <= height / width <= 2.6:
                return None
            bgr = core.cv2.cvtColor(core.np.asarray(image.convert('RGB')), core.cv2.COLOR_RGB2BGR)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    interpolation = core.cv2.INTER_AREA if width > WIDTH else core.cv2.INTER_LINEAR
    return core.cv2.resize(bgr, (WIDTH, round(height * WIDTH / width)), interpolation=interpolation), (width, height)


def has_support_buttons(image, center):
    # Both gold support buttons sit directly below the four portrait columns.
    vx, vy = center
    y = round(vy) + 243
    for ref_x in (255, 820):
        x = round(ref_x + vx - 540)
        patch = image[y-10:y+11, x-60:x+61]
        if patch.shape[:2] != (21, 121):
            return False
        h, s, v = core.cv2.split(core.cv2.cvtColor(patch, core.cv2.COLOR_BGR2HSV))
        gold = (h >= 7) & (h <= 35) & (s > 130) & (v > 125)
        if gold.mean() < 0.60:
            return False
    return True


def find_layout(image):
    h = len(image)
    scores = []
    for (path, _), (lo, hi) in zip(ANCHORS, ((0, 0.24), (0.43, 0.91))):
        anchor = load_anchor(path)
        top = round(h * lo)
        window = image[top:round(h * hi), 410:670]
        match = core.cv2.matchTemplate(window, anchor, core.cv2.TM_CCOEFF_NORMED)
        _, score, _, corner = core.cv2.minMaxLoc(match)
        scores.append(round(score, 4))
        center = (410 + corner[0] + anchor.shape[1] / 2, top + corner[1] + anchor.shape[0] / 2)
    title_score, versus_score = scores
    if versus_score < 0.80 or abs(center[0] - 540) > 22:
        return None
    if title_score < 0.80 and (versus_score < 0.90 or not has_support_buttons(image, center)):
        return None
    return scores, center


def resolve_portrait(rank):
    candidates = rank['candidates']
    if len(candidates) < 2:
        return {'reason': 'low_score', 'candidates': candidates}
    top, second = candidates[:2]
    hero_gap = round(top['similarity'] - second['similarity'], 4)
    forms = sorted(top.get('formCandidates', ()), key=lambda f: -f['similarity'])
    form_gap = round(forms[0]['similarity'] - forms[1]['similarity'], 4) if len(forms) > 1 else None
    metrics = {'score': top['similarity'], 'hero_gap': hero_gap, 'form_gap': form_gap}
    reason = None
    if top['similarity'] < MIN_SCORE:
        reason = 'low_score'
    elif hero_gap < MIN_HERO_GAP:
        reason = 'hero_margin'
    elif not forms or (form_gap is not None and form_gap < MIN_FORM_GAP):
        reason = 'form_margin'
    if reason:
        return {'reason': reason, 'candidates': candidates, **metrics}
    form = forms[0]
    return {'hero_id': form['hero_id'], 'name': form['name'], 'form': form['appearance'], 'icon': form['icon'],
            'compared_forms': [row['appearance'] for row in forms], **metrics}


@lru_cache(maxsize=32)
def foreground_patches(icon):
    image = core.icon_image(icon)
    if image is None or image.ndim != 3 or image.shape[2] < 3:
        return ()
    patch = core.cv2.resize(image, (110, 110), interpolation=core.cv2.INTER_AREA)[25:85, 20:90]
    variants = []
    for scale in core.SCALES:
        scaled = core.cv2.resize(patch, None, fx=scale, fy=scale, interpolation=core.cv2.INTER_AREA)
        mask = (scaled[:, :, 3] >= 230).astype(core.np.uint8) if scaled.shape[2] == 4 else core.np.ones(scaled.shape[:2], dtype=core.np.uint8)
        if mask.mean() >= 0.25:
            variants.append((scaled[:, :, :3], mask))
    return tuple(variants)


def confirm_foreground(region, initial):
    score = initial.get('score', 0)
    if initial.get('reason') != 'low_score' or score < MIN_FOREGROUND_CANDIDATE:
        return initial
    # A strong lead is required before a lower scoring candidate gets a second comparison.
    hero_gap, form_gap = (0.22, 0.20) if score < 0.70 else (MIN_HERO_GAP, MIN_FORM_GAP)
    if initial['hero_gap'] < hero_gap or (initial['form_gap'] or 0) < form_gap:
        return initial
    expected = initial['candidates'][0]
    coverage = min((mask.mean() for _, mask in foreground_patches(expected['icon'])), default=1.0)
    if coverage >= 0.90:
        return initial
    # A mostly opaque crop gives a weaker second check. Require a near pass and wider leads.
    mostly_opaque = coverage >= 0.85
    if mostly_opaque and (score < 0.75 or initial['hero_gap'] < 0.18 or initial['form_gap'] < 0.25):
        return initial
    # Transparency is rendered over a card background. Recheck the same candidates on the opaque artwork.
    candidates = []
    for candidate in initial['candidates']:
        forms = []
        for form in candidate['formCandidates']:
            scores = []
            for patch, mask in foreground_patches(form['icon']):
                values = core.cv2.matchTemplate(region, patch, core.cv2.TM_CCOEFF_NORMED, mask=mask)
                finite = values[core.np.isfinite(values)]
                if finite.size:
                    scores.append(float(finite.max()))
            if not scores:
                return initial
            forms.append({**form, 'similarity': round(max(scores), 4)})
        candidates.append({**max(forms, key=lambda f: f['similarity']), 'formCandidates': forms})
    result = resolve_portrait({'candidates': sorted(candidates, key=lambda c: -c['similarity'])})
    if (result.get('hero_id'), result.get('form')) != (expected['hero_id'], expected['appearance']):
        return initial
    if mostly_opaque and (result['score'] < 0.80 or result['hero_gap'] < 0.18 or (result['form_gap'] or 0) < 0.25):
        return initial
    return {**result, 'comparison': 'foreground', 'unmasked_score': initial['score']}


def recognize(data):
    decoded = read_image(data)
    if decoded is None:
        return {'status': 'not_monizhan'}
    image, size = decoded
    layout = find_layout(image)
    if layout is None:
        return {'status': 'not_monizhan'}
    scores, (vx, vy) = layout
    slots, issues = [], []
    references = templates()
    for side, rows in CENTERS.items():
        for row in ('front', 'back'):
            x = round(rows[row] + vx - 540)
            for lane, ref_y in enumerate(LANES, start=1):
                slot = f'{side[0].upper()}{row[0].upper()}{lane}'
                y = round(ref_y + vy - VS_REFERENCE_Y)
                info = {'slot': slot, 'side': side, 'row': row, 'lane': lane, 'center': [x, y]}
                if y - 61 < 0 or y + 57 > image.shape[0]:
                    issues.append({**info, 'reason': 'cropped_slot'})
                    continue
                region = image[y-61:y+57, x-62:x+62]
                result = confirm_foreground(region, resolve_portrait(core.rank_candidates(region, references)))
                (issues if 'reason' in result else slots).append({**info, **result})
    # Without the title, the high VS match and support buttons must also have a complete lineup.
    if scores[0] < 0.80 and issues:
        return {'status': 'not_monizhan'}
    return {'status': 'unreadable' if issues else 'ok', 'image_sha256': hashlib.sha256(data).hexdigest(),
            'image_size': list(size), 'anchor_scores': scores, 'slots': slots, 'issues': issues}
