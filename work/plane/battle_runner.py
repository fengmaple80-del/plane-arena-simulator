import time
from collections import Counter
from dataclasses import asdict

from combat import UnsupportedMechanism


def run_battle(session_factory, seed, *, round_budget=100, event_budget=30000, trace=False):
    started = time.monotonic()
    session = None
    current_round = 0
    phase = 'input'
    initial = {}
    result = {'seed': seed, 'simulation_round_budget': round_budget}
    try:
        session = session_factory(seed)
        initial = {u.uid: dict(hp=u.hp, max_hp=u.max_hp, template_id=u.template_id,
                                camp=u.camp, position=u.position)
                   for u in session.combat.units.values()}
        missing = {key: value for key, value in session.unresolved().items() if value}
        if missing:
            import json
            raise UnsupportedMechanism(json.dumps(missing, ensure_ascii=False))
        battle = session.combat
        battle.event_budget = event_budget

        def winner():
            camps = {unit.camp for unit in battle.alive()}
            return next(iter(camps)) if len(camps) == 1 else 0 if not camps else None

        def order(pool, opening=False):
            pool = list(pool)
            battle.rng.shuffle(pool)
            return sorted(pool, key=lambda unit: -(unit.panel['speed'] if opening else unit.speed))

        phase = 'opening'
        for actor in order(battle.alive(), opening=True):
            session.run_opening(actor.uid)
        for current_round in range(1, round_budget + 1):
            acted = set()
            phase = 'ordinary'
            while winner() is None:
                session.mechanics.refresh_stats(battle)
                pending = [unit for unit in battle.alive() if unit.uid not in acted]
                if not pending:
                    break
                actor = order(pending)[0]
                acted.add(actor.uid)
                session.run_turn(actor.uid)
                if len(battle.events) > event_budget:
                    raise UnsupportedMechanism('event budget reached')
            if winner() is not None:
                break
            phase = 'round_end'
            session.end_round()
            if winner() is not None:
                break
        win = winner()
        result.update(status='finished' if win is not None else 'round_limit', winner=win,
                      round=current_round, event_count=len(battle.events))
    except Exception as error:
        result.update(status='blocked', error_type=type(error).__name__, error=str(error),
                      phase=phase, round=current_round)
        if session:
            result['last_events'] = [asdict(event) for event in session.combat.events[-6:]]
    if trace and session:
        result['trace'] = session.export()
    if session:
        deaths = Counter(e.target_uid for e in session.combat.events if e.kind == 'death')
        result['terminal'] = dict(schema_version=1, status=result['status'],
            winner=result.get('winner'), round=current_round,
            units=[dict(instance_id=u.uid, camp=u.camp, position=u.position,
                template_id=u.template_id, initial_template_id=initial.get(u.uid,{}).get('template_id'),
                hp=u.hp, max_hp=u.max_hp, initial_hp=initial.get(u.uid,{}).get('hp'),
                initial_max_hp=initial.get(u.uid,{}).get('max_hp'),
                original_entrant=u.uid in initial, death_events=deaths[u.uid])
                for u in session.combat.units.values()])
    result['seconds'] = time.monotonic() - started
    return result
