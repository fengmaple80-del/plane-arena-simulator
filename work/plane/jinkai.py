"""Jinkai's source-derived candidate rules; no real-report validation yet.

Reserved passives240/241 are supported only if the catalog actually associates
their skill with the unit. This module never grants an unreleased awakening.
"""
from collections import defaultdict
from decimal import Decimal

from combat import UnsupportedMechanism, scaled


class JinkaiMechanics:
    supported_passives = {238, 239, 240, 241}
    supported_buffs = {223}
    DEMON, BEAST = 3, 1
    ARTIFACT = 66000108

    def __init__(self, catalog, common):
        self.catalog, self.common = catalog, common
        self.records = {}
        self.chases = defaultdict(int)
        self.attack_gain = defaultdict(int)

    def record(self, unit, code):
        if unit.uid not in self.records:
            hero = self.catalog.hero_for(unit)
            self.records[unit.uid] = {
                passive: (sid, values, sid in hero.artifact_ids)
                for sid in (*hero.skill_ids, *hero.artifact_ids)
                for passive, values in self.catalog.skill(sid).passives
            }
        return self.records[unit.uid].get(code)

    def datum(self, unit, code):
        record = self.record(unit, code)
        if record is None:
            return None
        sid, values, artifact = record
        level = self.common.artifact_level(unit, sid) if artifact else self.common.skill_level
        if level is None:
            raise UnsupportedMechanism(f'Jinkai artifact {sid} level required')
        return sid, scaled(values, level)

    def select_targets_override(self, battle, spec, actor, candidates, primary, ally):
        action = battle.current_action or {}
        if not ally and action.get('skill_id') == self.ARTIFACT:
            # Reports retain223 on beasts in the owner's opposing column.
            # Beast immunity gates damage redirection, not marker presence.
            return sorted((u for u in candidates
                           if u.alive and u.camp != actor.camp
                           and u.position % 3 == actor.position % 3),
                          key=lambda u: u.position)[:spec[1]]
        return None

    def after_select_targets(self, battle, spec, actor, selected, candidates, ally):
        if ally or spec[0] not in (7, 14) or not selected:
            return selected
        protected = [u for u in candidates if u.alive and u.camp != actor.camp
                     and self.record(u, 240)]
        if not protected:
            return selected
        protected.sort(key=lambda u: (u.hp, u.position))
        result = []
        for unit in protected + selected:
            if unit not in result:
                result.append(unit)
        return result[:len(selected)]

    def before_settlement(self, battle, actor, target, amount, skill_id, kind):
        if amount <= 0:
            return amount
        if kind != 'transfer' and actor.camp != target.camp and actor.race != self.BEAST and target.race == self.DEMON:
            for mark in battle.active_buffs(actor):
                if mark.buff_id != 223:
                    continue
                owner = battle.units.get(mark.source_uid)
                if not owner or not owner.alive or owner.camp != target.camp or not self.record(owner, 239):
                    continue
                demons = sum(u.race == self.DEMON for u in battle.alive(owner.camp))
                reduction = min(90, max(0, mark.value + demons * self.datum(owner, 239)[1]))
                strike=battle.current_damage or {}
                parts=strike.get('components') or [dict(kind=kind,resolved_amount=amount,raw_amount=amount)]
                # Captured Mother transfers retain separate magic/purification
                # amounts. Each component divides by100 before the remaining
                # whole-percent rate:487520 at90% reduction becomes48750.
                settled=[dict(p,resolved_amount=int(Decimal(p['resolved_amount']//100)*(100-Decimal(str(reduction)))),
                              transfer_remaining_percent=100-reduction) for p in parts]
                reduced=sum(p['resolved_amount'] for p in settled)
                battle.emit('jinkai_transfer', owner, target, mark.source_skill,
                            incoming_actor_uid=actor.uid, incoming_skill_id=skill_id,
                            incoming_amount=amount, transferred_amount=reduced,
                            reduction_percent=reduction,
                            component_settlements=settled,
                            validation='Mother transfer rounding observed in luoshen04/g1 and05/g3')
                # A composite strike is one transfer. The transfer kind prevents
                # repeated redirection while retaining the original attacker.
                son_artifact = skill_id == 66000103 and actor.template_id == self.common.SON
                battle.damage(actor, owner, reduced, skill_id, 'transfer',
                              transferred_components=settled,
                              # Son's artifact HP extra and the single magical
                              # hit retain their pre-transfer lifesteal basis.
                              lifesteal_components=[dict(p,pre_transfer_lifesteal=True)
                                                    if ((len(parts)==1 and p['kind']=='magical')
                                                        or (son_artifact and p['kind']=='max_hp'))
                                                    else dict(p) if p['kind']=='physical' else dict(s)
                                                    for p,s in zip(parts,settled)])
                return 0
        if self.record(target, 241):
            teammates = sum(u is not target and u.race == self.DEMON
                            for u in battle.alive(target.camp))
            if teammates >= 5:
                cap = int(Decimal(target.max_hp) * Decimal(str(self.datum(target, 241)[1])) / 100)
                amount = min(amount, cap)
        return amount

    def _on_death(self, battle, event, actor, target):
        if actor.camp == target.camp or actor.race != self.DEMON:
            return
        action = battle.current_action if battle.current_action is not None else {}
        origin = action.get('origin') or {}
        if isinstance(origin, dict) and (origin.get('kind') == 'round_end' or origin.get('mechanism') == 200):
            return
        source = event.payload.get('target_source_buff')
        if source in (117, 209):
            # Yueming #3 and Zaodianshui #11: a new blessing target's death
            # uses the attacking team's chase slot in either cast direction.
            # The two hit packets of one skill do not get two notifications.
            if (action.get('actor_uid') != actor.uid or target.race != self.DEMON
                    or action.get('blessing_new_target_uid') != target.uid
                    or action.get('jinkai_attack_notification')):
                return
            action['jinkai_attack_notification'] = 'blessing'
            camp = 1
        else:
            if (event.payload.get('lethal_component') or {}).get('source_buff') == 209:
                return
            camp = actor.camp
            if camp == 1:
                if action.get('jinkai_attack_notification') == 'blessing':
                    return
                action['jinkai_attack_notification'] = 'direct'
        for owner in battle.alive(camp):
            if self.record(owner, 238) and self.chases[owner.uid] < 2:
                self.chases[owner.uid] += 1
                active = self.catalog.hero_for(owner).active_ids
                if len(active) != 1:
                    raise UnsupportedMechanism('Jinkai chase requires one associated active skill')
                battle.queue_followup(owner.uid, active[0], skill_level=self.common.skill_level,
                                      origin=dict(kind='reaction', mechanism='jinkai_kill_chase',
                                                  source_uid=owner.uid, trigger_uid=actor.uid))

    def on_event(self, battle, event):
        actor = battle.units.get(event.actor_uid)
        target = battle.units.get(event.target_uid)
        if event.kind == 'death' and actor and target:
            self._on_death(battle, event, actor, target)
        elif (event.kind == 'damage' and actor and target and target.alive
              and event.payload.get('actual_hp_loss', 0) > 0 and self.record(target, 240)):
            sid, rate = self.datum(target, 240)
            source_attack = max(actor.panel['physical_attack'], actor.panel['magical_attack'])
            gain = int(Decimal(source_attack) * Decimal(str(rate)) / 100)
            previous = self.attack_gain[target.uid]
            current = min(target.panel['physical_attack'] * 3, previous + gain)
            self.attack_gain[target.uid] = current
            self.common.growth[(target.uid, 'physical_attack')] += current - previous
            self.common.refresh_stats(battle)
            battle.emit('candidate_passive_growth', target, target, sid,
                        source_actor_uid=actor.uid, physical_attack_increment=current - previous,
                        accumulated_increment=current, validation='reserved source-derived candidate')

    def end_round(self, battle):
        self.chases.clear()
