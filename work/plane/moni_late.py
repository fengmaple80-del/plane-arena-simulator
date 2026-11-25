"""Description-based Simulation Battle mechanics for five SSS entries.

The catalogue is authoritative for parameters. See the batch record for the
working rules without matching in-game action evidence.
"""
from collections import Counter, defaultdict
from decimal import Decimal

from combat import Buff, UnsupportedMechanism, scaled


ADAM, CORELLON, ALLEN, DRACULA, WILLIAM = (80600059, 80600065, 80600067, 80600068, 80600071)
NORMAL, DARK, FINGER, FEYLON = 61000001, 61010201, 61110201, 66000063
BOW, MOLTEN_SHIELD, BLOOD_MARK, RIPOSTE = 66000069, 66000071, 66000072, 61112001
CORELLON_SHOTS = {61010801, 61110801, 61210801, 67400096, 67400097}
HP_SIPHON = {61010011, 61110011, 61210011, 61010021, 61110021, 61210021}
CONTROL = {5, 6, 20, 21, 36, 97, 122, 155, 171, 217, 220}


def percent(amount, rate):
    return int(Decimal(amount) * Decimal(str(rate)) / 100)


def attack(unit):
    return max(unit.physical_attack, unit.magical_attack)


class MoniLate:
    heroes = {ADAM, CORELLON, ALLEN, DRACULA, WILLIAM}
    supported_buffs = {170, 171, 172, 173}
    supported_passives = {134, 135, 136, 144, 145, 146, 148, 149}
    supported_selectors = set()
    effect_arities = {46: 2, 48: 2, 51: 2}

    def __init__(self, catalog, common, units):
        self.catalog = catalog
        self.common = common
        self.initial_undead = Counter(u.camp for u in units if u.race == 4)
        self.devours = Counter()
        self.adam_stacks = defaultdict(list)
        self.absorbed_this_round = Counter()
        self.william_pool = {}
        self.william_phase = False

    def rate(self, sid, code, level):
        return scaled(next(v for k, v in self.catalog.skill(sid).passives if k == code), level)

    def owns(self, actor, sid):
        return sid in self.catalog.hero_for(actor).skill_ids

    @staticmethod
    def blocked(b, actor):
        return not actor.alive or any(b.has_buff(actor, bid) for bid in (5, 6, 21, 97, 122, 155, 220))

    def stat_inputs(self, b, unit):
        if unit.template_id != ADAM:
            return ()
        return self.common.round_index, tuple(self.adam_stacks.get(unit.uid, ()))

    def _adam_active(self, uid):
        expiry = self.common.round_index
        return sum((p for until, p in self.adam_stacks.get(uid, ()) if until > expiry), Decimal(0))

    def stat_bonuses(self, b, unit):
        bonuses = {}
        if unit.template_id == ADAM and unit.alive:
            gain = self._adam_active(unit.uid)
            if gain:
                bonuses['magical_attack'] = percent(unit.panel['magical_attack'], gain)
        slow = 0
        for buff in b.active_buffs(unit):
            if buff.buff_id != 173 or buff.source_skill != BLOOD_MARK:
                continue
            owner = b.units.get(buff.source_uid)
            if owner and owner.template_id == DRACULA:
                slow += percent(owner.panel['speed'], buff.value) * buff.stacks
        if slow:
            bonuses['speed'] = -slow
        return bonuses

    def critical_modifiers(self, b, actor, target):
        if actor.template_id != CORELLON or b.has_buff(actor, 192):
            return {}
        level = self.common.artifact_level(actor, BOW)
        if level is None:
            return {}
        count = sum(u.race == 4 for u in b.alive(actor.camp))
        return {'chance': count * self.rate(BOW, 144, level),
                'damage': count * self.rate(BOW, 145, level)}

    def before_apply_buff(self, b, target, buff):
        # Artifact fear is on the enemy caster, so it can fail to apply control
        # to any target, including when its confusion redirects onto an ally.
        if buff.buff_id not in CONTROL:
            return None
        source = b.units.get(buff.source_uid)
        if source is None:
            return None
        rate = max((x.value for x in b.active_buffs(source)
                    if x.buff_id == 170 and x.source_skill == MOLTEN_SHIELD), default=0)
        if rate > 0 and b.rng.random() * 100 < min(100, rate):
            return False
        return None

    def rewrite_effects(self, b, actor, target, skill, level, effects):
        if skill.skill_id in CORELLON_SHOTS:
            # Uses the same critical flag as the physical component, with no
            # second crit roll, no extra multiplier and no copy of the flat hit.
            return tuple((26 if code == 46 else code, values) for code, values in effects)
        return effects

    @staticmethod
    def _damage(b, actor, target, amount, sid, kind, *, mechanism, devour=False):
        old = b.current_hit_context
        b.current_hit_context = {'critical': False, 'moni_late_mechanism': mechanism}
        if devour:
            b.current_hit_context['bypass_invulnerability'] = True
        try:
            return b.damage(actor, target, amount, sid, kind, bypass_lethal_protection=devour)
        finally:
            b.current_hit_context = old

    def _siphon(self, b, actor, target, rate, sid):
        lost = self._damage(b, actor, target, percent(target.max_hp, rate), sid, 'max_hp',
                            mechanism='hp_siphon')
        if lost:
            b.heal(actor, actor, lost, sid)

    def apply_extra_effect(self, b, actor, target, skill, level, code, values, hp_before):
        sid = skill.skill_id
        if code == 48 and sid in HP_SIPHON:
            self._siphon(b, actor, target, scaled(values, level), sid)
        elif code == 51 and sid in (RIPOSTE, 61212001) and actor.template_id == WILLIAM:
            amount = self.william_pool.get(actor.uid, 0)
            if amount:
                divisor = max(1, (b.current_action or {}).get('enemy_target_count', 1))
                damage = percent(amount, scaled(values, level)) // divisor
                self._damage(b, actor, target, damage, sid, 'physical', mechanism='william_riposte')
        else:
            raise UnsupportedMechanism(f'Unsupported skill/effect pair {sid}/{code}')

    def after_damage(self, b, e):
        actor, target = b.units[e.actor_uid], b.units[e.target_uid]
        lost = e.payload['actual_hp_loss']
        if not lost:
            return
        if (target.template_id == WILLIAM and self.owns(target, RIPOSTE)
                and not self.william_phase and e.skill_id not in (RIPOSTE, 61212001)):
            self.absorbed_this_round[target.uid] += lost
        if (actor.template_id != ADAM or actor.camp == target.camp
                or not actor.alive or b.has_buff(actor, 192)
                or e.skill_id not in (NORMAL, DARK, FINGER)
                or e.payload['damage_kind'] not in ('physical', 'magical', 'composite')):
            return
        level = self.common.artifact_level(actor, FEYLON)
        if level is None:
            return
        whole = lost * 100 // target.max_hp
        if not whole:
            return
        stacks = self.adam_stacks[actor.uid]
        stacks[:] = [(end, p) for end, p in stacks if end > self.common.round_index]
        prior = self._adam_active(actor.uid)
        gain = min(Decimal(200) - prior, whole * Decimal(str(self.rate(FEYLON, 135, level))))
        if gain > 0:
            expiry = self.common.round_index + 2
            stacks.append((expiry, gain))
            b.emit('adam_artifact_gain', actor, actor, FEYLON, actual_hp_loss=lost,
                   added_percent=float(gain), total_percent=float(gain + prior),
                   expires_after_round_boundary=expiry)
            self.common.refresh_stats(b)

    def after_hit(self, b, actor, skill, hit):
        action = b.current_action
        if (actor.template_id != ADAM or skill.skill_id not in (NORMAL, DARK)
                or action.get('moni_adam_fingers') or not self.owns(actor, FINGER)):
            return
        action['moni_adam_fingers'] = True
        demons = sum(u.race == 3 for u in b.alive(actor.camp))
        threshold = self.rate(FINGER, 136, self.common.skill_level)
        for ordinal in range(min(3, 1 + demons // 2)):
            if self.blocked(b, actor):
                break
            self.common.refresh_stats(b)
            attacked = action.get('moni_adam_targets', ())
            eligible = [u for u in b.alive(3 - actor.camp)
                        if u.uid in attacked and u.hp * 100 < u.max_hp * threshold]
            if not eligible:
                break
            target, = b._highest(eligible, 1, attack)
            b.emit('adam_finger_selected', actor, target, FINGER, ordinal=ordinal + 1)
            events = b.execute(actor.uid, FINGER, skill_level=self.common.skill_level,
                               primary_uid=target.uid,
                               origin={'kind': 'reaction', 'mechanism': 136, 'force_enemy_primary': True})
            if any(e.kind == 'death' and e.actor_uid == actor.uid and e.skill_id == FINGER
                   and b.units[e.target_uid].camp != actor.camp for e in events):
                action['moni_adam_killed'] = True

    def on_event(self, b, e):
        actor = b.units.get(e.actor_uid)
        if actor is None or actor.template_id not in self.heroes:
            return
        action = b.current_action or {}
        if (e.kind in ('targets', 'damage', 'death') and actor.template_id == ADAM
                and action.get('actor_uid') == actor.uid and action.get('skill_id') == e.skill_id
                and e.skill_id in (NORMAL, DARK)):
            if e.kind == 'targets' and e.payload.get('side') == 'enemy':
                action.setdefault('moni_adam_targets', set()).update(e.payload['target_uids'])
            elif e.kind == 'damage' and b.units[e.target_uid].camp != actor.camp:
                action.setdefault('moni_adam_targets', set()).add(e.target_uid)
            elif (e.kind == 'death' and e.skill_id == DARK
                  and b.units[e.target_uid].camp != actor.camp):
                action['moni_adam_killed'] = True
        elif (e.kind == 'targets' and actor.template_id == CORELLON and e.skill_id == 61110801
              and e.payload.get('side') == 'enemy' and e.payload['target_uids']):
            action['moni_corellon_target'] = e.payload['target_uids'][0]
        elif e.kind == 'action_end' and actor.alive:
            if actor.template_id == ADAM and action.get('moni_adam_killed') and not self.blocked(b, actor):
                if b.alive(3 - actor.camp):
                    b.queue_followup(actor.uid, DARK, skill_level=self.common.skill_level,
                                     origin={'kind': 'reaction', 'mechanism': 134})
            elif actor.template_id == CORELLON and e.skill_id == 61110801 and not self.blocked(b, actor):
                self._corellon_repeat(b, actor, action)
            elif actor.template_id == ALLEN and e.skill_id == 61110011:
                self._allen_devour(b, actor, action.get('skill_level', self.common.skill_level))

    def _corellon_repeat(self, b, actor, action):
        target = b.units.get(action.get('moni_corellon_target'))
        if not target or not target.alive or target.camp == actor.camp:
            return
        origin = action.get('origin') or {}
        ordinal = origin.get('moni_corellon_cast', 1)
        count = min(3, 2 + sum(u.race == 4 for u in b.alive(actor.camp)) // 2)
        if ordinal < count:
            b.queue_followup(actor.uid, 61110801, skill_level=self.common.skill_level,
                             primary_uid=target.uid,
                             origin={'kind': 'reaction', 'mechanism': 146, 'force_enemy_primary': True,
                                     'moni_corellon_cast': ordinal + 1})

    def _allen_devour(self, b, actor, level):
        if self.devours[actor.uid] >= min(2, self.initial_undead[actor.camp] // 3):
            return
        threshold = self.rate(61110011, 148, level)
        eligible = [u for u in b.alive(3 - actor.camp) if u.hp * 100 < u.max_hp * threshold]
        if eligible:
            self.common.refresh_stats(b)
            target, = b._highest(eligible, 1, attack)
            self.devours[actor.uid] += 1
            b.emit('allen_devour', actor, target, 61110011, count=self.devours[actor.uid])
            # A devour is an execute; keep the death event and skip rescue.
            # Send the victim's remaining HP instead of pretending it's a hit
            # based on the caster's attack.
            self._damage(b, actor, target, target.hp, 61110011, 'devour',
                         mechanism='allen_devour', devour=True)

    def after_status_expiration(self, b, expired_by_uid):
        owners = sorted({v.source_uid for expired in expired_by_uid.values() for v in expired
                         if v.buff_id == 172 and v.source_skill == BLOOD_MARK})
        for uid in owners:
            actor = b.units[uid]
            if not actor.alive or actor.template_id != DRACULA or not self.owns(actor, 61110021):
                continue
            eligible = b.alive(3 - actor.camp)
            if not eligible:
                continue
            self.common.refresh_stats(b)
            leader, = b._highest(eligible, 1, attack)
            pool = [u for u in eligible if u is not leader]
            count = sum(u.race == 4 and u is not actor for u in b.alive(actor.camp))
            lv = self.common.skill_level
            high = self.rate(61110021, 149, lv) + 10 * count
            values = self.catalog.tables['Skill'][61110021]['最高'].split('#')[1].split('|')
            other = scaled(values, lv) + 5 * count
            if b.rng.random() * 100 < min(100, high):
                self._blood_slave(b, actor, leader)
            for recipient in b.rng.sample(pool, min(2, len(pool))):
                if b.rng.random() * 100 < min(100, other) and actor.alive:
                    self._blood_slave(b, actor, recipient)

    def _blood_slave(self, b, actor, target):
        if actor.alive and b.apply_buff(target, Buff(171, actor.uid, 61110021, 1)):
            b.emit('blood_slave', actor, target, 61110021)
            self._siphon(b, actor, target, 20, 61110021)

    def end_round(self, b):
        # Freeze the round's incoming total once; ripostes cannot feed one
        # another indefinitely or seed the next round's counter.
        self.william_pool = self.absorbed_this_round
        self.absorbed_this_round = Counter()
        self.william_phase = True
        try:
            for owner in sorted(b.alive(), key=lambda u: (-u.speed, u.camp, u.position)):
                if (owner.template_id == WILLIAM and self.william_pool.get(owner.uid)
                        and not self.blocked(b, owner) and b.alive(3 - owner.camp)):
                    b.execute(owner.uid, RIPOSTE, skill_level=self.common.skill_level,
                              origin={'kind': 'round_end', 'mechanism': 51})
        finally:
            self.william_phase = False
            self.william_pool = {}
