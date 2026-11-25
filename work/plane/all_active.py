"""Description-derived active operations for the versioned plane catalogue.

Base 610 rows are consulted alongside 611/612 rows: many awakening descriptions
describe only the added passive, although their active fields retain the base.
The malformed 1550|130 token is never silently claimed as a damage component:
the rank-9 replay shows 61020401 deals only its 65% hit, so the segment is
recorded as a no-damage candidate under an explicit source-checked policy.
"""
import copy
from decimal import Decimal
from fractions import Fraction

ADDITIONAL_EFFECTS = {7,9,13,15,16,18,20,23,26,27,31,36,37,39,40,41,42,
                      47,50,53,55,57,58,59,61,65,66,67,68,69,70,71,72,73,74,99,1550}
ADDITIONAL_SELECTORS = {5,6,8,9,10,12,14,18,19,30,31,32,34,35,38,39,43,44,46}
ADDITIONAL_EFFECTS.update((21,28,29,38,49))
PRIMARY_DAMAGE = {1,4,6,7,9,18,20,27,29,38,39,43,47,50,53,57,58,61,68}
BUFF_EFFECTS = {3,41,45,56,65,70,73,99}
ADDITIONAL_EFFECTS.add(45)
PRIMARY_DAMAGE.add(49)


def effect_arity(code):
    return 1 if code == 1550 else 6 if code == 99 else 5 if code in BUFF_EFFECTS else 3 if code in (20,27,57) else 2


def wolf_1550_matches(c, skill):
    row=c.catalog.tables['Skill'].get(skill.skill_id,{})
    return (skill.skill_id==61020401 and row.get('对敌方效果')=='1|0.65|0#1550|130'
            and row.get('最高')=='1550|130'
            and row.get('additional_damage_marker', '追加#伤害' in row.get('技能描述', ''))
            and skill.enemy_effects==((1,('0.65','0')),(1550,('130',))))


def active_requirements(c, skill):
    result=[]
    codes={code for code,_ in (*skill.enemy_effects,*skill.ally_effects)}
    if 45 in codes and (not any(code==133 for code,_ in skill.passives) or not any(hasattr(h,'singer_dispel') for h in c.hooks)):
        result.append('missing Singer dispel mechanism')
    if any(k==65 for k,_ in skill.passives) and skill.skill_id not in (66000015,66000039):
        if not any(65 in getattr(h,'supported_passives',()) and not hasattr(h,'glutton_dispel') for h in c.hooks):
            result.append('unsupported source for on-hit passive 65')
    for code,allowed in ((21,(61005401,61105401)),(49,(61010031,61110031,61210031))):
        if code in codes and (skill.skill_id not in allowed or not any(hasattr(h,'glutton_dispel') for h in c.hooks)):
            result.append(f'unsupported source for effect {code}')
    for code,allowed in ((28,(66000026,)),(29,(61006501,61106501)),(38,(66000052,))):
        if code in codes and (skill.skill_id not in allowed or not any(hasattr(h,'arthur_opening') for h in c.hooks)):
            result.append(f'unsupported source for effect {code}')
    if 1550 in codes:
        if c.mechanism_options.get('wolf_1550_policy') != 'ignored':
            result.append('effect 1550 malformed source requires explicit wolf_1550_policy=ignored')
        elif not wolf_1550_matches(c,skill):
            result.append('effect 1550 exact source and highest-field evidence mismatch')
    if 99 in codes and 'effect99_chance_policy' not in c.mechanism_options:
        result.append('candidate effect 99 config chance versus description beast-count chance')
    if skill.skill_id == 61205501 and '61205501_health_cost' not in c.mechanism_options:
        result.append('candidate 61205501 description removes health cost but raw effect16 remains')
    for effect in (26,68):
        if effect in codes and not any(hasattr(h,'resolve_hit_context') for h in c.hooks):
            result.append(f'critical hit resolver required for effect {effect}')
    if 55 in codes:
        if 'effect55_donor_cap_policy' not in c.mechanism_options:
            result.append('candidate effect 55 extraction cap allocation between donors')
    if 58 in codes and 'effect58_initial_hit' not in c.mechanism_options:
        result.append('candidate effect 58 beast-count additional hits versus total hits')
    implicit={42:{139,140,141,142},55:{152,183,184},73:{19,221,222}}
    covered=c.BUILTIN_BUFFS | set().union(*(set(getattr(h,'supported_buffs',())) for h in c.hooks))
    for effect,buffs in implicit.items():
        if effect in codes:
            result.extend(f'buff behaviour {bid}' for bid in sorted(buffs-covered))
    return result


