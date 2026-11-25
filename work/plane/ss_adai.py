"""SS Adai reflects primary damage and the observed Vik artifact appendage."""
from decimal import Decimal
from combat import UnsupportedMechanism,scaled


class SsAdaiMechanics:
    supported_reactive_skills={61004701}
    def __init__(self,catalog):self.catalog=catalog

    def on_event(self,battle,event):
        if event.kind!='damage':return
        target=battle.units.get(event.target_uid);actor=battle.units.get(event.actor_uid)
        if not target or target.template_id!=80500017 or not actor or not actor.alive or actor.camp==target.camp:return
        parts=event.payload.get('components',[])
        primary=sum(p['resolved_amount'] for p in parts if p['kind'] in ('physical','magical'))
        if primary<=0:return
        reflected=[primary]
        # luoshen-selection-05/11 g2/r1/a4; SQLite34f21086 g1/r1/a4:
        # Vik's awakened active has a separate artifact170 max-HP component.
        # Its current producer puts it immediately after magic, without tags.
        # Do not include unrelated percentage/true damage or Judgment193.
        if (actor.template_id==80600080 and event.skill_id==61120011
                and len(parts)>=2 and parts[0]['kind']=='magical'
                and parts[1]['kind']=='max_hp'
                and parts[1].get('source_buff') is None
                and parts[1].get('source_effect') is None
                and parts[1]['resolved_amount']>0):
            reflected.append(parts[1]['resolved_amount'])
        # The tooltip's60% is the base casting rate, not an independent roll.
        # User confirmed reflection is affected by casting. Plane inputs use
        # final100%; retain that contract for component fixtures without it.
        chance=target.panel.get('cast_chance',100)
        if not isinstance(chance,(int,float)) or isinstance(chance,bool) or not 0<=chance<=100:
            raise UnsupportedMechanism('SS Adai requires a final cast percentage in0..100')
        if chance==0 or (chance<100 and battle.rng.random()*100>=chance):return
        # Actual reports show reflection even when the incoming primary kills.
        skill=self.catalog.skill(61004701)
        values=dict(skill.enemy_effects)
        # Keep the existing cast gate and post-hit callback boundary. The
        # observed100% cases do not distinguish independent cast rolls.
        for incoming in reflected:
            if not actor.alive:break
            amount=int(Decimal(incoming)*Decimal(str(scaled(values[14],150)))/100
                       +Decimal(str(scaled(values[5],150))))
            battle.damage(target,actor,amount,61004701,'reflection')
