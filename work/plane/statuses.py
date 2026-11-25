"""Shared state semantics for the captured SSS roster.

Critical chance subtraction and mixed-stat ordering retain the main-stage
candidate convention. They are executable rules, not calibrated probabilities.
"""
from collections import defaultdict
from decimal import Decimal
from combat import Buff, UnsupportedMechanism

CHARM_BUFFS = (97, 122)


class StatusMechanics:
    supported_buffs={1,5,6,10,17,18,19,20,21,22,24,29,30,31,33,36,40,43,48,53,55,56,
        59,61,63,65,66,71,72,75,76,78,79,80,92,93,99,100,115,117,118,123,125,
        121,122,130,131,135,137,138,139,140,141,142,143,144,145,152,174,175,183,184,194,200,202,203,204,205,209,
        210,211,217,220,221,222,168,95,177}
    supported_control={5,6,20,21,36,97,117,122,123,171,209,217,220}
    controls={5,6,20,21,36,97,122,155,171,217,220}
    def __init__(self,catalog,common):
        self.catalog=catalog
        self.common=common
        self._selecting=False
        self._poison_heal=False

    def total(self,combat,unit,bid):
        return sum(b.value*b.stacks for b in combat.active_buffs(unit) if b.buff_id==bid)

    def stat_transform(self,combat,unit,stat,value):
        buffs=combat.active_buffs(unit)
        totals=defaultdict(int)
        for buff in buffs:totals[buff.buff_id]+=buff.value*buff.stacks
        total=lambda bid:totals.get(bid,0)
        rate=0
        if stat=='speed':rate-=total(179)
        if stat=='physical_attack':rate+=total(44)+total(102)*(1.5 if unit.race==1 else 1)
        if stat in ('physical_attack','magical_attack'):
            rate-=total(92)
            if stat=='physical_attack':
                rate-=total(48)
                # Lust120/Sloth118 share the42/64 attack-percentage base in
                # CommonMechanics; multiplying them here double-counts it.
            else:rate+=min(200,total(63))+min(200,total(100))
        if stat in ('physical_defence','magical_defence'):
            rate+=min(150,total(78))-total(93)
            # 71/72 are combined with the percentage base in CommonMechanics.
            rate+=total(53) if stat=='physical_defence' else 0
        if stat=='speed':
            rate-=total(137)
            for b in buffs:
                if b.buff_id==125:
                    owner=combat.units.get(b.source_uid)
                    if b.source_skill==66000046 and owner:
                        value+=owner.panel['speed']*b.value*b.stacks/100
                    else:rate+=b.value*b.stacks
                elif b.buff_id==138:
                    owner=combat.units.get(b.source_uid)
                    if owner:value+=owner.panel['speed']*b.value/100
                elif b.buff_id in (19,221,222) and not getattr(b,'stat_modifiers',{}):
                    value+=b.value*b.stacks*(-1 if b.buff_id==222 else 1)
        # Fox blessing explicitly excludes HP; this layer transforms numerical
        # attacks/defences/speed, not unprovided cast-chance panels.
        if stat in ('physical_attack','magical_attack','physical_defence','magical_defence','speed'):
            rate+=total(130)
        value*=max(0,1+rate/100)
        if stat=='physical_attack':
            combo_multiplier=1.0
            for buff in buffs:
                if buff.buff_id==194:
                    for _ in range(buff.stacks):
                        combo_multiplier*=1+buff.value/100
            value*=min(4.0,combo_multiplier)
        value+=sum(getattr(b,'stat_modifiers',{}).get(stat,0)*b.stacks for b in buffs)
        return max(0,value)

    def resolve_hit_context(self,combat,actor,target,skill_id,kind,context):
        context=dict(context)
        chance=float(actor.panel.get('critical_chance',0))+self.total(combat,actor,17)+self.total(combat,actor,33)+min(30,self.total(combat,actor,79))+float(context.get('critical_chance_bonus',0))
        resist=float(target.panel.get('critical_resist',0))+self.total(combat,target,55)+self.total(combat,target,66)
        extra_damage=self.total(combat,actor,18)
        damage_resist=float(target.panel.get('critical_damage_resist',0))
        for hook in combat.hooks:
            fn=getattr(hook,'critical_modifiers',None)
            if fn:
                modifiers=fn(combat,actor,target)
                chance+=modifiers.get('chance',0)
                resist+=modifiers.get('resistance',0)
                extra_damage+=modifiers.get('damage',0)
                damage_resist+=modifiers.get('damage_resistance',0)
        probability=max(0,min(100,chance-resist))/100
        if context.get('critical') is None:
            context['critical']=combat.rng.random()<probability
        context.setdefault('critical_multiplier',max(1,float(actor.panel.get('critical_multiplier',2))+(extra_damage-damage_resist)/100))
        context['critical_probability']=probability
        return context

    def before_apply_buff(self,combat,target,buff):
        if buff.buff_id==168 and buff.hits_remaining is None:
            buff.hits_remaining=3
        if buff.buff_id==61 and buff.source_skill==66000014 and target.race!=0:return False
        if combat.positive(buff) or combat.seal_bypasses_immunity(buff):return True
        if buff.buff_id in self.controls and any(combat.rng.random()*100 < b.value for b in combat.active_buffs(target) if b.buff_id==95):return False
        if combat.has_buff(target,152):return False
        if buff.buff_id in self.controls and combat.has_buff(target,40):return False
        if buff.buff_id==65 and combat.has_buff(target,76):return False
        return True

    def before_heal(self,combat,actor,target,amount,skill_id):
        if combat.has_buff(target,65) and not combat.has_buff(target,76):return 0
        amount=int(Decimal(amount)*max(Decimal(0),1-Decimal(str(self.total(combat,target,211)))/100))
        poison=next((b for b in combat.active_buffs(target) if b.buff_id==43),None)
        if poison and not self._poison_heal:
            self._poison_heal=True
            try:
                source=combat.units.get(poison.source_uid,actor)
                combat.damage(source,target,int(Decimal(amount)*Decimal(str(poison.value))/100),poison.source_skill,'heal_reversal')
            finally:self._poison_heal=False
            return 0
        return amount

    def before_damage(self,combat,actor,target,amount,skill_id,kind):
        if amount==0:return 0
        strike=combat.current_damage or {}
        component_index=strike.get('component_index')
        damage_kind=kind
        if kind=='transfer' and skill_id in (66000022,66000070) and component_index is not None:
            damage_kind=strike['components'][component_index].get('source_damage_kind',kind)
        total=lambda b:self.total(combat,target,b)
        # SS Adai reflects the settled incoming damage; Dawn does not amplify it again.
        dawn=0 if kind=='reflection' and skill_id==61004701 else total(61)
        vulnerability=total(115)+dawn
        # Fish59 scales the primary damage before the fixed skill addition;
        # CommonMechanics.damage_rule owns that part (raw minimum6726 cases).
        if kind=='physical':vulnerability+=total(31)+total(67)+total(104)
        if actor.race==3 and actor.camp!=target.camp and kind in ('physical','magical'):
            vulnerability+=total(176)
        reduction=total(24)+total(177)
        for hook in combat.hooks:
            fn=getattr(hook,'additional_damage_reduction',None)
            if fn:reduction+=fn(combat,target,damage_kind)
        remaining_offset=0
        if damage_kind=='physical':
            context=getattr(combat,'current_hit_context',{}) or {}
            if not context.get('bypass_physical_immunity'):
                reduction+=total(29)+min(40,sum(int(b.value)*b.stacks for b in combat.active_buffs(target) if b.buff_id==99))
                reduction+=total(135)*(2 if target.race==3 else 1)
                offset=float(context.get('physical_immunity_offset',0))
                remaining_offset=max(0,offset-max(0,reduction))
                reduction=max(0,reduction-offset)
        if damage_kind=='magical':reduction+=total(30)+total(169)
        reduction131=total(131)*(2 if target.race==1 else 1)
        # War Earrings do not reduce Shadow's shared holy burst (zone 1, days 18/19).
        if skill_id==66000100 and kind=='true':reduction131=0
        strike=combat.current_damage or {}
        index=strike.get('component_index')
        if (combat.mechanism_options.get('effect67_exempt131',False) and kind=='physical'
                and index is not None and strike['components'][index].get('source_effect')==67):
            # Explicit candidate: four corrected-state raw67 records improve;
            # the remaining423 residual stays visible. Other protection stays.
            reduction131=0
        reduction+=max(0,reduction131-remaining_offset)
        if kind in ('max_hp','current_hp'):
            reduction+=total(144)*(2 if target.race==3 else 1)
        if kind=='reflection':reduction+=self.total(combat,target,75)
        # Only the marked primary component carries the flat skill addition.
        # current_damage is restored after nested reflection, so neither this
        # part nor its index can leak into another strike. Earlier hooks here
        # scale whole physical/magical components proportionally.
        fixed=Decimal(0)
        strike=combat.current_damage or {}
        index=strike.get('component_index')
        if kind in ('physical','magical') and index is not None:
            part=strike['components'][index]
            if part.get('amount_after_shield',0)>0:
                fixed=part['fixed_after_shield']*Decimal(amount)/Decimal(part['amount_after_shield'])
            elif part['raw_amount']>0:
                fixed=Decimal(str(part.get('flat_skill_addition',0)))*Decimal(amount)/Decimal(part['raw_amount'])
        primary_before=Decimal(amount)-fixed
        primary=primary_before*(1+Decimal(str(vulnerability))/100)*max(Decimal(0),1-Decimal(str(reduction))/100)
        if fixed>0 and primary_before>0 and reduction>0:
            # 782e3bd0/g2/r1/a20 retains fixed4349 + minimum primary1
            # under131. Holy's minimum-body13953/12836 self share has the
            # same floor. Round the combined strike only after it.
            primary=max(Decimal(1),primary)
        fixed*=(1+Decimal(str(vulnerability-dawn))/100)
        value=primary+fixed
        protection=self.total(combat,target,168)*(2 if target.race==0 else 1)
        value*=max(Decimal(0),1-Decimal(str(protection))/100)
        return max(0,int(value))

    def after_damage(self,combat,event):
        if event.payload.get('raw_amount',0)<=0:
            return
        target=combat.units.get(event.target_uid)
        if not target:
            return
        for buff in list(combat.active_buffs(target)):
            if buff.buff_id==168:
                buff.hits_remaining=(3 if buff.hits_remaining is None else buff.hits_remaining)-1
                if buff.hits_remaining<=0:
                    target.buffs.remove(buff)

    def preflight(self,combat,actor,skill):
        # Runtime may turn silence/charm into normal attack or skip stun.
        # Six observed66000103 reactions retain21 and/or122; ordinary turns
        # still skip/fallback, and charm still redirects this artifact to allies.
        son_artifact=skill.skill_id==66000103
        blocked_controls=(21,220) if skill.skill_id in (66000091,66000044,66000045,66000023) else (6,220) if son_artifact else (6,21,220)
        if any(combat.has_buff(actor,b) for b in (*blocked_controls,155)):
            raise UnsupportedMechanism('Stunned unit must skip its ordinary turn')
        if combat.has_buff(actor,5) and skill.activation_type==1 and skill.skill_id!=61000001:
            raise UnsupportedMechanism('Silenced unit must use its normal attack')
        if any(combat.has_buff(actor,b) for b in CHARM_BUFFS) and skill.skill_id not in (61000001,66000023,66000091) and not son_artifact:
            raise UnsupportedMechanism('Charmed unit must use its normal attack')

    def select_targets_override(self,combat,spec,actor,candidates,primary,ally):
        if self._selecting:return None
        mode,count=spec
        if mode==0:return []
        if (ally and mode==1 and any(combat.has_buff(actor,b) for b in (*CHARM_BUFFS,217))
                and (combat.current_action or {}).get('skill_id')==66000023):
            # All six retained217-only Fish reactions send healing and46
            # to an enemy, including the two zero-heal full-HP recipients.
            enemies=[u for u in combat.alive() if u.camp!=actor.camp]
            return [combat.rng.choice(enemies)] if enemies else []
        if not ally and any(combat.has_buff(actor,b) for b in CHARM_BUFFS):
            friends=combat.alive(actor.camp)
            marked=[u for u in friends if combat.has_buff(u,206)]
            return [combat.rng.choice(marked or friends)] if friends else []
        confused=any(combat.has_buff(actor,b) for b in (36,171,217))
        if confused:
            action=getattr(combat,'current_action',None)
            if isinstance(action,dict):
                action['confusion_flip']=True
            # 混乱 is NOT spent by the action: corpus 105/106 confused attacks
            # end with no buff removal, and one unit stays confused for six of
            # its own actions. The buff only leaves via cleanse/duration.
        elif isinstance(getattr(combat,'current_action',None),dict) \
                and combat.current_action.get('confusion_flip') \
                and combat.current_action.get('actor_uid')==actor.uid:
            confused=True
        if confused and not ally:
            # 混乱 attacks its OWN side: across 318 reports every confused
            # offensive action (106 of them, including 先制 66000091 41 次 and
            # 协同 66000103 19 次) landed on an ally and only a passive
            # 黑炎之牙 reflection touched an enemy. 24 of 106 hit the actor
            # itself, so self is in the pool. Skills aimed at allies keep their
            # normal selection.
            pool=[u for u in combat.alive(actor.camp)]
            if not pool:return []
            count=max(1,int(spec[1])) if spec else 1
            # Audited single-target attacks under 217 keep plague priority
            # after switching camps. The caster is eligible if marked.
            if spec==(1,1) and combat.has_buff(actor,217):
                pool=[u for u in pool if combat.has_buff(u,206)] or pool
            if count>=len(pool):return list(pool)
            return combat.rng.sample(pool,count)
        return None

    def after_select_targets(self,combat,spec,actor,selected,candidates,ally):
        action=getattr(combat,'current_action',None)
        if not ally and action is not None:
            action.pop('blessing_added_target',None)
            action.pop('blessing_new_target_uid',None)
        if spec[0]==0:return selected
        if any(combat.has_buff(actor,b) for b in (36,217)):
            # 混乱 turns every offensive action onto the caster's own camp. The
            # corpus has no action that damages both sides, so the taunt/marked
            # rewrites and the209/117 extra target are all skipped here.
            return selected
        if not ally:
            marks=[u for u in candidates if u.alive and combat.has_buff(u,143)] if actor.race==3 else []
            if marks and selected:
                count=len(selected)
                forced=combat.rng.sample(marks,min(count,len(marks)))
                selected=forced+[u for u in selected if u not in forced][:max(0,count-len(forced))]
            guided=[u for u in candidates if u.alive and combat.has_buff(u,106)] if any(hasattr(h,'glutton_dispel') for h in combat.hooks) else []
            if guided and selected and not any(combat.has_buff(u,206) for u in candidates) and not marks:
                mark=guided[0]
                if spec[0]==3:selected=[u for u in candidates if u.alive and u.position%3==mark.position%3][:len(selected)]
                elif mark not in selected:selected=[mark,*selected[1:]]
            taunt=next((b for b in combat.active_buffs(actor) if b.buff_id==20),None)
            source=combat.units.get(taunt.source_uid) if taunt else None
            if source and source.alive and source.camp!=actor.camp and selected:
                if spec[0]==3:
                    selected=[u for u in candidates if u.alive and u.position%3==source.position%3][:len(selected)]
                elif source not in selected:selected=[source,*selected[:-1]]
        # Both statuses add an ATTACK target.61008301 explicitly excludes
        # skills directed at allies; real66000059+209 remains self-only.
        # 209 在主动、协同与回合末追击均增加选靶；无敌目标仍被选中，
        # 只是原报不写伤害。rank3/g1 a22/25/27/35 都保留空的1:2条目。
        if ally:return selected
        extra_source=117 if combat.has_buff(actor,117) else 209 if combat.has_buff(actor,209) else None
        if extra_source:
            pool=[u for u in candidates if u.alive and u not in selected]
            original_count=len(selected)
            selected=[*selected,*combat.rng.sample(pool,min(1,len(pool)))]
            if action is None:action={}
            #117 explicitly repeats an existing target when no extra enemy
            #is available. Plane Hela repeats and101/101 basic hits show
            #that only the primary damage halves; fixed additions stay full.
            living=[u for u in candidates if u.alive]
            if not pool and selected and extra_source==117:
                action['half_primary_target_indices']=[len(selected)]
                selected=[*selected,combat.rng.choice(selected)]
            elif (not pool and len(living)==1 and selected==living
                    and action.get('skill_id') in (61020101,61120101,61020041,61120041,61220041)
                    and extra_source==209):
                # The final survivor receives both full Plague attacks;
                # zaodianshui04/g2/a22 distinguishes Dragon from a normal
                # single target after the other enemy died on the first hit.
                selected=[*selected,living[0]]
            if len(selected)>original_count:
                # Track the hit index: an extra strike can repeat the original target.
                action['blessing_added_target']=(original_count,extra_source)
                if pool:
                    action['blessing_new_target_uid']=selected[original_count].uid
        return selected

    def hit_count_bonus(self,combat,actor,skill):
        if skill.activation_type!=1 or not combat.has_buff(actor,204):return 0
        count=sum(u.race==1 for u in combat.alive(actor.camp))
        return 2 if count>5 else 1

    def additional_components(self,combat,actor,target,skill,level):
        if not combat.has_buff(target,206):return []
        blessing=next((b for b in combat.active_buffs(actor) if b.buff_id==209),None)
        if not blessing:return []
        if blessing.source_attack is None:raise UnsupportedMechanism('209 requires the original Plague attack snapshot')
        #209 is part of this physical strike. Captured Dragon773598 produces
        #162400 intrinsic overflow healing, which requires the complete hit as
        #its physical basis; treating the appendage as true damage excludes it.
        return [dict(kind='physical',amount=int(blessing.source_attack*blessing.value/100),source_buff=209)]

    def before_action(self,combat,actor,skill):
        if combat.has_buff(actor,205):
            combat.dispel(actor,positive=False,actor=actor,skill_id=skill.skill_id)

    def on_event(self,combat,event):
        if event.kind!='damage':return
        actor=combat.units.get(event.actor_uid)
        target=combat.units.get(event.target_uid)
        if not actor or not target or not actor.alive or actor.camp==target.camp:return
        # Nine report actions include a lethal hit and charm on its attacker.
        # The blessing reacts to that hit even when the owner just died;
        # Golden Dragon must see122 before deciding whether to continue.
        if event.payload.get('hp_before',0)<=0:return
        if event.payload.get('damage_kind') in ('dot','transfer','reflection','heal_reversal'):return
        rates=[b.value for b in combat.active_buffs(target) if b.buff_id in (121,145)]
        prior=(combat.current_damage or {}).get('lust_charm_at_seal')
        if prior is not None:rates.append(prior)
        if rates and actor.is_male and combat.rng.random()*100<max(rates):
            combat.apply_buff(actor,Buff(122,target.uid,61008401,2))

    def end_round(self,combat):
        # Duration decay belongs to the runtime and occurs after periodic events.
        for unit in list(combat.alive()):
            for buff in list(combat.active_buffs(unit)):
                owner=combat.units.get(buff.source_uid)
                if not owner:raise UnsupportedMechanism('Periodic status requires source identity')
                if buff.buff_id in (1,22,202,203,56):
                    amount=int(buff.value*buff.stacks*(1 if buff.buff_id==56 else unit.max_hp/100))
                    combat.damage(owner,unit,max(0,amount),buff.source_skill,'dot')
                elif buff.buff_id==101 and unit.alive:
                    combat.damage(owner,unit,int(unit.hp*buff.value*buff.stacks/100),buff.source_skill,'dot')
                elif buff.buff_id==10 and unit.alive:
                    combat.heal(owner,unit,int(unit.max_hp*buff.value/100),buff.source_skill)
                elif buff.buff_id==131:buff.value/=2
