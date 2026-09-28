"""Starting point for a group's player.

Copy this whole directory to ``players/player_<k>/`` using your group number,
then rename the class to ``Player<k>``. Group 4 would end up with
``players/player_4/player.py`` containing ``class Player4``. The registry looks
for exactly that; nothing else needs editing.

Keep the ``__init__.py``. Discovery uses ``pkgutil.iter_modules``, which only
reports directories that have one, so a group directory without it is silently
invisible to the simulator - no error, just a player that never turns up.

This directory is not itself discovered - the registry only matches
``player_<digits>`` - so the template can never appear in a run as a competitor.
"""

import math
from itertools import combinations

from core.engine import EMBARRASSMENT_THRESHOLD, PACK_COST
from models.player import GameContext, PlayerSnapshot, Selection, TurnContext
from models.player import Player as BasePlayer
from models.sock import BLACK_CEILING, WHITE_FADE, WHITE_FLOOR, WHITE_START

# Below this much money left (but still enough for a pack) the budget counts as low:
# a share of the total budget, but never less than a fixed floor
MIN_BUDGET_FRACTION = 0.1
MIN_BUDGET_FLOOR = 100.0
# Discard socks by how often they have been worn, which reads the same for both colours
# (each tops out at 64 wears). Start at "well worn", loosen while underspending but never
# below the floor, so a nearly new sock is never thrown out
START_MIN_WEARS = 48
MIN_WEARS_FLOOR = 16
MAX_WEARS = 64
# Bigger budgets can afford to replace socks sooner, so the floor shrinks in proportion
# once the budget per roommate per day passes the reference (about $600 for 4 roommates
# over 720 days), but never below LOWEST_MIN_WEARS
REF_BUDGET_PER_ROOMMATE_DAY = 600 / (4 * 720)
LOWEST_MIN_WEARS = 4
# Shades at which a sock has a chance of developing a hole when worn
WORN_OUT = (WHITE_FLOOR, BLACK_CEILING)


def is_black(shade: int) -> bool:
	return shade <= BLACK_CEILING


def wears(shade: int) -> float:
	"""How many times a sock has been worn. White fades 2 per wear, black rises 1."""
	return shade if is_black(shade) else (WHITE_START - shade) / WHITE_FADE


class Player9(BudgetMethods, PairingMethods, DiscardMethods, BasePlayer):
	"""Rename me to Player<k>, where <k> is your group number."""

	def __init__(self, snapshot: PlayerSnapshot, ctx: GameContext) -> None:
		super().__init__(snapshot, ctx)

		# super() has already set these from ctx and snapshot:
		#
		#   self.index           which roommate you are (0-based)
		#   self.id              your UUID, stable for the whole simulation
		#   self.capacity        C, the drawer size at the start
		#   self.roommates       n, how many of you share the drawer
		#   self.selection_unit  how many socks you are handed each day
		#   self.days            how long the simulation runs
		#
		# The engine constructs you once, before day 1, and it constructs you
		# itself - you cannot preload state into an already-built object. Anything
		# you want to carry between days lives on self, so initialise it here.
		self.days_seen = 0
		self.total_budget = None
		self.min_wears = START_MIN_WEARS
		self.min_wears_floor = MIN_WEARS_FLOOR


	def is_black(self, shade: int) -> bool:
		return shade <= BLACK_CEILING

	def wears(self, shade: int) -> float:
		"""How many times a sock has been worn. White fades 2 per wear, black rises 1."""
		return shade if self.is_black(shade) else (WHITE_START - shade) / WHITE_FADE


	def select_socks(self, offered: tuple[int, ...], turn: TurnContext) -> Selection:
		"""
		WHAT YOU CAN SEE

			offered[i]                  the shade of the i-th sock on offer
			turn.day                    today's day number, 1-based
			turn.total_spent            dollars spent by the household so far
			turn.embarrassment_history  your own daily scores, one per day
			turn.total_embarrassment    the sum of that history
			self.capacity / self.roommates / self.selection_unit / self.days
		"""

		SPPPD, projected_error = self._update_budget(turn)

		# Initialize total_budget
		if self.total_budget is None:
			self.total_budget = turn.total_spent + turn.budget_remaining
			per_roommate_day = self.total_budget / (self.roommates * self.days)
			scaled = MIN_WEARS_FLOOR * REF_BUDGET_PER_ROOMMATE_DAY / max(per_roommate_day, 1e-9)
			self.min_wears_floor = max(LOWEST_MIN_WEARS, min(MIN_WEARS_FLOOR, round(scaled)))

		# No budget means no money for a pack, or no budget set at all
		broke = turn.budget_remaining < PACK_COST
		no_budget = broke or math.isinf(turn.budget_remaining)
		min_budget = max(MIN_BUDGET_FLOOR, MIN_BUDGET_FRACTION * self.total_budget)
		low_budget = not no_budget and turn.budget_remaining < min_budget

		def preference(p: tuple[int, int]) -> tuple[int, float, int, float, int]:
			a, b = offered[p[0]], offered[p[1]]
			diff = abs(a - b)
			cost = diff if diff > EMBARRASSMENT_THRESHOLD else 0
			# Prefer black socks over white when the budget is low
			whites = (not is_black(a)) + (not is_black(b)) if low_budget else 0
			# Prefer young socks over old when the budget is low or gone
			age = wears(a) + wears(b) if low_budget or no_budget else 0
			# A worn-out sock can get a hole, and with no money it is never replaced
			hole_risk = (a in WORN_OUT) + (b in WORN_OUT) if broke else 0
			return (hole_risk, cost, whites, age, diff)

		# Pick the least embarrassing pair, then the preferred one, then the two closest socks
		left, right = min(combinations(range(len(offered)), 2), key=preference)

		dis = []
		can_discard = False

		# Monitor budget activity for the first 20 days, don't discard anything
		if turn.day > 20 and turn.budget_remaining > 0:
			can_discard = True
			remaining_days = self.days - turn.day + 1
			remaining_average = turn.budget_remaining / remaining_days
			total_average = self.total_budget / self.days

			# If we are underspending (always, with no budget set), loosen restrictions on discards
			if math.isinf(turn.budget_remaining) or remaining_average > total_average:
				self.min_wears = max(self.min_wears_floor, self.min_wears - 1)
			# If we are overspending, tighten restrictions on discards
			elif remaining_average < total_average:
				self.min_wears = min(MAX_WEARS, self.min_wears + 1)

		for i in range(len(offered)):
			if i in (left, right):
				pass
			else:
				# Only discard socks worn enough times; newer ones stay in the drawer
				if can_discard and wears(offered[i]) >= self.min_wears:
					dis.append(i)

		return Selection(wear=(left, right), discard=(tuple(dis)))
