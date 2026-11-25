"""Activity-pool skills for Sariel, Constantine, Lion, Scylla and Leviathan.

These are description-derived rules; the live ordering and tie breaks have
not been calibrated against battle reports.
"""
from collections import Counter, defaultdict
from decimal import Decimal

from all_active import pct
from combat import scaled

SARIEL, CONSTANTINE, LION, SCYLLA, LEVIATHAN = (
    80600044, 80600045, 80600046, 80600056, 80600057,
)
SARIEL_SHOT, SARIEL_PREEMPT, SARIEL_ART = 61008701, 61108701, 66000048
CONSTANTINE_SKILLS, CONSTANTINE_ART = {61008801, 61108801}, 66000049
LION_SKILLS, LION_ART = {61008901, 61108901}, 66000050
SCYLLA_AWAKENED, SCYLLA_ART = 61109901, 66000060
LEVIATHAN_SKILLS, LEVIATHAN_ART = {61010001, 61110001}, 66000061
HUMAN, BEAST, DEMON = 0, 1, 3


class MoniMiddle:
    heroes = {SARIEL, CONSTANTINE, LION, SCYLLA, LEVIATHAN}
    supported_buffs = {128, 129, 149, 150}
    supported_passives = {108, 109, 110, 111, 112, 113, 126, 127, 128, 129, 130, 131}
    supported_selectors = {26, 27, 28, 29}
    effect_arities = {44: 2}

    def __init__(self, catalog, common, units):
        self.catalog, self.common = catalog, common
        self.opened = set()
        self.constantine_targets = Counter()
        self.sariel_preempts = Counter()
        self.lion_targets = defaultdict(set)
        self.lion_growth = Counter()
        self.scylla_and_leviathan_repeated = set()

    def rate(self, sid, code, level):
        return scaled(next(v for k, v in self.catalog.skill(sid).passives if k == code), level)

    @staticmethod
    def can_react(b, owner):
        return (owner.alive and not any(b.has_buff(owner, bid)
                for bid in (5, 6, 20, 21, 36, 97, 122, 155, 171, 192, 217, 220)))

    @staticmethod
    def positives(b, unit):
        return sum(b.positive(buff) for buff in b.active_buffs(unit))

    def active_id_override(self, b, actor):
        if actor.template_id == CONSTANTINE and self.catalog.hero_for(actor).awakening:
            return 61108801
        return None

    def before_opening(self, b, actor):
        if actor.template_id != CONSTANTINE or actor.uid in self.opened or not actor.alive:
            return
        self.opened.add(actor.uid)
        ally_humans = [u for u in b.alive(actor.camp) if u is not actor and u.race == HUMAN]
        level = self.common.artifact_level(actor, CONSTANTINE_ART)
        if level is not None:
            for ally in ally_humans:
                self._pay_hp(b, actor, ally, CONSTANTINE_ART, 1)
            gain = min(500, len(ally_humans) * self.rate(CONSTANTINE_ART, 111, level))
            self.common.growth[actor.uid, 'physical_attack'] += pct(actor.panel['physical_attack'], gain)
        if self.catalog.hero_for(actor).awakening:
            rate = self.rate(61108801, 112, self.common.skill_level)
            # Both once-per-battle costs are based on max HP. Include all the
            # described partners, including those past the three-target cap.
            for ally in ally_humans:
                self._pay_hp(b, actor, ally, 61108801, rate)
            self.constantine_targets[actor.uid] = min(2, len(ally_humans))
        self.common.refresh_stats(b)

    @staticmethod
    def _pay_hp(b, actor, target, sid, rate):
        old = target.hp
        target.hp = max(1, old - pct(target.max_hp, rate))
        if target.hp != old:
            b.emit('health_cost', actor, target, sid, hp_before=old, hp_after=target.hp)

    def _set_lion_growth(self, b, actor, gain):
        old = self.lion_growth[actor.uid]
        self.lion_growth[actor.uid] = gain
        panel = actor.panel['physical_attack']
        self.common.growth[actor.uid, 'physical_attack'] += pct(panel, gain) - pct(panel, old)
        self.common.refresh_stats(b)

    def before_action(self, b, actor, skill):
        origin = (b.current_action or {}).get('origin') or {}
        if isinstance(origin, dict) and origin.get('kind') != 'ordinary':
            return
        if not b.has_buff(actor, 5):
            return
        for owner in b.alive(3 - actor.camp):
            if not actor.alive:
                break
            if (owner.template_id != SARIEL or not self.can_react(b, owner)
                    or self.sariel_preempts[owner.uid] >= 3
                    or SARIEL_PREEMPT not in self.catalog.hero_for(owner).skill_ids):
                continue
            level = self.common.skill_level
            chance = scaled(self.catalog.tables['Skill'][SARIEL_PREEMPT]['技能释放概率'].split('|'), level)
            if b.rng.random() * 100 >= chance:
                continue
            self.sariel_preempts[owner.uid] += 1
            b.execute(owner.uid, SARIEL_PREEMPT, skill_level=level, primary_uid=actor.uid,
                      origin={'kind': 'preempt', 'mechanism': 'sariel_silenced_enemy',
                              'force_enemy_primary': True})

    def target_count_bonus(self, b, actor, skill, ally):
        if not ally and skill.skill_id == 61108801:
            return self.constantine_targets[actor.uid]
        return 0

    def hit_count_bonus(self, b, actor, skill):
        if skill.skill_id in LION_SKILLS:
            # Count the acting beast alongside the other living beasts.
            return max(0, sum(u.race == BEAST for u in b.alive(actor.camp)) - 1)
        return 0

    def select_targets_override(self, b, spec, actor, candidates, primary, ally):
        mode, count = spec
        if ally or mode not in self.supported_selectors:
            return None
        if mode == 26:
            return sorted(candidates, key=lambda u: (not b.has_buff(u, 5), u.position))[:min(2, count)]
        if mode == 27:
            return sorted(candidates, key=lambda u: (self.positives(b, u), u.position))[:min(3, count)]
        last = (b.current_action or {}).get('lion_last_position', -1)
        if mode == 28:
            return [u for u in candidates if u.uid not in self.lion_targets[actor.uid]][:1]
        return sorted(candidates, key=lambda u: (u.position <= last, u.position))[:1]

    def before_hit(self, b, actor, skill, hit):
        if actor.template_id == LION and skill.skill_id in LION_SKILLS:
            b.current_action['lion_hit_selected'] = False

    def on_event(self, b, event):
        if event.kind == 'buff' and event.payload['buff_id'] == 5:
            target = b.units[event.target_uid]
            for hero in b.alive(3 - target.camp):
                if hero.template_id != SARIEL or b.has_buff(hero, 192):
                    continue
                level = self.common.artifact_level(hero, SARIEL_ART)
                if level is not None:
                    panel = hero.panel['physical_attack']
                    key = (hero.uid, 'sariel_silence_attack')
                    old = self.common.growth[key]
                    gain = min(pct(panel, 300) - old, pct(panel, self.rate(SARIEL_ART, 109, level)))
                    self.common.growth[key] += gain
                    self.common.growth[hero.uid, 'physical_attack'] += gain
            self.common.refresh_stats(b)
        elif event.kind == 'targets' and event.skill_id in LION_SKILLS and event.payload['side'] == 'enemy':
            action = b.current_action
            targets = event.payload['target_uids']
            if targets and action is not None:
                self.lion_targets[event.actor_uid].update(targets)
                action['lion_last_position'] = b.units[targets[-1]].position
                action['lion_hit_selected'] = True
                action['lion_hit_killed'] = False
        elif event.kind == 'action_end':
            self._extra_cast(b, event)

    def after_hit(self, b, actor, skill, hit):
        action = b.current_action or {}
        if (actor.template_id == LION and skill.skill_id in LION_SKILLS
                and action.get('lion_hit_selected')):
            gain = 0 if action.get('lion_hit_killed') else min(300,
                self.lion_growth[actor.uid] + self.rate(skill.skill_id, 113, action['skill_level']))
            self._set_lion_growth(b, actor, gain)

    def critical_modifiers(self, b, actor, target):
        action = b.current_action or {}
        sid = action.get('skill_id')
        if sid in CONSTANTINE_SKILLS and self.positives(b, actor) > self.positives(b, target):
            return {'chance': self.rate(sid, 110, action['skill_level'])}
        return {}

    def resolve_hit_context(self, b, actor, target, sid, kind, context):
        if sid == SARIEL_SHOT and kind == 'physical' and b.has_buff(target, 5):
            level = (b.current_action or {}).get('skill_level', self.common.skill_level)
            context['damage_multiplier'] = context.get('damage_multiplier', 1) * (1 + self.rate(sid, 108, level) / 100)
        return context

    def before_apply_buff(self, b, target, buff):
        if buff.buff_id == 150 and buff.source_skill == SCYLLA_ART and target.uid == buff.source_uid:
            level = self.common.artifact_level(target, SCYLLA_ART)
            if level is None:
                return None
            # Snapshot living teammates at the opening; self is not a teammate.
            count = sum(2 if u.race == DEMON else 1 for u in b.alive(target.camp) if u is not target)
            buff.value = count * self.rate(SCYLLA_ART, 126, level)
        return None

    def stat_transform(self, b, unit, stat, value):
        rate = 0
        for v in b.active_buffs(unit):
            if stat == 'speed' and v.buff_id == 128 and v.source_skill == LION_ART:
                rate += v.value  # Negative speed value in the combat row.
            elif stat in ('physical_attack', 'magical_attack') and v.buff_id == 129 and v.source_skill == LION_ART:
                rate -= v.value  # For attack use 14+2L from the combat effect, not 7+1.5L from display.
            elif stat in ('physical_defence', 'magical_defence') and v.buff_id == 149:
                rate -= v.value * v.stacks
            elif stat == 'magical_attack' and v.buff_id == 150 and v.source_skill == SCYLLA_ART:
                rate += v.value
        return value if not rate else pct(value, max(0, 100 + rate))

    def rewrite_effects(self, b, actor, target, skill, level, effects):
        if skill.skill_id in LEVIATHAN_SKILLS:
            return tuple((4 if code == 44 else code, values) for code, values in effects)
        return effects

    def additional_components(self, b, actor, target, skill, level):
        if actor.template_id != LEVIATHAN or b.has_buff(actor, 192):
            return ()
        level = self.common.artifact_level(actor, LEVIATHAN_ART)
        if level is None:
            return ()
        beasts = sum(u is not actor and u.race == BEAST for u in b.alive(actor.camp))
        rate = self.rate(LEVIATHAN_ART, 128, level) + beasts * self.rate(LEVIATHAN_ART, 129, level)
        return (dict(kind='true', amount=pct(actor.magical_attack, rate)),)

    def after_damage(self, b, event):
        action = b.current_action or {}
        if (event.skill_id != action.get('skill_id') or event.actor_uid != action.get('actor_uid')
                or event.payload['damage_kind'] not in ('physical', 'magical', 'composite')):
            return
        actor = b.units[event.actor_uid]
        if (actor.template_id == LION and event.skill_id in LION_SKILLS and event.payload['killed']
                and actor.camp != b.units[event.target_uid].camp):
            action['lion_hit_killed'] = True
        if event.skill_id in LEVIATHAN_SKILLS and actor.alive and not b.has_buff(actor, 192):
            rate = self.rate(event.skill_id, 131, action['skill_level'])
            b.heal(actor, actor, pct(event.payload['actual_hp_loss'], rate), event.skill_id)

    def _extra_cast(self, b, event):
        actor = b.units[event.actor_uid]
        sid = event.skill_id
        if (sid, actor.template_id) not in ((SCYLLA_AWAKENED, SCYLLA), (61110001, LEVIATHAN)):
            return
        if actor.uid in self.scylla_and_leviathan_repeated or not self.can_react(b, actor):
            return
        enemies = b.alive(3 - actor.camp)
        if not enemies:
            return
        level = event.payload['level']
        if sid == SCYLLA_AWAKENED:
            eligible = sum(u.race == DEMON for u in b.alive(actor.camp)) >= 2
            mechanism = 'scylla_extra_cast'
        else:
            threshold = Decimal(str(self.rate(sid, 130, level)))
            eligible = any(u.hp * 100 > u.max_hp * threshold for u in enemies)
            mechanism = 'leviathan_extra_cast'
        if eligible:
            self.scylla_and_leviathan_repeated.add(actor.uid)
            b.queue_followup(actor.uid, sid, skill_level=level, primary_uid=event.payload.get('primary_uid'),
                             origin={'kind': 'reaction', 'mechanism': mechanism}, front=True)

    def end_round(self, b):
        self.sariel_preempts.clear()
        self.lion_targets.clear()
        self.scylla_and_leviathan_repeated.clear()
        for uid in tuple(self.lion_growth):
            if self.lion_growth[uid]:
                self._set_lion_growth(b, b.units[uid], 0)
