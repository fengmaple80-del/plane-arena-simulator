"""Abilities first required by the eight previously unscorable zone-one reports."""
from collections import Counter
from combat import scaled

SNAKE,WOLF,ANGEL,CURSE,SMITH=80600070,80600032,80600007,80600063,80500006

class PointsMissing:
    heroes={SNAKE,WOLF,ANGEL,CURSE,SMITH}
    supported_buffs={44,67,88,101,102,103,104,167,176,179}
    supported_passives={73,101,142,143,152}

    def __init__(self,catalog,common,units):
        self.catalog=catalog;self.common=common
        self.awake={u.uid for u in units if u.template_id in self.heroes and catalog.hero_for(u).awakening}
        self.demons=Counter(u.camp for u in units if u.race==3)
        self.used=Counter();self.gained=Counter()

    def owners(self,b,camp,tid):
        return [u for u in b.alive(camp) if u.template_id==tid and u.uid in self.awake]

    def rate(self,sid,code,level):
        return scaled(next(v for k,v in self.catalog.skill(sid).passives if k==code),level)

    def stat_bonuses(self,b,u):
        return {'speed':sum(int(v.panel['speed']*self.rate(61107501,101,self.common.skill_level)/100) for v in self.owners(b,u.camp,WOLF))}

    def critical_modifiers(self,b,a,t):
        return {'resistance':-sum(v.value*v.stacks for v in b.active_buffs(t) if v.buff_id==88)}

    def before_damage(self,b,a,t,amount,sid,kind):
        if a.camp!=t.camp and a.race==3 and kind in ('physical','magical') and t.race in (0,1,4) and self.owners(b,a.camp,CURSE):
            pair=self.catalog.tables['Skill'][61110601]['最高'].split('#')[0 if t.race==1 else 1].split('|')
            return int(amount*(1+scaled(pair,self.common.skill_level)/100))
        return amount

    def devour(self,b,t):
        for u in self.owners(b,3-t.camp,SNAKE):
            if self.used[u.uid]>=self.demons[u.camp]//3:continue
            self.used[u.uid]+=1;t.hp=0
            b.emit('devour',u,t,61111001)
            b.heal(u,u,int(u.max_hp*self.rate(61111001,152,self.common.skill_level)/100),61111001)
            return u
        return None

    def on_event(self,b,e):
        if e.kind!='buff' or e.payload['buff_id'] not in (68,210):return
        t=b.units[e.target_uid]
        if not t.alive or (b.current_damage or {}).get('resolving_lethal_uid')==t.uid:return
        source=self.devour(b,t)
        if source:b.emit('death',source,t,61111001,lethal_component=None,target_source_buff=None)

    def after_lethal_protection(self,b,t,events):
        if t.alive and any(e.target_uid==t.uid and (e.kind in ('revive','rebirth') or
                e.kind=='buff' and e.payload['buff_id'] in (68,210) or
                e.kind=='rescue' and t.hp==1) for e in events):
            source=self.devour(b,t)
            if source:b.current_damage['devour_kill']=(source.uid,t.uid)

    def bleed(self,b,t,v,*,burst=False):
        source=b.units[v.source_uid]
        b.damage(source,t,int(source.physical_attack*(2 if burst else v.value/100)),v.source_skill,'dot')
        if burst:return
        level=self.common.artifact_level(source,66000067)
        if level is not None:
            base=source.panel['physical_attack'];gain=min(2*base-self.gained[source.uid],int(base*self.rate(66000067,142,level)/100))
            self.gained[source.uid]+=gain;self.common.growth[source.uid,'physical_attack']+=gain;self.common.refresh_stats(b)
        v.hits_remaining=(4 if v.hits_remaining is None else v.hits_remaining)-1
        if v.hits_remaining==0 and v in t.buffs:
            t.buffs.remove(v)
            if t.alive:self.bleed(b,t,v,burst=True)

    def before_action(self,b,u,skill):
        if (b.current_action or {}).get('origin',{}).get('kind')=='opening':return
        for v in list(b.active_buffs(u)):
            if u.alive and v.buff_id==167:self.bleed(b,u,v)

    def after_damage(self,b,e):
        t=b.units[e.target_uid];action=b.current_action or {}
        if (not t.alive or e.actor_uid==t.uid or action.get('actor_uid')!=e.actor_uid or action.get('skill_id')!=e.skill_id
                or e.payload['damage_kind'] in ('dot','transfer','reflection','heal_reversal')):return
        for v in list(b.active_buffs(t)):
            if t.alive and v.buff_id==167:self.bleed(b,t,v)

    def removed(self,b,u,buffs):
        for v in buffs:
            if v.buff_id==167 and u.alive:self.bleed(b,u,v,burst=True)
