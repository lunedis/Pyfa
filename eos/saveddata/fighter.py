#===============================================================================
# Copyright (C) 2010 Diego Duclos
#
# This file is part of eos.
#
# eos is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# eos is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Lesser General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with eos.  If not, see <http://www.gnu.org/licenses/>.
#===============================================================================

from eos.modifiedAttributeDict import ModifiedAttributeDict, ItemAttrShortcut, ChargeAttrShortcut
from eos.effectHandlerHelpers import HandledItem, HandledCharge, HandledDroneCargoList
from sqlalchemy.orm import validates, reconstructor
import eos.db
from eos.enum import Enum
import logging
from eos.types import FighterAbility, Slot

logger = logging.getLogger(__name__)


class Fighter(HandledItem, HandledCharge, ItemAttrShortcut, ChargeAttrShortcut):
    DAMAGE_TYPES = ("em", "kinetic", "explosive", "thermal")
    DAMAGE_TYPES2 = ("EM", "Kin", "Exp", "Therm")

    def __init__(self, item):
        """Initialize a fighter from the program"""
        self.__item = item
        print self.__item.category.name
        if self.isInvalid:
            raise ValueError("Passed item is not a Fighter")

        self.itemID = item.ID if item is not None else None
        self.projected = False
        self.active = True

        # -1 is a placeholder that represents max squadron size, which we may not know yet as ships may modify this with
        # their effects. If user changes this, it is then overridden with user value.
        self.amount = -1

        self.__abilities = self.__getAbilities()

        self.build()

    @reconstructor
    def init(self):
        """Initialize a fighter from the database and validate"""
        self.__item = None

        if self.itemID:
            self.__item = eos.db.getItem(self.itemID)
            if self.__item is None:
                logger.error("Item (id: %d) does not exist", self.itemID)
                return

        if self.isInvalid:
            logger.error("Item (id: %d) is not a Fighter", self.itemID)
            return

        self.build()

    def build(self):
        """ Build object. Assumes proper and valid item already set """
        self.__charge = None
        self.__dps = None
        self.__volley = None
        self.__miningyield = None
        self.__itemModifiedAttributes = ModifiedAttributeDict()
        self.__chargeModifiedAttributes = ModifiedAttributeDict()

        if len(self.abilities) != len(self.item.effects):
            self.__abilities = []
            for ability in self.__getAbilities():
                self.__abilities.append(ability)

        if self.__item:
            self.__itemModifiedAttributes.original = self.__item.attributes
            self.__itemModifiedAttributes.overrides = self.__item.overrides
            self.__slot = self.__calculateSlot(self.__item)

            chargeID = self.getModifiedItemAttr("fighterAbilityLaunchBombType")
            if chargeID is not None:
                charge = eos.db.getItem(int(chargeID))
                self.__charge = charge
                self.__chargeModifiedAttributes.original = charge.attributes
                self.__chargeModifiedAttributes.overrides = charge.overrides

    def __getAbilities(self):
        """Returns list of FighterAbilities that are loaded with data"""
        return [FighterAbility(effect) for effect in self.item.effects.values()]

    def __calculateSlot(self, item):
        types = {"Light": Slot.F_LIGHT,
                 "Support": Slot.F_SUPPORT,
                 "Heavy": Slot.F_HEAVY}

        for t, slot in types.iteritems():
            if self.getModifiedItemAttr("fighterSquadronIs{}".format(t)):
                return slot

    @property
    def slot(self):
        return self.__slot

    @property
    def amountActive(self):
        return int(self.getModifiedItemAttr("fighterSquadronMaxSize")) if self.amount == -1 else self.amount

    @amountActive.setter
    def amountActive(self, i):
        self.amount = int(max(min(i, self.getModifiedItemAttr("fighterSquadronMaxSize")), 0))

    @property
    def abilities(self):
        return self.__abilities or []

    @property
    def charge(self):
        return self.__charge

    @property
    def itemModifiedAttributes(self):
        return self.__itemModifiedAttributes

    @property
    def chargeModifiedAttributes(self):
        return self.__chargeModifiedAttributes

    @property
    def isInvalid(self):
        return self.__item is None or self.__item.category.name != "Fighter"

    @property
    def item(self):
        return self.__item

    @property
    def charge(self):
        return self.__charge

    @property
    def hasAmmo(self):
        return self.charge is not None

    @property
    def dps(self):
        return self.damageStats()

    def damageStats(self, targetResists = None):
        if self.__dps is None:
            self.__volley = 0
            self.__dps = 0
            if self.active:
                for ability in self.abilities:
                    if ability.dealsDamage and ability.active and self.amountActive > 0:

                        cycleTime = self.getModifiedItemAttr("{}Duration".format(ability.attrPrefix))
                        if ability.attrPrefix == "fighterAbilityLaunchBomb":
                            # bomb calcs
                            volley = sum(map(lambda attr: (self.getModifiedChargeAttr("%sDamage" % attr) or 0) * (1 - getattr(targetResists, "%sAmount" % attr, 0)), self.DAMAGE_TYPES))
                            print volley
                        else:
                            volley = sum(map(lambda d2, d:
                                         (self.getModifiedItemAttr("{}Damage{}".format(ability.attrPrefix, d2)) or 0) *
                                         (1-getattr(targetResists, "{}Amount".format(d), 0)),
                                         self.DAMAGE_TYPES2, self.DAMAGE_TYPES))

                        volley *= self.amountActive
                        volley *= self.getModifiedItemAttr("{}DamageMultiplier".format(ability.attrPrefix)) or 1
                        self.__volley += volley
                        self.__dps += volley / (cycleTime / 1000.0)

        return self.__dps, self.__volley

    @property
    def maxRange(self):
        attrs = ("shieldTransferRange", "powerTransferRange",
                 "energyDestabilizationRange", "empFieldRange",
                 "ecmBurstRange", "maxRange")
        for attr in attrs:
            maxRange = self.getModifiedItemAttr(attr)
            if maxRange is not None: return maxRange
        if self.charge is not None:
            delay = self.getModifiedChargeAttr("explosionDelay")
            speed = self.getModifiedChargeAttr("maxVelocity")
            if delay is not None and speed is not None:
                return delay / 1000.0 * speed

    # Had to add this to match the falloff property in modules.py
    # Fscking ship scanners. If you find any other falloff attributes,
    # Put them in the attrs tuple.
    @property
    def falloff(self):
        attrs = ("falloff", "falloffEffectiveness")
        for attr in attrs:
            falloff = self.getModifiedItemAttr(attr)
            if falloff is not None: return falloff

    @validates("ID", "itemID", "chargeID", "amount", "amountActive")
    def validator(self, key, val):
        map = {"ID": lambda val: isinstance(val, int),
               "itemID" : lambda val: isinstance(val, int),
               "chargeID" : lambda val: isinstance(val, int),
               "amount" : lambda val: isinstance(val, int) and val >= -1,
               }

        if map[key](val) == False: raise ValueError(str(val) + " is not a valid value for " + key)
        else: return val

    def clear(self):
        self.__dps = None
        self.__volley = None
        self.__miningyield = None
        self.itemModifiedAttributes.clear()
        self.chargeModifiedAttributes.clear()

    def canBeApplied(self, projectedOnto):
        """Check if fighter can engage specific fitting"""
        item = self.item
        # Do not allow to apply offensive modules on ship with offensive module immunite, with few exceptions
        # (all effects which apply instant modification are exception, generally speaking)
        if item.offensive and projectedOnto.ship.getModifiedItemAttr("disallowOffensiveModifiers") == 1:
            offensiveNonModifiers = set(("energyDestabilizationNew", "leech", "energyNosferatuFalloff", "energyNeutralizerFalloff"))
            if not offensiveNonModifiers.intersection(set(item.effects)):
                return False
        # If assistive modules are not allowed, do not let to apply these altogether
        if item.assistive and projectedOnto.ship.getModifiedItemAttr("disallowAssistance") == 1:
            return False
        else:
            return True

    def calculateModifiedAttributes(self, fit, runTime, forceProjected = False):
        if not self.active:
            return

        if self.projected or forceProjected:
            context = "projected", "fighter"
            projected = True
        else:
            context = ("fighter",)
            projected = False

        for ability in self.abilities:
            if ability.active:
                effect = ability.effect
                if effect.runTime == runTime and \
                ((projected and effect.isType("projected")) or not projected):
                    if ability.grouped:
                        effect.handler(fit, self, context)
                    else:
                        i = 0
                        while i != self.amountActive:
                            effect.handler(fit, self, context)
                            i += 1

    def __deepcopy__(self, memo):
        copy = Fighter(self.item)
        copy.amount = self.amount
        return copy

    def fits(self, fit):
        # If ships doesn't support this type of fighter, don't add it
        if fit.getNumSlots(self.slot) == 0:
            return False

        return True

