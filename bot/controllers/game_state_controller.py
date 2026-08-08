from typing import TYPE_CHECKING

from ares.behaviors.macro import (
    AutoSupply,
    BuildStructure,
    BuildWorkers,
    GasBuildingController,
    MacroPlan,
    Mining,
    SpawnController,
    TechUp,
    UpgradeController,
)
from bot.controllers.controller import Controller
from bot.expansion_controller import FixedExpansionController
from sc2.ids.unit_typeid import UnitTypeId
from sc2.ids.upgrade_id import UpgradeId
from sc2.units import Unit

if TYPE_CHECKING:
    from bot.main import WilldZergBot


class GameStateController(Controller):
    def __init__(
        self,
        ai: "WilldZergBot",
    ) -> None:
        self.ai = ai

        self._being_rushed = False

    async def start(self) -> None:
        pass

    def _set_being_rushed(self) -> None:
        if self.ai.time > 270:
            self._being_rushed = False
            return

        if not self._being_rushed:
            being_rushed = self.ai.mediator.get_did_enemy_rush
            if being_rushed:
                self._being_rushed = True
                print("Detected that we're being rushed")

    async def update(self) -> None:
        self._set_being_rushed()

    def being_rushed(self) -> bool:
        return self._being_rushed
