"""Description-derived common plane callbacks; numerical ordering remains candidate.

Panels are explicit, including static passives. No template stat reconstruction or
implicit artifact level occurs here. See integration-mechanism-notes.md for gaps.
"""
from collections import defaultdict
from decimal import Decimal
import os

from combat import Buff, UnsupportedMechanism, scaled
from passives import (fallen_healing_multiplier, dragon_magic_damage,
                      mod_defence_multiplier, parse_angel_rescue)


class CommonMechanics:
    # These codes have live callbacks. Partial compound records stay excluded.
    supported_passives = {100, 155, 156, 171, 179, 180, 181, 182, 183, 184, 185, 223,
                          191, 193, 198, 201, 206, 207, 210, 211, 212,
                          215, 217, 221, 227, 229, 231}
    supported_buffs = {11, 16, 25, 26, 27, 28, 42, 46, 52, 64, 68, 69, 70,
                       119, 120, 147, 148, 180, 185, 186, 197, 212, 216}
    FALLEN, DRAGON, PLAGUE = 80600089, 80600083, 80600093
    LULU, MERMAID, ANGEL = 80600073, 80600086, 80600017
    ADAI, SON, HELA = 80600100, 80600099, 80600072
    VIK, SHADOW, SOLOMON, LION = 80600080, 80600096, 80600087, 80600094

    def __init__(self, catalog, artifact_levels=None, skill_level=150):
        if isinstance(skill_level, bool) or not isinstance(skill_level, int) or skill_level < 1:
            raise ValueError('Positive integer skill level required')
        self.catalog = catalog
        self.skill_level = skill_level
        self.artifact_levels = dict(artifact_levels or {})
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 1
               for v in self.artifact_levels.values()):
            raise ValueError('Artifact levels must be explicit positive integers')
        self.immunity = defaultdict(int)
        self.rescued = set()
        self.growth = defaultdict(float)
        self.applied = defaultdict(float)
        self._stats_key = None
        self._passive_cache = {}
        self.defence_below_zero = {}
        self._hela_dragon_attack = {}
        self.dragon_actions = defaultdict(int)
        self.round_index = 0
        self.has_acted = set()
        self.hit_kind = {}
        self._passive_heal = 0
        self._next_lifesteal_fallen_multiplier = None
        self._combat = None
        self.supported_passives = set(type(self).supported_passives)
        if not self.artifact_levels:
            self.supported_passives -= {180, 185}

    def owners(self, combat, template, camp=None):
        return [u for u in combat.alive(camp) if u.template_id == template]

    def artifact_level(self, unit, skill_id):
        for key in (unit.uid, unit.template_id, skill_id):
            if key in self.artifact_levels:
                return self.artifact_levels[key]
        return None

    def hela_copy_attack(self, unit):
        physical = self._hela_dragon_attack.get(unit.uid,unit.physical_attack) if unit.template_id==80600021 else unit.physical_attack
        magic = unit.magical_attack
        if unit.template_id==80600013:
            magic=max(magic,unit.panel.get('magic_hp_conversion',{}).get('pre_conversion_magic',0))
        return max(physical,magic)

    def initialize(self, combat):
        if self._combat is combat:
            return
        if self._combat is not None:
            raise ValueError('A mechanics instance belongs to one combat')
        self._combat = combat
        for owner in combat.alive():
            allies = combat.alive(owner.camp)
            if owner.template_id == self.LULU:
                self.immunity[owner.uid] += sum(u.race == 0 for u in allies)
            if owner.template_id == self.MERMAID:
                for ally in allies:
                    if ally is not owner and ally.profession == 2 and ally.position % 3 == owner.position % 3:
                        self.immunity[ally.uid] += 6
        self.refresh_stats(combat)

    def _set_bonus(self, unit, stat, value, bonuses):
        key = (unit.uid, stat)
        # Remove only this controller's previous contribution.
        base = getattr(unit, stat) - self.applied[key]
        result = self._resolve_stat(unit, stat, base + value, bonuses)
        setattr(unit, stat, result)
        self.applied[key] = result-base

    def _resolve_stat(self, unit, stat, value, bonuses):
        for hook in self._combat.hooks if self._combat else ():
            if hook is self:continue
            fn=getattr(hook,'stat_bonuses',None)
            if fn:value+=bonuses[hook].get(stat,0)
            fn=getattr(hook,'stat_transform',None)
            if fn:value=fn(self._combat,unit,stat,value)
        return max(0,int(value))

    def _speed_aura(self, combat, unit, allies):
        # Only the larger of Bloodbone's and Mod's eligible auras is active.
        policy = combat.mechanism_options.get('mod_speed_aura_policy','pre_percent')
        if policy not in ('pre_percent','post_percent_integer'):
            raise ValueError('Unknown Mod speed aura policy')
        flat_mod = policy == 'post_percent_integer'
        mod = max(((a.panel['speed']//100)*30 if flat_mod else a.panel['speed']*.3
                   for a in allies if self._has_passive(a,206)), default=0) if unit.profession==2 and unit.alive else 0
        bloodbone = max((a.panel['speed'] * self._passive_values(a,223)[0] / 100
                         for a in allies if a is not unit and self._passive_values(a,223)),
                        default=0) if unit.race==1 and unit.alive else 0
        if mod>0 and mod>=bloodbone:
            return mod, flat_mod, True
        return bloodbone, False, False

    def _speed_before_hooks(self, base, aura, after_percent, rate, buffs):
        basis = base if after_percent else base + aura
        value = int(basis * (1+rate/100)) + (int(aura) if after_percent else 0)
        if rate:
            sophia_rate = max((b.value for b in buffs if b.buff_id==42 and b.source_skill==66000001), default=0)
            if sophia_rate:
                dice_delta = sum(b.stat_modifiers.get('speed',0)*b.stacks for b in buffs
                                 if b.buff_id in (221,222) and b.source_skill==66000107)
                if dice_delta:
                    # Report opening orders put both the shared speed and
                    # the stolen speed inside Sophia's multiplier. The
                    # status hook still adds the signed raw delta once.
                    factor = 1+sophia_rate/100
                    value += int((basis+dice_delta)*factor)-int(basis*factor)-dice_delta
        return value

    def clown_speed_theft_basis(self, combat, target):
        if self._combat is not combat or not self._speed_aura(combat,target,combat.alive(target.camp))[2]:
            return target.speed
        # Exclude the applied Mod aura, not all prior speed changes and not
        # a replacement aura that wasn't active when the dice were cast.
        base = target.speed-self.applied[(target.uid,'speed')]
        buffs = combat.active_buffs(target)
        rate = max((b.value for b in buffs if b.buff_id==42), default=0)
        value = self._speed_before_hooks(base,0,False,rate,buffs)
        bonuses = {hook:hook.stat_bonuses(combat,target) for hook in combat.hooks
                   if hook is not self and hasattr(hook,'stat_bonuses')}
        return self._resolve_stat(target,'speed',value,bonuses)

    STATS = ('physical_attack', 'magical_attack', 'speed', 'physical_defence', 'magical_defence')
    # Verification only: recompute on every skipped refresh and fail if stale.
    SHADOW_STATS = os.environ.get('PLANE_STATS_SHADOW') == '1'

    def _refresh_inputs(self, combat):
        """Everything refresh_stats and the stat hooks read, besides static catalog data.

        Recalculation is idempotent, so an unchanged key after a completed
        refresh means the result would be identical.
        """
        stats = self.STATS
        hooks = [h for h in (self._combat.hooks if self._combat else ())
                 if h is not self and hasattr(h, 'stat_inputs')]
        rows = []
        for unit in combat.units.values():
            uid = unit.uid
            rows.append((unit.hp > 0, unit.template_id,
                         tuple(getattr(unit, s) for s in stats),
                         tuple(unit.panel.get(s) for s in stats),
                         tuple(self.applied.get((uid, s), 0.0) for s in stats),
                         self.growth.get((uid, 'physical_attack'), 0.0),
                         self.growth.get((uid, 'magical_attack'), 0.0),
                         self.dragon_actions.get(uid, 0),
                         tuple((b.buff_id, b.value, b.stacks, b.duration > 0, b.hits_remaining != 0,
                                b.source_uid, b.source_skill, b.source_attack,
                                tuple(sorted(b.stat_modifiers.items())) if b.stat_modifiers else ())
                               for b in unit.buffs),
                         tuple(h.stat_inputs(combat, unit) for h in hooks)))
        return (tuple(sorted(combat.mechanism_options.items(), key=lambda kv: kv[0])), tuple(rows))

    def _stats_snapshot(self, combat):
        return ([tuple(getattr(u, s) for s in self.STATS) for u in combat.units.values()],
                dict(self.applied), dict(self.defence_below_zero))

    def refresh_stats(self, combat):
        if getattr(combat,'defer_copy_stats',False):return
        key = self._refresh_inputs(combat)
        if key == self._stats_key:
            if self.SHADOW_STATS:
                before = self._stats_snapshot(combat)
                self._refresh_stats_full(combat)
                if self._stats_snapshot(combat) != before:
                    raise AssertionError(f'Skipped stat refresh would be stale after {len(combat.events)} events')
            return
        self._refresh_stats_full(combat)
        self._stats_key = self._refresh_inputs(combat)

    def _refresh_stats_full(self, combat):
        fish_policy=combat.mechanism_options.get('fish_artifact_defence_policy','stack')
        if fish_policy not in ('stack','largest_active'):
            raise ValueError('Unknown Fish artifact defence policy')
        sealed_camps = {u.camp for u in combat.alive() if combat.has_buff(u, 192)}
        for unit in combat.units.values():
            allies = combat.alive(unit.camp)
            buffs = combat.active_buffs(unit)
            totals=defaultdict(int);maxima={}
            for buff in buffs:
                totals[buff.buff_id]+=buff.value*buff.stacks
                maxima[buff.buff_id]=max(maxima.get(buff.buff_id,buff.value),buff.value)
            total = lambda bid: totals.get(bid,0)
            maximum = lambda bid: maxima.get(bid,0)
            # These bonuses read panels, buffs and counters, none of which
            # changes while this unit's five current stats are recalculated.
            # Keep the cache local: later buff mutations must be seen immediately.
            bonuses={hook:hook.stat_bonuses(combat,unit)
                     for hook in (self._combat.hooks if self._combat else ())
                     if hook is not self and hasattr(hook,'stat_bonuses')}
            physical = self.growth[(unit.uid, 'physical_attack')]
            magical = self.growth[(unit.uid, 'magical_attack')]
            if unit.alive:
                # Internal mage mirrors: sealing one's own allies does not
                # enable one's own Vik (zaodianshui15/g3/Mod's first hit).
                if any(camp != unit.camp for camp in sealed_camps) and unit.position >= 3:
                    for vik in self.owners(combat, self.VIK, unit.camp):
                        if vik is not unit:
                            magical += (vik.panel['magical_attack']//100) * scaled(('15', '.7'), self.skill_level)
                # A seal disables the recipient's positive effects, without
                # removing their ownership. Fallen's separate aura counts
                # retained layers (selection attempt10's first dragon cast).
                positives = sum(b.stacks for b in unit.buffs if b.duration > 0
                                and b.hits_remaining != 0 and combat.positive(b))
                for fallen in self.owners(combat, self.FALLEN, unit.camp):
                    if unit is not fallen and unit.race == 3 and unit.position % 3 == fallen.position % 3:
                        physical += (fallen.panel['physical_attack'] // 100) * min(200, 15 * positives)
                if unit.race == 3:
                    karma_count = sum(b.stacks for b in buffs if combat.positive(b))
                    for karma in (b for b in buffs if b.buff_id == 216):
                        if karma.source_attack is None:
                            raise ValueError('Karma requires caster panel attack captured at application')
                        physical += (karma.source_attack // 100) * min(200, karma.value * karma_count)
                level = self.artifact_level(unit, 66000087)
                if unit.template_id == self.DRAGON and level is not None:
                    physical += unit.panel['physical_attack'] * min(1.5, self.dragon_actions[unit.uid] * scaled(('10', '2'), level) / 100)
            # Retained Yoyo heals and attack bounds use the integer percent;
            # keep the original status value for transfers and evidence.
            all_rate = (maximum(42) + int(maximum(64))) / 100
            for stat, growth, flat in [('physical_attack', physical, total(11) + total(25) - maximum(26)),
                                       ('magical_attack', magical, total(25) - maximum(26))]:
                base = getattr(unit, stat) - self.applied[(unit.uid, stat)]
                rate=all_rate
                rate+=(total(133)+(total(132) if stat=='physical_attack' else 0))/100
                if stat == 'physical_attack':
                    rate-=maximum(120)/100
                    for buff in buffs:
                        if buff.buff_id==118:
                            source=combat.units.get(buff.source_uid)
                            demons=sum(u.race==3 for u in combat.alive(source.camp)) if source else 0
                            rate-=(buff.value+5*demons)/100
                if unit.template_id == self.SON and stat == 'physical_attack':
                    # User-confirmed: Son's conversion and panel attack are
                    # combined before Lust/Sloth percentage attack changes.
                    value=max(0,(base+growth+flat)*max(0,1+rate))
                else:
                    value=max(0,base*max(0,1+rate)+growth+flat)
                if unit.template_id==80600021 and stat=='physical_attack':
                    # Hela's transfer ignores the combo increment, not other
                    # statuses. Resolve the same transforms for the ranking copy.
                    combo=sum(h.combo_growth_for(unit) for h in combat.hooks if hasattr(h,'combo_growth_for'))
                    if combo:
                        ranked=max(0,base*max(0,1+rate)+(growth-combo)+flat)
                        self._hela_dragon_attack[unit.uid]=self._resolve_stat(unit,stat,int(ranked),bonuses)
                        # The combo portion receives the same percentage changes
                        # as panel attack; unrelated flat gains stay outside.
                        value=max(0,(base+combo)*max(0,1+rate)+(growth-combo)+flat)
                    else:self._hela_dragon_attack.pop(unit.uid,None)
                self._set_bonus(unit, stat, int(value) - base, bonuses)
            aura, after_percent, _ = self._speed_aura(combat,unit,allies)
            base = unit.speed - self.applied[(unit.uid, 'speed')]
            adjusted_speed = self._speed_before_hooks(base,aura,after_percent,maximum(42),buffs)
            self._set_bonus(unit, 'speed', adjusted_speed-base, bonuses)
            for stat in ('physical_defence', 'magical_defence'):
                base = getattr(unit, stat) - self.applied[(unit.uid, stat)]
                # Sophia/Queen artifact description parameters:6+L attack/
                # speed,12+2L defence. Reuse the existing stage1890 analysis.
                # The tooltip gives six layers, but repeated-copy reports
                # distinguish that naive48% interpretation from an empirical
                # cap. Keep the alternative explicit in every run manifest.
                hela_cap=combat.mechanism_options.get('hela_defence_cap_percent',maximum(180)*6)
                if not isinstance(hela_cap,(int,float)) or isinstance(hela_cap,bool) or not 0<=hela_cap<=100:
                    raise ValueError('Hela defence cap must be an explicit percent in0..100')
                # 52/71 are the same physical-defence percentage opcode49.
                # Both defence kinds use their base before percentage changes.
                rate = maximum(42) * 2 - min(total(180),hela_cap) - total(71 if stat=='physical_defence' else 72)
                if stat == 'physical_defence':
                    fish_46=min(300,sum(b.value*b.stacks for b in buffs if b.buff_id==46 and b.source_skill==66000023))
                    if fish_policy=='largest_active':
                        fish_46=max((b.value for b in buffs if b.buff_id==46 and b.source_skill==66000023),default=0)
                    other_46=max((b.value for b in buffs if b.buff_id==46 and b.source_skill!=66000023),default=0)
                    rate += fish_46+other_46-total(52)-min(30,total(81))
                raw_base = max(0, base + total(27) - maximum(28))
                raw_value = raw_base * (1 + rate / 100)
                # Keep the part lost to the display/stat floor. Mod's temporary
                # hit defence shares this percentage sum and is applied later.
                self.defence_below_zero[(unit.uid,stat)] = min(0,raw_value)
                value = max(0,raw_value)
                self._set_bonus(unit, stat, int(value) - base, bonuses)

    def _has_passive(self, unit, code):
        return bool(self._passive_values(unit, code))

    def _passive_values(self, unit, code):
        # Catalog, skill level and artifact levels are fixed for this instance.
        key = (unit.uid, unit.template_id, code)
        if key not in self._passive_cache:
            self._passive_cache[key] = self._lookup_passive_values(unit, code)
        return list(self._passive_cache[key])

    def _lookup_passive_values(self, unit, code):
        try:
            hero = self.catalog.hero_for(unit)
            values = [scaled(v, self.skill_level) for sid in hero.active_ids
                      for c, v in self.catalog.skill(sid).passives if c == code and len(v) == 2]
            for sid in hero.artifact_ids:
                level = self.artifact_level(unit, sid)
                if level is not None:
                    values.extend(scaled(v, level) for c, v in self.catalog.skill(sid).passives
                                  if c == code and len(v) == 2)
            return values
        except KeyError:
            return []

    def before_apply_buff(self, combat, target, buff):
        if buff.buff_id == 216 and buff.source_attack is None:
            source = combat.units.get(buff.source_uid)
            if source is None:
                raise ValueError('Karma requires actual caster')
            buff.source_attack = source.panel['physical_attack']
        # Effect56's seal explicitly ignores negative-immunity mechanisms.
        if combat.seal_bypasses_immunity(buff) or combat.positive(buff):
            return True
        charge_policy=combat.mechanism_options.get('sealed_charge_immunity_policy','active')
        if charge_policy not in ('active','suspend'):
            raise ValueError('Unknown sealed charge immunity policy')
        suspended=charge_policy=='suspend' and combat.has_buff(target,192)
        action=combat.current_action or {}
        mod_copy=bool(action.get('negative_copy_source')) and action.get('skill_id') in (61020301,61120301,61220301)
        copied_speed=mod_copy and (buff.buff_id,buff.source_skill) in ((137,66000055),(222,66000107))
        lust_source=(combat.units.get(buff.source_uid) if mod_copy and action.get('copying_existing_negative')
                     and buff.buff_id in (120,122,123) and buff.source_skill in (61008401,66000045) else None)
        copied_lust=lust_source is not None and lust_source.template_id==80600041
        # Hela transfers these existing statuses without consuming finite charges;
        # the separate, newly generated defence reduction still uses a charge.
        hela_transfer=(action.get('copying_existing_negative') and action.get('skill_id') in (61013001,61113001,61213001)
                       and buff.buff_id in (21,22,120,122,123,202,203))
        if self.immunity[target.uid] > 0 and not suspended and not (copied_speed or copied_lust or hela_transfer):
            self.immunity[target.uid] -= 1
            return False
        if target.race == 3:
            chance = max((b.value for b in combat.active_buffs(target) if b.buff_id == 186), default=0)
            if chance > 0 and combat.rng.random() * 100 < min(100, chance):
                return False
        return True

    def _fallen_heal_multiplier(self, combat, target):
        multipliers = [fallen_healing_multiplier(sum(u.race == 3 for u in combat.alive(f.camp)), fallen_alive=True)
                       for f in self.owners(combat, self.FALLEN) if f.camp != target.camp]
        # Duplicate-owner stacking is not sourced; strongest aura is the candidate.
        return min(multipliers, default=1)

    def before_heal(self, combat, actor, target, amount, skill_id):
        multiplier = self._next_lifesteal_fallen_multiplier
        self._next_lifesteal_fallen_multiplier = None
        if multiplier is None:
            multiplier = self._fallen_heal_multiplier(combat, target)
        for buff in combat.active_buffs(target):
            if buff.buff_id == 212:
                multiplier *= max(0, 1 - buff.value / 100)
        if multiplier == 1:
            return amount
        return max(0, int(Decimal(amount // 100) * Decimal(str(round(multiplier * 100, 10)))))

    def _bypass(self, combat, actor, kind):
        if (getattr(combat,'current_hit_context',{}) or {}).get('bypass_rescue'):
            return True
        allies = combat.alive(actor.camp)
        if actor.template_id == self.SON and sum(u.race == 0 for u in allies) == 6:
            return True
        return (kind == 'physical' and actor.race == 3 and combat.has_buff(actor, 216)
                and sum(u.race == 3 for u in allies) >= 5
                and bool(self.owners(combat, self.ADAI, actor.camp)))

    def primary_immunity(self, combat, target, kind):
        if target.template_id == 80600041 and kind == 'physical':
            # The opening artifact records145 instead of119+121. Its blessing
            # includes one active-skill immunity layer (user-confirmed), while
            # LegacyMechanics supplies the separate awakening increment.
            if any(b.buff_id == 145 and b.source_skill == 66000045
                   for b in combat.active_buffs(target)):
                return sum(scaled(values[1:3], self.skill_level)
                           for code, values in self.catalog.skill(61008401).ally_effects
                           if code == 3 and int(values[0]) == 119)
            return 0
        if target.template_id != self.HELA:
            return 0
        level = self.artifact_level(target, 66000076)
        return scaled(('5', '1.5'), level) if level is not None else 0

    def _add_dragon_marks(self, combat, camp, marks):
        for dragon in self.owners(combat, self.DRAGON, camp):
            others = [u for u in combat.alive(camp) if u.race == 3 and u is not dragon]
            recipients = [dragon] + sorted(others, key=lambda u: (-u.max_hp, u.position))[:1]
            for recipient in recipients:
                combat.apply_buff(recipient, Buff(197, dragon.uid, 61120041, 2, stacks=marks, max_stacks=5))

    def _dragon_artifact_dispel(self, combat, actor, target, kind):
        # Yue Ming g2/a31 and auto03 g1/a33: the hit gives marks before the
        # spear removes them. A later hit on another demon can rebuild them.
        pre_mark = kind == 'physical' and target.alive and target.race == 3 and actor.camp != target.camp
        if pre_mark:
            self._add_dragon_marks(combat, target.camp, 2)
        combat.dispel(target, positive=True, actor=actor, skill_id=66000087)
        return pre_mark

    def resolve_hit_context(self, combat, actor, target, skill_id, kind, context):
        if (actor.alive and actor.template_id==self.DRAGON
                and skill_id in (61020041,61120041,61220041)
                and self.artifact_level(actor,66000087) is not None):
            # The attack/defence snapshot precedes dispel, while mitigation
            # such as 119 must already be removed when this hit settles.
            context['dragon_pre_dispel_attack']=actor.physical_attack
            context['dragon_pre_dispel_defence']=target.physical_defence
            context['dragon_marks_before_dispel'] = self._dragon_artifact_dispel(combat, actor, target, kind)
            context['dragon_prepared_target_uid']=target.uid
        return context

    def damage_rule(self, combat, actor, target, kind, multiplier, bonus):
        self.refresh_stats(combat)
        context=getattr(combat,'current_hit_context',{}) or {}
        attack = context.get('dragon_pre_dispel_attack',actor.physical_attack) if kind == 'physical' else actor.magical_attack
        defence = context.get('dragon_pre_dispel_defence',target.physical_defence) if kind == 'physical' else target.magical_defence
        if self._has_passive(target, 207):
            base=target.panel['physical_defence' if kind=='physical' else 'magical_defence']
            defence+=base*(mod_defence_multiplier(combat.negative_count(actor,include_seal=True))-1)
            defence+=self.defence_below_zero.get((target.uid,'physical_defence' if kind=='physical' else 'magical_defence'),0)
        context=getattr(combat,'current_hit_context',{}) or {}
        attack*=float(context.get('attack_multiplier',1))
        defence*=max(0,1-float(context.get('ignore_defence_percent',0))/100)
        defence=max(0,defence-float(context.get('defence_ignore_flat',0)))
        if kind=='magical' and target.race==4 and combat.current_action is not None:
            # Signed basis for Via only. Ordinary damage keeps its floor, flat and RNG.
            combat.current_hit_context['via_primary']=(actor.uid,target.uid,
                int((Decimal(str(attack))-Decimal(str(defence)))*Decimal(str(multiplier))))
        raw = max(1, int((Decimal(str(attack)) - Decimal(str(defence))) * Decimal(str(multiplier))
                         * Decimal(str(context.get('hades_post_defence_multiplier',
                                        context.get('hades_normal_multiplier',1))))))
        raw=max(raw,int(context.get('intellect_damage_floor',0)))
        primary = max(1, int(raw * combat.rng.uniform(.95, 1.05)))
        if context.get('critical'):
            primary=max(1,int(primary*float(context.get('critical_multiplier',2))))
        if kind in ('physical','magical') and not (kind=='physical' and context.get('bypass_physical_immunity')):
            reduction=sum(getattr(hook,'primary_immunity',lambda *_:0)(combat,target,kind) for hook in combat.hooks)
            if kind=='physical':
                reduction+=sum(b.value*b.stacks for b in combat.active_buffs(target) if b.buff_id==119)
                reduction=max(0,reduction-float(context.get('physical_immunity_offset',0)))
            primary = max(1, int(Decimal(primary) * max(Decimal(0), 1 - Decimal(str(reduction)) / 100)))
        if kind == 'physical':
            vulnerability=sum(b.value*b.stacks for b in combat.active_buffs(target) if b.buff_id==59)
            primary=max(1,int(Decimal(primary)*(1+Decimal(str(vulnerability))/100)))
        primary=max(1,int(primary*float(context.get('primary_damage_multiplier',1))))
        return int((primary+bonus)*float(context.get('damage_multiplier',1)))

    def before_damage(self, combat, actor, target, amount, skill_id, kind):
        if (actor.alive and actor.template_id == self.DRAGON
                and skill_id in (61020041, 61120041, 61220041)
                and self.artifact_level(actor, 66000087) is not None
                and not combat.current_damage.get('dragon_artifact_prepared')
                and combat.current_hit_context.get('dragon_prepared_target_uid')!=target.uid):
            combat.current_damage['dragon_marks_before_dispel'] = self._dragon_artifact_dispel(combat, actor, target, kind)
            combat.current_damage['dragon_artifact_prepared'] = True
        # Observed Vik hits: primary magic still sees Ironwall, then seal
        #removes positive effects before the artifact's max-HP component.
        #Keep one composite damage event so shared damage/counters are not
        #duplicated merely because the parts use different buff snapshots.
        if (kind=='max_hp' and skill_id in (61120011,61220011)
                and combat.current_action is not None
                and sum(u.profession==2 for u in combat.alive(actor.camp))>=4):
            done=combat.current_action.setdefault('vik_sealed_targets',set())
            if target.uid not in done:
                # A newly applied seal does not cancel Lust's reaction to this hit.
                combat.current_damage['lust_charm_at_seal'] = max(
                    (b.value for b in combat.active_buffs(target) if b.buff_id in (121,145)),default=None)
                combat.apply_buff(target,Buff(192,actor.uid,skill_id,1))
                done.add(target.uid)
        self.hit_kind[(actor.uid, target.uid)] = kind
        bypass = self._bypass(combat, actor, kind)
        bypass = bypass or (getattr(combat,'current_hit_context',{}) or {}).get('bypass_invulnerability',False)
        if (combat.has_buff(target,68) or combat.has_buff(target,210)) and not combat.has_buff(target,200) and not bypass:
            return 0
        if (actor.race == 0 and kind in ('true', 'true_damage', 'max_hp', 'percentage')
                and self.owners(combat, self.LION, actor.camp)):
            strike=combat.current_damage or {}
            index=strike.get('component_index')
            part=strike.get('components',[])[index] if index is not None else {}
            if part.get('source_buff')==193:
                # Cross-skill193 records retain their own floored amount;
                # Lion's20% does not multiply that mark a second time.
                pass
            elif (skill_id in (66000103,61121011,61221011) and kind=='max_hp') or part.get('source_effect')==60:
                amount += (amount//100)*20
            else:
                amount = int(Decimal(amount) * Decimal('1.2'))
        if actor.race == 3:
            vulnerability = max((b.value for b in combat.active_buffs(target) if b.buff_id == 185), default=0)
            amount = int(Decimal(amount) * (1 + Decimal(str(vulnerability)) / 100))
        marks = min(5, sum(b.stacks for b in combat.active_buffs(target) if b.buff_id == 197))
        amount = int(dragon_magic_damage(amount, target.max_hp, marks, is_magic=kind == 'magical'))
        # Intrinsic66/67 (including Hela) act in damage_rule before the
        # skill's fixed addition; applying them here would reduce it twice.
        if kind not in ('dot', 'transfer'):
            shields = [b for b in combat.active_buffs(target) if b.buff_id == 147]
            strike=combat.current_damage or {}
            index=strike.get('component_index')
            part=strike.get('components',[])[index] if index is not None else {}
            retained=part.get('retained_shield_percent',0) if part.get('source_effect')==60 else 0
            shield_rate=max([retained,*[b.value for b in shields]])
            if shield_rate:
                context=combat.current_hit_context
                offset=float(context.get('physical_immunity_offset',0) or 0)
                effective_rate=max(0,shield_rate-offset)
                if effective_rate<shield_rate:
                    # 166 offsets all demon-target damage reduction. Consume
                    # the part used by147 here so later hooks cannot apply it
                    # a second time.
                    context['physical_immunity_offset']=max(
                        0,offset-(shield_rate-effective_rate)
                    )
                fixed=Decimal(0)
                if kind in ('physical','magical') and part.get('raw_amount',0)>0:
                    fixed=(Decimal(str(part.get('flat_skill_addition',0)))
                           *Decimal(amount)/Decimal(part['raw_amount']))
                    fixed=min(Decimal(amount),max(Decimal(0),fixed))
                # 28 active147 Vik cells and reachable Mother cells preserve
                # the flat addition. Only the absorbed body feeds148 growth.
                blocked = int((Decimal(amount)-fixed)*min(Decimal(1),Decimal(str(effective_rate))/100))
                amount -= blocked
                if fixed>0:
                    # Later hooks can scale the component proportionally,
                    # but must not infer its fixed fraction from pre147 raw.
                    part['fixed_after_shield']=fixed
                    part['amount_after_shield']=amount
                conversions = [b for b in combat.active_buffs(target) if b.buff_id == 148]
                growth_policy=combat.mechanism_options.get('ironwall_growth_policy','physical')
                if growth_policy not in ('physical','all_absorbed'):
                    raise ValueError('Unknown Ironwall growth policy')
                if conversions and (kind=='physical' or growth_policy=='all_absorbed'):
                    key = (target.uid, 'ironwall')
                    old = self.growth[key]
                    new = min(target.panel['physical_attack'] * 3, old + blocked * max(b.value for b in conversions) / 100)
                    self.growth[key] = new
                    self.growth[(target.uid, 'physical_attack')] += new - old
                    self.refresh_stats(combat)
        return amount

    def _plague_rescuers(self, combat, target):
        if target.position >= 3 or target.race != 3:
            return ()
        owners = self.owners(combat, self.PLAGUE, target.camp)
        if target.template_id == self.PLAGUE and not target.alive:
            owners.append(target)
        return [owner for owner in owners if owner.uid not in self.rescued]

    def surviving_component_basis(self, combat, actor, target, skill_id, resolved, remaining):
        # Luohua 15 and Qingxingmeng: collect the HP part for ordered settlement
        # when the direct physical part can trigger an unused Plague rescue.
        if (remaining or skill_id != 61121011 or actor.template_id != self.SON
                or actor.camp == target.camp or len(resolved) != 1
                or resolved[0]['kind'] != 'physical' or self._bypass(combat, actor, 'physical')
                or not any(owner.physical_attack > 0 for owner in self._plague_rescuers(combat, target))):
            return remaining
        strike = combat.current_damage
        parts = strike['components']
        if (len(parts) < 2 or parts[1]['kind'] != 'max_hp'
                or any(p['kind'] != 'true' or p.get('source_buff') != 193 for p in parts[2:])):
            return remaining
        # A combined cap, transfer, or blocked/reversed heal needs separate evidence.
        if (any(b.buff_id in (43, 65, 70, 211) for b in combat.active_buffs(target))
                or self._has_passive(target, 105) or self._has_passive(target, 241)
                or combat.has_buff(actor, 223)
                or any(b.buff_id == 166 and b.source_skill == 66000066
                       for u in combat.alive() for b in combat.active_buffs(u))):
            return remaining
        strike['son_plague_component_order'] = True
        return target.hp

    def settle_component_hp(self, combat, actor, target, skill_id, parts, amount):
        strike = combat.current_damage
        if not strike.get('son_plague_component_order'):
            return None
        observed = strike.get('observed_component_amounts')
        values = observed if observed is not None else [p['resolved_amount'] for p in parts]
        if len(values) != len(parts) or sum(values) != amount:
            return None
        actual = 0
        for index, (part, value) in enumerate(zip(parts, values)):
            actual += combat._apply_damage_hp(actor, target, value, skill_id, consume_mark=index == 0)
            if not target.alive or combat.has_buff(target, 68):
                del parts[index + 1:]
                if observed is not None:
                    del observed[index + 1:]
                return actual, None if target.alive else dict(part, resolved_amount=value)
        return actual, None

    def before_settlement(self, combat, actor, target, amount, skill_id, kind):
        strike = combat.current_damage or {}
        if strike.get('son_plague_component_order'):
            # With a rescue between the parts, the report deducts Judgment
            # before maximum-HP damage. Ordinary Son hits keep their order.
            parts = strike['components']
            parts[1:] = sorted(parts[1:], key=lambda part: part.get('source_buff') != 193)
        if (skill_id == 61121011 and kind == 'composite' and actor.template_id == self.SON
                and actor.camp != target.camp):
            parts = strike['components']
            if (parts[0]['kind'] == 'physical' and parts[0]['resolved_amount'] > 0
                    and any(p['kind'] == 'max_hp' and p['resolved_amount'] > 0 for p in parts[1:])
                    and all(p['kind'] == 'max_hp' or p.get('source_buff') == 193 for p in parts[1:])):
                strike['son_fallen_before_primary'] = self._fallen_heal_multiplier(combat, actor)
        # One transfer per strike, including physical+percentage composite hits.
        # Sharing is not shared recursively and the original attacker is retained.
        if kind != 'transfer':
            for buff in combat.active_buffs(target):
                owner = combat.units.get(buff.source_uid)
                if buff.source_skill==66000022 and kind=='dot':
                    continue
                if (buff.buff_id == 70 and owner and owner.alive
                        and (owner is not target or buff.source_skill == 66000022)
                        and not (amount == 0 and buff.source_skill == 61105901)):
                    # Old Ed can be among his artifact's two protected mages.
                    # His own hit still has a separate share packet; Angel's
                    # self rescue retains its non-sharing behavior.
                    damage=combat.current_damage
                    if damage and not damage.get('lifesteal_components'):
                        # Routing part of this hit to70's owner does not
                        # discard that part of the attacker's life steal.
                        damage['lifesteal_components']=[dict(p,pre_transfer_lifesteal=True)
                            for p in damage['components']]
                    share = int(amount * min(1, buff.value / 100))
                    amount -= share
                    # Preserve the original kind for the artifact recipient's
                    # physical/magic immunity without recursively sharing.
                    parts=([dict(kind='transfer',amount=share,source_damage_kind=kind)]
                           if buff.source_skill==66000022 and kind in ('physical','magical') else None)
                    combat.damage(actor, owner, share, buff.source_skill, 'transfer',components=parts)
                    break
        return amount

    def on_lethal(self, combat, actor, target, skill_id):
        if target.alive:return
        context = getattr(combat, 'current_damage', None)
        kinds = {c['kind'] for c in context['components']} if context else set()
        kind = 'physical' if 'physical' in kinds else self.hit_kind.get((actor.uid, target.uid), 'physical')
        if self._bypass(combat, actor, kind):
            return
        for owner in self._plague_rescuers(combat, target):
            self.rescued.add(owner.uid)
            count = sum(u.race == 3 and u is not target for u in combat.alive(target.camp))
            raw = int(Decimal(owner.physical_attack) * (Decimal('1.2') + Decimal('.6') * count))
            self._heal(combat, owner, target, raw, 61120501, allow_rescue=True)
            combat.emit('rescue', owner, target, 61120501, hp_after=target.hp)
            if target.alive:
                if context is not None:
                    context['plague_rescued'] = True
                return
        for owner in self.owners(combat, self.LION, target.camp):
            if (owner.uid in self.rescued or owner is target or target.race != 0
                    or owner.position // 3 != target.position // 3):
                continue
            self.rescued.add(owner.uid)
            self._heal(combat, owner, target, int(Decimal(owner.magical_defence) * Decimal('6.5')),
                       61120601, allow_rescue=True)
            if target.alive:
                combat.apply_buff(target, Buff(68, owner.uid, 61120601, 1))
            combat.emit('rescue', owner, target, 61120601, hp_after=target.hp)
            if target.alive:
                return
        angels = self.owners(combat, self.ANGEL, target.camp)
        # Seventeen single-Angel report windows show the holder also survives
        # its own lethal hit. At this boundary it is absent from alive().
        if target.template_id == self.ANGEL:
            angels.append(target)
        #117's one-HP return is consumed before Angel's single protection.
        # Retain all other rescue ordering, including battles without Angel.
        if angels:
            for hook in combat.hooks:
                rescue=getattr(hook,'try_dwarf_rescue',None)
                if rescue and rescue(combat,actor,target,skill_id):return
        # Zhuo Zhuo Qi Hua 11/16 g3: elemental and Mod fire/poison ticks
        # kill before a later direct hit on Nono consumes Divine Blessing.
        if kinds == {'dot'} and skill_id in (61003601, 61103601, 61203601, 66000095):
            return
        for owner in angels:
            # 神恩祝福整场仅救一次；保护只属于被救者当前回合。
            # rank3/g1 a32救圣堂，a39莱恩阵亡是同回合不同角色，不是保护到期。
            if owner.uid in self.rescued:
                continue
            self.rescued.add(owner.uid)
            plan = parse_angel_rescue(self.catalog.skill(61105901).passives, skill_level=self.skill_level)
            target.hp = 1
            for bid, duration, value in [(68, plan.invulnerable_rounds, 0),
                                         (69, plan.recovery_rounds, 100 * plan.recovery_fraction),
                                         (70, plan.shared_damage_rounds, 100 * plan.shared_damage_fraction)]:
                combat.apply_buff(target, Buff(bid, owner.uid, 61105901, duration, value))
            combat.emit('rescue', owner, target, 61105901, hp_after=target.hp)
            return

    def _heal(self, combat, actor, target, amount, skill_id, **kwargs):
        self._passive_heal += 1
        try:
            return combat.heal(actor, target, int(amount), skill_id, **kwargs)
        finally:
            self._passive_heal -= 1

    def _lifesteal(self, combat, actor, raw, skill_id, overflow_fraction=0, *, demon_only=False,
                   demon_boost=False, fallen_multiplier=None):
        raw = int(raw)
        missing = max(0, actor.max_hp - actor.hp)
        # Dragon's innate heal and Adai's karma split at the unmodified HP gap.
        # Fallen then suppresses the self heal and the overflow separately.
        split_before_suppression = ((demon_only or demon_boost) and raw > missing
                                    and self._fallen_heal_multiplier(combat, actor) < 1)
        start = len(combat.events)
        previous = self._next_lifesteal_fallen_multiplier
        self._next_lifesteal_fallen_multiplier = fallen_multiplier
        try:
            self._heal(combat, actor, actor, missing if split_before_suppression else raw, skill_id)
        finally:
            self._next_lifesteal_fallen_multiplier = previous
        event = next((e for e in combat.events[start:] if e.kind == 'heal' and e.target_uid == actor.uid), None)
        overflow = raw - missing if split_before_suppression else (event.payload['overflow'] if event else 0)
        if not overflow or not overflow_fraction:
            return
        recipients = [u for u in combat.alive(actor.camp) if not demon_only or u.race == 3]
        if split_before_suppression:
            recipients = [u for u in recipients if u is not actor]
        if skill_id == 66000053:
            # Real134 overflow heals an injured ally even when a full ally
            # has less absolute HP (zaodianshui15/g1/actions6,7). Preserve
            # the existing order among injured units and all-full zero rows.
            injured = [u for u in recipients if u.hp < u.max_hp]
            if injured:
                recipients = injured
        if recipients:
            key = (lambda u: (u.hp / u.max_hp, u.hp, u.position)) if demon_only or demon_boost else (lambda u: (u.hp, u.position))
            target = min(recipients, key=key)
            # Adai's demon rate is already in raw. Real injured-owner
            # checkpoints require 35% self healing, followed by its remainder;
            # multiplying the remainder by 1.2 a second time is not observed.
            # Same order as sim/battle.py::intrinsic_overflow_heal;
            # independently reproduced by plane6e6095/game1/actions15,16.
            transferred = (int(Decimal(overflow // 100) * Decimal(str(overflow_fraction)) * 100)
                           if demon_only else int(overflow * overflow_fraction))
            if demon_boost:
                # Karma caps the forwarded share at 20% of its recipient's max HP.
                transferred = min(transferred, target.max_hp // 5)
            # After a split, both portions still need their healing modifiers.
            # Luoshen04/g1/action11 retains888/6344, rather than suppressing
            # those amounts a second time to35/253.
            self._heal(combat, actor, target, transferred, skill_id, modifiers_applied=not split_before_suppression)

    @staticmethod
    def lifesteal_components(event, *, use_requested=False):
        """Separate reported damage kinds; same-kind appendages share one heal."""
        original = event.payload.get('lifesteal_components')
        remaining = sum(p['resolved_amount'] for p in original) if original else event.payload[
            'requested' if use_requested else 'actual_hp_loss']
        parts = original or event.payload.get('settled_components') or event.payload.get('components') or [dict(
            kind=event.payload['damage_kind'], resolved_amount=remaining)]
        groups = []
        for part in parts:
            amount = min(remaining, part['resolved_amount'])
            remaining -= amount
            if not amount:
                continue
            if groups and groups[-1][0] == part['kind']:
                groups[-1] = (part['kind'], groups[-1][1] + amount)
            else:
                groups.append((part['kind'], amount))
        return groups

    def before_hit(self, combat, actor, skill, hit):
        if hit > 1 and any(code == 6 for code, _ in skill.enemy_effects):
            self.before_action(combat, actor, skill)

    def before_action(self, combat, actor, skill=None):
        # 人族伙伴的协同(17)与先制(15)也是行动，不只包括普通主动(1)。
        if skill is not None and skill.activation_type not in (1,15,17):return
        if actor.race == 0:
            for lulu in self.owners(combat, self.LULU, actor.camp):
                increment = lulu.panel['magical_attack'] * scaled(('4', '.04'), self.skill_level) / 100
                for stat in ('physical_attack', 'magical_attack'):
                    key = (actor.uid, 'lulu_' + stat)
                    old = self.growth[key]
                    new = min(actor.panel[stat], old + increment)
                    self.growth[key] = new
                    self.growth[(actor.uid, stat)] += new - old
        self.refresh_stats(combat)

    def on_event(self, combat, event):
        actor = combat.units.get(event.actor_uid)
        target = combat.units.get(event.target_uid)
        if (event.kind == 'health_cost' and actor and target is actor
                and actor.template_id == 80600049 and event.skill_id == 61009201
                and event.payload['hp_before'] == actor.max_hp
                and combat.current_action is not None):
            # Titan Strike's self-cost and its self-lifesteals share the
            # full-health classification for Son conversion. HP settlement
            # still restores the actual missing HP after the self-cost.
            combat.current_action['son_full_titan_heal_uid'] = actor.uid
        elif event.kind == 'action_start' and actor:
            # Opening artifacts are not the human partner's ordinary action.
            if self.catalog.skill(event.skill_id).activation_type == 1:
                self.has_acted.add(actor.uid)
                if actor.profession == 2 and event.skill_id != 61000001:
                    for shadow in self.owners(combat, self.SHADOW, actor.camp):
                        if shadow is not actor and shadow.uid not in self.has_acted:
                            key = (shadow.uid, 'shadow_growth')
                            old = self.growth[key]
                            new = min(shadow.panel['magical_attack'] * 3,
                                      old + shadow.panel['magical_attack'] * .6)
                            self.growth[key] = new
                            self.growth[(shadow.uid, 'magical_attack')] += new - old
                if not (getattr(combat,'current_action',None) or {}).get('before_action_dispatched'):
                    self.before_action(combat, actor)
        elif event.kind == 'action_end' and actor:
            origin = event.payload.get('origin')
            origin_kind = origin.get('kind') if isinstance(origin, dict) else origin
            activation = self.catalog.skill(event.skill_id).activation_type
            counts_as_action = activation == 1 and origin_kind not in ('opening', 'round_end')
            if actor.race == 3 and counts_as_action:
                for dragon in self.owners(combat, self.DRAGON, actor.camp):
                    if dragon is not actor and self.artifact_level(dragon, 66000087) is not None:
                        self.dragon_actions[dragon.uid] += 1
            self.refresh_stats(combat)
        elif event.kind == 'damage' and actor and target:
            kind = event.payload['damage_kind']
            dealt = event.payload['actual_hp_loss']
            components = event.payload.get('components', [])
            kinds = {c['kind'] for c in components} if components else {kind}
            # Dragon uses full settled damage, including lethal overflow,
            # as in the main-stage engine and 14 discriminating plane actions.
            # Other heroes retain their separate physical-HP-loss candidate.
            healing_basis = event.payload['requested'] if actor.template_id == self.DRAGON else dealt
            physical_dealt = min(healing_basis, sum(c['resolved_amount'] for c in components if c['kind'] == 'physical')) if components else (healing_basis if kind == 'physical' else 0)
            if event.payload.get('lifesteal_components'):
                physical_dealt = sum(amount for damage_kind, amount in self.lifesteal_components(event)
                                     if damage_kind == 'physical')
            hit = event.payload.get('context') or {}
            marks_prepared = (hit.get('dragon_prepared_target_uid') == target.uid
                              and hit.get('dragon_marks_before_dispel')
                              and actor.template_id == self.DRAGON
                              and event.skill_id in (61020041, 61120041, 61220041))
            marks_prepared = marks_prepared or (combat.current_damage or {}).get('dragon_marks_before_dispel')
            if not marks_prepared and target.race == 3 and kinds & {'physical', 'magical', 'max_hp'}:
                marks = 2
                primary_kind = {61120011: 'magical', 61121051: 'magical', 66000103: 'physical'}.get(event.skill_id)
                if (primary_kind and actor.camp != target.camp
                        and tuple(p['kind'] for p in components) == (primary_kind, 'max_hp')
                        and not event.payload.get('settled_components')
                        and not event.payload.get('lifesteal_components')
                        and event.payload['hp_before'] > dealt
                        and event.payload['before_settlement_amount'] == event.payload['requested'] == dealt
                        and all(p['resolved_amount'] > 0 for p in components)):
                    # Both direct components deducted HP. Preserve one damage event.
                    marks = 4
                self._add_dragon_marks(combat, target.camp, marks)
            if actor.alive:
                generic = sum(b.value for b in combat.active_buffs(actor) if b.buff_id == 16)
                generic += sum(b.value*(1.5 if actor.race==1 else 1) for b in combat.active_buffs(actor) if b.buff_id==103)
                generic += sum(self._passive_values(actor, 100))
                if generic:
                    before_primary = (combat.current_damage or {}).get('son_fallen_before_primary')
                    if (before_primary is not None and not event.payload.get('lifesteal_components')
                            and not event.payload.get('settled_components')
                            and sum(p['resolved_amount'] for p in components) == event.payload['requested']):
                        # Luohua 15/g3: the physical part sees the pre-hit aura,
                        # the HP extra sees the settled count, and Charlie's
                        # Judgment mark is not part of Son's lifesteal.
                        bases = [(p['resolved_amount'], before_primary if p['kind'] == 'physical' else None)
                                 for p in components if p.get('source_buff') != 193]
                    elif actor.template_id == self.SON:
                        bases = [(amount, None) for _, amount in self.lifesteal_components(event, use_requested=True)]
                    else:
                        bases = [(physical_dealt, None)]
                    for basis, fallen_multiplier in bases:
                        if basis:
                            self._lifesteal(combat, actor, basis * generic / 100, event.skill_id,
                                            fallen_multiplier=fallen_multiplier)
            if physical_dealt and actor.alive:
                dealt = physical_dealt
                if self.owners(combat, self.PLAGUE, actor.camp) and actor.race == 3:
                    # Retained non-Dragon lethal hits heal20% of the complete
                    # settled physical damage, including its lethal overflow.
                    plague_basis=sum(amount for damage_kind,amount in self.lifesteal_components(event,use_requested=True)
                                     if damage_kind=='physical')
                    self._lifesteal(combat, actor, plague_basis * 20 // 100, 61120501)
                if actor.template_id == self.DRAGON and event.skill_id in (61000001, 61020041, 61120041, 61220041):
                    self._lifesteal(combat, actor, dealt * scaled(('9', '.14'), self.skill_level) / 100,
                                    61120041, scaled(('25', '.3'), self.skill_level) / 100, demon_only=True)
                if actor.race == 3 and combat.has_buff(actor, 216) and self.owners(combat, self.ADAI, actor.camp):
                    # The demon recipient (including the attacker itself)
                    # receives 15+20 percentage points, not 15% times 1.2.
                    # Keep exact integer percentage arithmetic before healing.
                    # Like Plague, karma uses settled damage even when it exceeds
                    # the victim's remaining HP. Keep Dragon's existing basis.
                    basis = dealt if actor.template_id == self.DRAGON else sum(
                        amount for kind, amount in self.lifesteal_components(event, use_requested=True)
                        if kind == 'physical')
                    self._lifesteal(combat, actor, basis * 35 // 100, 61121021, 1, demon_boost=True)
        elif event.kind == 'heal' and target and target.race == 0:
            # Conversion alters attack only; it cannot recurse into another heal.
            for template, coefficient in ((self.SON, .2), (self.SOLOMON, .15)):
                for owner in self.owners(combat, template, target.camp):
                    key = (owner.uid, 'heal_conversion')
                    old = self.growth[key]
                    if template == self.SON:
                        # Ziyu51939555:50*20%+50*40%=30 attack.
                        action = combat.current_action or {}
                        full_titan_lifesteal = (
                            actor is target and actor is not None
                            and actor.uid == action.get('actor_uid')
                            and action.get('skill_id') == 61009201
                            and actor.uid == action.get('son_full_titan_heal_uid')
                            and event.skill_id in (61009201, 66000053)
                            and self._passive_heal > 0)
                        if full_titan_lifesteal:
                            amount = event.payload['amount'] + event.payload['overflow']
                            event.payload['son_conversion_basis'] = dict(
                                effective=0, overflow=amount,
                                basis='full_hp_before_titan_self_cost')
                            gain = amount * .4
                        else:
                            gain=event.payload['amount']*.2+event.payload['overflow']*.4
                    else:
                        gain=(event.payload['amount']+event.payload['overflow']*1.2)*coefficient
                    new = min(owner.panel['physical_attack'] * 3, old + gain)
                    self.growth[key] = new
                    self.growth[(owner.uid, 'physical_attack')] += new - old
            self.refresh_stats(combat)
        elif event.kind in ('buff', 'dispel', 'death'):
            if not getattr(combat,'defer_copy_stats',False):self.refresh_stats(combat)


    def heal_round_end(self, combat):
        """Recover69 after late attacks, before periodic damage and decay."""
        for unit in list(combat.alive()):
            for buff in list(combat.active_buffs(unit)):
                if buff.buff_id == 69:
                    owner = combat.units.get(buff.source_uid)
                    if owner:
                        self._heal(combat, owner, unit, unit.max_hp * int(buff.value) // 100, buff.source_skill)

    def dragon_can_chase_again(self, combat, dragon):
        return self._has_passive(dragon, 182) and sum(u.race == 3 for u in combat.alive(dragon.camp)) >= 4

    def end_round(self, combat):
        """Queue late attacks; the caller resolves recovery after those actions."""
        # 61020041：回合结束追击一次；觉醒后存活恶魔不少于4名再加一次。
        # 每次释放仍按 effect6 结算两段，不由队友行动提前消费。
        # Yue Ming 11/18 g2: the faster defender completes its chases first.
        earlier_dragon = False
        for dragon in sorted(self.owners(combat, self.DRAGON), key=lambda unit: -unit.speed):
            if any(combat.has_buff(dragon, bid) for bid in (5, 6, 21, 220)):
                combat.emit('skipped_followup', dragon, None, 61120041,
                            reason='controlled', origin={'kind': 'round_end', 'mechanism': 'dragon_chase'})
                continue
            sid = self.catalog.hero_for(dragon).active_ids[0]
            origin = {'kind': 'round_end', 'mechanism': 'dragon_chase'}
            combat.queue_followup(dragon.uid, sid, skill_level=self.skill_level, origin=origin)
            if earlier_dragon:
                # Yue Ming g2: the first Dragon can remove demons before this one begins.
                combat.followups[-1]['check_extra_dragon_chase'] = True
            elif self.dragon_can_chase_again(combat, dragon):
                combat.queue_followup(dragon.uid, sid, skill_level=self.skill_level,
                                      origin=origin)
            earlier_dragon = True
        self.round_index += 1
