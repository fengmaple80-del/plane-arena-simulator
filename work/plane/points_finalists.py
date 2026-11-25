"""Zone-one finals additions: per-source triggers, with report coverage in the batch manifest."""
from collections import Counter
from fractions import Fraction
from combat import Buff,scaled

BLACK,GLUTTON,WIND,KID,SAINT,TYR=80600060,80600012,80600035,80600043,80600038,80600069

class PointsFinalists:
    heroes={BLACK,GLUTTON,WIND,KID,SAINT,TYR}
    supported_buffs={106,107,108,109,112,126,155,156}
    supported_passives={65,104,106,107,137,138,150,151}
    def __init__(self,catalog,common,units):
        self.common=common;self.catalog=catalog;self.count=Counter()
        self.awake={u.uid for u in units if u.template_id in self.heroes and catalog.hero_for(u).awakening}

    def rate(self,sid,code,level):return scaled(next(v for k,v in self.catalog.skill(sid).passives if k==code),level)
    def level(self,u,sid):return self.common.artifact_level(u,sid)
    def owners(self,b,t,camp):return [u for u in b.alive(camp) if u.template_id==t]
    def glutton_dispel(self,b,a,t,skill,value):
        removed=b.dispel(t,positive=True,actor=a,skill_id=skill.skill_id)
        n=min(len(removed),50-self.count[a.uid,'glutton']);self.count[a.uid,'glutton']+=n
        self.common.growth[a.uid,'magical_attack']+=int(n*value);self.common.refresh_stats(b)

    def stat_bonuses(self,b,u):
        if u.race!=0:return {}
        return {'speed':sum(int(v.panel['speed']*self.rate(61110301,138,self.common.skill_level)/100) for v in self.owners(b,BLACK,u.camp) if v.uid in self.awake)}

    def stat_transform(self,b,u,stat,value):
        if stat in ('physical_defence','magical_defence'):
            value*=max(0,1+sum(v.value for v in b.active_buffs(u) if v.buff_id==109)/100)
        return value

    def critical_modifiers(self,b,a,t):
        buffs=b.active_buffs(t)
        return {'resistance':sum(v.value for v in buffs if v.buff_id==107),'damage':sum(v.value for v in buffs if v.buff_id==108)}

    def additional_damage_reduction(self,b,u,kind):
        kid=self.count[u.uid,'kid']*self.rate(61008601,106,self.common.skill_level) if u.template_id==KID else 0
        return min(100,kid)+(100 if kind=='physical' and b.has_buff(u,156) else 0)

    def before_apply_buff(self,b,u,v):
        if b.positive(v) or b.seal_bypasses_immunity(v):return
        if u.race==0 and v.buff_id in (43,65,154) and any(x.uid in self.awake for x in self.owners(b,BLACK,u.camp)):return False
        action=b.current_action
        if v.buff_id in (4,15) or (action or {}).get('copying_existing_negative'):return
        key=(u.uid,(action or {}).get('hit'));blocked=(action or {}).get('saint_blocked',set())
        if key in blocked:return False
        shield=next((x for x in b.active_buffs(u) if x.buff_id==112),None)
        if shield:
            u.buffs.remove(shield)
            if action is not None:action.setdefault('saint_blocked',set()).add(key)
            return False

    def on_lethal(self,b,actor,u,sid):
        if u.template_id!=BLACK or u.alive or self.count[u.uid,'rescue']:return
        if b.current_hit_context.get('bypass_revive'):return
        level=self.level(u,66000064)
        if level is None:return
        self.count[u.uid,'rescue']=1;u.hp=min(u.max_hp,int(u.max_hp*self.rate(66000064,137,level)/100))
        b.apply_buff(u,Buff(156,u.uid,66000064,1,100))
        if actor.alive and actor.camp!=u.camp:b.apply_buff(actor,Buff(155,u.uid,66000064,2))
        b.emit('revive',u,u,66000064,hp_after=u.hp)

    def on_event(self,b,e):
        if e.kind!='dispel' or not e.payload.get('positive'):return
        n=len(e.payload['buff_ids']);u=b.units.get(e.actor_uid)
        if not n or not u:return
        if u.template_id==KID:self.count[u.uid,'kid']+=n
        for guard in b.active_buffs(u):
            if guard.buff_id!=126:continue
            owner=b.units.get(guard.source_uid)
            if not owner:continue
            gained=min(100,(guard.source_attack or 0)+n*guard.value*(2 if u.race==0 else 1))
            guard.source_attack=gained;guard.stat_modifiers={k:int(owner.panel[k]*gained/100) for k in ('physical_attack','magical_attack','physical_defence','magical_defence','speed')}
            self.common.refresh_stats(b)

    def after_damage(self,b,e):
        a=b.units[e.actor_uid];u=b.units[e.target_uid];sid=e.skill_id;d=e.payload
        action=b.current_action or {};direct=(a.camp!=u.camp and action.get('skill_id')==sid and action.get('actor_uid')==a.uid and d.get('damage_kind') not in ('reflection','dot','heal_reversal','transfer'))
        if not direct:return
        physical=sum(p['resolved_amount'] for p in d['components'] if p['kind']=='physical');loss=d['actual_hp_loss']
        if a.alive and u.alive:
            for tid,artifact in ((WIND,66000039),(GLUTTON,66000015)):
                if a.template_id!=tid:continue
                level=self.level(a,artifact)
                if level is None:continue
                pars=next(v for k,v in self.catalog.skill(artifact).passives if k==65)
                if b.rng.random()*100<scaled(pars[:2],level):b.apply_buff(u,Buff(int(pars[2]),a.uid,artifact,int(pars[3]),scaled(pars[4:],level)))
            for owner in self.owners(b,KID,a.camp):
                level=self.level(owner,66000047)
                if level is not None and b.rng.random()*100<self.rate(66000047,107,level)*(2 if a.race==0 else 1):b.dispel(u,positive=True,actor=a,skill_id=66000047)
        if u.template_id==SAINT and u.uid in self.awake and physical and a.alive:
            b.damage(u,a,int(min(loss,physical)*self.rate(61108101,104,self.common.skill_level)/100),61108101,'reflection')
        if a.template_id==TYR and a.alive:
            if a.uid in self.awake and loss:
                ally=b._highest(b.alive(a.camp),1,lambda x:-Fraction(x.hp,x.max_hp))[0]
                b.heal(a,ally,int(loss*self.rate(61110031,151,self.common.skill_level)/100),61110031)
            if sid==66000073:
                level=self.level(a,sid);old=self.count[a.uid,'tyr'];new=min(300,old+self.rate(sid,150,level));self.count[a.uid,'tyr']=new
                self.common.growth[a.uid,'physical_attack']+=int(a.panel['physical_attack']*(new-old)/100);self.common.refresh_stats(b)
        if physical and u.race==0 and self.common.round_index==0 and a.alive and sid!=66000073:
            for owner in self.owners(b,TYR,u.camp):
                level=self.level(owner,66000073)
                if level is not None:b.queue_followup(owner.uid,66000073,skill_level=level,primary_uid=a.uid,origin={'kind':'counter','mechanism':150})
