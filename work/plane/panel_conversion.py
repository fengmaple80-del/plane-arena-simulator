"""Magic-to-HP conversion shared by panel compilation and raw-panel sessions.

bonus_plus_base is an explicit working hypothesis: all44 observed level10
Wing Mother positions support six times the pre-conversion magic within6 HP.
The old tooltip's500% alone does not explain that extra base100%; preserve this
distinction in panel provenance instead of calling it a decoded server rule.
Community descriptions support consuming the magic attack during conversion.
"""
from decimal import Decimal

def convert_physical_to_hp(panel,percent):
    # Zone 1 d19/c1/10 g2: 714205 + 6 * 240188 = 2155333 (observed 2155332).
    panel['max_hp']+=int(Decimal(panel['physical_attack'])*(1+Decimal(str(percent))/100))
    panel['physical_attack']=0


def convert_magic_to_hp(panel,percent,*,policy='bonus_plus_base'):
    if policy not in ('bonus_plus_base','scaled_only'):
        raise ValueError('Unknown magic-to-HP conversion policy')
    before=panel['magical_attack']
    rate=Decimal(str(percent))/100+(1 if policy=='bonus_plus_base' else 0)
    gain=int(Decimal(before)*rate)
    panel['max_hp']+=gain
    panel['magical_attack']=0
    panel['magic_hp_conversion']=dict(policy=policy,configured_percent=float(percent),
        pre_conversion_magic=before,hp_added=gain,remaining_magic=0,
        evidence='44 plane HP records; residual up to6; zero magic from community description')
    return gain
