# moduleBonusSiegeWarfareLinkShieldEfficiency
#
# Used by:
# Variations of module: Siege Warfare Link - Shield Efficiency I (2 of 2)
type = "gang", "active"
gangBoost = "shieldResistance"
runTime = "late"

def handler(fit, module, context):
    if "gang" not in context: return
    for damageType in ("Em", "Explosive", "Thermal", "Kinetic"):
        fit.ship.boostItemAttr("shield%sDamageResonance" % damageType,
                               module.getModifiedItemAttr("commandBonus"),
                               stackingPenalties = True)
