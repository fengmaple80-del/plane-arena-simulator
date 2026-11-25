"""Plane finalists. Initial team counts persist through deaths; active buffs do not."""
from collections import Counter
from combat import Buff, scaled

ARTHUR,APOLLO,ENVOY=80600018,80600048,80600061
class Finalists:
    heroes={ARTHUR,APOLLO,ENVOY}
    supported_passives={86,115,116,139,140}
    supported_buffs={81,132,133,157}
    no_transfer={4,15,5,6,20,21,36,97,122,155,171,192,193,217,220}

    def __init__(self,catalog,common,units):
        self.common=common;self.catalog=catalog
        self.teams=Counter((u.camp,u.race) for u in units)
        self.awake={u.uid for u in units if u.template_id in self.heroes and catalog.hero_for(u).awakening}
        self.blocks=Counter();self.rebirths=Counter();self.absorptions=Counter();self.redirecting=False

    def rate(self,skill,code,level):
        return scaled(next(v for k,v in self.catalog.skill(skill).passives if k==code),level)

    def hit_count_bonus(self,b,actor,skill):
        return max(0,self.teams[actor.camp,4]-1) if actor.template_id==ARTHUR and skill.skill_id in (61006501,61106501) else 0

    def arthur_opening(self,b,actor,target,rate):
        key=(actor.uid,'physical_attack');base=actor.panel['physical_attack']
        gain=min(2*base,int(target.physical_attack*rate/100))
        self.common.growth[key]+=gain;self.common.refresh_stats(b)
        b.emit('arthur_opening_attack',actor,target,66000026,gain=gain)

    def stat_bonuses(self,b,u):
        if u.template_id!=ARTHUR or u.uid not in self.awake:return {}
        rate=self.rate(61106501,86,self.common.skill_level)
        return {'physical_attack':int(u.panel['physical_attack']*rate*sum(v.race==4 for v in b.alive(u.camp))/100)}

    def apollo_blocks(self,b,actor,u,sid,part):
        action=b.current_action or {};level=self.common.artifact_level(u,66000052)
        if (part['kind']!='physical' or actor.camp==u.camp or level is None or sid==61000001
            or action.get('actor_uid')!=actor.uid or action.get('skill_id')!=sid or action.get('origin',{}).get('kind')!='ordinary'
            or self.catalog.skill(sid).activation_type!=1):return False
        if 'apollo_counter' in b.current_damage:return True
        if self.blocks[u.uid]>=min(6,self.teams[u.camp,1]):return False
        self.blocks[u.uid]+=1;b.current_damage['apollo_counter']=level
        b.emit('apollo_block',u,actor,66000052,used=self.blocks[u.uid]);return True

    def resolve_hit_context(self,b,actor,target,sid,kind,ctx):
        if sid==66000052 and actor.uid in self.awake:ctx['ignore_defence_percent']=self.rate(61109101,115,self.common.skill_level)

    def after_damage(self,b,event):
        level=b.current_damage.pop('apollo_counter',None)
        if level is None:return
        u=b.units[event.target_uid];enemy=b.units[event.actor_uid]
        if not u.alive or not enemy.alive:return
        result=b.execute(u.uid,66000052,skill_level=level,primary_uid=enemy.uid,origin={'kind':'counter'})
        if u.uid in self.awake and any(e.kind=='damage' and e.skill_id==66000052 for e in result):
            rate,step,bid,turns=next(v for k,v in self.catalog.skill(61109101).passives if k==116)
            for ally in b.alive(u.camp):
                if ally.race==1:b.apply_buff(ally,Buff(int(bid),u.uid,61109101,int(turns),scaled((rate,step),self.common.skill_level)))

    def before_apply_buff(self,b,ally,buff):
        if self.redirecting or ally.race!=1 or buff.buff_id in self.no_transfer or b.positive(buff):return
        if (b.current_action or {}).get('copying_existing_negative'):return
        for owner in b.alive(ally.camp):
            if owner is ally or owner.template_id!=ENVOY:continue
            level=self.common.artifact_level(owner,66000065)
            if level is None or self.absorptions[owner.uid]>=30:continue
            from dataclasses import replace
            self.redirecting=True
            try:
                n=min(buff.stacks,30-self.absorptions[owner.uid]);self.absorptions[owner.uid]+=n
                b.apply_buff(owner,replace(buff,stacks=n))
                rate=self.rate(66000065,139,level)
                b.apply_buff(ally,Buff(157,owner.uid,66000065,2,rate,stacks=n,stat_modifiers={k:int(getattr(owner,k)*rate/100) for k in ('physical_attack','magical_attack')}))
                if n<buff.stacks:b.apply_buff(ally,replace(buff,stacks=buff.stacks-n))
            finally:self.redirecting=False
            return False

    def on_lethal(self,b,actor,u,sid):
        if u.alive or u.template_id!=ENVOY or u.uid not in self.awake or self.rebirths[u.uid]>=min(2,self.teams[u.camp,1]//3):return
        self.rebirths[u.uid]+=1;u.buffs[:]=[v for v in u.buffs if b.positive(v)]
        u.hp=int(u.max_hp*self.rate(61110401,140,self.common.skill_level)/100);self.common.refresh_stats(b)
        b.emit('rebirth',u,u,61110401,restored=u.hp,used=self.rebirths[u.uid])
