"""Inputs and results for the local plane prediction window."""

from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
import json
import os
import re
import subprocess
from tempfile import NamedTemporaryFile

from live_prediction import (CURRENT_INPUT_RULES, ROOT, compile_snapshot, digest,
                             initialize, simulator, summarize, summarize_game)
from corpus_panels import compile_panel


FIELDS = ('template_id', 'star', 'advancement')
FIELD_LABELS = {'template_id': '角色', 'star': '星级', 'advancement': '进阶'}
POSITIONS = {(camp, pos) for camp in (1, 2) for pos in range(6)}
DEFAULT_SEED = 2026112001000
DEFAULT_ACCOUNT_SERIAL = '127.0.0.1:16448'


def slot_problem(slot):
    for field, lo, hi in (('template_id', 80000000, 89999999), ('star', 1, 6),
                          ('advancement', 0, 50)):
        value = slot.get(field)
        if type(value) is not int or not lo <= value <= hi:
            return FIELD_LABELS[field]
    return None


def select_slots(slots, game=None):
    teams = sorted({s['team'] for s in slots})
    if not teams or any(t not in (1, 2, 3) for t in teams):
        raise ValueError('没有可计算的对局。')
    if game is None and len(teams) != 1 and teams != [1, 2, 3]:
        raise ValueError('请选择一小局，或补齐双方三队。')
    if game is not None and game not in teams:
        raise ValueError(f'没有第 {game} 小局。')
    selected = [s for s in slots if game is None or s['team'] == game]
    for team in sorted({s['team'] for s in selected}):
        rows = [s for s in selected if s['team'] == team]
        if len(rows) != 12 or {(s['camp'], s['position']) for s in rows} != POSITIONS:
            raise ValueError(f'第 {team} 小局需要双方各 6 个有效站位。')
    return sorted(selected, key=lambda s: (s['team'], s['camp'], s['position']))


def ready_to_autorun(slots, game, mode):
    if mode != 'auto':
        return False
    try:
        return all(not slot_problem(s) for s in select_slots(slots, game))
    except ValueError:
        return False


def compile_image(slots, catalog, game=None, *, source='screenshot'):
    # Both typed and recognized positions use the same panel and battle rules.
    rules = dict(CURRENT_INPUT_RULES)
    selected = select_slots(slots, game)
    by_game = {}
    for slot in selected:
        camp, pos, team = (slot[key] for key in ('camp', 'position', 'team'))
        side = '左方' if camp == 1 else '右方'
        label = f'第 {team} 小局 · {side}第 {pos + 1} 位'
        missing = slot_problem(slot)
        if missing:
            raise ValueError(f'{label}的{missing}需要确认。')
        tid, star, adv = (slot[key] for key in FIELDS)
        if source == 'manual' and adv > 6:
            raise ValueError(f'{label}的位面进阶应为 +0 至 +6。')
        if tid not in catalog.tables['Hero']:
            raise ValueError(f'{label}的角色不在当前位面配置中。')
        if slot.get('level', rules['level']) != rules['level']:
            raise ValueError(f'{label}不是当前支持的 {rules["level"]} 级角色。')
        panel = compile_panel(catalog, tid, star, adv, battle=True,
                              growth_mode=rules['panel_growth_mode'],
                              artifact_level=rules['artifact_skill_level'])
        by_game.setdefault(team, []).append(dict(
            uid=f'{camp}:{pos}', camp=camp, position=pos, template_id=tid,
            name=catalog.hero(tid).name, star=star, advancement=adv, panel=panel,
            input_source='manual_formation' if source == 'manual' else 'local_screenshot',
            hp_source='calculated_from_knowledge_rules',
            panel_source=f"knowledge_formula_{rules['panel_growth_mode']}"))
    games = [dict(match_id='screenshot', rank=0, game=team, players=['左方', '右方'],
                  units=units, input_ready=True, actual_winner=None)
             for team, units in by_game.items()]
    roster = [{k: s[k] for k in ('camp', 'team', 'position', *FIELDS)} for s in selected]
    return dict(schema=1, observed_at=datetime.now(timezone(timedelta(hours=8))).isoformat(),
                roster_sha256=digest(roster), games=games, catalog_sources=dict(catalog.sources),
                input_rules=rules, assumptions=['independent team games for BO3 aggregation'])


def compile_account(snapshot, catalog, game=None):
    if snapshot.get('missing'):
        ranks = '、'.join(str(m['rank']) for m in snapshot['missing'])
        raise ValueError(f'没有读取到名次 {ranks} 的完整阵容，请先在游戏里打开这些对阵。')
    if len(snapshot.get('matches', ())) != 1:
        raise ValueError('请选择一场最终决战对阵。')
    compiled = compile_snapshot(snapshot, catalog)
    if game is not None:
        compiled['games'] = [g for g in compiled['games'] if g['game'] == game]
        if not compiled['games']:
            raise ValueError(f'没有第 {game} 小局。')
    return compiled


