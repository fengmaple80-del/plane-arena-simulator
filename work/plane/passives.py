"""Pure, description-derived plane mechanics; NOT a battle executor.

Fractions are 0..1 where applicable; values remain unrounded. Callers own event
ordering, targeting, durations, living-count snapshots, and integer conversion.
Linear coefficients use base + growth * level, matching the project's current
convention without asserting that server rounding has been verified for these
awakenings. Artifact levels are always explicit, never inherited from level150.
"""
from dataclasses import dataclass
from math import isfinite
from typing import Literal, Sequence


SOURCE_SKILL_IDS = {
    'fallen': (61120101, 61220101),
    'dragon': (61120041, 61220041, 66000087),
    'plague': (61120501, 61220501),
    'mod': (61120301, 61220301),
    'lulu': (61114001, 61214001, 66000077),
    'mermaid': (61120071, 61220071, 66000090),
    'angel': (61105901, 61205901),
    'adai': (61121021, 61221021),
    'son': (61121011, 61221011, 66000103),
    'lion': (61120601, 61220601),
}


def _count(value: int, name: str, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'{name} must be a nonnegative integer')
    if maximum is not None and value > maximum:
        raise ValueError(f'{name} must not exceed {maximum}')
    return value


def _value(value: float, name: str) -> float:
    if isinstance(value, bool) or not isfinite(value) or value < 0:
        raise ValueError(f'{name} must be finite and nonnegative')
    return value


def _linear(base: float, growth: float, level: int) -> float:
    if _count(level, 'level') == 0:
        raise ValueError('level must be at least one')
    return base + growth * level


def fallen_healing_multiplier(living_demons: int, *, fallen_alive: bool) -> float:
    """61120101/201: count includes the living holder; six doubles suppression."""
    _count(living_demons, 'living_demons', 6)
    suppression = .08 * living_demons * (2 if living_demons == 6 else 1)
    return 1 - suppression if fallen_alive else 1.0


def dragon_mark_count(current_marks: int, added_marks: int = 2) -> int:
    """61120041: apply the count cap only; marks last two rounds externally."""
    return min(5, _count(current_marks, 'current_marks', 5)
               + _count(added_marks, 'added_marks'))


def dragon_magic_damage(damage_before_marks: float, max_hp: float,
                        marks: int, *, is_magic: bool) -> float:
    """61120041: strict >85% trigger; pre-mark input's mitigation stage is caller-owned."""
    _value(damage_before_marks, 'damage_before_marks')
    if _value(max_hp, 'max_hp') == 0:
        raise ValueError('max_hp must be positive')
    _count(marks, 'marks', 5)
    if is_magic and damage_before_marks > .85 * max_hp:
        return damage_before_marks * (1 - .12 * marks)
    return damage_before_marks


def dragon_chase_bonus(living_demons: int) -> int:
    return int(_count(living_demons, 'living_demons', 6) >= 4)


def dragon_artifact_attack_bonus(other_demon_actions: int, *, artifact_level: int) -> float:
    """66000087/180: fractional bonus capped at +150%; no action eligibility here."""
    return min(1.5, _count(other_demon_actions, 'other_demon_actions')
               * _linear(10, 2, artifact_level) / 100)


def plague_lifesteal_fraction(*, plague_alive: bool, recipient_is_demon: bool) -> float:
    return .20 if plague_alive and recipient_is_demon else 0.0


def plague_rescue_heal(physical_attack: float, other_living_demons: int, *,
                       plague_alive: bool, target_is_front_demon: bool,
                       target_near_death: bool, rescue_used: bool) -> float | None:
    """61120501/211,212: raw heal; None means no trigger, zero is a valid heal.

    Count excludes the rescued ally and can include the living Plague holder.
    The caller consumes the once-per-battle flag even if healing is suppressed.
    """
    _value(physical_attack, 'physical_attack')
    _count(other_living_demons, 'other_living_demons', 5)
    if rescue_used or not (plague_alive and target_is_front_demon and target_near_death):
        return None
    return physical_attack * (1.2 + .6 * other_living_demons)


