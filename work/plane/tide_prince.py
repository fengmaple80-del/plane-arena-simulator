"""Tide Prince base/awakening/artifact, user-authorized model on 2026-11-21.

No Prince reports are available in the local corpus. Working interpretation:
take 90% of the beast ally's resolved magic component, apply the recipient's
magic immunity once, and decline that share if the final packet is lethal.
Nonmagical extras stay on the ally. This is not a report-validated ordering.
"""
from collections import defaultdict
from decimal import Decimal

from combat import scaled


class TidePrinceMechanics:
    supported_passives = {147}
    supported_buffs = {169}
    ARTIFACT = 66000070

    def __init__(self, catalog, common):
        self.catalog, self.common = catalog, common
        self.absorptions = defaultdict(int)
        self.growth = defaultdict(int)

    def _grow(self, battle, owner):
        # Supplemental awakening text is a duplicate, not a second passive.
        records = [(sid, values) for sid in self.catalog.hero_for(owner).skill_ids
                   for code, values in self.catalog.skill(sid).passives if code == 147]
        if not owner.alive or not records or self.absorptions[owner.uid] >= 10:
            return
        sid, values = records[0]
        self.absorptions[owner.uid] += 1
        rate = scaled(values, self.common.skill_level)
        total = int(Decimal(owner.panel['physical_attack']) * Decimal(str(rate))
                    * self.absorptions[owner.uid] / 100)
        self.common.growth[(owner.uid, 'physical_attack')] += total - self.growth[owner.uid]
        self.growth[owner.uid] = total
        self.common.refresh_stats(battle)
        battle.emit('tide_attack_growth', owner, owner, sid,
                    stacks=self.absorptions[owner.uid], percent_per_stack=rate,
                    attack_bonus=total, physical_attack=owner.physical_attack)

    def before_settlement(self, battle, actor, target, amount, skill_id, kind):
        if kind == 'transfer' or amount <= 0 or target.race != 1:
            return amount
        strike = battle.current_damage or {}
        parts = strike.get('transferred_components') or strike.get('components', ())
        total = sum(p['resolved_amount'] for p in parts)
        if not total or not any(p['kind'] == 'magical' and p['resolved_amount'] > 0 for p in parts):
            return amount
        owners = [u for u in battle.alive(target.camp)
                  if u is not target and battle.has_buff(u, 169)]
        for owner in sorted(owners, key=lambda u: u.position):
            # Preserve an earlier share/cap without sending the full original
            # packet twice. Pure magic is exact; mixed packets are apportioned.
            settled = [dict(p, resolved_amount=p['resolved_amount'] * amount // total)
                       for p in parts]
            settled[-1]['resolved_amount'] += amount - sum(p['resolved_amount'] for p in settled)
            shares = [p['resolved_amount'] * 90 // 100 if p['kind'] == 'magical' else 0
                      for p in settled]
            share = sum(shares)
            if share <= 0:
                return amount
            start = len(battle.events)
            # 'transfer' suppresses recursive sharing and attack callbacks;
            # source_damage_kind lets shared mitigation apply magic immunity.
            # Sharing is not a second critical attack. Isolate the original
            # target's hit modifiers and restore them for its own settlement.
            hit_context = battle.current_hit_context
            battle.current_hit_context = {'critical': False}
            try:
                absorbed = battle.damage(actor, owner, share, self.ARTIFACT, 'transfer',
                    components=[dict(kind='transfer', amount=share, source_damage_kind='magical')],
                    reject_lethal=True)
            finally:
                battle.current_hit_context = hit_context
            rejected = any(e.kind == 'damage_rejected' and e.target_uid == owner.uid
                           and e.skill_id == self.ARTIFACT for e in battle.events[start:])
            if rejected:
                continue
            if not strike.get('lifesteal_components'):
                strike['lifesteal_components'] = [dict(p, pre_transfer_lifesteal=True) for p in settled]
            for part, taken in zip(settled, shares):
                part['resolved_amount'] -= taken
            strike['transferred_components'] = settled
            battle.emit('tide_magic_share', owner, target, self.ARTIFACT,
                        incoming_amount=amount, transferred_amount=share,
                        prince_hp_loss=absorbed, ally_remaining=amount-share)
            self._grow(battle, owner)
            return amount - share
        return amount