def read_account(rank, serial=DEFAULT_ACCOUNT_SERIAL):
    return read_accounts([rank], serial)


def read_accounts(ranks, serial=DEFAULT_ACCOUNT_SERIAL):
    if (not isinstance(ranks, list) or not ranks or len(ranks) > 32
            or any(type(rank) is not int or not 1 <= rank <= 32 for rank in ranks)
            or len(set(ranks)) != len(ranks)):
        raise ValueError('最终决战名次应为 1 至 32。')
    if not re.fullmatch(r'127\.0\.0\.1:[1-9][0-9]{0,4}|emulator-[1-9][0-9]{0,4}', serial):
        raise ValueError('请选择本机 MuMu 地址，例如 127.0.0.1:16448。')
    from plane_memory import PlaneMemoryReader, RuntimeSource, MemoryReadError
    reader = PlaneMemoryReader(RuntimeSource(serial=serial))
    try:
        try:
            reader.start()
        except MemoryReadError as exc:
            if not (serial.startswith('127.0.0.1:') and f"device '{serial}' not found" in str(exc)):
                raise
            # MuMu may register only its emulator alias after a restart.
            # Reconnect the explicitly selected address, then bind afresh.
            connected = subprocess.run([reader.source.adb, 'connect', serial],
                                       capture_output=True, timeout=8, stdin=subprocess.DEVNULL,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            detail = (connected.stdout + connected.stderr).decode(errors='replace').strip()
            if connected.returncode or 'connected to ' + serial not in detail:
                raise MemoryReadError(f'无法连接 MuMu {serial}：{detail}') from exc
            reader.start()
        return reader.read(ranks)
    finally:
        reader.stop()


def _unjudged_reason(row, names):
    if row['status'] == 'round_limit':
        return '达到模拟回合上限，仍未分出胜负'
    if row['status'] == 'finished' and row.get('winner') == 0:
        return '双方均无存活角色'
    error = row.get('error', '')
    if error == 'event budget reached':
        return '达到模拟事件上限，仍未分出胜负'
    if error.startswith('{'):
        try:
            missing = json.loads(error)
        except json.JSONDecodeError:
            missing = {}
        heroes = list(dict.fromkeys(name for uid, name in names.items() if missing.get(uid)))
        if heroes:
            return '、'.join(heroes) + '的技能机制暂未支持'
    return '模拟器未能完成结算'


def summarize_prediction(compiled, rows):
    if len(compiled['games']) == 3:
        report = summarize(compiled, rows)
    else:
        game, = compiled['games']
        stats = dict(rank=game['rank'], game=game['game'], players=game['players'],
                     **summarize_game(rows))
        report = dict(matches=[], games=[stats], statuses=dict(Counter(r['status'] for r in rows)),
                      sample_bounds_meaning='unknown samples assigned to either camp; not statistical confidence intervals')
    for game, stats in zip(compiled['games'], report['games']):
        if stats['unknown']:
            names = {u['uid']: u['name'] for u in game['units']}
            stats['unjudged_reasons'] = dict(Counter(
                _unjudged_reason(r, names) for r in rows
                if (r['match_id'], r['game']) == (game['match_id'], game['game'])
                and (r['status'] != 'finished' or r.get('winner') not in (1, 2))))
    return report


def run_prediction(compiled, samples=300, *, seed_start=DEFAULT_SEED, workers=None, on_progress=None, temp_dir=None):
    if type(samples) is not int or not 1 <= samples <= 3000:
        raise ValueError('每小局模拟次数应为 1 至 3000。')
    jobs = [(g['match_id'], g['game'], seed_start + i) for g in compiled['games']
            for i in range(samples)]
    scenario = 'candidate54_integer59'
    workers = min(len(jobs), workers if workers is not None else min(6, os.cpu_count() or 1))
    rows = []

    def accept(row):
        rows.append(row)
        if on_progress and (len(rows) % 6 == 0 or len(rows) == len(jobs)):
            on_progress(len(rows), len(jobs))

    with NamedTemporaryFile(mode='w', encoding='utf8', prefix='plane-desktop-', suffix='.json',
                            dir=temp_dir or ROOT / 'work/tmp', delete_on_close=False) as compiled_file:
        json.dump(compiled, compiled_file, ensure_ascii=False)
        compiled_file.close()
        if workers == 1:
            initialize(compiled_file.name, scenario)
            for job in jobs:
                accept(simulator.one(job))
        else:
            with ProcessPoolExecutor(max_workers=workers, initializer=initialize,
                                     initargs=(compiled_file.name, scenario)) as pool:
                futures = []
                try:
                    for job in jobs:
                        futures.append(pool.submit(simulator.one, job))
                    for future in as_completed(futures):
                        accept(future.result())
                except BaseException:
                    # Stop queued work before the context waits for running jobs.
                    for future in futures:
                        future.cancel()
                    raise
    return summarize_prediction(compiled, rows)
