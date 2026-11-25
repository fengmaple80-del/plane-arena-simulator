"""Modern SSS reactive rules from the versioned plane tables.

This controller composes with CommonMechanics; it never calls Common's event
callbacks. Numerical order and ambiguous source parameters are exposed, not
silently replaced by a nominal artifact level or an observed battle outcome.
"""
from collections import defaultdict
from decimal import Decimal

from combat import Buff, UnsupportedMechanism, scaled, PRIMARY_DAMAGE


class ModernMechanics:
    # Strengthening precedes the shared suppression/integer settlement.
    healing_priority = -1
    HUMAN, BEAST, DEMON, UNDEAD = 0, 1, 3, 4
    CONTROL_BUFFS = {5, 6, 20, 21, 36, 122, 217, 220}
    supported_passives = {
        96, 97, 132, 133, 141, 154, 157, 158, 159, 160, 161, 162, 163, 164, 165, 166, 167, 168, 169,
        170, 172, 173, 174, 175, 176, 177, 178, 186, 187, 188, 189,
        190, 192, 194, 195, 196, 197, 199, 200, 202, 203, 204, 224, 225,
        205, 208, 209, 213, 214, 216, 218, 219, 220, 222, 226, 228,
        230, 232, 233, 234, 235, 236, 237,
    }
    # Resolved by native description formatter and69 actual DOTticks;
    # see source-gap-investigation.json. Artifact level remains explicit.
    source_gaps = {}
    supported_buffs = {151,164,165,166,178,182,188,189,190,193,195,196,198,199,201,213,214,215,218,219}

    def __init__(self, catalog, common, parameters=None):
        self.catalog, self.common = catalog, common
        self.parameters = dict(parameters or {})
        self.supported_passives = set(type(self).supported_passives)
        for code, name in self.source_gaps.items():
            if name in self.parameters:
                self.supported_passives.add(code)
        self.count = defaultdict(int)
        self.value = defaultdict(float)
        self.initial_races = {}
        self.initial_mages = {}
        self.records = {}
        self.acted = set()
        self.active_actions = {}
        self.opening_order = {}
        self.action_turns = set()
        self.last_target = {}
        self.last_attacker = {}
        self.round = 1
        self._combat = None
        self._aura_dispel = None
        self._overflow_redirect = False
        self.claw_links = {}

    def _records(self, unit):
        if unit.uid not in self.records:
            hero = self.catalog.hero_for(unit)
            result = {}
            # Operation-2 awakenings live in skill_ids with activation type 14/5.
            # Supplemental copies are documentation, not second passive owners.
            for sid in (*hero.skill_ids, *hero.artifact_ids):
                for code, values in self.catalog.skill(sid).passives:
                    result[code] = (sid, values, sid in hero.artifact_ids)
            self.records[unit.uid] = result
        return self.records[unit.uid]

    def has(self, unit, code):
        return code in self._records(unit)

    def datum(self, unit, code, *, required=True):
        record = self._records(unit).get(code)
        if not record:
            return None
        sid, values, artifact = record
        level = self.common.artifact_level(unit, sid) if artifact else self.common.skill_level
        if level is None:
            if required:
                raise UnsupportedMechanism(f'Artifact {sid} level required for passive {code} on {unit.uid}')
            return None
        return sid, scaled(values, level)

    def owners(self, battle, code, camp=None):
        return [u for u in battle.alive(camp) if self.has(u, code)]

    def race_count(self, battle, unit, race, *, initial=False, exclude=False):
        if initial:
            return self.initial_races[(unit.camp, race)] - int(exclude and unit.race == race)
        return sum(u.race == race and (not exclude or u is not unit) for u in battle.alive(unit.camp))

    def initialize(self, battle):
        if self._combat is battle:
            return
        if self._combat is not None:
            raise ValueError('ModernMechanics belongs to one combat')
        self._combat = battle
        for camp in {u.camp for u in battle.units.values()}:
            self.initial_mages[camp] = sum(u.camp == camp and u.profession == 2 for u in battle.units.values())
            for race in (0, 1, 2, 3, 4):
                self.initial_races[(camp, race)] = sum(u.camp == camp and u.race == race for u in battle.units.values())
        self.refresh_stats(battle)

    def unresolved(self, battle):
        result = []
        for unit in battle.units.values():
            for code, (sid, values, artifact) in self._records(unit).items():
                if code not in self.supported_passives and code not in self.source_gaps:
                    continue
                if artifact and self.common.artifact_level(unit, sid) is None:
                    result.append(f'{unit.uid}: artifact {sid} level required for passive {code}')
                if code in self.source_gaps and self.source_gaps[code] not in self.parameters:
                    result.append(f'{unit.uid}: passive {code} requires {self.source_gaps[code]} from source evidence')
        return tuple(result)

    def preflight(self, battle, actor, skill):
        missing = self.unresolved(battle)
        if missing:
            raise UnsupportedMechanism('; '.join(missing))
        if (skill.activation_type==1 and actor.race==self.UNDEAD and battle.has_buff(actor,218)
                and sum(u.profession==2 and u.camp!=actor.camp for u in battle.alive())>=4
                and not skill.enemy_effects):
            if battle.mechanism_options.get('blessing218_healer_policy')!='no_extra_target':
                raise UnsupportedMechanism('Blessing218 healer extra attack requires an explicit attack skill/coefficient')

    def grow(self, battle, unit, key, amount, cap, stat='physical_attack'):
        address = (unit.uid, key, stat)
        old = self.value[address]
        new = min(cap, max(0, old + amount))
        self.value[address] = new
        self.common.growth[(unit.uid, stat)] += new-old
        self.common.refresh_stats(battle)

    def queue(self, battle, owner, sid, code, *, primary=None, immediate=False, parent_skill=None):
        artifact = sid in self.catalog.hero_for(owner).artifact_ids
        level = self.common.artifact_level(owner, sid) if artifact else self.common.skill_level
        if level is None:
            raise UnsupportedMechanism(f'Artifact {sid} level required for followup')
        origin={'mechanism':code,'source_uid':owner.uid,'kind':'reaction'}
        if parent_skill is not None:origin['parent_skill']=parent_skill
        if immediate:
            if owner.alive:
                battle.emit('candidate_timing',owner,None,sid,mechanism=code,
                            ordering='immediate reactive action before next primary hit')
                battle.execute(owner.uid,sid,skill_level=level,primary_uid=primary,origin=origin)
        else:
            battle.queue_followup(owner.uid, sid, skill_level=level, primary_uid=primary,origin=origin)

    @staticmethod
    def ordinary(event):
        origin = event.payload.get('origin')
        return not origin or origin == 'ordinary' or isinstance(origin, dict) and origin.get('kind') == 'ordinary'

    def active_id(self, owner):
        # The 36 retained Eason attacks under217 use normal attacks; other
        # confused heroes retain their separately verified active skills.
        if self.has(owner,226) and self._combat is not None and self._combat.has_buff(owner,217):
            return 61000001
        if self._combat is not None:
            sid=self._combat.active_id_override(owner)
            if sid is not None:return sid
        return self.catalog.hero_for(owner).active_ids[0]

    def lulu_threshold(self,owner):
        if 'lulu_hp_threshold_percent' in self.parameters:
            return self.parameters['lulu_hp_threshold_percent']
        level=self.common.artifact_level(owner,66000077)
        if level is None:raise UnsupportedMechanism('Lulu artifact level required')
        values=self.catalog.tables['Skill'][66000077]['最高'].split('|')
        return scaled(values,level)

    def refresh_stats(self, battle):
        self.common.refresh_stats(battle)

    def stat_inputs(self,battle,unit):
        # Non-buff state read by stat_bonuses; part of CommonMechanics' refresh key.
        return tuple(self.count.get((unit.uid,'215',b.source_uid),0) for b in unit.buffs if b.buff_id==215)

    def via_rank_physical_offset(self,unit):
        return self.value.get((unit.uid,'king_round','physical_attack'),0)

    def stat_bonuses(self,battle,unit):
        penalty=max((owner.panel['speed']*self.datum(owner,220)[1]/100
                     for owner in self.owners(battle,220)
                     if owner.camp!=unit.camp and unit.race!=self.UNDEAD
                     and self.race_count(battle,owner,self.UNDEAD)>=5),default=0)
        bonuses=defaultdict(float, speed=-penalty)
        for buff in battle.active_buffs(unit):
            if buff.buff_id not in (199,201,214,215,218):continue
            source=battle.units.get(buff.source_uid)
            if source is None:
                continue
            if buff.buff_id==199:
                # Six first-burst report prefixes support +30 percentage
                # points per mage, not multiplying the base coefficient.
                # Opening bonus retains its source roster, including the
                # caster. Deaths or stealing it into another camp do not
                # retroactively change that roster.
                mages=self.initial_mages.get(source.camp)
                if mages is None:  # Pre-existing buffs during initial refresh.
                    mages=sum(u.profession==2 and u.camp==source.camp for u in battle.units.values())
                    self.initial_mages[source.camp]=mages
                bonuses['magical_attack']+=(source.panel['magical_attack']//100)*(buff.value+30*mages)
            elif buff.buff_id==214:
                attack_bonus=source.panel['magical_attack']*buff.value/100
                bonuses['physical_attack']+=attack_bonus
                bonuses['magical_attack']+=attack_bonus
            elif buff.buff_id==201:
                bonuses['physical_attack']+=source.panel['physical_attack']*buff.value/100
            elif buff.buff_id==215:
                growth=min(3,1+.5*self.count[(unit.uid,'215',buff.source_uid)])
                # Horn's 300% cap is relative to the caster's defence, not its opening bonus.
                bonuses['physical_defence']+=source.panel['physical_defence']*min(300,buff.value*growth)/100
            elif buff.buff_id==218:
                factor=buff.value/100*(2 if unit.race==self.UNDEAD else 1)
                for stat in ('physical_attack','magical_attack','physical_defence','magical_defence','speed'):
                    bonuses[stat]+=source.panel[stat]*factor
                if buff.source_skill==66000106 and source.race==self.UNDEAD and self.has(unit,178):
                    # The dragon amplifies the crown's speed share, not its attack or defence.
                    bonuses['speed']+=source.panel['speed']*factor*self.datum(unit,178)[1]/100
        for victim in battle.alive():
            for buff in battle.active_buffs(victim):
                if buff.buff_id not in (164,165) or buff.source_skill!=66000066:
                    continue
                if self.claw_links.get((buff.source_uid,victim.uid))==unit.uid:
                    for stat,amount in buff.stat_modifiers.items():
                        bonuses[stat]-=amount
        return dict(bonuses)

    def _solomon_preempt(self, battle, actor, skill):
        action=getattr(battle,'current_action',None) or {}
        origin=action.get('origin')
        ordinary=not origin or origin=='ordinary' or isinstance(origin,dict) and origin.get('kind')=='ordinary'
        # 恶堕给予的额外主动仍属于主动回合；回合末龙骑追击不触发先制。
        extra=(actor.template_id==self.common.DRAGON and isinstance(origin,dict)
               and origin.get('kind')=='reaction' and origin.get('mechanism')==199)
        if (ordinary or extra) and skill is not None and any(code in PRIMARY_DAMAGE for code, _ in skill.enemy_effects):
            enemies=[u for u in battle.alive() if u.camp!=actor.camp]
            counterpart=battle.ordinary_primary(actor,enemies)
            if counterpart is not None and counterpart.race==self.HUMAN:
                for owner in list(battle.alive(counterpart.camp)):
                    if owner.alive and actor.alive and 66000091 in self.catalog.hero_for(owner).artifact_ids:
                        level=self.common.artifact_level(owner,66000091)
                        if level is None:
                            raise UnsupportedMechanism('Solomon preempt requires artifact level')
                        battle.execute(owner.uid,66000091,skill_level=level,
                            primary_uid=actor.uid,origin={'kind':'preempt','mechanism':61,
                                                        'source_uid':owner.uid})

    def before_hit(self, battle, actor, skill, hit):
        if hit>1 and skill.skill_id==61120201 and actor.race==self.UNDEAD:
            origin=(battle.current_action or {}).get('origin')
            if (not origin or origin=='ordinary' or isinstance(origin,dict) and origin.get('kind')=='ordinary') and any(u.camp!=actor.camp for u in battle.alive()):
                self._undead_assist(battle,actor,immediate=True)
        # 原报将龙骑双段分成两格，两格前分别有圣堂先制（rank3/g1 a19/21）。
        if actor.template_id==self.common.DRAGON and skill.skill_id in (61020041,61120041,61220041):
            self._solomon_preempt(battle,actor,skill)
        elif hit>1 and skill.skill_id in (61117001,61120201,61119001,61120051):
            # The opening preempt already ran in before_action. Check the live counterpart again.
            self._solomon_preempt(battle,actor,skill)

    def before_action(self, battle, actor, skill=None, level=None):
        action=getattr(battle,'current_action',None) or {}
        if skill and skill.skill_id==66000083:
            action['via_opening_stats']={s:getattr(actor,s) for s in ('physical_attack','magical_attack','physical_defence','magical_defence','speed')}
            # The crown protects Via, but its lent magic defence is not lent again.
            borrowed=sum(battle.units[v.source_uid].panel['magical_defence']*v.value*2/100
                         for v in battle.active_buffs(actor) if v.buff_id==218 and v.source_skill==66000106)
            action['via_opening_stats']['magical_defence']-=int(borrowed)
        origin=action.get('origin')
        ordinary=not origin or origin=='ordinary' or isinstance(origin,dict) and origin.get('kind')=='ordinary'
        solomon_on_action=(actor.template_id!=self.common.DRAGON or skill is None or skill.skill_id not in (61020041,61120041,61220041))
        undead_opening=ordinary and actor.race==self.UNDEAD
        if solomon_on_action and not undead_opening:
            self._solomon_preempt(battle,actor,skill)
        if skill and skill.skill_id in (66000035,67101032,67400035,66000075) and self.has(actor,96) and self.has(actor,97):
            key=(actor.uid,'defence_conversion')
            if not self.count[key]:
                self.count[key]=1
                amount=int(actor.physical_defence*self.datum(actor,96)[1]/100+actor.magical_defence*self.datum(actor,97)[1]/100)
                actor.max_hp+=amount
                actor.hp+=amount
                actor.panel['max_hp']=actor.max_hp
                for stat in ('physical_defence','magical_defence'):
                    actor.panel[stat]=0
                    setattr(actor,stat,0)
                    self.common.applied[(actor.uid,stat)]=0
                battle.emit('defence_conversion',actor,actor,skill.skill_id,hp_gain=amount)
        if ordinary:
            self.action_turns.add(actor.uid)
        if undead_opening:
            for owner in self.owners(battle,226,actor.camp):
                key=(owner.uid,'first_undead',self.round)
                if self.race_count(battle,owner,self.UNDEAD)>=5 and not self.count[key]:
                    self.count[key]=1
                    battle.execute(owner.uid,self.active_id(owner),skill_level=self.common.skill_level,
                                   origin={'mechanism':226,'kind':'preempt','source_uid':owner.uid})
            if solomon_on_action and actor.alive:
                self._solomon_preempt(battle,actor,skill)
        # King first-round attack increase is conditional on actual target state.
        if self.round == 1 and self.has(actor, 175):
            enemies = [u for u in battle.alive() if u.camp != actor.camp]
            primary = battle.units.get(action.get('primary_uid')) or battle.ordinary_primary(actor, enemies)
            if primary is not None and primary.uid not in self.acted:
                rate = self.datum(actor, 175)[1]
                rate += self.race_count(battle, actor, self.UNDEAD, exclude=True)//2 * self.datum(actor, 176)[1]
                basis=actor.physical_attack-self.value[(actor.uid,'king_round','physical_attack')]
                self.grow(battle, actor, 'king_round', basis*rate/100,basis*rate/100)

    def hit_count(self, battle, actor, skill, hits):
        if skill.skill_id not in self.catalog.hero_for(actor).active_ids:
            return hits
        if self.has(actor, 202):
            threshold = self.datum(actor, 202)[1]/100
            count = sum(u.camp != actor.camp and (not u.alive or u.hp/u.max_hp < threshold)
                        for u in battle.units.values())
            hits += min(4, count)
        if self.has(actor, 162):
            hits += self.race_count(battle, actor, self.BEAST, exclude=True)
        return hits

    def hit_count_bonus(self,battle,actor,skill):
        return self.hit_count(battle,actor,skill,0)

    def target_count(self, battle, actor, spec, count, ally):
        if not ally or actor.race != self.HUMAN:
            return count
        action = self.active_actions.get(actor.uid)
        if action is None:
            return count
        skill = self.catalog.skill(action.skill_id)
        if (skill.activation_type == 1 and any(c in (2, 69) for c, _ in skill.ally_effects)
                and self.owners(battle, 189, actor.camp)):
            return count*2
        return count

    def target_count_bonus(self,battle,actor,skill,ally):
        if ally and actor.race==self.HUMAN and skill.activation_type==1:
            if any(c in (2,69) for c,_ in skill.ally_effects) and self.owners(battle,189,actor.camp):
                return skill.ally_target[1]
        return 0

    def after_select_targets(self,battle,spec,actor,selected,candidates,ally):
        action=getattr(battle,'current_action',None) or {}
        sid=action.get('skill_id')
        if sid is None:
            return selected
        if any(battle.has_buff(actor,b) for b in (36,217)):
            # 混乱 keeps an offensive action inside the caster's own camp
            # (D5-147); no report action damages both sides in one action.
            return selected
        skill=self.catalog.skill(sid)
        if not ally and skill.activation_type==1 and actor.race==self.UNDEAD and battle.has_buff(actor,218):
            if not skill.enemy_effects and battle.mechanism_options.get('blessing218_healer_policy')=='no_extra_target':
                return selected
            mages=[u for u in battle.alive() if u.camp!=actor.camp and u.profession==2]
            available=[u for u in mages if u not in selected]
            if len(mages)>=4 and available:
                if not any(c in (1,4,6,7,9,18,20,39,43,53,57,58,61,65,66,67,68) for c,_ in skill.enemy_effects):
                    raise UnsupportedMechanism('Blessing218 healer extra attack requires an explicit attack skill/coefficient')
                selected=list(selected)+[battle.rng.choice(available)]
        return self._redirect_guardian_targets(battle,actor,selected,ally)

    def after_hit(self, battle, actor, skill, hit):
        if self.has(actor, 163) and skill.skill_id in self.catalog.hero_for(actor).active_ids:
            count = self.count[(actor.uid, 'rage_hits')] = self.count[(actor.uid, 'rage_hits')]+1
            rage = self.count[(actor.uid, 'rage')]
            # The first stack is the enraged state itself; only later stacks add the third-hit bonus.
            extra = max(0, rage-1)
            added_rate = self.datum(actor, 164) if extra and count >= 3 else None
            added_cap = self.datum(actor, 165) if extra else None
            rate = self.datum(actor, 163)[1] + (extra*added_rate[1] if added_rate else 0)
            cap = actor.panel['physical_attack'] * (2 + extra*added_cap[1]/100 if added_cap else 2)
            self.grow(battle, actor, 'rage_round', actor.panel['physical_attack']*rate/100, cap)
        action=battle.current_action or {}
        origin=action.get('origin');ordinary=not origin or origin=='ordinary' or isinstance(origin,dict) and origin.get('kind')=='ordinary'
        if any(code==6 for code,_ in skill.enemy_effects) or (ordinary and skill.skill_id in (61117001,61119001,61120051)):
            action['lulu_checked_per_hit']=True
            self._check_lulu(battle)

    def critical_modifiers(self, battle, actor, target):
        result = dict(chance=0., damage=0., resistance=0.)
        if actor.race == self.UNDEAD:
            for owner in self.owners(battle, 237, actor.camp):
                if actor.profession == 1:
                    result['chance'] += self.datum(owner,237)[1]
                    result['damage'] += self.datum(owner,237)[1]
            result['damage'] += self.value[(actor.uid,'bear_critical')]
        if self.has(actor,204):
            result['chance'] += self.value[(actor.uid,'walker_critical')]
        if target.race == self.BEAST:
            for code in (141,233):
                owners=self.owners(battle,code,target.camp)
                if owners:
                    result['resistance'] += max(self.datum(u,code)[1] for u in owners)
        return result

    def resolve_hit_context(self, battle, actor, target, skill_id, kind, context):
        if kind=='healing':
            return context
        #187 says "when attacking". The successful dispel (including a
        #confused offensive target) and190 growth precede this hit's damage;
        #Luoshen04/g1/action8 distinguishes this from a post-damage callback.
        if kind in ('physical','magical') and actor.race==self.HUMAN:
            for owner in self.owners(battle,187,actor.camp):
                if owner is not actor and battle.rng.random()*100<self.datum(owner,187)[1]:
                    previous=self._aura_dispel
                    self._aura_dispel=owner
                    try:
                        battle.dispel(target,positive=True,actor=actor,skill_id=self.datum(owner,187)[0])
                    finally:
                        self._aura_dispel=previous
        skill=self.catalog.skill(skill_id)
        if self.has(actor,177) and skill.skill_id in self.catalog.hero_for(actor).active_ids:
            context['bypass_revive'] = context['bypass_rescue'] = True
        if self.has(actor,204):
            chance = actor.panel.get('critical_chance')
            if chance is None:
                raise UnsupportedMechanism('Walker requires explicit critical_chance panel')
            if chance+self.value[(actor.uid,'walker_critical')] >= 100:
                context['bypass_invulnerability'] = True
        if kind=='physical' and actor.race == self.UNDEAD and context.get('critical'):
            for owner in self.owners(battle,237,actor.camp):
                if self.race_count(battle,owner,self.UNDEAD) >= 5:
                    context['bypass_revive'] = context['bypass_rescue'] = context['bypass_invulnerability'] = True
                    context['mother_critical_dispel_skill']=self.datum(owner,237)[0]
                    context['mother_critical_dispel_target']=target.uid
                    pos=self.opening_order.get((owner.uid,66000106))
                    skins=[b for b in battle.active_buffs(target) if b.buff_id==187]
                    if skins and pos is not None and all(b.source_skill==66000081 and
                            self.opening_order.get((b.source_uid,66000081),-1)>pos for b in skins):
                        context['mother_precedes_stone_skin']=True
        if actor.race == self.BEAST:
            streak = self.count[(actor.uid,'same_target')] if self.last_target.get(actor.uid)==target.uid else 0
            if streak >= 2:
                owners=self.owners(battle,208,actor.camp)
                if owners:
                    context['defence_ignore_flat'] = max(u.panel['physical_attack']*self.datum(u,208)[1]/100 for u in owners)
                    context['bypass_invulnerability'] = context['bypass_physical_immunity'] = True
        if self.has(actor,166) and target.race == self.DEMON:
            context['physical_immunity_offset'] = min(80,10*self.count[(actor.uid,'rage')])
        return context

    def additional_components(self, battle, actor, target, skill, level):
        parts=[]
        if self.has(actor,170) and skill.skill_id in self.catalog.hero_for(actor).active_ids:
            count=sum(u.profession==2 for u in battle.alive(actor.camp))
            sid, per_pair = self.datum(actor,170)
            if sid == 66000084 and self.common.artifact_level(actor,sid) == 10:
                # Three living mages give 22%, not the catalog's 21%, in clean plane captures.
                per_pair = 12
            rate=min(30,10+count//2*per_pair)
            parts.append(dict(kind='max_hp',amount=int((target.max_hp//100)*rate)))
        for mark in list(battle.active_buffs(target)):
            if mark.buff_id!=193:
                continue
            owner=battle.units.get(mark.source_uid)
            if owner is None:
                raise UnsupportedMechanism('Judgment mark requires its actual source unit')
            rate=mark.value
            if self.has(owner,172):
                rate+=self.race_count(battle,owner,self.HUMAN,initial=True)//3*self.datum(owner,172)[1]
            amount=int(Decimal(owner.panel['physical_attack']//100)*Decimal(str(rate))
                       *(2 if actor.race==self.HUMAN else 1))
            parts.append(dict(kind='true',amount=amount,source_buff=193))
            target.buffs.remove(mark)
            battle.emit('mark_consumed',actor,target,mark.source_skill,buff_id=193,amount=amount)
        return parts

    def before_apply_buff(self, battle, target, buff):
        source=battle.units.get(buff.source_uid)
        if source and buff.source_skill==66000083 and buff.buff_id in (189,190):
            stats=('physical_attack','magical_attack','physical_defence','magical_defence','speed') if buff.buff_id==189 else ('magical_attack',)
            snap=(battle.current_action or {}).get('via_opening_stats',{})
            buff.stat_modifiers={stat:int(snap.get(stat,getattr(source,stat))*buff.value/100) for stat in stats}
        if source and buff.buff_id==178 and buff.source_skill==66000075:
            self.count[(source.uid,'william_immortal')]=3
        if source and buff.source_skill==66000066 and buff.buff_id in (164,165,166):
            key=(source.uid,target.uid)
            if key not in self.claw_links:
                allies=[u for u in battle.alive(source.camp) if u is not source]
                self.claw_links[key]=min(allies,key=lambda u:(-max(u.physical_attack,u.magical_attack),u.position)).uid if allies else None
            if buff.buff_id in (164,165):
                stat='physical_attack' if buff.buff_id==164 else 'magical_attack'
                buff.stat_modifiers={stat:-int(getattr(target,stat)*buff.value/100)}
        if (buff.buff_id in self.CONTROL_BUFFS and target.race==self.BEAST
                and source is not None and source.alive and source.camp!=target.camp):
            for owner in self.owners(battle,224,target.camp):
                if owner is target:continue
                cap=3+self.race_count(
                    battle,owner,self.BEAST,initial=True,exclude=True
                )//2
                key=(target.uid,'bloodbone_control',owner.uid)
                if self.count[key]>=cap:continue
                self.count[key]+=1
                battle.apply_buff(source,Buff(
                    buff.buff_id,owner.uid,buff.source_skill,buff.duration,
                    buff.value,buff.stacks,buff.max_stacks,buff.initial_duration,buff.hits_remaining,
                    buff.source_attack,dict(buff.stat_modifiers),
                ))
                return False
        if self.has(target,178) and source and source.race==self.UNDEAD:
            if int(battle.buff_row(buff.buff_id).get('效果类型',0))==19:
                buff.value *= 1+self.datum(target,178)[1]/100
            elif buff.buff_id==189 and buff.source_skill==66000083:
                # Via's blessing stores speed beside four unrelated stats.
                # Only the speed share receives the undead dragon's amplifier.
                buff.stat_modifiers['speed']=int(buff.stat_modifiers['speed']*(1+self.datum(target,178)[1]/100))
        if not battle.positive(buff):
            if self.has(target,166) and self.count[(target.uid,'rage')] and target.uid in self.active_actions:
                return False
            if target.race==self.HUMAN and self.owners(battle,214,target.camp):
                # Match semantic type; ids differ across versions.
                label=battle.buff_row(buff.buff_id).get('技能名称','')
                if '禁疗' in label or '毒奶' in label:
                    return False
        return True

    def before_heal(self, battle, actor, target, amount, skill_id):
        if target.race==self.HUMAN:
            bonus=max((self.datum(u,173)[1] for u in self.owners(battle,173,target.camp)),default=0)
            amount=int(Decimal(amount)*(1+Decimal(str(bonus))/100))
        if target.race==self.BEAST:
            bonus=max((min(45,int(self.value[(u.uid,'beast_damage')]/(u.max_hp*.2))*self.datum(u,236)[1])
                       for u in self.owners(battle,236,target.camp)),default=0)
            amount=int(Decimal(amount)*(1+Decimal(str(bonus))/100))
        return amount

    def _redirect_guardian_targets(self,battle,actor,selected,ally):
        if ally:
            return selected
        action=battle.current_action or {}
        skill_id=action.get('skill_id')
        if skill_id is None or not any(code in PRIMARY_DAMAGE for code,_ in self.catalog.skill(skill_id).enemy_effects):
            return selected
        redirected=[]
        for target in selected:
            mark=next((b for b in battle.active_buffs(target) if b.buff_id==178 and b.source_skill==66000075),None)
            guardian=battle.units.get(mark.source_uid) if mark else None
            redirected.append(guardian if guardian and guardian.alive and guardian.camp==target.camp else target)
        return redirected

    def before_settlement(self,battle,actor,target,amount,skill_id,kind):
        if kind=='transfer' or amount<=0:
            return amount
        for victim in battle.alive():
            for buff in battle.active_buffs(victim):
                if buff.buff_id!=166 or buff.source_skill!=66000066:
                    continue
                source=battle.units.get(buff.source_uid)
                ally=self.claw_links.get((buff.source_uid,victim.uid))
                if source and target.camp==source.camp and target.uid in (source.uid,ally):
                    share=int(amount*min(100,max(0,buff.value))/100)
                    if share:
                        battle.damage(actor,victim,share,buff.source_skill,'transfer')
                    return amount-share
        return amount

    def singer_dispel(self,battle,actor,target,skill,level,values):
        removed=battle.dispel(target,positive=True,actor=actor,skill_id=skill.skill_id)
        if removed and target.alive:
            rate=scaled(next(v for code,v in skill.passives if code==133),level)
            battle.apply_buff(target,Buff(int(values[0]),actor.uid,skill.skill_id,int(values[3]),rate,stacks=len(removed)))

    def singer_cleanse(self,battle,actor):
        sid,rate=self.datum(actor,132)
        pool=[u for u in battle.alive(actor.camp) if u.race==self.UNDEAD]
        for target in sorted(pool,key=lambda u:(-battle.negative_count(u,include_seal=True),u.position))[:3]:
            if not battle.negative_count(target,include_seal=True):continue
            removed=battle.dispel(target,positive=False,actor=actor,skill_id=sid)
            if removed and target.alive:
                battle.heal(actor,target,int(actor.magical_attack*rate*len(removed)/100),sid)

    def before_damage(self, battle, actor, target, amount, skill_id, kind):
        if actor.race==self.UNDEAD and actor.camp!=target.camp and kind in ('physical','magical','true','fixed') and battle.has_buff(target,151):
            bonus=min(100,sum(v.value*v.stacks for v in battle.active_buffs(target) if v.buff_id==151))
            amount=int(Decimal(amount)*(1+Decimal(str(bonus))/100))
        # The critical bypass applies to this hit, but its attack/defence
        # formula uses the state before237 removes the target's blessings.
        hit=getattr(battle,'current_hit_context',{}) or {}
        strike=battle.current_damage if battle.current_damage is not None else {}
        if (kind=='physical' and hit.get('mother_critical_dispel_target')==target.uid
                and not strike.get('mother_critical_dispelled')):
            strike['mother_critical_dispelled']=True
            battle.dispel(target,positive=True,actor=actor,skill_id=hit['mother_critical_dispel_skill'],
                          bypass_stone_skin=hit.get('mother_precedes_stone_skin',False))
        if kind=='magical' and amount>0 and target.race==self.UNDEAD and actor.camp!=target.camp:
            strike=battle.current_damage if battle.current_damage is not None else {}
            state=strike.setdefault('via_protection',{})
            for owner in self.owners(battle,169,target.camp):
                key=(owner.uid,'via_magic_guard')
                decision=(owner.uid,target.uid)
                if decision not in state:
                    cap=1+self.race_count(battle,owner,self.UNDEAD,initial=True,exclude=True)//2
                    state[decision]=self.count[key]<cap
                    if state[decision]:
                        self.count[key]+=1
                if state[decision]:
                    reflection=strike.setdefault('via_reflection',{})
                    entry=reflection.setdefault(owner.uid,{'amount':0,'percent':self.datum(owner,169)[1]})
                    basis=hit.get('via_primary')
                    if strike.get('component_index')==0 and basis and basis[:2]==(actor.uid,target.uid):
                        entry['signed_primary']=basis[2]
                    else:entry['amount']+=amount
                    return 0
        if self.has(target,195) and self.last_attacker.get(target.uid)!=actor.uid:
            self.value[(target.uid,'same_attacker_reduction')]=0
        if kind=='physical':
            strike=battle.current_damage if battle.current_damage is not None else {}
            state=strike.setdefault('modern_strike',{})
            physical_total=sum(p.get('raw_amount',p.get('amount',0)) for p in strike.get('components',[]) if p['kind']=='physical') or amount
            if actor.race==self.BEAST and 'flower_floor' not in state:
                state['flower_floor']=True
                floor=0
                for owner in self.owners(battle,197,actor.camp):
                    candidates=[u for u in battle.alive(actor.camp) if u.race==self.BEAST and
                                self.catalog.skill(self.active_id(u)).enemy_target[1]==1]
                    if (self.race_count(battle,owner,self.BEAST)>=4 and actor in candidates and
                        actor.panel['physical_attack']==max(u.panel['physical_attack'] for u in candidates)):
                        floor=max(floor,int(Decimal(owner.panel['physical_attack']//100)*Decimal(str(self.datum(owner,197)[1]))))
                invulnerable=(
                    (battle.has_buff(target,68) or battle.has_buff(target,210))
                    and not (getattr(battle,'current_hit_context',{}) or {}).get('bypass_invulnerability')
                )
                if floor and physical_total>0 and not invulnerable:
                    state['flower_floor_amount']=floor
            if amount==0 and not state.get('flower_floor_amount'):
                return 0
            if self.has(target,195):
                amount=int(amount*(1-min(80,self.value[(target.uid,'same_attacker_reduction')])/100))
            for owner in self.owners(battle,157,target.camp):
                cap=min(6,self.race_count(battle,owner,self.DEMON,initial=True))
                key=('parry',owner.uid)
                if key not in state:
                    state[key]=((target is owner or target.race==self.DEMON) and self.count[(owner.uid,'parry')]<cap
                                and not (getattr(battle,'current_hit_context',{}) or {}).get('ignore_parry'))
                    if state[key]:
                        self.count[(owner.uid,'parry')]+=1
                        if self.has(owner,158):
                            battle.apply_buff(owner,Buff(182,owner.uid,self.datum(owner,158)[0],2,self.datum(owner,158)[1],max_stacks=6))
                if state[key]:
                    sid,rate=self.datum(owner,157)
                    gainkey=('parry_growth',owner.uid)
                    gain=min(max(0,owner.panel['physical_attack']-state.get(gainkey,0)),amount*rate/100)
                    state[gainkey]=state.get(gainkey,0)+gain
                    self.grow(battle,owner,'parry',gain,owner.panel['physical_attack']*3)
                    battle.emit('parry',owner,target,sid,prevented=amount)
                    return 0
            for owner in self.owners(battle,186,target.camp):
                shield=next((b for b in battle.active_buffs(owner) if b.buff_id==195),None)
                capacity=next((b for b in battle.active_buffs(owner) if b.buff_id==196),None)
                if target.race!=self.BEAST or target.position>=3 or shield is None or capacity is None:
                    continue
                high=self.race_count(battle,owner,self.BEAST,initial=True)>4
                max_hits=4 if high else 2
                total=owner.max_hp*(self.datum(owner,186)[1] if high else capacity.value)/100
                key=('ancestral',owner.uid)
                if key not in state:
                    state[key]=self.count[(owner.uid,'ancestral_hits')]<max_hits and self.value[(owner.uid,'ancestral_blocked')]<total
                    if state[key]:
                        self.count[(owner.uid,'ancestral_hits')]+=1
                if state[key]:
                    blocked=min(amount,max(0,int(total-self.value[(owner.uid,'ancestral_blocked')])))
                    if blocked and blocked==amount:
                        strike['ancestral_absorbed_hit_uid']=target.uid
                    self.value[(owner.uid,'ancestral_blocked')]+=blocked
                    amount-=blocked
            if amount==0 and not state.get('flower_floor_amount'):
                return 0
            for owner in self.owners(battle,232,target.camp):
                n=sum(u is not owner and u.race==self.DEMON and u.profession==1 for u in battle.alive(owner.camp))
                key=('adai',owner.uid)
                if key not in state:
                    eligible=target.race==self.DEMON and target.position<3
                    # The source grants four shared physical-immunity hits.
                    # Extra physical demon allies extend only the confusion
                    # opportunities, not the95% reduction window.
                    state[key]=(eligible and self.count[(owner.uid,'adai_guard')]<4)
                    if state[key]:
                        self.count[(owner.uid,'adai_guard')]+=1
                    confuse_key=('adai_confuse_count',owner.uid)
                    if confuse_key not in state:
                        state[confuse_key]=True
                        confuse_active=(
                            eligible
                            and self.count[(owner.uid,'adai_confuse')]<4+2*n
                        )
                        if confuse_active:
                            self.count[(owner.uid,'adai_confuse')]+=1
                        if (confuse_active and actor.alive
                                and battle.rng.random()*100<min(100,25+15*n)):
                            state.setdefault('adai_confuse_owners',[]).append(owner.uid)
                if state[key]:
                    strike=battle.current_damage if battle.current_damage is not None else {}
                    index=strike.get('component_index')
                    part=strike.get('components',[])[index] if index is not None else {}
                    if 'fixed_after_shield' in part:
                        fixed=min(amount,int(part['fixed_after_shield']))
                    elif part.get('raw_amount',0)>0:
                        fixed=min(amount,int(
                            Decimal(str(part.get('flat_skill_addition',0)))
                            *Decimal(amount)/Decimal(part['raw_amount'])
                        ))
                    else:
                        fixed=0
                    guarded=amount-fixed
                    # Eason's panel-defence addition is fixed physical damage,
                    # outside Adai's primary reduction (eight retained games).
                    guard_rate=0 if part.get('source_effect')==67 else self.datum(owner,232)[1]
                    primary=int(guarded*max(0,1-guard_rate/100))
                    # zaodianshui04/g2/a15:11048 flat +1 primary remains
                    #11049 under Adai guard. Preserve a positive body's
                    #minimum1; an already-zero body stays zero.
                    amount=fixed+(max(1,primary) if guarded>0 else 0)
            if self.has(target,234):
                pairs=self.race_count(battle,target,self.BEAST)//2
                key=('agua',target.uid)
                if key not in state:
                    state[key]=physical_total>target.max_hp*.2 and self.count[(target.uid,'agua_guard')]<1+pairs
                    if state[key]:
                        self.count[(target.uid,'agua_guard')]+=1
                if state[key]:
                    rate=min(99,self.datum(target,234)[1]+pairs*self.datum(target,235)[1])
                    amount=int(amount*(1-rate/100))
            floor_amount=state.get('flower_floor_amount',0)
            if floor_amount and not state.get('flower_floor_applied'):
                # The source says *final physical damage* cannot fall below
                # this floor. Shields, Adai guard and Agua guard run above.
                amount=max(amount,floor_amount)
                state['flower_floor_applied']=True
        if kind=='magical':
            if target.race==self.HUMAN:
                skill=self.catalog.skill(skill_id)
                if skill.enemy_target[1]>1:
                    reduction=max((self.datum(u,213)[1]+15*(self.race_count(battle,u,self.HUMAN)//2)
                                   for u in self.owners(battle,213,target.camp)),default=0)
                    strike=battle.current_damage or {}
                    index=strike.get('component_index')
                    part=strike.get('components',[])[index] if index is not None else {}
                    if 'fixed_after_shield' in part:
                        fixed=min(amount,int(part['fixed_after_shield']))
                    elif part.get('raw_amount',0)>0:
                        fixed=min(amount,int(Decimal(str(part.get('flat_skill_addition',0)))
                                             *Decimal(amount)/Decimal(part['raw_amount'])))
                    else:
                        fixed=0
                    amount=fixed+int((amount-fixed)*max(0,1-reduction/100))
            if target.race==self.BEAST:
                action=getattr(battle,'current_action',None) or {}
                single_target=action.get('enemy_target_count',1)<=1
                for owner in self.owners(battle,225,target.camp):
                    if owner is target or not single_target:continue
                    cap=3+self.race_count(
                        battle,owner,self.BEAST,initial=True,exclude=True
                    )//2
                    key=(target.uid,'bloodbone_magic',owner.uid)
                    if self.count[key]>=cap:continue
                    self.count[key]+=1
                    amount=int(amount*max(0,1-self.datum(owner,225)[1]/100))
                    break
        # Prince receives already-resolved ally damage. Its recipient-side
        # mitigation still applies, but the attacker's bonus was paid once.
        if actor.profession==2 and not (kind=='transfer' and skill_id==66000070):
            rate=self.value[(actor.uid,'mermaid_damage')]
            if rate:
                strike=battle.current_damage or {}
                index=strike.get('component_index')
                part=strike.get('components',[])[index] if index is not None else {}
                fixed=0
                if kind in ('physical','magical') and part.get('raw_amount',0)>0:
                    fixed=min(amount,int(part.get('fixed_after_shield',part.get('flat_skill_addition',0))))
                amount=int(((amount-fixed)//100)*(100+rate))+fixed
        context=getattr(battle,'current_action',None) or {}
        origin=context.get('origin') or {}
        is_counter=kind=='counter' or isinstance(origin,dict) and (origin.get('kind')=='counter' or origin.get('mechanism')=='counter')
        if is_counter and target.race==self.UNDEAD and self.count[(target.camp,'king_counter_round')]==self.round:
            strike=battle.current_damage if battle.current_damage is not None else {}
            state=strike.setdefault('modern_strike',{})
            available=max(0,1-state.get('counter_damage',0))
            result=min(available,amount)
            state['counter_damage']=state.get('counter_damage',0)+result
            return result
        if amount and self.round==1 and kind in ('physical','magical','true','max_hp','current_hp','percentage'):
            self._charlie_heal(battle,actor)
        return amount

    def _charlie_heal(self,battle,actor):
        for owner in self.owners(battle,174,actor.camp):
            humans=[u for u in battle.alive(actor.camp) if u.race==self.HUMAN and u is not owner]
            if humans and actor is max(humans,key=lambda u:(max(u.physical_attack,u.magical_attack),-u.position)):
                ally=min(battle.alive(actor.camp),key=lambda u:(u.hp/u.max_hp,u.hp,u.position))
                self.common._heal(battle,owner,ally,
                    int((owner.panel['physical_attack']//100)*self.datum(owner,174)[1]),self.datum(owner,174)[0])

    def on_lethal(self, battle, actor, target, skill_id):
        context=getattr(battle,'current_hit_context',None) or {}
        if target.alive or context.get('bypass_revive') or context.get('bypass_rescue'):
            return
        guardians=[u for u in battle.alive(target.camp) if any(b.buff_id==178 and b.source_uid==target.uid for b in battle.active_buffs(u))]
        key=(target.uid,'william_immortal')
        if guardians and self.count[key]>0:
            self.count[key]-=1
            target.hp=1
            battle.emit('rescue',target,target,66000075,hp_after=1)
            return
        if self.has(target,209) and not self.count[(target.uid,'white_revive')]:
            self.count[(target.uid,'white_revive')]=1
            base=int(target.max_hp*self.datum(target,209)[1]/100)
            restored=self.before_heal(battle,target,target,base,skill_id)
            target.hp=max(1,min(target.max_hp,restored))
            battle.emit('revive',target,target,self.datum(target,209)[0],hp_after=target.hp)

    def _overflow(self,battle,owner,raw,sid,*,race=None,fraction=1.,priority=lambda u:u.hp/u.max_hp):
        start=len(battle.events)
        self.common._heal(battle,owner,owner,int(raw),sid)
        healing=next((e for e in battle.events[start:] if e.kind=='heal' and e.target_uid==owner.uid),None)
        if healing and healing.payload['overflow']:
            allies=[u for u in battle.alive(owner.camp) if race is None or u.race==race]
            if allies:
                recipient=min(allies,key=lambda u:(priority(u),u.position))
                self.common._heal(battle,owner,recipient,int(healing.payload['overflow']*fraction),sid)

    def after_damage(self,battle,event):
        strike=battle.current_damage or {}
        attacker=battle.units.get(event.actor_uid)
        if not attacker or not attacker.alive:
            return
        for owner_uid,entry in strike.get('via_reflection',{}).items():
            owner=battle.units.get(owner_uid)
            amount=int(Decimal(entry['amount'])*Decimal(str(entry['percent']))/100)
            if 'signed_primary' in entry:
                amount+=int(Decimal(int(Decimal(entry['signed_primary'])/100))*Decimal(str(entry['percent'])))
            if owner and owner.alive and attacker.alive and amount>0:
                battle.damage(owner,attacker,amount,61120001,'reflection')
            elif owner and owner.alive and attacker.alive and amount<0:
                before=attacker.hp
                attacker.hp-=amount
                battle.emit('signed_reflection',owner,attacker,61120001,amount=amount,hp_before=before,hp_after=attacker.hp)

    def on_event(self,battle,event):
        actor=battle.units.get(event.actor_uid)
        target=battle.units.get(event.target_uid)
        if event.kind=='action_start' and actor:
            self.active_actions[actor.uid]=event
            if event.skill_id in (66000106,66000081) and (event.payload.get('origin') or {}).get('kind')=='opening':
                self.opening_order.setdefault((actor.uid,event.skill_id),len(battle.events))
        elif event.kind=='targets' and actor and event.payload.get('side')=='enemy':
            selected=event.payload.get('target_uids',[])
            if selected:
                self.value[(actor.uid,'primary')]=event.payload.get('ordinary_primary_uid') or selected[0]
            if self.has(actor,164):
                positives=sum(len([b for b in battle.active_buffs(battle.units[uid]) if battle.positive(b)]) for uid in selected)
                self.count[(actor.uid,'rage')]=min(10,self.count[(actor.uid,'rage')]+positives)
        elif event.kind=='action_end' and actor:
            self._action_end(battle,actor,event)
            self.active_actions.pop(actor.uid,None)
            if self.ordinary(event):
                self.action_turns.discard(actor.uid)
            # Neither an opening nor Solomon's preempt spends his ordinary turn
            # for the Undead King's first-round damage (day18-camp1-01/g3).
            origin=event.payload.get('origin')
            if not (isinstance(origin,dict) and (origin.get('kind')=='opening' or
                    (origin.get('kind')=='preempt' and event.skill_id==66000091))):
                self.acted.add(actor.uid)
            if self.has(actor,204):
                self.value[(actor.uid,'walker_critical')]=0
            if actor.race==self.UNDEAD:
                for buff in battle.active_buffs(actor):
                    # Retained self-preemptions/assists do not grow215.
                    # Other recipients and ordinary actions keep their
                    # existing behavior pending a distinguishing report.
                    if buff.buff_id==215 and (buff.source_uid!=actor.uid or self.ordinary(event)):
                        self.count[(actor.uid,'215',buff.source_uid)]+=1
        elif event.kind=='damage' and actor and target:
            self._damage_event(battle,actor,target,event)
            if actor.alive:
                direct=sum(part['resolved_amount'] for part in event.payload.get('components',()) if part['kind'] in ('physical','magical'))
                dealt=min(event.payload.get('requested',0),direct) if event.payload.get('actual_hp_loss',0)>0 else 0
                for buff in [b for b in battle.active_buffs(actor) if b.buff_id==188]:
                    if not dealt:
                        continue
                    start=len(battle.events)
                    self.common._heal(battle,actor,actor,int(dealt*buff.value/100),buff.source_skill)
                    healing=next((e for e in battle.events[start:] if e.kind=='heal' and e.target_uid==actor.uid),None)
                    allies=[u for u in battle.alive(actor.camp) if u is not actor]
                    if healing and healing.payload['overflow'] and allies:
                        recipient=min(allies,key=lambda u:(u.hp/u.max_hp,u.hp,u.position))
                        amount=min(healing.payload['overflow'],int(recipient.max_hp*.2))
                        self.common._heal(battle,actor,recipient,amount,buff.source_skill)
        elif event.kind=='death' and actor and target and actor.camp!=target.camp:
            self._kill(battle,actor,target,event)
        elif event.kind=='heal' and actor and target is not None:
            overflow=event.payload.get('overflow',0)
            if event.skill_id==66000091 and target is actor and self.has(actor,194):
                humans=[u for u in battle.alive(actor.camp) if u.race==self.HUMAN]
                if overflow and humans and not self._overflow_redirect:
                    recipient=min(humans,key=lambda u:(u.hp/u.max_hp,u.position))
                    self._overflow_redirect=True
                    try:
                        self.common._heal(battle,actor,recipient,int(overflow*self.datum(actor,194)[1]/100),event.skill_id)
                    finally:
                        self._overflow_redirect=False
            # CommonMechanics owns the human healing-to-attack conversion.
        elif event.kind=='dispel' and actor and target:
            removed=event.payload.get('buff_ids',[])
            offensive_aura=(self._aura_dispel is not None and event.payload.get('positive'))
            if actor.race==self.HUMAN and (actor.camp!=target.camp or offensive_aura) and removed:
                for owner in self.owners(battle,190,actor.camp):
                    humans=[u for u in battle.alive(actor.camp) if u.race==self.HUMAN]
                    recipient=max(humans,key=lambda u:(max(u.panel['physical_attack'],u.panel['magical_attack']),-u.position))
                    rate=self.datum(owner,190)[1]*len(removed)/100
                    for stat in ('physical_attack','magical_attack'):
                        self.grow(battle,recipient,'mother_'+owner.uid,owner.panel['magical_attack']*rate,owner.panel['magical_attack']*2,stat)
            if self._aura_dispel is not None and removed:
                owner=self._aura_dispel
                humans=[u for u in battle.alive(owner.camp) if u.race==self.HUMAN]
                if humans:
                    recipient=min(humans,key=lambda u:(u.hp/u.max_hp,u.hp,u.position))
                    self.common._heal(battle,owner,recipient,int(owner.panel['magical_attack']*self.datum(owner,188)[1]/100*len(removed)),self.datum(owner,188)[0])
        if event.kind in ('buff','death','dispel','action_end') and not getattr(battle,'defer_copy_stats',False):
            self.refresh_stats(battle)

    def after_negative_copy(self,battle,actor,target,source,skill):
        if not self.has(actor,205) or skill.activation_type!=1:return
        # Ziyu46917876 BUG3: no new artifact fire/poison when all surviving
        # enemies occupy the attacked column. Existing negatives still copy.
        if all(u.position%3==target.position%3 for u in battle.alive(target.camp)):return
        dots=self.parameters.get('storm_dot_percent',{202:10,203:10})
        sid,chance=self.datum(actor,205)
        if battle.rng.random()*100<min(100,chance):
            for recipient in (target,source):
                for bid in (202,203):
                    if str(bid) not in dots and bid not in dots:
                        raise UnsupportedMechanism(f'Storm requires explicit DoT percentage for {bid}')
                    battle.apply_buff(recipient,Buff(bid,actor.uid,sid,2,float(dots.get(str(bid),dots.get(bid)))))

    def _undead_assist(self,battle,actor,*,immediate=False):
        for owner in self.owners(battle,226,actor.camp):
            available=1+self.race_count(battle,owner,self.UNDEAD,initial=True)//2
            if owner is not actor and self.count[(owner.uid,226)]<available:
                self.count[(owner.uid,226)]+=1
                # Eason chooses its own target rather than the partner's target.
                self.queue(battle,owner,self.active_id(owner),226,immediate=immediate)

    def _action_end(self,battle,actor,event):
        if actor.alive and event.skill_id in (61010101,61110101) and self.has(actor,132) and self.datum(actor,132,required=False):
            self.singer_cleanse(battle,actor)
        primary=self.value.get((actor.uid,'primary'))
        if (primary not in battle.units or not battle.units[primary].alive
                or battle.units[primary].camp==actor.camp):
            enemies=[u for u in battle.alive() if u.camp!=actor.camp]
            selected=battle.ordinary_primary(actor,enemies)
            primary=selected.uid if selected else None
        ordinary=self.ordinary(event)
        if ordinary:
            if actor.race==self.HUMAN:
                for code,cap,sid in ((160,6,61116001),(228,5,66000103)):
                    for owner in self.owners(battle,code,actor.camp):
                        if owner is actor:
                            continue
                        available=min(cap,self.race_count(battle,owner,self.HUMAN,initial=True))
                        if self.count[(owner.uid,code)]<available:
                            self.count[(owner.uid,code)]+=1
                            #253 clean first-round Son artifacts use his own
                            #ordinary target;119 distinguish it from the partner.
                            self.queue(battle,owner,sid,code,primary=None if code==228 else primary)
            if actor.race==self.UNDEAD:
                self._undead_assist(battle,actor)
                for owner in self.owners(battle,203,actor.camp):
                    allies=[u for u in battle.alive(actor.camp) if u is not owner and u.race==self.UNDEAD]
                    top=sorted(allies,key=lambda u:(-max(u.panel['physical_attack'],u.panel['magical_attack']),u.position))[:2]
                    if actor in top:
                        self.grow(battle,owner,'walker',max(actor.panel['physical_attack'],actor.panel['magical_attack'])*self.datum(owner,203)[1]/100,owner.panel['physical_attack']*3)
            if self.round==1 and actor.race==self.DEMON:
                for owner in self.owners(battle,199,actor.camp):
                    key=(owner.uid,'fallen_extra',actor.uid)
                    if owner is not actor and owner.position%3==actor.position%3 and not self.count[key]:
                        self.datum(owner,199)
                        self.count[key]=1
                        self.queue(battle,actor,self.active_id(actor),199,primary=primary)
        origin=event.payload.get('origin')
        if isinstance(origin,dict):
            if origin.get('mechanism')==226 and origin.get('kind') in ('preempt','reaction'):
                return
            if event.skill_id==66000091 and origin.get('kind')=='preempt':
                interrupted=battle.units.get(event.payload.get('primary_uid'))
                if interrupted and interrupted.alive:return
        if isinstance(origin,dict) and origin.get('mechanism') in (154,228):
            # Real luoshen04/g1 cells8→9→10 wait through Son's coordination
            # before Medusa triggers Lulu. The200-window corpus contains no
            # Lulu immediately after66000103. Ordinary Son attacks still check.
            return
        if (battle.current_action or {}).get('lulu_checked_per_hit'):
            return
        self._check_lulu(battle)

    def _check_lulu(self,battle):
        # 每名低血伙伴在本次检查中至多触发一次；每次治疗后重新检查下一名。
        # 171组插入治疗的触发伙伴及停止位置均支持此语义，不能自递归耗光额度。
        for owner in self.owners(battle,154):
            threshold=self.lulu_threshold(owner)
            cap=min(3,self.race_count(battle,owner,self.HUMAN,initial=True)//2)
            for partner in list(battle.alive(owner.camp)):
                if not owner.alive or self.count[(owner.uid,154)]>=cap:
                    break
                if partner.alive and partner.hp/partner.max_hp<threshold/100:
                    self.datum(owner,154)
                    self.count[(owner.uid,154)]+=1
                    battle.execute(owner.uid,self.active_id(owner),skill_level=self.common.skill_level,
                                   origin={'kind':'reaction','mechanism':154,'source_uid':owner.uid,
                                           'trigger_uid':partner.uid})

    def _damage_event(self,battle,actor,target,event):
        dealt=event.payload['actual_hp_loss']
        kind=event.payload['damage_kind']
        components=event.payload.get('components',[])
        strike=getattr(battle,'current_damage',None) or {}
        state=strike.get('modern_strike',{})
        # The retaliation was rolled before HP loss, including a killing hit on Adai.
        for uid in state.pop('adai_confuse_owners',()):
            if actor.alive:
                battle.apply_buff(actor,Buff(217,uid,self.datum(battle.units[uid],230)[0],1))
        if dealt and target.alive and target.uid in self.action_turns:
            for buff in list(battle.active_buffs(target)):
                if buff.buff_id==213:
                    source=battle.units.get(buff.source_uid)
                    if source is None:
                        raise UnsupportedMechanism('Regeneration213 requires Bear source unit')
                    # Bear regeneration uses the panel even after Barbara extracts magic.
                    regen=int((source.panel['magical_attack']//100)*buff.value)
                    self.common._heal(battle,source,target,regen,buff.source_skill)
        physical=min(dealt,sum(p['resolved_amount'] for p in components if p['kind']=='physical')) if components else (dealt if kind=='physical' else 0)
        landed_physical=(
            any(p['kind']=='physical' and p.get('raw_amount',p.get('amount',0))>0
                for p in components)
            if components else kind=='physical' and event.payload.get('raw_amount',0)>0
        )
        if landed_physical and self.has(target,195):
            # Flower's artifact tracks hits from the same attacker even when
            # an ancestral shield reduces the settled HP loss to0.
            if self.last_attacker.get(target.uid)!=actor.uid:
                self.value[(target.uid,'same_attacker_reduction')]=0
            self.last_attacker[target.uid]=actor.uid
            self.value[(target.uid,'same_attacker_reduction')]=min(80,self.value[(target.uid,'same_attacker_reduction')]+self.datum(target,195)[1])
        if dealt and target.race==self.BEAST:
            for owner in self.owners(battle,236,target.camp):
                self.value[(owner.uid,'beast_damage')]+=dealt
        if target.race==self.UNDEAD:
            for buff in battle.active_buffs(target):
                if buff.buff_id==215:
                    self.count[(target.uid,'215',buff.source_uid)]+=1
            self.refresh_stats(battle)
        if dealt and actor.alive and self.has(actor,159):
            rate=self.datum(actor,159)[1]*(dealt/target.max_hp*100)/100
            self.grow(battle,actor,'mercy',actor.panel['physical_attack']*rate,actor.panel['physical_attack']*3)
        if dealt and actor.alive and self.has(actor,160):
            self.common._heal(battle,actor,actor,int(dealt*.3),61116001)
        if actor.alive and actor.race==self.BEAST and kind=='magical' and dealt:
            for owner in self.owners(battle,196,actor.camp):
                self.common._heal(battle,actor,actor,int(dealt*self.datum(owner,196)[1]/100),self.datum(owner,196)[0])
        if physical and actor.alive:
            if actor.race==self.BEAST:
                if self.last_target.get(actor.uid)==target.uid:
                    self.count[(actor.uid,'same_target')]+=1
                else:
                    self.last_target[actor.uid]=target.uid
                    self.count[(actor.uid,'same_target')]=1
                for owner in self.owners(battle,196,actor.camp):
                    self.common._heal(battle,actor,actor,int(physical*self.datum(owner,196)[1]/100),self.datum(owner,196)[0])
            julie=sum(b.value*b.stacks for b in battle.active_buffs(actor) if b.buff_id==182)
            if julie:
                self._overflow(battle,actor,physical*julie/100,event.skill_id)
            for buff in battle.active_buffs(actor):
                if buff.buff_id==219:
                    # Mother overkill uses the settled physical strike, not the victim's last HP.
                    basis=sum(n for k,n in self.common.lifesteal_components(event,use_requested=True) if k=='physical')
                    self._overflow(battle,actor,basis*buff.value/100,buff.source_skill,
                                   priority=lambda u:(u.hp/u.max_hp,u.hp))
            if self.has(actor,167):
                positives=len([b for b in battle.active_buffs(actor) if battle.positive(b)])
                rate=self.datum(actor,167)[1]+positives*self.datum(actor,168)[1]
                self._overflow(battle,actor,physical*rate/100,self.datum(actor,167)[0],race=self.BEAST)
        if dealt and actor.race==self.UNDEAD and not (event.skill_id==61120001 and kind=='reflection'):
            for owner in self.owners(battle,219,actor.camp):
                # Five clean archived owner hits retain positive buffs.
                #219 benefits another same-column undead, not its owner.
                if owner is not actor and owner.position%3==actor.position%3 and target.alive:
                    battle.dispel(target,positive=True,actor=actor,skill_id=self.datum(owner,219)[0])
        if actor.race==self.DEMON and (not target.alive or target.hp<target.max_hp*.1):
            for owner in self.owners(battle,200,actor.camp):
                if owner is not actor and owner.position%3==actor.position%3 and not self.count[(owner.uid,200)]:
                    self.datum(owner,200)
                    self.count[(owner.uid,200)]=1
                    # Server reports complete the triggering multi-hit action
                    # before the artifact chase. Queue it; do not execute
                    # between the dragon's two strike cells.
                    primary = target.uid if target.alive and target.camp != owner.camp else None
                    action = battle.current_action or {}
                    origin = action.get('origin') or {}
                    if (primary and actor.template_id == self.common.DRAGON
                            and event.skill_id in (61020041,61120041,61220041)
                            and (action.get('actor_uid'), action.get('skill_id')) == (actor.uid, event.skill_id)
                            and isinstance(origin, dict)
                            and (origin.get('kind') == 'ordinary' or origin.get('mechanism') == 199)):
                        # A low-health extra target starts the chase but does not choose its column.
                        original = battle.units.get(self.value.get((actor.uid,'primary')))
                        if original and original.alive and original.camp != owner.camp:
                            primary = original.uid
                    self.queue(battle,owner,self.active_id(owner),200,primary=primary)
        if self.has(actor,222) and actor.alive:
            # For an artifact burst saved by Plague, use the target's settled HP.
            plague_saved_during_burst = event.skill_id == 66000100 and strike.get('plague_rescued')
            revived = (
                not plague_saved_during_burst
                and event.payload.get('hp_after', 0) > 0
                and event.payload.get('requested', 0) >= event.payload.get('hp_before', 1)
            )
            #25 four-mage cases cast3 bursts; one six-mage case casts4.
            #The cap of3 is additional casts, and the deployed count includes self.
            cap=1+min(3,sum(u.profession==2 for u in battle.units.values() if u.camp==actor.camp)//2)
            action=battle.current_action
            reserved=action.setdefault('shadow_burst_reserved',[]) if action is not None else []
            if ((target.hp<target.max_hp*.3 or not target.alive or revived)
                    and actor.uid not in reserved and self.count[(actor.uid,'burst')]<cap):
                # Two kills in one complete attack reserve one burst, not two.
                # Its own later attack gets a fresh context and may chain again
                # (zaodianshui11/15 g3: two kills, one burst, opposing turn).
                reserved.append(actor.uid)
                self.count[(actor.uid,'burst')]+=1
                # Match17 game2 cells10..13 finish both active targets before
                # three consecutive bursts. Burst damage can itself qualify;
                # reserve its slot now, then settle after the current action.
                self.queue(battle,actor,66000100,222)
        if event.payload.get('critical') and actor.race==self.UNDEAD:
            for owner in self.owners(battle,204,actor.camp):
                self.value[(owner.uid,'walker_critical')]+=self.datum(owner,204)[1]

    def _kill(self,battle,actor,target,event):
        for buff in battle.active_buffs(actor):
            if buff.buff_id==198:
                owner=battle.units.get(buff.source_uid)
                if owner is None:
                    raise UnsupportedMechanism('Life return requires caster unit')
                # 28 uncapped heals across 13 reports use panel attack and
                # divide it before the coefficient, unaffected by Sophia.
                amount=int((owner.panel['magical_attack']//100)*buff.value)
                for ally in battle.alive(actor.camp):
                    if ally.profession==2:
                        self.common._heal(battle,owner,ally,amount,buff.source_skill)
        if self.has(actor,177) and event.skill_id in self.catalog.hero_for(actor).active_ids:
            self.count[(actor.camp,'king_counter_round')]=self.round
        if actor.race==self.UNDEAD:
            for owner in self.owners(battle,161,actor.camp):
                cap=min(3,self.race_count(battle,owner,self.UNDEAD,initial=True)//2)
                if self.count[(owner.uid,161)]<cap:
                    action=battle.current_action or {}
                    origin=action.get('origin')
                    royal_chain=event.skill_id==61120031
                    if royal_chain:
                        # One pair of hits after the King's entire sweep, including rear kills.
                        queued=action.setdefault('barbara_royal_followups',set())
                        if owner.uid in queued:continue
                        queued.add(owner.uid)
                    elif (isinstance(origin,dict) and origin.get('parent_skill') in (61120031,61120201)
                          and origin.get('source_uid')==owner.uid):
                        continue
                    # The King's rear splash belongs to the same attack. Its kill
                    # cannot dispatch Barbara between the front and rear targets.
                    self.count[(owner.uid,161)]+=1
                    self.queue(battle,owner,self.active_id(owner),161,
                               immediate=event.skill_id not in (61020031,61120031),
                               parent_skill=event.skill_id if royal_chain or event.skill_id==61120201 else None)
            for owner in self.owners(battle,218,actor.camp):
                for ally in battle.alive(actor.camp):
                    if ally.race==self.UNDEAD and ally.position%3==owner.position%3 and ally is not owner:
                        self.value[(ally.uid,'bear_critical')]+=self.datum(owner,218)[1]
            for owner in self.owners(battle,204,actor.camp):
                self.value[(owner.uid,'walker_critical')]+=self.datum(owner,204)[1]
        if actor.race==self.HUMAN:
            for owner in self.owners(battle,216,actor.camp):
                sid,rate=self.datum(owner,216)
                bid=212 if self.race_count(battle,owner,self.HUMAN)>=5 else 211
                for enemy in battle.alive():
                    if enemy.camp!=actor.camp:
                        battle.apply_buff(enemy,Buff(bid,owner.uid,sid,9999,rate))
        if actor.profession==2:
            for owner in self.owners(battle,192,actor.camp):
                if sum(u.profession==2 for u in battle.alive(actor.camp))>=4:
                    for ally in battle.alive(actor.camp):
                        if ally is not owner and ally.profession==2 and ally.position%3==owner.position%3:
                            self.value[(ally.uid,'mermaid_damage')]+=self.datum(owner,192)[1]

    def end_round(self,battle):
        for unit in battle.units.values():
            for name in ('rage_round','king_round'):
                address=(unit.uid,name,'physical_attack')
                self.common.growth[(unit.uid,'physical_attack')]-=self.value[address]
                self.value[address]=0
            self.count[(unit.uid,'rage_hits')]=0
        self.round+=1
        self.acted.clear()
        self.common.refresh_stats(battle)
        self.refresh_stats(battle)
