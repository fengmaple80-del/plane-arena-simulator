"""Legacy SSS callbacks from the versioned base/awakening/artifact records.

This controller owns no ordinary static panel attributes. Unknown artifact
levels remain errors at preflight, never an implicit level-one artifact.
"""
from collections import defaultdict
from decimal import Decimal
from dataclasses import replace
from combat import Buff, UnsupportedMechanism, scaled
from panel_conversion import convert_magic_to_hp


class LegacyMechanics:
    supported_passives = {0, 17, 19, 20, 21, 61, 62, 63, 66, 67, 68, 69, 70, 71, 72, 74, 75,
                          76, 77, 78, 79, 80, 81, 83, 84, 85, 88, 89, 90, 91, 94, 95,
                          93, 103, 114, 117, 118, 119, 120, 121, 122, 123, 124, 125, 1001}
    # 105 has conflicting semantic prose/config; require a chosen model.
    unresolved = {
        105: 'Sloth: base says maxHP*(100-x)%, supplemental says maxHP*x%; demon count scope also unspecified',
    }
    interpretation_notes = {
        103: '61208201 parameterized # supersedes older61108201 literal20:5+.075*skill_level percent',
        122: '61209601 parameterized # supersedes older61109601 literal50:40+.6*skill_level percent',
    }
    supported_buffs = {41, 73, 80, 96, 114, 116, 124, 127, 134, 136}
    CONTROL = {5, 6, 20, 21, 36, 122}
    DIRECT = {'physical', 'magical', 'max_hp', 'percentage', 'current_hp', 'true', 'composite'}
    BLOOD_LION = 80600015

    def __init__(self, catalog, common, *, rules=None):
        self.catalog, self.common = catalog, common
        self.skill_level = common.skill_level
        self.rules = {'tree_crit_resistance':'scaled','sloth_cap':'complement', **(rules or {})}
        self.supported_passives = set(type(self).supported_passives)
        if self.rules.get('sloth_cap') in ('scaled', 'complement'):
            self.supported_passives.add(105)
        if self.rules.get('tree_crit_resistance') not in ('scaled', 'literal50'):
            raise ValueError('tree_crit_resistance must be scaled or literal50')
        self.counts = defaultdict(int)
        self.last_target = {}
        self.gains = defaultdict(float)
        self.round = 1
        self._combat = None
        self._reacting = 0
        self._reflecting = False
        self._executing = False
        self._pending_extra = set()
        self._pending_double = set()
        self._pending_coord = set()
        self._rows = {}
        self._aura = defaultdict(float)

    def records(self, unit, code=None):
        """Supplemental rows override same-code base rows, never double-add."""
        if unit.uid not in self._rows:
            try:
                h = self.catalog.hero_for(unit)
            except KeyError:
                self._rows[unit.uid] = []
            else:
                normal = {}
                for sid in (*h.skill_ids, *h.supplemental_ids):
                    for c, args in self.catalog.skill(sid).passives:
                        normal[c] = (c, sid, args, False)
                artifact = [(c, sid, args, True) for sid in h.artifact_ids
                            for c, args in self.catalog.skill(sid).passives]
                self._rows[unit.uid] = list(normal.values()) + artifact
        return [r for r in self._rows[unit.uid] if code is None or r[0] == code]

    def value(self, unit, code, default=0):
        values = []
        for _, sid, args, artifact in self.records(unit, code):
            level = self.common.artifact_level(unit, sid) if artifact else self.skill_level
            if level is not None and len(args) == 2:
                values.append(scaled(args, level))
        return max(values, default=default)

    def has(self, unit, code):
        return bool(self.records(unit, code))

    def owners(self, combat, code, camp=None):
        return [u for u in combat.alive(camp) if self.has(u, code)]

    def preflight(self, combat, actor, skill):
        for unit in combat.alive():
            for code, sid, args, artifact in self.records(unit):
                if code in self.unresolved and code not in self.supported_passives:
                    raise UnsupportedMechanism(self.unresolved[code])
                if artifact and code in self.supported_passives and self.common.artifact_level(unit, sid) is None:
                    raise UnsupportedMechanism(f'Explicit artifact level required: {unit.uid}/{sid}')

    def initialize(self, combat):
        if self._combat is combat:
            return
        if self._combat is not None:
            raise ValueError('Legacy controller cannot be shared between battles')
        self._combat = combat
        for unit in combat.alive():
            if self.has(unit, 68) and not unit.panel.get('includes_static_passives', True):
                p=dict(unit.panel,max_hp=unit.max_hp,magical_attack=unit.magical_attack)
                addition=convert_magic_to_hp(p,self.value(unit,68),policy=combat.mechanism_options.get('wing_conversion_policy','bonus_plus_base'))
                unit.panel.update(p);unit.max_hp=p['max_hp'];unit.hp+=addition
                unit.magical_attack=p['magical_attack']
            if self.has(unit, 114):
                self.counts[(unit.uid, 'reflect_left')] = min(6, sum(u.race == 1 for u in combat.alive(unit.camp)))
            if self.has(unit, 124):
                self.counts[(unit.uid, 'silence_guarantees')] = min(6, sum(u.race == 0 for u in combat.alive(unit.camp)))
            if self.has(unit, 117):
                self.counts[(unit.uid, 'rescue_left')] = min(2, sum(u.race == 0 for u in combat.alive(unit.camp)) // 3)
        self.refresh_auras(combat)
        self._refresh_buff_attacks(combat)

    def refresh_auras(self, combat):
        for unit in combat.units.values():
            amount = max((self.value(o, 72) for o in self.owners(combat, 72, unit.camp)), default=0) if unit.alive else 0
            for stat in ('physical_defence', 'magical_defence'):
                key = (unit.uid, stat)
                setattr(unit, stat, max(0, int(getattr(unit, stat) - self._aura[key] + amount)))
                self._aura[key] = amount
        self.common.refresh_stats(combat)

    def _gain(self, combat, unit, stat, amount, key, cap=None):
        k = (unit.uid, key)
        old = self.gains[k]
        new = max(0, old + amount)
        if cap is not None:
            new = min(cap, new)
        self.gains[k] = new
        self.common.growth[(unit.uid, stat)] += new - old
        self.common.refresh_stats(combat)

    def before_apply_buff(self, combat, target, buff):
        if buff.buff_id == 137 and buff.source_skill == 66000055:
            self.last_target[(target.uid, 'speed_before', buff.source_uid)] = target.speed
        if combat.positive(buff) or combat.seal_bypasses_immunity(buff):
            return True
        # All three holders' BASE records explicitly state negative immunity.
        if self.has(target, 120):
            return False
        if buff.buff_id in self.CONTROL and combat.rng.random() * 100 < min(100, self.value(target, 61)):
            return False
        if target.race == 0 and buff.buff_id in {5, 6, 21, 65, 43}:
            chance = max((self.value(o, 89) for o in self.owners(combat, 89, target.camp)), default=0)
            if chance and combat.rng.random() * 100 < min(100, chance):
                return False
        if target.race == 1 and buff.buff_id in self.CONTROL and not self._reflecting:
            source = combat.units.get(buff.source_uid)
            for owner in self.owners(combat, 114, target.camp):
                key = (owner.uid, 'reflect_left')
                if source and source.camp != target.camp and self.counts[key] > 0:
                    self.counts[key] -= 1
                    self._reflecting = True
                    try:
                        combat.apply_buff(source, replace(buff, source_uid=owner.uid, source_skill=61209001))
                    finally:
                        self._reflecting = False
                    return False
        return True

    def before_damage(self, combat, actor, target, amount, skill_id, kind):
        # Percentage immunity is combined once by StatusMechanics, before the
        # fixed skill addition. Multiplying a separate aura stage double-counts
        # it against status immunity and shrinks the fixed addition.
        return amount

    def additional_damage_reduction(self, combat, target, kind):
        reduction=0
        if kind in ('physical', 'magical'):
            reduction = max((int(self.value(o, 71)) for o in self.owners(combat, 71, target.camp)), default=0)
            if target.race == 0:
                reduction += max((self.value(o, 88) for o in self.owners(combat, 88, target.camp)), default=0)
            # Intrinsic66/67 reduce the primary before its fixed addition in
            # CommonMechanics. They cannot be represented by panel attributes.
            reduction += min(80, sum(b.value * b.stacks for b in combat.active_buffs(target) if b.buff_id == 96))
        return min(80,reduction)

    def primary_immunity(self,combat,target,kind):
        if target.template_id==80600072:return 0
        if target.template_id==80600041 and kind=='physical':
            # User/guide-confirmed Lust awakening:0.025% per skill level,
            # 3.75% at150. Its66|1|0.025 header is not an extra1% immunity.
            return max((float(Decimal(args[1])*self.skill_level)
                        for _,_,args,artifact in self.records(target,66)
                        if not artifact and len(args)==2),default=0)
        return self.value(target,66 if kind=='physical' else 67)

    def before_settlement(self, combat, actor, target, amount, skill_id, kind):
        if self.has(target, 105) and kind not in ('dot', 'transfer'):
            if 105 not in self.supported_passives:
                raise UnsupportedMechanism(self.unresolved[105])
            rate = self.value(target, 105)
            if self.rules['sloth_cap'] == 'complement':
                rate = 100 - rate
            #45 first-round cap observations in21 matches distinguish allied
            #demons from all camps (including the Sloth owner himself).
            rate -= 5 * sum(u.race == 3 for u in combat.alive(target.camp))
            limit = int(target.max_hp * max(0, rate) / 100)
            parts = (combat.current_damage or {}).get('components', ())
            # 32a51ed1c09e selection 02/11: the HP extra and magical hit
            # both deduct HP. Sharing interactions have not been established.
            if (amount > limit and kind == 'composite' and skill_id in (61120011, 61121051)
                    and actor.camp != target.camp
                    and tuple(p['kind'] for p in parts) == ('magical', 'max_hp')
                    and amount == sum(p['resolved_amount'] for p in parts)
                    and not combat.has_buff(actor, 223)
                    and not any(b.buff_id == 166 and b.source_skill == 66000066
                                for unit in combat.alive() for b in combat.active_buffs(unit))):
                for part in parts:
                    part['resolved_amount'] = min(part['resolved_amount'], limit)
                amount = sum(p['resolved_amount'] for p in parts)
            else:
                amount = min(amount, limit)
        # Save the settled amount for overflow rescue, not HP-clipped damage.
        self.last_target[(target.uid, 'incoming')] = (amount, target.hp)
        # day19-camp2-06 g2: the first two dragon strikes land on cursed
        # Agua but both are fully absorbed by Lily and record zero HP loss.
        shielded=not amount and (combat.current_damage or {}).get('ancestral_absorbed_hit_uid')==target.uid
        if not shielded and actor.race == 3 and kind not in ('dot', 'transfer', 'counter', 'reflection'):
            curse = sum(b.value * b.stacks for b in combat.active_buffs(target) if b.buff_id in (124, 127))
            amount += int(target.hp * curse / 100)
        return amount

    def settle_component_hp(self, combat, actor, target, skill_id, parts, amount):
        # Nine retained friendly Justice strikes show Sword triggering at the
        # lethal component, before Angel, without a separate on-hit reaction.
        if (skill_id != 61100201 or actor.template_id != 80600002 or target.template_id != 80600015
                or actor.camp != target.camp or amount < target.hp
                or tuple(p['kind'] for p in parts) != ('physical', 'true', 'max_hp')):
            return None
        strike = combat.current_damage
        key = (target.uid, 'sword_rescue')
        if (self.counts[key] or self._executing or getattr(combat, 'bypass_resurrection', False)
                or combat.current_hit_context.get('bypass_rescue', False)
                or any(strike.get(name) for name in ('jinkai_component_settlements', 'transferred_components',
                                                     'lifesteal_components'))
                or any(combat.has_buff(target, bid) for bid in (43, 68, 114, 116))):
            return None
        values = strike.get('observed_component_amounts', [p['resolved_amount'] for p in parts])
        if len(values) != len(parts) or sum(values) != amount or min(values) <= 0:
            return None
        sword = next((args for _, sid, args, _ in self.records(target, 1001) if sid == 66000017), None)
        level = self.common.artifact_level(target, 66000017)
        if sword is None or level is None or level < 1:
            return None
        # At level 10 all nine report values use 30 + 15 * (10 - 1),
        # with no extra HP added to a positive overflow recovery.
        rate = scaled(sword, level - 1)

        def sword_first(before, incoming):
            if self.counts[key]:
                return False
            self.counts[key] = 1
            heal = int((incoming - before) * rate / 100)
            self.common._heal(combat, target, target, heal, 66000017, allow_rescue=True)
            target.hp = max(1, target.hp)
            combat.emit('rescue', target, target, 66000017, hp_after=target.hp)
            return True

        actual = 0
        for index, (part, value) in enumerate(zip(parts, values)):
            actual += combat._apply_damage_hp(actor, target, value, skill_id,
                                             consume_mark=index == 0, rescue_first=sword_first)
            if not target.alive:
                return actual, dict(part, resolved_amount=value)
            if combat.has_buff(target, 68):
                break
        return actual, None

    def try_dwarf_rescue(self,combat,actor,target,skill_id):
        if (target.alive or self._executing or getattr(combat,'bypass_resurrection',False)
                or getattr(combat,'current_hit_context',{}).get('bypass_rescue',False)):
            return False
        if target.race==0:
            rescuers=self.owners(combat,117,target.camp)
            if self.has(target,117):rescuers.append(target)
            for owner in rescuers:
                key=(owner.uid,'rescue_left')
                if self.counts[key]>0:
                    self.counts[key]-=1
                    target.hp=1
                    combat.emit('rescue',owner,target,61209201,hp_after=1)
                    return True
        return False

    def on_lethal(self, combat, actor, target, skill_id):
        if (target.alive or self._executing or getattr(combat, 'bypass_resurrection', False)
                or getattr(combat, 'current_hit_context', {}).get('bypass_rescue', False)):
            return
        for _, sid, args, artifact in self.records(target, 0):
            if sid != 66000003 or self.counts[(target.uid, 'light_revive')]:
                continue
            level = self.common.artifact_level(target, sid)
            if level is None:
                continue
            self.counts[(target.uid, 'light_revive')] = 1
            chance = scaled(self.catalog.tables['Skill'][sid]['技能释放概率'].split('|'), level)
            if combat.rng.random() * 100 < min(100, chance):
                target.hp = min(target.max_hp, max(1, int(target.max_hp * scaled(args, level) / 100)))
                combat.emit('revive', target, target, sid, hp_after=target.hp)
                return
        if self.has(target,90) and not getattr(combat,'current_hit_context',{}).get('bypass_revive',False):
            _,sid,args,_=self.records(target,90)[0]
            level=self.common.artifact_level(target,sid)
            count=self.counts[(target.uid,'lucifer_revives')]
            if level is None:
                raise UnsupportedMechanism('Lucifer artifact level required')
            if count<5:
                chance=max(0,scaled(args[:2],level)-count*float(args[6]))
                hp_percent=max(0,scaled(args[2:4],level)-count*float(args[6]))
                if combat.rng.random()*100<min(100,chance):
                    self.counts[(target.uid,'lucifer_revives')]+=1
                    target.hp=max(1,min(target.max_hp,int(target.max_hp*hp_percent/100)))
                    gain=target.panel['physical_attack']*scaled(args[4:6],level)/100
                    self._gain(combat,target,'physical_attack',gain,'lucifer_revive_attack',target.panel['physical_attack']*5)
                    combat.emit('revive',target,target,sid,hp_after=target.hp)
                    return
        immortal = next((b for b in combat.active_buffs(target) if b.buff_id == 116), None)
        blessing = next((b for b in combat.active_buffs(target) if b.buff_id == 114), None)
        if immortal or blessing:
            target.hp = 1
            if blessing:
                target.buffs.remove(blessing)
                combat.apply_buff(target, Buff(116, blessing.source_uid, blessing.source_skill, 1, blessing.value))
            combat.emit('rescue', target, target, 66000043, hp_after=1)
            return
        key = (target.uid, 'sword_rescue')
        if self.has(target, 1001) and not self.counts[key]:
            self.counts[key] = 1
            incoming, hp_before = self.last_target.get((target.uid, 'incoming'), (0, 0))
            target.hp = 1
            self.common._heal(combat, target, target, int(max(0, incoming - hp_before) * self.value(target, 1001) / 100), 66000017)
            combat.emit('rescue', target, target, 66000017, hp_after=target.hp)
            return
        if self.try_dwarf_rescue(combat,actor,target,skill_id):return
        # Main-character template is 80000000 in this game's Hero table; callers
        # may explicitly mark their main unit instead of relying on this id.
        row = self.catalog.tables['Hero'].get(target.template_id, {})
        is_main = bool(target.panel.get('is_main_character')) or row.get('英雄名称') == '主角'
        if is_main:
            for owner in self.owners(combat, 83, target.camp):
                _, sid, args, _ = self.records(owner, 83)[0]
                level = self.common.artifact_level(owner, sid)
                if level is None:
                    continue
                k = (owner.uid, 'main_revives')
                limit = 2 if self.has(owner, 91) else 1
                if self.counts[k] >= limit:
                    continue
                if combat.rng.random() * 100 >= min(100, scaled(args[:2], level)):
                    continue
                self.counts[k] += 1
                target.hp = max(1, min(target.max_hp, int(target.max_hp * scaled(args[2:], level) / 100)))
                combat.emit('revive', owner, target, sid, hp_after=target.hp)
                if self.has(owner, 91):
                    a = self.records(owner, 91)[0][2]
                    for ally in combat.alive(owner.camp):
                        value = scaled(a[4:6], self.skill_level) + (scaled(a[:2], self.skill_level) if ally.race == 0 else 0)
                        combat.apply_buff(ally, Buff(int(a[2]), owner.uid, 61206401, int(a[3]), value))
                return

    def before_action(self, combat, actor, skill):
        self.refresh_auras(combat)
        self._refresh_buff_attacks(combat)

    def stat_bonuses(self, combat, unit):
        return {stat: self.gains[(unit.uid, 'revenge_' + stat)]
                for stat in ('physical_defence',)}

    def stat_inputs(self, combat, unit):
        # Non-buff state read by the additions and the current-attack transform.
        return (self.gains.get((unit.uid, 'revenge_physical_defence'), 0),
                self.counts.get((unit.uid, 'revenge_attack_deaths'), 0),
                self.combo_growth_for(unit))

    def combo_growth_for(self, unit):
        return self.gains.get((unit.uid,'combo_growth'),0)

    def stat_transform(self, combat, unit, stat, value):
        if stat in ('physical_attack', 'magical_attack'):
            deaths = self.counts.get((unit.uid, 'revenge_attack_deaths'), 0)
            if deaths:
                value *= 1 + deaths * self.value(unit, 19) / 100
        return value

    def _status_type_count(self, combat, camp=None, *, positive_only=False):
        # The accepted Wrath model counts retained icons, including sealed
        # statuses, once per hero/type. Reapplying53 is not a second icon.
        return sum(len({b.buff_id for b in unit.buffs
                        if b.duration>0 and b.hits_remaining!=0
                        and (not positive_only or combat.positive(b))})
                   for unit in combat.alive(camp))

    def _refresh_buff_attacks(self, combat):
        for unit in combat.alive():
            active=next((b for b in combat.active_buffs(unit) if b.buff_id==41),None)
            if active is not None:
                count=self._status_type_count(combat,unit.camp,positive_only=True)
                cap=max(0,unit.panel['physical_attack']*4-self.gains[(unit.uid,'buff_attack')])
                wanted=min(cap,unit.panel['physical_attack']*count*active.value/100)
                self._gain(combat,unit,'physical_attack',
                           wanted-self.gains[(unit.uid,'ally_buffs')],'ally_buffs')
        for unit in combat.alive():
            self._buff_attack(combat,unit)

    def _buff_attack(self, combat, actor):
        if not self.has(actor, 62):
            return
        count = self._status_type_count(combat)
        rate = min(400, count * self.value(actor, 62))
        cap = max(0, actor.panel['physical_attack'] * 4 - self.gains[(actor.uid, 'ally_buffs')])
        wanted = min(cap, actor.panel['physical_attack'] * rate / 100)
        self._gain(combat, actor, 'physical_attack', wanted - self.gains[(actor.uid, 'buff_attack')], 'buff_attack')

    def resolve_hit_context(self, combat, actor, target, skill_id, kind, context):
        if kind == 'healing':
            return context
        if kind == 'physical':
            if skill_id in (61007201,61107201):
                args=next(values for code,values in self.catalog.skill(skill_id).passives if code==17)
                ignore=scaled(args,self.skill_level)
            elif actor.template_id!=80600029 and self.has(actor,17):
                ignore=self.value(actor,17)
            else:
                ignore=0
            context['ignore_defence_percent']=min(100,context.get('ignore_defence_percent',0)+ignore)
        if self.has(actor, 21) and skill_id != 61000001 and self.catalog.skill(skill_id).activation_type == 1:
            action = getattr(combat, 'current_action', None)
            seen = action.setdefault('legacy_magic_steals', set()) if action is not None else set()
            if target.uid not in seen:
                seen.add(target.uid)
                stolen = min(target.magical_attack, int(target.magical_attack * self.value(actor, 21) / 100))
                self.common.growth[(target.uid, 'magical_attack')] -= stolen
                self._gain(combat, actor, 'magical_attack', stolen, 'leviathan')
        if self.has(actor, 69) and self.counts[(actor.uid, 'steals')] < 10:
            #First-hit report prefixes exclude this gain from the current hit.
            #Capture before death/status reactions alter the target's stats.
            context['legacy_hades_gain']=target.magical_attack * self.value(actor, 69) / 100
        if self.has(actor, 75):
            multiplier=1 + min(1, self.value(actor, 75) / 100 * target.hp / target.max_hp)
            if kind=='magical' and skill_id==61005201:
                context['hades_post_defence_multiplier']=multiplier
            elif (kind=='physical' and skill_id==61000001
                    and combat.mechanism_options.get('hades_normal_artifact_order')=='after_defence'):
                context['hades_normal_multiplier']=multiplier
            else:
                # Active magic has separate unresolved timing constraints.
                context['attack_multiplier']=context.get('attack_multiplier',1)*multiplier
        if self.has(actor, 70):
            if actor.template_id == self.BLOOD_LION:
                if kind == 'physical':
                    # Luoshen 18 g1/a7: settled overflow survives both rescues
                    # and raises attack before defence, not the final damage.
                    taken = self.counts.get((actor.uid, 'blood_lion_settled_damage'), 0)
                    context['attack_multiplier'] = context.get('attack_multiplier', 1) * (1 + min(3, taken / actor.max_hp))
            else:
                context['damage_multiplier'] = context.get('damage_multiplier', 1) * min(3, 1 + 1 - actor.hp / actor.max_hp)
        if self.has(actor, 80) and skill_id in (61000001, 61006301, 61106301, 61206301):
            key = (actor.uid, 'combo_growth')
            old = self.gains[key]
            previous = self.last_target.get(actor.uid)
            if previous is not None and previous != target.uid:
                retained = old * self.value(actor, 85) / 100
                self._gain(combat, actor, 'physical_attack', retained - old, 'combo_growth')
            self.last_target[actor.uid] = target.uid
            context['legacy_golden_growth']=skill_id!=61000001
        return context

    def hit_count_bonus(self, combat, actor, skill):
        hits = 0
        if self.has(actor, 80) and skill.skill_id in (61006301, 61106301, 61206301):
            args = self.records(actor, 80)[0][2]
            for chance in args[:3]:
                if combat.rng.random() * 100 >= min(100, float(chance) + self.value(actor, 81)):
                    break
                hits += 1
        return hits

    def target_count_bonus(self, combat, actor, skill, ally):
        count = 0
        if not ally and self.has(actor, 78) and skill.skill_id in (61003601, 61103601, 61203601):
            if combat.rng.random() * 100 < min(100, self.value(actor, 78)):
                count += 1
        return count

    def status_probability(self, combat, actor, target, buff_id, chance, skill):
        sid = skill.skill_id if hasattr(skill, 'skill_id') else skill
        if buff_id == 5 and actor.race == 0 and sid != 66000058:
            if any(self.counts[(owner.uid,'silence_guarantees')]>0 for owner in self.owners(combat,124,actor.camp)):
                chance=100
        if buff_id == 6:
            if actor.template_id == 80600008 and sid in (61005001, 61105001, 61205001):
                chance = self.value(actor, 76, chance)
            elif self.has(actor, 84):
                chance += self.value(actor, 84)
        return min(100, max(0, chance))

    def critical_resistance(self, combat, target):
        if not self.owners(combat, 122, target.camp):
            return 0
        value = max(self.value(o, 122) for o in self.owners(combat, 122, target.camp))
        if self.rules['tree_crit_resistance'] == 'literal50':
            value = 50
        return value * (2 if target.race == 3 else 1)

    def critical_modifiers(self, combat, actor, target):
        revives=self.counts[(actor.uid,'lucifer_revives')]
        return {'damage_resistance': self.critical_resistance(combat, target),
                'chance':revives*self.value(actor,94), 'damage':revives*self.value(actor,95)}

    def _execute_low_hp(self, combat):
        if self._executing:
            return
        self._executing = True
        try:
            for owner in self.owners(combat, 77):
                for target in list(combat.alive()):
                    if target.camp == owner.camp or self.counts[(owner.uid, 'executes')] >= 2:
                        continue
                    threshold = self.value(owner, 77)
                    threshold += (1 - owner.hp / owner.max_hp) * self.value(owner, 79)
                    threshold += min(20, sum(b.value * b.stacks for b in combat.active_buffs(target) if b.buff_id == 80))
                    if target.hp / target.max_hp * 100 < threshold:
                        self.counts[(owner.uid, 'executes')] += 1
                        old = target.hp
                        target.hp = 0  # Explicit execution ignores ALL rescue hooks.
                        combat.emit('execution', owner, target, 61006001, hp_before=old, hp_after=0)
                        combat.emit('death', owner, target, 61006001)
                        for ally in combat.alive(owner.camp):
                            if ally.position < 3:
                                combat.apply_buff(ally, Buff(73, owner.uid, 61006001, 2, scaled(('10', '.05'), self.skill_level)))
        finally:
            self._executing = False

    def _reactive(self, combat, actor, target, event):
        if self._reacting or not target.alive or event.payload['damage_kind'] not in self.DIRECT:
            return
        self._reacting += 1
        try:
            if (target.template_id==80600020 and actor.camp!=target.camp
                    and 61106201 in self.catalog.hero_for(target).skill_ids):
                sid=61106201
                chance=scaled(self.catalog.tables['Skill'][sid]['技能释放概率'].split('|'),self.skill_level)
                if combat.rng.random()*100<chance:
                    combat.queue_followup(target.uid,sid,skill_level=self.skill_level,
                        origin={'kind':'reaction','mechanism':'old_ed_attacked','source_uid':target.uid})
            reflect = sum(b.value * b.stacks for b in combat.active_buffs(target) if b.buff_id == 73)
            if actor.alive and reflect:
                combat.damage(target, actor, int(event.payload['actual_hp_loss'] * reflect / 100), event.skill_id, 'reflection')
            immortality = next((b for b in combat.active_buffs(target) if b.buff_id == 116), None)
            if actor.alive and immortality:
                raw = max(1, int(target.max_hp * immortality.value / 100) - actor.magical_defence)
                if self.has(target, 103):
                    # 61208201 replaces the older literal20 prose with '#%'.
                    raw += int(actor.hp * self.value(target, 103) / 100)
                combat.damage(target, actor, raw, 66000043, 'reflection')
            if self.has(target, 79) and actor.alive:
                level = self.common.artifact_level(target, 66000021)
                if level is not None:
                    rate = scaled(('10', '1'), level)
                    combat.apply_buff(target, Buff(16, target.uid, 66000021, 1, rate))
                    dealt = combat.damage(target, actor, int(actor.hp * scaled(('5', '1.5'), level) / 100), 66000021, 'counter')
                    # Real replay 0e25df.../game2/r0/a10:108200 damage,
                    #21640 same-action heal. Percentage counter carries its
                    #own lifesteal even though generic physical lifesteal does not.
                    self.common._lifesteal(combat, target, dealt * rate / 100, 66000021)
            origin = (getattr(combat, 'current_action', None) or {}).get('origin') or {}
            if target.template_id == 80600009 and actor.alive and origin.get('mechanism') != 'counter' and combat.rng.random() < .6:
                # The actual attacker may be an ally under charm/confusion;
                # only an enemy can be an explicit ordinary primary.
                combat.queue_followup(target.uid, 61005101, skill_level=self.skill_level,
                                      primary_uid=actor.uid if actor.camp!=target.camp else None,
                                      origin={'kind':'counter','mechanism': 'counter', 'source_uid': target.uid})
        finally:
            self._reacting -= 1

    def after_damage(self, combat, event):
        actor=combat.units.get(event.actor_uid)
        if (actor and event.payload['damage_kind'] in self.DIRECT
                and event.payload.get('context',{}).get('legacy_golden_growth')
                and self.has(actor,80) and not combat.has_buff(actor,122)):
            # 9f500f and ab8df204 distinguish skipping this charmed hit's
            # gain from clearing the attack accumulated by earlier hits.
            args=self.records(actor,80)[0][2]
            self._gain(combat,actor,'physical_attack',actor.panel['physical_attack']*scaled(args[3:5],self.skill_level),
                       'combo_growth',actor.panel['physical_attack']*4)

    def on_event(self, combat, event):
        actor = combat.units.get(event.actor_uid)
        target = combat.units.get(event.target_uid)
        if event.kind == 'action_start' and actor:
            self.counts[(actor.uid, 'action_damage')] = 0
            if event.skill_id == 66000055:
                self.counts[(actor.uid, 'speed_pool')] = 0
        elif event.kind == 'damage' and actor and target:
            if target.template_id == self.BLOOD_LION:
                self.counts[(target.uid, 'blood_lion_settled_damage')] += event.payload['requested']
            dealt = event.payload['actual_hp_loss']
            context=event.payload.get('context',{})
            if event.payload['damage_kind'] in self.DIRECT:
                if 'legacy_hades_gain' in context and self.has(actor,69) and self.counts[(actor.uid,'steals')]<10:
                    self.counts[(actor.uid,'steals')]+=1
                    self._gain(combat,actor,'magical_attack',context['legacy_hades_gain'],'steals')
            if dealt and event.payload['damage_kind'] in self.DIRECT:
                self.counts[(actor.uid, 'action_damage')] += dealt
            if actor.alive:
                for buff in combat.active_buffs(actor):
                    if buff.buff_id == 134:
                        for kind, amount in self.common.lifesteal_components(event,use_requested=True):
                            if kind in self.DIRECT:
                                self.common._lifesteal(combat, actor, amount * buff.value / 100, buff.source_skill, 1)
            if actor.alive and self.has(actor, 63) and event.payload['damage_kind'] in self.DIRECT:
                target.speed = max(0, int(target.speed * max(0, 1 - self.value(actor, 63) / 100)))
            if dealt and (target.alive or event.payload['damage_kind'] in self.DIRECT) and self.has(target, 121):
                args = self.records(target, 121)[0][2]
                # A lethal direct hit still heals a surviving ally from the uncropped damage.
                basis=dealt if target.alive else event.payload['requested']
                raw = min(basis * scaled(args[:2], self.skill_level) / 100, target.max_hp * scaled(args[2:], self.skill_level) / 100)
                if target.alive:self.common._heal(combat, target, target, int(raw), 61209501)
                allies = [u for u in combat.alive(target.camp) if u is not target]
                if allies:
                    ally = min(allies, key=lambda u: (u.hp / u.max_hp, u.position))
                    self.common._heal(combat, target, ally, int(raw), 61209501)
            self._reactive(combat, actor, target, event)
            self._execute_low_hp(combat)
        elif event.kind == 'action_end' and actor:
            if event.skill_id == 66000055:
                pool = self.counts[(actor.uid, 'speed_pool')]
                allies = combat.alive(actor.camp)
                if pool and allies:
                    share = pool // len(allies)
                    for ally in allies:
                        combat.apply_buff(ally, Buff(136, actor.uid, 66000055, 2, share,
                                                     stat_modifiers={'speed': share}))
                self.counts[(actor.uid, 'speed_pool')] = 0
            self._extra_actions(combat, actor, event.skill_id)
        elif event.kind in ('buff', 'dispel', 'death', 'revive'):
            if event.kind == 'death' and target:
                # Retained plane Hiller hits also require19's allied-death
                # attack growth, despite its PVE-only text. Enemy deaths do
                # not count;20's physical-defence growth remains separate.
                for owner in self.owners(combat, 19, target.camp):
                    if owner is not target:
                        self.counts[(owner.uid, 'revenge_attack_deaths')] += 1
                for owner in self.owners(combat, 20, target.camp):
                    if owner is not target:
                        for stat in ('physical_defence',):
                            self.gains[(owner.uid, 'revenge_' + stat)] += owner.panel[stat] * self.value(owner, 20) / 100
            if event.kind == 'buff' and target:
                bid = event.payload['buff_id']
                if bid == 5 and actor and actor.camp != target.camp:
                    if actor.race == 0 and event.skill_id != 66000058:
                        for owner in self.owners(combat,124,actor.camp):
                            key=(owner.uid,'silence_guarantees')
                            if self.counts[key]>0:
                                self.counts[key]-=1
                                break
                    origin=(combat.current_action or {}).get('origin') or {}
                    if not isinstance(origin,dict) or origin.get('kind') != 'opening':
                        for owner in self.owners(combat,123,actor.camp):
                            allies=combat.alive(owner.camp)
                            recipient=min(allies,key=lambda u:(-combat.negative_count(u,include_seal=True),u.position))
                            combat.dispel(recipient,positive=False,actor=owner,skill_id=66000058)
                            rate=self.value(owner,123)/100
                            mods={stat:int(owner.panel[stat]*rate) for stat in ('physical_attack','magical_attack')}
                            combat.apply_buff(recipient,Buff(25,owner.uid,66000058,2,value=0,stat_modifiers=mods))
                        self.common.refresh_stats(combat)
                #Actual61106101 ->66000023 follows a successful enemy stun,
                #not an attempted/immune application. Resolve after the action.
                action=combat.current_action or {}
                # Hela/Mod copy retains the original status source. It is not
                # another stun performed by that source's Fish (60 replay windows).
                direct_source=not action or (action['actor_uid']==event.actor_uid and action['skill_id']==event.skill_id)
                if (bid==6 and actor and actor.template_id==80600019 and actor.camp!=target.camp
                        and direct_source and not action.get('fish_artifact_queued')):
                    level=self.common.artifact_level(actor,66000023)
                    if level is None:raise UnsupportedMechanism('Fish stun reaction requires artifact level')
                    combat.queue_followup(actor.uid,66000023,skill_level=level,
                        origin={'kind':'reaction','mechanism':'fish_stun','source_uid':actor.uid})
                    #38 double-stun actions produce one defence application;
                    #four uncapped heals are10% of max HP, not20%.
                    if combat.current_action is not None:combat.current_action['fish_artifact_queued']=True
                if bid == 137 and event.skill_id == 66000055 and actor:
                    self.common.refresh_stats(combat)
                    before = self.last_target.pop((target.uid, 'speed_before', actor.uid), target.speed)
                    self.counts[(actor.uid, 'speed_pool')] += max(0, before - target.speed)
            self._refresh_buff_attacks(combat)
            if not getattr(combat,'defer_copy_stats',False):self.refresh_auras(combat)

    def _extra_actions(self, combat, actor, skill_id):
        if not actor.alive or self.catalog.skill(skill_id).activation_type != 1 or skill_id == 61000001:
            return
        # Each queue owns a pending marker; its completion cannot schedule itself.
        double_key = actor.uid
        if double_key in self._pending_double:
            self._pending_double.remove(double_key)
        elif self.has(actor, 93) and self.round <= 2 and not self.counts[(actor.uid, 'double_round', self.round)]:
            self.counts[(actor.uid, 'double_round', self.round)] = 1
            self._pending_double.add(double_key)
            combat.queue_followup(actor.uid, skill_id, skill_level=self.skill_level,
                                  origin={'kind':'reaction','mechanism': 93, 'source_uid': actor.uid})
        if actor.uid in self._pending_extra:
            self._pending_extra.remove(actor.uid)
        elif actor.race == 3:
            demons = [u for u in combat.alive(actor.camp) if u.race == 3]
            strongest = max(demons, key=lambda u: (max(u.physical_attack, u.magical_attack), -u.position), default=None)
            if strongest is actor:
                for owner in self.owners(combat, 118, actor.camp):
                    key = (owner.uid, 'double_actions')
                    if self.counts[key] < min(2, len(demons) // 3):
                        self.counts[key] += 1
                        self._pending_extra.add(actor.uid)
                        combat.queue_followup(actor.uid, skill_id, skill_level=self.skill_level,
                                              origin={'kind':'reaction','mechanism': 118, 'source_uid': owner.uid})
                        break
        if actor.uid in self._pending_coord:
            self._pending_coord.remove(actor.uid)
            return
        demons = [u for u in combat.alive(actor.camp) if u.race == 3]
        strongest = max(demons, key=lambda u: (max(u.physical_attack, u.magical_attack), -u.position), default=None)
        if actor is strongest and self.counts[(actor.uid, 'action_damage')] > 0:
            for owner in self.owners(combat, 125, actor.camp):
                key = (owner.uid, 'coordinate')
                # 61209801: one coordinated attack per two living demons plus
                # the first one. rank3/g1 shows four casts with six demons and
                # 61109801 carries no description text, so the replays are the
                # only authority (D5-138).
                if owner is actor or self.counts[key] >= min(5, 1 + len(demons) // 2):
                    continue
                enemies = [u for u in combat.alive() if u.camp != owner.camp]
                if enemies:
                    self.counts[key] += 1
                    target = min(enemies, key=lambda u: (u.hp / u.max_hp, u.position))
                    self._pending_coord.add(owner.uid)
                    combat.queue_followup(owner.uid, 61109801, skill_level=self.skill_level, primary_uid=target.uid,
                                          origin={'kind':'reaction','mechanism': 125, 'source_uid': owner.uid})

    def end_round(self, combat):
        self.round += 1

    def after_status_expiration(self, combat):
        """Drop41-owned growth only when its actual status has expired.

        Periodic damage and queued reactions still see the old round's gain;
        the runtime calls this after decrementing status lifetimes. Artifact62
        remains a separate source and follows surviving statuses in both camps.
        A sealed but retained41 is not an expiration.
        """
        for unit in combat.units.values():
            gain=self.gains[(unit.uid,'ally_buffs')]
            if gain and not any(b.buff_id==41 and b.duration>0 for b in unit.buffs):
                self._gain(combat,unit,'physical_attack',-gain,'ally_buffs')
        self._refresh_buff_attacks(combat)

