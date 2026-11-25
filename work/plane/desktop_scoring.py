"""Versioned score candidates; raw terminals can be relabeled without combat."""
from decimal import Decimal
import json
import hashlib
from pathlib import Path

def digest(value):
    data = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(data.encode()).hexdigest()


def load_profile(path=None):
    value = json.loads(Path(path or Path(__file__).with_name('selection-score-live-round-v3.json')).read_text('utf-8'))
    for key in ('participation','match_win','survivor','enemy_defeated','hp_per_game',
                'round_per_game','round_penalty','maximum'):
        if type(value.get(key)) is not int or value[key]<0:
            raise ValueError('Nonnegative integer score parameter required: '+key)
    if value.get('hp_basis')!='remaining_hp_over_initial_six_max_hp_sum' or value.get('hp_rounding')!='floor_per_game':
        raise ValueError('Unsupported explicit HP rule')
    if value.get('difficulty_multiplier')!=1:
        raise ValueError('User confirmed no difficulty multiplier')
    if type(value.get('round_penalty_divisor',1)) is not int or value.get('round_penalty_divisor',1)<1:
        raise ValueError('Positive integer round penalty divisor required')
    value['version'] = value['id']+':'+digest(value)
    return value


def score_challenge(games, profile, optimized_camp=1):
    if len(games) not in (1,3) or any(g.get('status')!='finished' for g in games):
        return dict(status='incomplete',score=None,score_version=profile['version'],evidence=profile['evidence'])
    details=[];wins=0
    for index,game in enumerate(games,1):
        terminal=game['terminal']
        own=[u for u in terminal['units'] if u['camp']==optimized_camp and u.get('original_entrant',True)]
        enemy=[u for u in terminal['units'] if u['camp']!=optimized_camp and u.get('original_entrant',True)]
        if len(own)!=6 or len(enemy)!=6:
            raise ValueError('Scoring requires six initial entrants per camp')
        won=game.get('winner')==optimized_camp;wins+=won
        denominator=sum(u['initial_max_hp'] for u in own)
        if denominator<=0:
            raise ValueError('Positive initial HP basis required')
        ratio=min(Decimal(1),max(Decimal(0),Decimal(sum(u['hp'] for u in own))/Decimal(denominator)))
        hp_points=int(ratio*profile['hp_per_game']) if won else 0
        round_number=game['round']
        extra=max(0,round_number-(1 if profile['first_round_free'] else 0))
        divisor=profile.get('round_penalty_divisor',1)
        round_points=max(0,(profile['round_per_game']*divisor-extra*profile['round_penalty'])//divisor) if won else 0
        details.append(dict(game=index,winner=game.get('winner'),round=round_number,
            survivors=sum(u['hp']>0 for u in own),defeated_enemies=sum(u['hp']==0 for u in enemy),
            own_remaining_hp=sum(u['hp'] for u in own),own_initial_max_hp=denominator,
            survivor_points=sum(u['hp']>0 for u in own)*profile['survivor'],
            kill_points=sum(u['hp']==0 for u in enemy)*profile['enemy_defeated'],
            hp_points=hp_points,round_points=round_points))
    bonus=profile['match_win'] if wins>=2 else 0
    participation = profile['participation'] if len(games) == 3 else 0
    raw=participation+bonus+sum(d[k] for d in details for k in
        ('survivor_points','kill_points','hp_points','round_points'))
    score=min(profile['maximum'],raw)
    return dict(status='finished',score=score,raw_total=raw,range_adjustment=score-raw,
        participation=participation,match_win_points=bonus,games=details,
        wins=wins,score_version=profile['version'],evidence=profile['evidence'])


def summarize_scores(rows):
    from collections import defaultdict
    from statistics import mean

    by_seed = defaultdict(list)
    for row in rows:
        by_seed[row['seed']].append(row)
    profile = load_profile()
    samples = []
    for seed, games in sorted(by_seed.items()):
        games.sort(key=lambda game: game['game'])
        samples.append(dict(seed=seed, left=score_challenge(games, profile, 1)['score'],
                            right=score_challenge(games, profile, 2)['score']))
    complete = [s for s in samples if s['left'] is not None and s['right'] is not None]
    return dict(samples=samples, completed=len(complete), whole=len(by_seed[samples[0]['seed']])==3,
                averages={side: mean(s[side] for s in complete) if complete else None for side in ('left','right')})
