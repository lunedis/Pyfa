# shipBonusRemoteArmorRepairCapNeedGC1
#
# Used by:
# Ship: Exequror
type = "passive"
def handler(fit, ship, context):
    fit.modules.filteredItemBoost(lambda mod: mod.item.group.name == "Remote Armor Repairer",
                                  "capacitorNeed", ship.getModifiedItemAttr("shipBonusGC"), skill="Gallente Cruiser")
