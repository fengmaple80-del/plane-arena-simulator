"""Description-derived skills for the first seven missing activity heroes."""
from collections import Counter, defaultdict
from decimal import Decimal

from combat import Buff, UnsupportedMechanism, scaled


IVAN, GALANORD, BAIYU, JACK, COOK, ANUBIS, SIREN = (
    80600024, 80600025, 80600030, 80600031, 80600034, 80600036, 80600037,
)
IVAN_SKILLS = {61006701, 61106701, 61206701, 66000028}
GALANORD_SKILLS = {61006801, 61106801, 61206801}
BAIYU_SKILLS = {61007301, 61107301, 61207301}
JACK_SKILLS = {61007401, 61107401, 61207401}
COOK_SKILLS = {61007701, 61107701, 61207701}
ANUBIS_SKILLS = {61007901, 61107901, 61207901}
SIREN_SKILLS = {61008001, 61108001, 61208001, 66000041}
CONTROLLED = {6, 21, 36, 97, 122, 155, 171, 217, 220}
DIRECT = {'physical', 'magical', 'max_hp', 'current_hp', 'percentage', 'true', 'composite'}


def percent(amount, rate):
    return int(Decimal(str(amount)) * Decimal(str(rate)) / 100)


class MoniEarly:
    heroes = {IVAN, GALANORD, BAIYU, JACK, COOK, ANUBIS, SIREN}
    supported_buffs = {82, 83, 85, 86, 94, 97, 98, 105, 110, 111, 113}
    supported_passives = {65, 84, 102}
    supported_selectors = {20, 22, 23}
    effect_arities = {30: 2, 32: 4, 33: 2, 34: 2, 35: 3}

    def __init__(self, catalog, common, units):
        self.catalog = catalog
        self.common = common
        self.anubis_casts = Counter()
        self.anubis_attack_gain = defaultdict(Decimal)
        self.cook_executions = Counter()

    @staticmethod
    def controlled(b, target):
        return any(buff.buff_id in CONTROLLED for buff in b.active_buffs(target))

    def select_targets_override(self, b, spec, actor, candidates, primary, ally):
        mode, count = spec
        sid = (b.current_action or {}).get('skill_id')
        if mode == 20 and (actor.template_id == COOK or sid in COOK_SKILLS) and not ally:
            controlled = [u for u in candidates if self.controlled(b, u)]
            other = [u for u in candidates if u not in controlled]
            selected = []
            for group in (controlled, other):
                while group and len(selected) < count:
                    target = primary if primary in group else b.ordinary_primary(actor, group)
                    selected.append(target)
                    group.remove(target)
            return selected
        if mode == 22 and (actor.template_id == SIREN or sid in SIREN_SKILLS) and ally:
            return b._highest([u for u in candidates if u is not actor], count,
                              lambda u: u.magical_attack)
        if mode == 23 and (actor.template_id == ANUBIS or sid in ANUBIS_SKILLS) and not ally:
            if self.anubis_casts[actor.uid] % 2 == 0:
                return [primary or b.ordinary_primary(actor, candidates)] if candidates else []
            front = [u for u in candidates if u.position < 3]
            return (front or [u for u in candidates if u.position >= 3])[:count]
        return None

    def rewrite_effects(self, b, actor, target, skill, level, effects):
        sid = skill.skill_id
        if sid in GALANORD_SKILLS:
            return tuple((2, (str(Decimal(values[0]) * (Decimal('1.5') if target.race == 4 else 1)),
                              str(Decimal(values[1]) * (Decimal('1.5') if target.race == 4 else 1))))
                         if code == 30 else (code, values) for code, values in effects)
        if sid in COOK_SKILLS:
            return tuple((1, values[:2]) if code == 32 else (code, values)
                         for code, values in effects)
        return effects

    def resolve_hit_context(self, b, actor, target, skill_id, kind, context):
        if skill_id in COOK_SKILLS and self.controlled(b, target):
            extra = next((v[2:] for code, v in self.catalog.skill(skill_id).enemy_effects
                          if code == 32), None)
            if extra:
                level = (b.current_action or {}).get('skill_level', self.common.skill_level)
                context['attack_multiplier'] *= 1 + scaled(extra, level)
        return context

    def apply_extra_effect(self, b, actor, target, skill, level, code, values, hp_before):
        sid = skill.skill_id
        if code == 33 and sid in COOK_SKILLS:
            if (0 < target.hp * 100 < target.max_hp * scaled(values, level)
                    and self.cook_executions[actor.uid] < 2):
                self.cook_executions[actor.uid] += 1
                before = target.hp
                target.hp = 0
                b.emit('execution', actor, target, sid, hp_before=before, hp_after=0)
                b.emit('death', actor, target, sid)
        elif code == 34 and sid in ANUBIS_SKILLS and target is actor:
            old_hp = actor.hp
            actor.hp = min(old_hp, max(1, int(Decimal(values[0]) * actor.max_hp)))
            b.emit('health_cost', actor, actor, sid, hp_before=old_hp, hp_after=actor.hp)
            old = self.anubis_attack_gain[actor.uid]
            gained = Decimal(old_hp - actor.hp) * Decimal(values[1]) * actor.panel['physical_attack'] / actor.max_hp
            new = min(Decimal(actor.panel['physical_attack'] * 4), old + gained)
            self.anubis_attack_gain[actor.uid] = new
            self.common.growth[(actor.uid, 'physical_attack')] += int(new) - int(old)
            self.common.refresh_stats(b)
        elif code == 35 and sid == 66000041 and actor.template_id == SIREN:
            gain = percent(actor.magical_attack, scaled(values[:2], level))
            b.apply_buff(target, Buff(111, actor.uid, sid, 2, stat_modifiers={'magical_attack': gain}))
        else:
            raise UnsupportedMechanism(f'Unsupported skill/effect pair {sid}/{code}')

    def status_probability(self, b, actor, target, buff_id, chance, skill):
        if buff_id == 97 and skill.skill_id in BAIYU_SKILLS:
            level = (b.current_action or {}).get('skill_level', self.common.skill_level)
            chance += scaled(next(v for code, v in skill.passives if code == 84), level)
        return chance

    def before_apply_buff(self, b, target, buff):
        if buff.buff_id in (82, 94) and buff.source_skill in IVAN_SKILLS:
            if buff.buff_id == 82:
                # Keep the base cast and the opening artifact as separate links.
                buff.max_stacks = 1
            peers = [u for u in b.alive(target.camp) if u is not target]
            linked = b._highest(peers, 1, lambda u: max(u.physical_attack, u.magical_attack))
            if linked:
                buff.stat_modifiers['moni_link_uid'] = linked[0].uid
        if buff.buff_id == 73 and buff.source_skill == 66000035:
            buff.duration = buff.initial_duration = 1
        if buff.buff_id == 5:
            rate = max((x.value for x in b.active_buffs(target)
                        if x.buff_id == 86 and x.source_skill == 66000029), default=0)
            if rate and b.rng.random() * 100 < min(100, rate * (1.5 if target.race == 4 else 1)):
                return False
        if buff.buff_id == 110 and buff.source_skill in SIREN_SKILLS and target.is_male:
            return False
        return None

    def stat_bonuses(self, b, unit):
        bonuses = Counter()
        for buff in b.active_buffs(unit):
            if buff.buff_id == 105 and buff.source_skill in BAIYU_SKILLS:
                for stat in ('physical_attack', 'magical_attack'):
                    bonuses[stat] += percent(unit.panel[stat], buff.value) * buff.stacks
            elif buff.buff_id == 111 and buff.source_skill in SIREN_SKILLS and buff.value:
                bonuses['magical_attack'] += percent(unit.panel['magical_attack'], buff.value) * buff.stacks
        return dict(bonuses)

    def additional_damage_reduction(self, b, target, kind):
        if kind != 'physical':
            return 0
        buffs = b.active_buffs(target)
        galanord = sum(x.value * x.stacks for x in buffs
                       if x.buff_id == 85 and x.source_skill in GALANORD_SKILLS)
        siren = sum(x.value * x.stacks for x in buffs
                    if x.buff_id == 113 and x.source_skill in SIREN_SKILLS)
        return min(80, galanord * (1.5 if target.race == 4 else 1) + siren)

    def before_damage(self, b, actor, target, amount, skill_id, kind):
        strike = b.current_damage
        if strike.get('siren_ward_consumed'):
            return 0
        if amount > 0 and actor is not target and kind in DIRECT:
            ward = next((x for x in b.active_buffs(target)
                         if x.buff_id == 110 and x.source_skill in SIREN_SKILLS), None)
            if ward is not None:
                target.buffs.remove(ward)
                b.emit('attack_ward', ward.source_uid, target, ward.source_skill, incoming=amount)
                # Zero the entire strike before the engine can split the damage.
                strike['siren_ward_consumed'] = True
                return 0
        return amount

    def _ivan_links(self, b, actor, target, lost):
        by_recipient = defaultdict(list)
        for buff in b.active_buffs(target):
            if buff.buff_id not in (82, 94) or buff.source_skill not in IVAN_SKILLS:
                continue
            owner = b.units.get(buff.source_uid)
            if owner is None:
                continue
            peers = [u for u in b.alive(target.camp) if u is not target]
            if not peers:
                continue
            # The chosen recipient is bound on the original application below.
            linked_uid = buff.stat_modifiers.get('moni_link_uid')
            peer = next((u for u in peers if u.uid == linked_uid), None)
            if peer is None:
                continue
            rate = buff.value * buff.stacks
            if buff.source_skill != 66000028:
                rate += sum(x.value for x in b.active_buffs(owner)
                            if x.buff_id == 83 and x.source_skill == 66000028) * buff.stacks
            if buff.buff_id == 82:
                rate = min(30, rate)
            by_recipient[peer.uid].append((buff, rate))
        cap = 70 if any(x.buff_id == 94 for links in by_recipient.values() for x, _ in links) else 60
        for uid, links in by_recipient.items():
            peer = b.units[uid]
            rate = min(cap, sum(value for _, value in links))
            cap -= rate
            amount = percent(lost, rate)
            if not amount or not peer.alive:
                continue
            old_context = b.current_hit_context
            b.current_hit_context = {'critical': False, 'bypass_invulnerability': True}
            try:
                b.damage(actor, peer, amount, links[0][0].source_skill, 'link', bypass_lethal_protection=True)
            finally:
                b.current_hit_context = old_context

    def after_damage(self, b, event):
        actor, target = b.units[event.actor_uid], b.units[event.target_uid]
        kind = event.payload.get('damage_kind')
        lost = event.payload['actual_hp_loss']
        if lost and kind != 'link':
            self._ivan_links(b, actor, target, lost)
        if (lost and target.alive and actor.template_id == COOK and actor.camp != target.camp
                and kind in DIRECT):
            level = self.common.artifact_level(actor, 66000038)
            if level is not None and not b.has_buff(actor, 192):
                values = next(v for code, v in self.catalog.skill(66000038).passives if code == 65)
                if b.rng.random() * 100 < scaled(values[:2], level):
                    b.apply_buff(target, Buff(int(values[2]), actor.uid, 66000038, int(values[3])))

    def on_event(self, b, event):
        actor = b.units.get(event.actor_uid)
        target = b.units.get(event.target_uid)
        sid = event.skill_id
        if event.kind == 'death' and target is not None:
            for charm in list(b.active_buffs(target)):
                if charm.buff_id != 97 or charm.source_skill not in BAIYU_SKILLS:
                    continue
                peers = b._highest(b.alive(target.camp), 1, lambda u: u.physical_attack)
                if peers:
                    b.apply_buff(peers[0], Buff(97, charm.source_uid, charm.source_skill, 1))
        elif event.kind == 'action_end' and actor and sid in ANUBIS_SKILLS:
            self.anubis_casts[actor.uid] += 1

    def end_round(self, b):
        for unit in b.alive():
            for buff in list(b.active_buffs(unit)):
                if buff.buff_id == 98 and buff.source_skill in JACK_SKILLS:
                    b.heal(unit, unit, percent(unit.max_hp, buff.value) * buff.stacks, buff.source_skill)
