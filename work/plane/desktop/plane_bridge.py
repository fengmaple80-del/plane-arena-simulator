"""Single-request stdio bridge used by the local arena window."""

import base64
from io import BytesIO
import json
import multiprocessing
from pathlib import Path
import sys

from catalog import load_current_catalog
from .plane_desktop import (DEFAULT_ACCOUNT_SERIAL, compile_account, compile_image, read_account,
                            read_accounts, run_prediction)

MAX_IMAGE_BYTES = 20 * 1024 * 1024


def _portrait(image, rect):
    from PIL import Image

    data = BytesIO()
    tile = image.crop(rect)
    tile.thumbnail((144, 144), Image.Resampling.LANCZOS)
    tile.save(data, format='JPEG', quality=87)
    return 'data:image/jpeg;base64,' + base64.b64encode(data.getvalue()).decode('ascii')


def _with_names(slots, catalog):
    return [dict(s, name=catalog.hero(s['template_id']).name if s.get('template_id') else '')
            for s in slots]


def _decode_image(data):
    if not isinstance(data, str) or not data:
        raise ValueError('没有收到可读取的图片。')
    too_large = '图片超过 20 MB，请裁剪到双方阵容后重新导入。'
    if len(data) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
        raise ValueError(too_large)
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError:
        raise ValueError('无法读取图片，请重新复制截图。') from None
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError(too_large)
    return BytesIO(raw)


def _recognize(request, catalog):
    from .plane_image import recognize_image, roster_icon_entries
    from PIL import Image

    if request.get('path'):
        source = Path(request['path'])
        label = source.name
    else:
        source = _decode_image(request.get('image_base64'))
        label = request.get('label') or '粘贴的截图'
    too_large = '截图过大，请裁剪到双方阵容后重新导入。'
    try:
        with Image.open(source) as image:
            if image.width * image.height > 24_000_000:
                raise ValueError(too_large)
            draft = recognize_image(image, catalog)
    except Image.DecompressionBombError:
        raise ValueError(too_large) from None
    except OSError:
        raise ValueError('无法读取图片，请使用 PNG、JPG、WebP 或 BMP 格式。') from None
    fields = ('team', 'camp', 'position', 'template_id', 'star', 'advancement')
    slots = [dict({k: s[k] for k in fields}, portrait=_portrait(draft['image'], s['source_rect']),
                  candidates=s['candidates']) for s in draft['slots']]
    heroes = [dict(id=tid, name=catalog.hero(tid).name, full_name=catalog.tables['Hero'][tid]['英雄名称'])
              for _, tid in roster_icon_entries(catalog)]
    return dict(source='screenshot', label=label, players=['左方', '右方'],
                slots=_with_names(slots, catalog), heroes=heroes)


def _account(request, catalog):
    snapshot = read_account(request.get('rank'), request.get('serial', DEFAULT_ACCOUNT_SERIAL))
    return _account_roster(snapshot, catalog)


def _account_roster(snapshot, catalog):
    compile_account(snapshot, catalog)
    row, = snapshot['matches']
    snapshot = {k: snapshot[k] for k in ('kind', 'observed_at', 'missing', 'matches')}
    snapshot['matches'] = [{k: row[k] for k in ('rank', 'players', 'slots')}]
    return dict(source='account', label=f'最终决战 · 第 {row["rank"]} 名', players=row['players'],
                snapshot=snapshot, slots=_with_names(row['slots'], catalog))


def _accounts(request, catalog):
    ranks = request.get('ranks')
    snapshot = read_accounts(ranks, request.get('serial', DEFAULT_ACCOUNT_SERIAL))
    matches = {row['rank']: row for row in snapshot['matches']}
    entries = []
    for rank in ranks:
        if rank not in matches:
            entries.append(dict(rank=rank, error='阵容未加载，请在游戏里打开该对阵后重新批量读取。'))
            continue
        one = {**snapshot, 'matches': [matches[rank]], 'missing': []}
        try:
            entries.append(dict(rank=rank, roster=_account_roster(one, catalog)))
        except ValueError as exc:
            entries.append(dict(rank=rank, error=str(exc)))
    return dict(entries=entries)


def dispatch(request, emit=None, *, workers=None):
    action = request.get('action')
    if action not in ('catalog', 'recognize', 'account', 'accounts', 'simulate'):
        raise ValueError('不支持的位面操作。')
    catalog = load_current_catalog()
    if hasattr(catalog, 'dataset_label') and (action in ('recognize', 'account', 'accounts')
            or (action == 'simulate' and request.get('source') != 'manual')):
        raise ValueError('此版本请使用手动排阵。')
    if action == 'catalog':
        heroes = [dict(id=tid, name=row['短命名'] or row['英雄名称'], full_name=row['英雄名称'],
                       attributes={key: row[key] for key in ('力量', '敏捷', '智力', '体质', '韧性') if key in row})
                  for tid, row in catalog.tables['Hero'].items()
                  if 80500000 < tid < 80501000 or 80600000 < tid < 80601000]
        return dict(heroes=heroes, dataset=getattr(catalog, 'dataset_label', '本地位面角色'))
    if action == 'recognize':
        return _recognize(request, catalog)
    if action == 'account':
        return _account(request, catalog)
    if action == 'accounts':
        return _accounts(request, catalog)
    game = request.get('game')
    if request['source'] == 'account':
        compiled = compile_account(request['snapshot'], catalog, game)
    elif request['source'] in ('screenshot', 'manual'):
        compiled = compile_image(request['slots'], catalog, game, source=request['source'])
    else:
        raise ValueError('没有可用的阵容来源。')
    return run_prediction(compiled, samples=request['samples'], workers=workers, on_progress=emit)


class _OutputDisconnected(Exception):
    pass


def _send(kind, **payload):
    message = json.dumps(dict(kind=kind, **payload), ensure_ascii=True, allow_nan=False)
    try:
        print(message, flush=True)
    except OSError as exc:
        # There is no reader left, including on Windows where the pipe may
        # raise EINVAL. Do not flush the broken stream again at interpreter exit.
        sys.stdout = None
        raise _OutputDisconnected from exc


def main():
    try:
        request = json.load(sys.stdin)
        response = dispatch(request, emit=lambda done, total: _send('progress', done=done, total=total))
        _send('result', value=response)
    except _OutputDisconnected:
        return 1
    except Exception as exc:
        try:
            _send('error', message=str(exc) or type(exc).__name__)
        except _OutputDisconnected:
            pass
        return 1
    return 0


if __name__ == '__main__':
    multiprocessing.freeze_support()
    raise SystemExit(main())
