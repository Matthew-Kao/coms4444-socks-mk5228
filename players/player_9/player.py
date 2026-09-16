"""Group 9's player.

Wears the closest-matching pair, then tosses any leftover sock that has been
worn more than MAX_WEARS times. That keeps the drawer close to brand new, so
pairs stay within the free 6-shade window, without greedy's habit of tossing
fresh socks just because they are the other colour.
"""

from itertools import combinations

from core.engine import PACK_COST
from models.player import GameContext, PlayerSnapshot, Selection, TurnContext
from models.player import Player as BasePlayer

MAX_WEARS = 6


def wears(shade: int) -> float:
	"""How many times a sock has been worn. White fades 2 per wear, black rises 1."""
	return (255 - shade) / 2 if shade > 64 else shade


class Player9(BasePlayer):
	def __init__(self, snapshot: PlayerSnapshot, ctx: GameContext) -> None:
		super().__init__(snapshot, ctx)

	def select_socks(self, offered: tuple[int, ...], turn: TurnContext) -> Selection:
		i, j = min(
			combinations(range(len(offered)), 2), key=lambda p: abs(offered[p[0]] - offered[p[1]])
		)

		discard: tuple[int, ...] = ()
		if turn.budget_remaining >= PACK_COST:
			discard = tuple(
				k for k in range(len(offered)) if k not in (i, j) and wears(offered[k]) > MAX_WEARS
			)

		return Selection(wear=(i, j), discard=discard)
