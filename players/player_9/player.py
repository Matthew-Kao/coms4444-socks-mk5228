"""Group 9 player: budget-aware pairing, with discards sized by what the budget can afford."""

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
# Shades at which a sock has a chance of developing a hole when worn
WORN_OUT = (WHITE_FLOOR, BLACK_CEILING)
# Socks in a pack, and how many recent shades we keep as a picture of the drawer
PACK_SIZE = 6
SAMPLE_WINDOW = 200


def is_black(shade: int) -> bool:
	return shade <= BLACK_CEILING


def wears(shade: int) -> float:
	"""How many times a sock has been worn. White fades 2 per wear, black rises 1."""
	return shade if is_black(shade) else (WHITE_START - shade) / WHITE_FADE


class Player9(BasePlayer):
	"""Budget-aware pair preference, with discards chosen by how far a leftover
	sits from its own colour's running average.

	How far is "too far" is not a constant. The money left and the days left
	give the socks a day the household can still afford to replace; our share of
	that is the fraction of the socks we are handed that we can afford to bin.
	We then set the cut at the distance that leaves roughly that fraction of the
	shades we have sampled outside it - so a tight budget bins only the worst
	outliers, and an unlimited one bins everything we are not wearing.
	"""

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

		self.total_budget = None
		self.white_seen = []
		self.black_seen = []

	def select_socks(self, offered: tuple[int, ...], turn: TurnContext) -> Selection:
		"""Choose two socks to wear, and decide the fate of the rest.

		Called once per day, in an order that is reshuffled daily. Everything you
		are allowed to know is in the two arguments.

		``offered`` is a tuple of ``selection_unit`` shade values, 0-255.

		WHAT YOU CAN SEE

			offered[i]                  the shade of the i-th sock on offer
			turn.day                    today's day number, 1-based
			turn.total_spent            dollars spent by the household so far
			turn.embarrassment_history  your own daily scores, one per day
			turn.total_embarrassment    the sum of that history
			self.capacity / self.roommates / self.selection_unit / self.days

		WHAT YOU CANNOT SEE

			- Which sock is which. Indices are positions in THIS tuple only. The
				same index tomorrow is a different sock, so you cannot track an
				individual sock across turns or build up a map of the drawer.
			- Anyone else's socks, choices or embarrassment.
			- The shade distribution left in the drawer.
			- How many socks have been discarded, or how close the household is to
				the next six-pack. You see total_spent only, after the fact.

		With n == 1 you are alone with the drawer, so tracking its full state IS
		possible. That is intentional, not a leak - it is what makes the pooled
		versus separate comparison in goal 3 meaningful.

		WHAT THE SHADES MEAN

		White socks start at 255 and fade by 2 per wear, stopping at 127. Black
		socks start at 0 and rise by 1 per wear, stopping at 64. The two ranges
		never overlap, so a shade above 64 is a white sock and a shade at or below
		64 is a black one. Inferring colour from shade is fair game.

		Wearing a pair whose shades differ by MORE than 6 costs you that
		difference. A difference of exactly 6 is free.

		A sock already at 127 or 64 when you are handed it has a 25% chance of
		developing a hole when worn, and is thrown out immediately. Six discards
		of one colour buy a fresh six-pack for $10, and the surplus carries over.

		RETURNING A DECISION

			wear     exactly two distinct indices into ``offered``
			discard  any subset of the REMAINING indices, possibly empty

		Anything you neither wear nor discard goes back in the drawer unworn and
		keeps its shade. Only worn socks age.

		IF YOU GET IT WRONG

		An invalid selection, an exception, or taking longer than the --timeout
		budget forfeits your turn: the engine wears the first two socks and
		discards nothing. It is recorded as a fault and shown in the results, so a
		forfeit is visible rather than silent. Your failure never affects the
		other groups.
		"""
		# Keep count of each color 
		for s in offered:
			if is_black(s):
				self.black_seen.append(s)
			else:
				self.white_seen.append(s)

		# Only include samples in the last SAMPLE_WINDOW days
		self.white_seen = self.white_seen[-SAMPLE_WINDOW:]
		self.black_seen = self.black_seen[-SAMPLE_WINDOW:]

		if self.total_budget is None:
			self.total_budget = turn.total_spent + turn.budget_remaining

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

		# Running estimate of the middle of each colour pool
		w_avg = sum(self.white_seen) / len(self.white_seen) if self.white_seen else WHITE_START
		b_avg = sum(self.black_seen) / len(self.black_seen) if self.black_seen else 0

		# Fraction of socks a single roomate can afford to replace
		remaining_days = self.days - turn.day + 1
		affordable = (turn.budget_remaining / remaining_days) / PACK_COST * PACK_SIZE
		share = affordable / (self.roommates * self.selection_unit)

		# Array of distances between each sock and its sampled average
		distances = sorted(
			[abs(s - w_avg) for s in self.white_seen] + [abs(s - b_avg) for s in self.black_seen]
		)
		if share >= 1:
			bound = 0.0
		elif share <= 0 or not distances:
			bound = math.inf
		else:
			bound = distances[min(len(distances) - 1, int((1 - share) * len(distances)))]

		dis = []

		# If we can afford to buy atleast one pack, run discard logic with bound
		if turn.budget_remaining >= PACK_COST:
			for i in range(len(offered)):
				if i in (left, right):
					continue
				target = b_avg if is_black(offered[i]) else w_avg
				if abs(offered[i] - target) > bound:
					dis.append(i)

		return Selection(wear=(left, right), discard=tuple(dis))