def mod_speed_bonus(panel_speed: float, *, mod_alive: bool, recipient_is_mage: bool,
                    competing_aura_bonus: float = 0) -> float:
    """61120301/206: returns selected flat aura bonus, not total speed."""
    _value(panel_speed, 'panel_speed')
    _value(competing_aura_bonus, 'competing_aura_bonus')
    own = .30 * panel_speed if mod_alive and recipient_is_mage else 0
    return max(own, competing_aura_bonus)


def mod_defence_multiplier(attacker_negative_count: int) -> float:
    """61120301/207: +400% cap gives x5, applies only during incoming damage."""
    return 1 + min(4, .45 * _count(attacker_negative_count, 'attacker_negative_count'))


def lulu_immunity_count(deployed_humans: int) -> int:
    return _count(deployed_humans, 'deployed_humans', 6)


def lulu_reactive_cast_cap(deployed_humans: int) -> int:
    """66000077: count only; undocumented HP threshold is not invented."""
    return min(3, _count(deployed_humans, 'deployed_humans', 6) // 2)


def lulu_attack_bonus(lulu_magic_attack: float, ally_base_attack: float,
                      current_bonus: float, *, skill_level: int = 150,
                      eligible_human_action: bool = True) -> float:
    """61114001/156: new accumulated bonus for ONE of physical/magic attack.

    Caller supplies the chosen cap baseline and Lulu attack snapshot. Apply
    separately to each attack stat; baseline/snapshot timing remains unverified.
    """
    for name, value in [('lulu_magic_attack', lulu_magic_attack),
                        ('ally_base_attack', ally_base_attack), ('current_bonus', current_bonus)]:
        _value(value, name)
    increment = lulu_magic_attack * _linear(4, .04, skill_level) / 100
    if current_bonus > ally_base_attack:
        raise ValueError('current_bonus already exceeds the supplied cap baseline')
    return min(ally_base_attack, current_bonus + (increment if eligible_human_action else 0))


def mermaid_immunity_count(*, recipient_is_other_column_mage: bool) -> int:
    return 6 if recipient_is_other_column_mage else 0


def consume_debuff_immunity(remaining: int, *, incoming_is_negative: bool) -> tuple[bool, int]:
    """One attempted negative application; multi-effect ordering is external."""
    _count(remaining, 'remaining')
    blocked = incoming_is_negative and remaining > 0
    return blocked, remaining - int(blocked)


def mermaid_kill_bonus(mage_kills: int, team_mages: int, *,
                       minimum_mages: Literal[4, 5],
                       recipient_is_other_column_mage: bool) -> float:
    """61120071/192: explicit threshold because '超过4人及以上' is ambiguous."""
    _count(mage_kills, 'mage_kills')
    _count(team_mages, 'team_mages', 6)
    if minimum_mages not in (4, 5):
        raise ValueError('minimum_mages must explicitly choose 4 or 5')
    return .15 * mage_kills if recipient_is_other_column_mage and team_mages >= minimum_mages else 0


def mermaid_artifact_attack_bonus(panel_magic_attack: float, team_mages: int, *,
                                  artifact_level: int,
                                  scaling: Literal['relative', 'percentage_points'],
                                  recipient_is_other_column_mage: bool) -> float:
    """66000090/199: two unresolved readings of '效果提升30%' require a choice."""
    _value(panel_magic_attack, 'panel_magic_attack')
    _count(team_mages, 'team_mages', 6)
    coefficient = _linear(50, 10, artifact_level) / 100
    if scaling == 'relative':
        coefficient *= 1 + .30 * team_mages
    elif scaling == 'percentage_points':
        coefficient += .30 * team_mages
    else:
        raise ValueError('unknown Mermaid artifact scaling')
    return panel_magic_attack * coefficient if recipient_is_other_column_mage else 0


@dataclass(frozen=True)
class AngelRescuePlan:
    invulnerable_rounds: int
    recovery_fraction: float
    recovery_rounds: int
    shared_damage_fraction: float
    shared_damage_rounds: int


def parse_angel_rescue(passives: Sequence[tuple[int, Sequence[str]]], *,
                       skill_level: int = 150) -> AngelRescuePlan:
    """61105901/74: preserve nested status records rather than unpacking triples.

    Recovery is a fraction; its HP basis and tick scheduling remain external.
    Accept exactly the known 68|duration|69|base|growth|duration|70|... schema.
    """
    records = [values for code, values in passives if code == 74]
    if len(records) != 1:
        raise ValueError('expected one Angel passive74 record')
    values = tuple(records[0])
    if len(values) != 10 or tuple(str(values[i]) for i in (0, 2, 6)) != ('68', '69', '70'):
        raise ValueError('unsupported Angel passive74 schema')
    durations = [int(values[i]) for i in (1, 5, 9)]
    if any(x <= 0 for x in durations):
        raise ValueError('Angel effect durations must be positive')
    coefficients = [float(values[i]) for i in (3, 4, 7, 8)]
    for coefficient in coefficients:
        _value(coefficient, 'Angel coefficient')
    recovery = _linear(*coefficients[:2], skill_level) / 100
    sharing = _linear(*coefficients[2:], skill_level) / 100
    if sharing > 1:
        raise ValueError('shared damage fraction exceeds one')
    return AngelRescuePlan(durations[0], recovery, durations[1], sharing, durations[2])


def angel_rescue_plan(passives: Sequence[tuple[int, Sequence[str]]], *,
                       angel_alive: bool, ally_near_death: bool, rescue_used: bool,
                       skill_level: int = 150) -> AngelRescuePlan | None:
    """Call only for a teammate, never the holder; consume flag once a plan is emitted."""
    plan = parse_angel_rescue(passives, skill_level=skill_level)
    return plan if angel_alive and ally_near_death and not rescue_used else None


@dataclass(frozen=True)
class HealingSplit:
    raw_lifesteal: float
    self_heal: float
    overflow: float
    raw_ally_heal: float


def adai_lifesteal_split(eligible_damage: float, missing_hp: float, *,
                         eligible_demon_with_karma: bool,
                         overflow_target_is_demon: bool) -> HealingSplit:
    """61121021/231: isolate this 15% effect; no suppression or ally HP clamp.

    eligible_damage is caller-supplied resolved damage eligible for lifesteal;
    how damage overkill or other lifesteal combines is not silently decided.
    """
    _value(eligible_damage, 'eligible_damage')
    _value(missing_hp, 'missing_hp')
    raw = .15 * eligible_damage if eligible_demon_with_karma else 0
    own = min(raw, missing_hp)
    overflow = raw - own
    return HealingSplit(raw, own, overflow, overflow * (1.2 if overflow_target_is_demon else 1))


def adai_bypasses_rescue(living_demons: int, *, attacker_is_physical_demon: bool,
                         attacker_has_karma: bool) -> bool:
    return (_count(living_demons, 'living_demons', 6) >= 5
            and attacker_is_physical_demon and attacker_has_karma)


def son_bypasses_rescue(living_humans: int) -> bool:
    """61121011/229: caller must establish Son is the killing attacker."""
    return _count(living_humans, 'living_humans', 6) == 6


def son_lifesteal_fraction() -> float:
    return .30


def son_chase_cap(deployed_humans: int) -> int:
    return min(5, _count(deployed_humans, 'deployed_humans', 6))


def son_artifact_max_hp_fraction(*, artifact_level: int) -> float:
    """66000103 enemy54: extra max-HP coefficient; target HP basis remains external."""
    return _linear(5, 1, artifact_level) / 100


def lion_human_damage_multiplier(*, attacker_is_human: bool,
                                 damage_is_true_or_percentage: bool) -> float:
    return 1.2 if attacker_is_human and damage_is_true_or_percentage else 1.0


def lion_kill_healing_effect(living_humans: int, *, killer_is_human: bool) -> tuple[float, bool]:
    """61120601/216: (healing multiplier, nondispellable); duration unresolved."""
    _count(living_humans, 'living_humans', 6)
    return (.2, living_humans >= 5) if killer_is_human else (1.0, False)