def preflight_active(c, actor, skill):
    from combat import UnsupportedMechanism
    codes={code for code,_ in (*skill.enemy_effects,*skill.ally_effects)}
    for key,allowed in {'effect99_chance_policy':('config','base_description'),
                        'effect58_initial_hit':(0,1),
                        'effect55_donor_cap_policy':('recipient_cap',),
                        'wolf_1550_policy':('ignored',),
                        '61205501_health_cost':(True,False)}.items():
        if key in c.mechanism_options and c.mechanism_options[key] not in allowed:
            raise ValueError(f'Invalid explicit mechanism option {key}')
    if 15 in codes and 'intellect' not in actor.panel:
        raise UnsupportedMechanism('Effect 15 requires explicit intellect panel; magical attack is not intellect')
    if 15 in codes and (not isinstance(actor.panel['intellect'], (int,float)) or actor.panel['intellect'] < 0):
        raise ValueError('Invalid explicit intellect panel')


def select_additional(c,mode,count,actor,pool,primary,ally,skill):
    if mode == 5:
        rear=[u for u in pool if u.position >= 3] or pool
        return rear if len(rear)<=count else c.rng.sample(rear,count)
    if mode == 6:
        # 61006201 explicitly says current HP; the other source rows say HP%.
        key=(lambda u:u.hp) if skill and skill.skill_id==61006201 else (lambda u:Fraction(u.hp,u.max_hp))
        if not ally and skill and skill.skill_id in (61008501,61108501):
            marked=[u for u in pool if c.has_buff(u,206)]
            if marked:
                if len(marked)>count:return c.rng.sample(marked,count)
                return marked+c._highest([u for u in pool if u not in marked],count-len(marked),key)
        return c._highest(pool,count,key)
    if mode in (8,9):
        return c._highest(pool,count,lambda u:u.physical_defence*(1 if mode==8 else -1))
    if mode==10:
        return c._highest(pool,count,lambda u:u.physical_attack)
    if mode==12:
        return c._highest(pool,count,lambda u:u.magical_attack)
    if mode==14:
        marked=[u for u in pool if not ally and c.has_buff(u,206)]
        if marked:
            # zaodianshui04/g2/a17 targets marked Hela rather than the
            # lower-HP Nono. Keep the normal absolute-HP order otherwise.
            return c._highest(marked,min(count,len(marked)),lambda u:-u.hp)+c._highest(
                [u for u in pool if u not in marked],max(0,count-len(marked)),lambda u:-u.hp)
        return c._highest(pool,count,lambda u:-u.hp)
    if mode==18:
        count=min(4,count+c.active_counts.get((actor.uid,'dragon_targets'),0))
        return c.rng.sample(pool,min(len(pool),count))
    if mode==30:
        return c._highest([u for u in pool if u.race==0],count,lambda u:max(u.physical_attack,u.magical_attack))
    if mode==31:
        return c._highest([u for u in pool if not c.has_buff(u,167)] or pool,count,lambda u:-u.hp)
    if mode == 19:
        include_self=skill is not None and skill.skill_id==66000083
        return [u for u in pool if u.race==4 and (include_self or u.uid!=actor.uid)][:count]
    if mode == 32:
        return c._highest([u for u in pool if u.race==4 and u.uid!=actor.uid],count,
                          lambda u:max(u.physical_attack,u.magical_attack))
    if mode==34:
        front=[u for u in pool if u.position<3] or pool
        return c._highest(front,count,lambda u:-u.hp)
    if mode==35:
        return [u for u in pool if u.race==4 and u.uid!=actor.uid][:count]
    if mode==38:
        return c._highest([u for u in pool if u.race==1],count,lambda u:u.panel['physical_attack'])
    if mode==39:
        return [u for u in pool if u.race==1 and u.uid!=actor.uid and u.position%3==actor.position%3][:count]
    if mode==43:
        confused=[u for u in pool if any(c.has_buff(u,b) for b in (36,171,217))]
        return (confused[:count] if confused else [primary or c.ordinary_primary(actor,pool)][:count])
    if mode==44:
        return [u for u in pool if u is actor or u.race==4 and u.position//3==actor.position//3][:count]
    if mode==46:
        return c._highest([u for u in pool if u is not actor],count,lambda u:u.physical_attack)
    raise AssertionError(mode)


def pct(amount,percent):
    return int(Decimal(str(amount))*Decimal(str(percent))/100)


def apply_status(c,actor,target,values,skill,level,chance_bonus=0):
    from combat import Buff,scaled
    bid,base,growth,duration,chance=values
    chance=float(chance)+chance_bonus
    for hook in c.hooks:
        fn=getattr(hook,'status_probability',None)
        if fn:chance=fn(c,actor,target,int(bid),chance,skill)
    if c.rng.random()*100 < min(100,max(0,chance)):
        # Buff194's source row explicitly marks its percentage as fixed
        # ("写死暂定"); do not apply ordinary active-skill level growth.
        value=float(base) if int(bid)==194 else scaled((base,growth),level)
        if int(bid)==201 and skill.skill_id==61120091 and (c.current_action or {}).get('flower_dual_blessing'):
            # Same-target report pairs support two 70% shares in one status per recipient.
            value*=2
        if int(bid)==59 and c.mechanism_options.get('fish59_integer_percent',False):
            value=int(value)
        if skill.skill_id==66000023 and int(bid)==46:value=min(50,value)
        if int(bid)==175 and skill.skill_id in (61011001,61111001):
            from combat import Buff
            mods={s:pct(getattr(target,s),value) for s in ('physical_defence','magical_defence')}
            applied=c.apply_buff(target,Buff(175,actor.uid,skill.skill_id,int(duration),value,stat_modifiers={s:-v for s,v in mods.items()}))
            if applied:c.apply_buff(actor,Buff(174,actor.uid,skill.skill_id,int(duration),value,stat_modifiers=mods))
            return applied
        if int(bid)==214:
            source_attack=actor.panel['magical_attack']
            return c.apply_buff(target,Buff(
                int(bid),actor.uid,skill.skill_id,int(duration),value,
                source_attack=source_attack,
            ))
        return c.apply_buff(target,Buff(int(bid),actor.uid,skill.skill_id,int(duration),
                                       value,source_attack=actor.panel['physical_attack']))
    return False


def hit_context(c,actor,target,skill,kind,primary):
    from combat import UnsupportedMechanism
    context=dict(critical=None,ignore_defence_percent=0,ignore_parry=False,
                 attack_multiplier=1,damage_multiplier=1)
    action=c.current_action or {}
    if action.get('target_index') in action.get('half_primary_target_indices',()):
        context['primary_damage_multiplier']=.5
    added=action.get('blessing_added_target')
    if added is not None and action.get('target_index')==added[0]:
        context['target_source_buff']=added[1]
        context['target_source_actor_uid']=actor.uid
    if primary==53:context['ignore_defence_percent']=30
    if primary==49:context['ignore_defence_percent']=50
    if primary==39:
        lost=Fraction(100-actor.hp*100//actor.max_hp,100)
        context.update(attack_multiplier=1+2*float(lost),ignore_defence_percent=100*float(lost))
    if primary==18 and not target.is_male:context['damage_multiplier']=1.5
    if primary==20 and target.race==0:
        context['damage_multiplier']=1.5
        # Lv150 Dawn reads 75% of current human defence on this hit.
        # Keep the later Weakness debuff and the non-human hit path unchanged.
        if skill.skill_id==61105301 and actor.template_id==80600011 and action.get('skill_level')==150:
            context['ignore_defence_percent']=25
    if primary==68:
        # +100 percentage points still competes with the victim's resistance.
        if target.hp==target.max_hp:context['critical_chance_bonus']=100
        context['ignore_parry']=any(c.has_buff(target,b) for b in (36,171,217))
    for hook in c.hooks:
        fn=getattr(hook,'resolve_hit_context',None)
        if fn:
            updated=fn(c,actor,target,skill.skill_id,kind,context)
            if updated is not None:context=updated
    if any(code==26 for code,_ in skill.enemy_effects) and context.get('critical') is None:
        raise UnsupportedMechanism('Effect 26 critical outcome remains unresolved')
    return context


def apply_effects(c,actor,target,effects,skill,level,mod_snapshot):
    from combat import Buff,scaled,UnsupportedMechanism
    if any(code in c.extra_effects for code, _ in effects):
        for rewrite in c.effect_rewriters:
            effects = rewrite(c, actor, target, skill, level, effects)
    codes={code for code,_ in effects}
    primary=next((code for code,_ in effects if code in PRIMARY_DAMAGE),None)
    fish_break = (next((v for code,v in effects if code==3 and int(v[0])==52),None)
                  if skill.skill_id==61106101 and primary is not None else None)
    bonus=sum(int(scaled(v,level)) for code,v in effects if code in (5,12))
    flat_bonus=bonus
    if 1550 in codes:
        # Explicit, narrowly source-checked interpretation; never rewrite the
        # raw catalogue, infer this policy for another opcode, or hide it.
        c.emit('candidate_mechanism',actor,target,skill.skill_id,
               mechanism='wolf_1550_policy',raw_segment='1550|130',
               interpreted_segment='(no damage effect)',amount=0,
               source_id=skill.source_id,server_verified=False)
    intellect_floor=0
    for code,values in effects:
        if code!=15:continue
        if skill.skill_id in (61005001,61105001):
            # User-approved Sasha rule, supported by retained report prefixes.
            # It is a pre-fluctuation floor, not another additive component.
            coefficient=Decimal(str(values[0]))+Decimal(str(values[1]))*level
            intellect_floor=max(intellect_floor,int(Decimal(str(actor.panel['intellect']))*coefficient))
        else:
            bonus+=pct(actor.panel['intellect'],scaled(values,level))
    fish=sum(scaled(v,level) for code,v in effects if code==19)
    fish*=sum(u.race==1 for u in c.alive(actor.camp))
    hp_before=target.hp
    purification_positive=(any(b.duration>0 and c.positive(b) for b in target.buffs)
                           if 60 in codes else None)
    negative_count=c.negative_count(target,include_seal=True)
    old_context=c.current_hit_context
    kind='healing' if codes & {2,74} else 'magical' if primary in (4,9,18) else 'physical' if primary else 'true'
    if primary==39:
        old_hp=actor.hp
        # 67 retained casts (31 non-full-HP discriminators) leave 1% of
        # current HP, not max HP. The cost precedes attack amplification.
        actor.hp=min(actor.hp,max(1,actor.hp//100))
        c.emit('health_cost',actor,actor,skill.skill_id,hp_before=old_hp,hp_after=actor.hp)
    needs_context=primary is not None or bool(codes & {2,13,16,26,54,59,66,67,71,74})
    c.current_hit_context=hit_context(c,actor,target,skill,kind,primary) if needs_context else {}
    if intellect_floor:
        c.current_hit_context['intellect_damage_floor']=intellect_floor
    try:
        # Non-primary damage effects form components of the SAME attack.
        extras=[]
        for code,v in effects:
            amount=0;extra_kind='true'
            if code==13:
                # Selection reports retain Justice's fixed 2000 holy part,
                # including noncritical hits. The raw995 is not its growth.
                amount=int(Decimal(v[0])) if skill.skill_id in (61100201,61200201) else int(scaled(v,level))
            elif code==16:
                if skill.skill_id!=61205501 or c.mechanism_options.get('61205501_health_cost',False):
                    if primary is not None and skill.skill_id in (61006201,61107001):
                        # 86 two-part Ed/Dragon records distinguish post-primary
                        # HP and floor(HP/100). Resolve after primary mitigation,
                        # without emitting a second attack or reaction callback.
                        extras.append(dict(kind='current_hp',amount=0,
                                           current_hp_after_primary_percent=scaled(v,level)))
                        continue
                    amount=(int(Decimal(hp_before//100)*Decimal(str(scaled(v,level))))
                            if skill.skill_id==66000030 else pct(hp_before,scaled(v,level)))
                    extra_kind='current_hp'
            elif code==26 and c.current_hit_context.get('critical'):
                amount=pct(target.max_hp,scaled(v,level));extra_kind='max_hp'
                if skill.skill_id in (61100201,61200201):
                    # Retained Justice critical rows carry their own uniform
                    # roll and critical multiplier, then the same skill5 flat.
                    amount=int(amount*c.rng.uniform(.95,1.05))
                    amount=int(amount*c.current_hit_context.get('critical_multiplier',2))+flat_bonus
            elif code==54 or code==59 and target.race==3:
                amount=pct(target.max_hp,scaled(v,level));extra_kind='max_hp'
                if skill.skill_id in (61120051,61220051,66000103,61121011,61221011):
                    amount=int(Decimal(target.max_hp//100)*Decimal(str(scaled(v,level))))
            elif code==67:
                amount=int(Decimal(actor.panel['physical_defence'])*Decimal(str(scaled(v,level))));extra_kind='physical'
            elif code==71:
                amount=max(1,int(Decimal(target.max_hp//100)*Decimal(str(min(40,negative_count*scaled(v,level))))));extra_kind='max_hp'
            elif code==66:
                divisor=max(1,c.current_action.get('enemy_target_count',1))
                # One shared burst snapshots its attack as well as its divisor.
                # A sealed target dying can end Vik's aura between recipients;
                # kill-triggered damage bonuses still resolve independently.
                attack=c.current_action.setdefault('shared_magic_attack',actor.magical_attack)
                amount=int((attack//100)*scaled(v,level))//divisor
            if amount:extras.append(dict(kind=extra_kind,amount=amount,**({'source_effect':67} if code==67 else {})))
        if primary is not None:
            for hook in c.hooks:
                fn=getattr(hook,'additional_components',None)
                if fn:extras.extend(fn(c,actor,target,skill,level) or [])
        if skill.skill_id==66000103:
            # Raw Son artifact records place193 before the HP percentage.
            # The mark is still resolved after a lethal primary; the HP part
            # exists only if the primary plus mark leave the target alive.
            extras.sort(key=lambda p:0 if p.get('source_buff')==193 else 1)
        if skill.skill_id in (66000103,61021011,61121011,61221011,61120051):
            # Retained normal casts also omit HP damage after a lethal
            # physical part. Their existing mark order remains independent.
            for part in extras:
                if part['kind']=='max_hp':part['requires_surviving_prior_components']=True
        damage_done=False
        for code,values in effects:
            if not actor.alive:break
            # Hela's post-hit transfer uses the killed primary's statuses.
            # Other status/damage components still cannot affect a dead unit.
            if not target.alive and code not in (31,52):continue
            if code in (5,12,15,19,1550):continue
            if fish_break is not None and code==3 and int(values[0])==52:continue
            if code in PRIMARY_DAMAGE:
                multiplier=scaled(values,level)
                amount=c.candidate_damage(actor,target,kind,multiplier,bonus)
                # Fish reads pre-hit defence. A lethal strike can still log its
                # break, so apply it after pricing damage but before HP loss.
                if fish_break is not None:
                    apply_status(c,actor,target,fish_break,skill,level,fish)
                flat_addition=min(amount,float(Decimal(flat_bonus)*Decimal(str(c.current_hit_context.get('damage_multiplier',1)))))
                parts=[dict(kind=kind,amount=amount,flat_skill_addition=flat_addition),*extras]
                for ec,ev in effects:
                    if ec==60:
                        shield=max((b.value for b in c.active_buffs(target) if b.buff_id==147),default=0)
                        extra=c._purification(actor,target,ev,skill,level,had_positive=purification_positive,defer_dispel=True)
                        parts[0]['dispel_positive_after_component']=True
                        if extra:parts.append(dict(kind='true',amount=extra,source_effect=60,
                                                   retained_shield_percent=shield,
                                                   requires_surviving_prior_components=True))
                actual=c.damage(actor,target,sum(p['amount'] for p in parts),skill.skill_id,kind,
                                components=parts)
                damage_done=True
                if code in (43,47,61):c.heal(actor,actor,pct(actual,30),skill.skill_id)
                if code==50:
                    healed=c.heal(actor,actor,pct(actual,50),skill.skill_id)
                    if healed:
                        c.apply_buff(actor,Buff(177,actor.uid,skill.skill_id,2,value=healed*100/actor.max_hp))
                if code==57 and not target.alive:
                    rear=next((u for u in c.alive(target.camp) if u.position==target.position+3),None)
                    if rear:
                        settled=next(e for e in reversed(c.events) if e.kind=='damage' and e.target_uid==target.uid and e.skill_id==skill.skill_id)
                        c.damage(actor,rear,pct(settled.payload['requested'],float(values[2])),skill.skill_id,'physical')
                if code==27 and not target.alive:
                    c.current_action['kill_chain']=True
            elif code==28:
                next(h for h in c.hooks if hasattr(h,'arthur_opening')).arthur_opening(c,actor,target,scaled(values,level))
            elif code in (13,16,26,54,59,66,67,71):
                if primary is None and not damage_done and extras:
                    c.damage(actor,target,sum(p['amount'] for p in extras),skill.skill_id,
                             extras[0]['kind'],components=extras if len(extras)>1 else None)
                    damage_done=True
            elif code==2:
                attack = actor.magical_attack
                if skill.skill_id == 61105601 and c.current_action is not None:
                    # A preceding self-heal grants 64 for later casts, not later recipients.
                    attack = c.current_action.setdefault('nono_heal_attack', attack)
                amount=int(Decimal(attack)*Decimal(str(scaled(values,level))))
                if c.current_hit_context.get('critical'):
                    amount=int(amount*c.current_hit_context.get('critical_multiplier',2))
                healing=amount+bonus
                if skill.skill_id in (61114001,61214001):
                    # Consecutive uncapped Lulu heals in both captured human
                    # teams retain the nearest hundred before aura modifiers.
                    healing=int(round(healing/100))*100
                c.heal(actor,target,healing,skill.skill_id)
            elif code==23:
                rate=scaled(values,level)
                if skill.skill_id==66000023:rate=min(10,rate)
                basis=actor.max_hp if skill.skill_id==66000023 else target.max_hp
                c.heal(actor,target,pct(basis,rate),skill.skill_id)
            elif code in (3,41,56,65,70):
                # Bone King's source skill grants speed only to undead; the
                # generic source selector2|6 alone does not encode that filter.
                if skill.skill_id==66000056 and int(values[0])==138 and target.race!=4:continue
                if int(values[0])==188 and target.race!=4:continue
                if code==56:
                    if target.uid in c.current_action.get('vik_sealed_targets',()):continue
                    required=4 if skill.skill_id in (61120011,61220011) else 5
                    if sum(u.profession==2 for u in c.alive(actor.camp))<required:continue
                if code==41 and target.race!=3:continue
                if code==65:
                    c.dispel(target,positive=False,actor=actor,skill_id=skill.skill_id)
                    if target.race!=4:continue
                if code==70 and target.race!=4:continue
                apply_status(c,actor,target,values,skill,level,fish if int(values[0]) in (6,52) else 0)
            elif code==31:
                if skill.skill_id not in (61007101,61107101,61207101):
                    raise UnsupportedMechanism('Copied active is only defined for Mammon')
                origin=c.current_action.get('origin') or {}
                if isinstance(origin,dict) and origin.get('mechanism')=='copy_active':
                    raise UnsupportedMechanism('Recursive active copying is unresolved')
                hero=c.catalog.hero_for(target)
                if not hero.active_ids:
                    c.emit('copy_active_skipped',actor,target,skill.skill_id,reason='no_active_skill')
                    continue
                if len(hero.active_ids)==1:
                    copied=hero.active_ids[0]
                else:
                    copied=c.active_id_override(target)
                    if copied not in hero.active_ids:
                        raise UnsupportedMechanism('Copied target requires an unambiguous active skill')
                c.queue_followup(actor.uid,copied,skill_level=level,
                                 origin={'kind':'reaction','mechanism':'copy_active','copied_from_uid':target.uid})
            elif code==21:
                next(h for h in c.hooks if hasattr(h,'glutton_dispel')).glutton_dispel(c,actor,target,skill,scaled(values,level))
            elif code==36:
                c.dispel(target,positive=True,actor=actor,skill_id=skill.skill_id)
            elif code==45:
                next(h for h in c.hooks if hasattr(h,'singer_dispel')).singer_dispel(c,actor,target,skill,level,values)
            elif code==37:
                removed=c.dispel(target,positive=False,actor=actor,skill_id=skill.skill_id)
                c.heal(actor,target,pct(target.max_hp,scaled(values,level)*len(removed)),skill.skill_id)
            elif code==74:
                removed=c.dispel(target,positive=False,actor=actor,skill_id=skill.skill_id)
                if removed:
                    # Uncapped Clown cleanses follow the panel even while a
                    # seal gives its attacks Vik's temporary magic bonus.
                    # 195 uncapped retained heals: divide the source panel
                    # before multiplying the per-dispel percentage and count.
                    amount=int((actor.panel['magical_attack']//100)*scaled(values,level)*len(removed))
                    if c.current_hit_context.get('critical'):
                        amount=int(amount*c.current_hit_context.get('critical_multiplier',2))
                    c.heal(actor,target,amount,skill.skill_id)
            elif code in (40,62):
                removed=c.dispel(target,positive=True,actor=actor,skill_id=skill.skill_id) if code==40 else c.steal(target,actor=actor,skill_id=skill.skill_id)
                if code==40:
                    recipients=c._highest(c.alive(actor.camp),1,lambda u:max(u.physical_attack,u.magical_attack))
                else:
                    recipients=[u for u in c.alive(actor.camp) if u is not actor and u.race==3 and u.position%3==actor.position%3]
                for recipient in recipients:
                    for buff in removed:c.apply_buff(recipient,buff)
            elif code==42:
                rate=scaled(values,level)
                # Bone King's two stolen stats count as separate buffs for Rage targeting.
                transfers=[(stat,gain,loss,pct(getattr(target,stat),rate))
                           for stat,gain,loss in (('physical_defence',139,141),('magical_defence',140,142))]
                for stat,gain,loss,n in transfers:
                    c.apply_buff(target,Buff(loss,actor.uid,skill.skill_id,3,rate,stat_modifiers={stat:-n}))
                    c.apply_buff(actor,Buff(gain,actor.uid,skill.skill_id,3,rate,stat_modifiers={stat:n}))
            elif code==52:c._hela(actor,target,skill,level)
            elif code==55:
                # Freeze the donor's attack at extraction. A pre-existing218
                # blessing is included; the saved183 amount never recomputes.
                taken=pct(target.magical_attack,scaled(values,level))
                accrued=c.current_action.get('magic_extracted',0)
                gained=min(taken,max(0,3*actor.panel['magical_attack']-accrued))
                c.current_action['magic_extracted']=accrued+gained
                c.apply_buff(target,Buff(183,actor.uid,skill.skill_id,2,taken,stat_modifiers={'magical_attack':-taken}))
                c.apply_buff(actor,Buff(184,actor.uid,skill.skill_id,2,accrued+gained,stat_modifiers={'magical_attack':accrued+gained}))
            elif code==60:
                if primary is None:
                    amount=c._purification(actor,target,values,skill,level)
                    if amount:c.damage(actor,target,amount,skill.skill_id,'true')
            elif code==63:
                source=c.units.get(c.current_action.get('negative_copy_source'))
                #The artifact is reached through a nonempty cross-unit copy.
                #Use the live source: first-target DOT additions can be copied
                #again onto the second target in the same action (217d3280...).
                if source is None or source is target or not source.alive:continue
                current=copy.deepcopy(c.negative_buffs(source,include_seal=True))
                if not current:continue
                for buff in current:
                    # A Mod copy starts with the source's remaining lifetime.
                    # Hela later refreshes this copied instance to that lifetime,
                    # not to the original caster's longer duration (3ea299/g3).
                    buff.initial_duration=buff.duration
                    # The transferred old status remains on the recipient; Mod's
                    # separately applied artifact DoTs can still be intercepted.
                    c.current_action['copying_existing_negative']=True
                    try:c.apply_buff(target,buff)
                    finally:c.current_action.pop('copying_existing_negative',None)
                for hook in c.hooks:
                    fn=getattr(hook,'after_negative_copy',None)
                    if fn:fn(c,actor,target,source,skill)
            elif code==69:
                amount=pct(target.max_hp,scaled(values,level))
                # Split the raw pool first. Each recipient then applies healing modifiers once.
                own=min(amount,target.max_hp-target.hp)
                overflow=amount-own
                c.heal(actor,target,own,skill.skill_id)
                others=c._highest([u for u in c.alive(actor.camp) if u is not actor],2,lambda u:-Fraction(u.hp,u.max_hp))
                if others:
                    for other in others:c.heal(actor,other,pct(overflow,80)//len(others),skill.skill_id)
            elif code==72 and negative_count>=scaled(values,level):
                c.apply_buff(target,Buff(6,actor.uid,skill.skill_id,2))
            elif code==73:
                bid,base,growth,duration,chance=values
                basis=target.speed
                if skill.skill_id==66000107:
                    for hook in c.hooks:
                        fn=getattr(hook,'clown_speed_theft_basis',None)
                        if fn:
                            basis=fn(c,target)
                            break
                stolen=pct(basis,scaled((base,growth),level))
                if c.rng.random()*100<float(chance):
                    landed=c.apply_buff(target,Buff(int(bid),actor.uid,skill.skill_id,int(duration),stolen,stat_modifiers={'speed':-stolen}))
                    policy=c.mechanism_options.get('effect73_gain_policy','selected_targets')
                    if policy not in ('selected_targets','landed_targets'):
                        raise ValueError('Unknown effect73_gain_policy')
                    # Oct-25 regular-plane reports grant 221 even when 222 is
                    # resisted. Nov-15 selection reports grant no 221 on two
                    # immune columns, but do grant it on a non-immune column.
                    # Keep the selection branch explicit in its input version.
                    if landed or policy=='selected_targets':
                        c.current_action.setdefault('speed_stolen',[]).append(stolen)
            elif code==99:
                bid,base,growth,duration,chance,chance_growth=values
                policy=c.mechanism_options.get('effect99_chance_policy','config')
                if policy=='config':chance=scaled((chance,chance_growth),level)
                elif policy=='base_description':
                    chance=10+scaled(('3','.03'),level)*sum(u.race==1 for u in c.alive())
                else:raise ValueError('effect99_chance_policy must be config or base_description')
                apply_status(c,actor,target,(bid,base,growth,duration,chance),skill,level)
            elif code in c.extra_effects:
                c.extra_effects[code][1].apply_extra_effect(c, actor, target, skill, level,
                                                           code, values, hp_before)
    finally:c.current_hit_context=old_context


def execute_active(c,actor_uid,skill_id,*,skill_level,primary_uid=None,active_only=False,origin=None):
    from combat import Buff,UnsupportedMechanism
    actor=c.units[actor_uid];skill=c.catalog.skill(skill_id)
    missing=c.preflight(actor,skill,skill_level,active_only=active_only)
    primary=c.units.get(primary_uid) if primary_uid is not None else None
    if primary_uid is not None and (primary is None or not primary.alive or primary.camp==actor.camp):
        raise ValueError('primary_uid must name a living enemy ordinary primary')
    start=len(c.events);previous=c.current_action
    c.current_action=dict(actor_uid=actor_uid,skill_id=skill_id,skill_level=skill_level,
                          origin=copy.deepcopy(origin) if origin is not None else {'kind':'opening' if skill.activation_type==5 else 'ordinary'})
    try:
        if missing:c.emit('unresolved_passives',actor,None,skill_id,requirements=list(missing))
        for hook in c.hooks:
            fn=getattr(hook,'before_action',None)
            if fn:fn(c,actor,skill)
        if not actor.alive:
            return tuple(c.events[start:])
        c.current_action['before_action_dispatched']=True
        c.emit('action_start',actor,None,skill_id,skill_level=skill_level,level=skill_level,
               active_only=active_only,primary_uid=primary_uid,ordinary_primary_uid=primary_uid,
               origin=c.current_action['origin'])
        # Hiller's attack and Justice/King critical buffs apply before the
        # first hit. The fox ancestor blessing also resolves before the
        # caster's own hit (rank-9/g3 action 6), while still reaching the allies.
        precast=tuple((code,v) for code,v in skill.ally_effects
                      if code==3 and (skill_id==61100301 and int(v[0])==25
                          or skill_id in (61100201,61200201) and int(v[0])==17
                          or skill_id in (61109001,61009001) and int(v[0])==130
                          or skill_id==61120031 and int(v[0]) in (17,18)
                          or skill_id in (61004801,61104801,61204801)
                             and int(v[0]) in (40,41,75,76)))
        if precast:c._apply(actor,actor,precast,skill,skill_level,[])
        if skill_id == 66000044:
            # A self-applied 117 can add a recipient to this opening's 118.
            blessed = c.select_targets(skill.ally_target,actor,c.alive(actor.camp),ally=True,skill=skill)
            c.emit('targets',actor,None,skill_id,side='ally',hit=1,target_uids=[u.uid for u in blessed])
            for target in blessed:
                c._apply(actor,target,skill.ally_effects,skill,skill_level,[])
        if any(code==55 for code,_ in skill.ally_effects):
            c.apply_buff(actor,Buff(152,actor.uid,skill_id,1))
        enemies=[u for u in c.alive() if u.camp!=actor.camp]
        primary=primary if primary and primary.alive else c.ordinary_primary(actor,enemies)
        snapshot=[]
        if any(code==63 for code,_ in skill.enemy_effects) and enemies:
            source=c._highest(enemies,1,lambda u:c.negative_count(u,include_seal=True))[0]
            snapshot=copy.deepcopy(c.negative_buffs(source,include_seal=True))
            c.current_action['negative_copy_source']=source.uid
        codes={code for code,_ in skill.enemy_effects}
        hits=3 if 7 in codes else 2 if codes & {6,9} else 1
        if 58 in codes:
            initial=c.mechanism_options.get('effect58_initial_hit',1)
            if initial not in (0,1):raise ValueError('effect58_initial_hit must be 0 or 1')
            hits=max(1,initial+sum(u.race==1 for u in c.alive(actor.camp)))
        for hook in c.hooks:
            fn=getattr(hook,'hit_count_bonus',None)
            if fn:hits+=fn(c,actor,skill)
        if not isinstance(hits,int) or hits<1:raise ValueError('Hit count must be a positive integer')
        hit=0
        while hit<hits and actor.alive:
            for hook in c.hooks:
                fn=getattr(hook,'before_hit',None)
                if fn:fn(c,actor,skill,hit+1)
            if not actor.alive:break
            enemies=[u for u in c.alive() if u.camp!=actor.camp]
            if primary is not None and not primary.alive:primary=c.ordinary_primary(actor,enemies)
            c.current_action['half_primary_target_indices']=[]
            selected=c.select_targets(skill.enemy_target,actor,enemies,primary=primary,skill=skill)
            c.current_action.update(hit=hit+1,enemy_target_count=len(selected),kill_chain=False)
            c.emit('targets',actor,None,skill_id,side='enemy',hit=hit+1,
                   ordinary_primary_uid=primary.uid if primary else None,target_uids=[u.uid for u in selected])
            for target_index,target in enumerate(selected):
                c.current_action['target_index']=target_index
                effects=tuple(e for e in skill.enemy_effects if skill_id!=61113001 or e[0]!=52)
                if actor.alive:c._apply(actor,target,effects,skill,skill_level,snapshot)
            c.current_action.pop('target_index',None)
            if skill_id==61113001:
                #0e25df/g3 multi-target hits and c0f6c9/g3 repeated target
                #exclude applying the first hit's copied defence reductions
                #to the second hit. Settle damage before52 refresh/copy.
                for target in selected:
                    c._apply(actor,target,tuple(e for e in skill.enemy_effects if e[0]==52),skill,skill_level,snapshot)
            if c.current_action.get('kill_chain'):
                # Each continuation needs a fresh kill; maximum is bounded by live targets.
                if any(u.camp!=actor.camp for u in c.alive()):hits+=1
            if 58 in codes and actor.alive:
                for code,values in skill.ally_effects:
                    if code==3 and int(values[0])==194:apply_status(c,actor,actor,values,skill,skill_level)
            for hook in c.hooks:
                fn=getattr(hook,'after_hit',None)
                if fn:fn(c,actor,skill,hit+1)
            #Seven clean first-round Golden Dragon attacks stop after their
            #first damaging hit applies Lust's charm to the attacker.
            if skill_id==61106301 and c.has_buff(actor,122):
                c.emit('combo_interrupted',actor,None,skill_id,buff_id=122)
                break
            hit+=1
            if hit>128:raise UnsupportedMechanism('Active hit chain limit reached')
        if skill.enemy_target[0]==18:
            key=(actor.uid,'dragon_targets');c.active_counts[key]=min(2,c.active_counts.get(key,0)+1)
        if skill_id != 66000044:
            # Confused Bone Dragon grants its opening speed buff to the other camp.
            ally_camp=3-actor.camp if skill_id==66000056 and c.has_buff(actor,36) else actor.camp
            selected=c.select_targets(skill.ally_target,actor,c.alive(ally_camp),ally=True,skill=skill) if actor.alive else []
            if skill_id==61120091:
                c.current_action['flower_dual_blessing']=len(selected)==2
            c.emit('targets',actor,None,skill_id,side='ally',hit=1,target_uids=[u.uid for u in selected])
            ally_effects=tuple((code,v) for code,v in skill.ally_effects
                              if not (58 in codes and code==3 and int(v[0])==194))
            for target in selected:
                if not actor.alive:break
                effects=ally_effects if target is not actor else tuple(
                    e for e in ally_effects if e not in precast)
                c._apply(actor,target,effects,skill,skill_level,snapshot)
        stolen=c.current_action.get('speed_stolen') or ()
        if stolen:
            allies=c.alive(actor.camp)
            for ally in allies:
                for amount in stolen:
                    share=amount//len(allies)
                    if share:
                        c.apply_buff(ally,Buff(221,actor.uid,skill_id,1,share,stat_modifiers={'speed':share}))
        c.emit('action_end',actor,None,skill_id,active_only=active_only,level=skill_level,
               primary_uid=primary_uid,origin=c.current_action['origin'])
        return tuple(c.events[start:])
    finally:c.current_action=previous
