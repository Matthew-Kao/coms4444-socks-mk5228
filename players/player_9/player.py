"""Group 9 player: greedy pairing with budget-paced, drawer-aware discards."""

from itertools import combinations

from models.player import GameContext, PlayerSnapshot, Selection, TurnContext
from models.player import Player as BasePlayer

BASE_THRESHOLD = 6.0
MIN_THRESHOLD = 2.0


class Player9(BasePlayer):
	"""Greedy pairing; discard the leftover furthest from its colour's average.

	Budget pacing can only make discarding more aggressive when the household is
	underspending - it never shuts discarding off when others overspend.
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
		self.white_seen = []
		self.black_seen = []
		self.total_budget = None
		self.threshold = BASE_THRESHOLD

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
		# Keep track of the color of White and Black socks
		for s in offered:
			if s > 64:
				self.white_seen.append(s)
			else:
				self.black_seen.append(s)

		# We only want recent samples, here I have set to last 200 samples
		self.white_seen = self.white_seen[-200:]
		self.black_seen = self.black_seen[-200:]

		left, right = min(
			combinations(range(len(offered)), 2), key=lambda p: abs(offered[p[0]] - offered[p[1]])
		)

		# Find the average color of each sock
		w_avg = sum(self.white_seen) / len(self.white_seen) if self.white_seen else 190
		b_avg = sum(self.black_seen) / len(self.black_seen) if self.black_seen else 32

		if self.total_budget is None:
			self.total_budget = turn.total_spent + turn.budget_remaining

		# Kevin's idea, after watching 20 days, loosen when underspending, vice versa
		if turn.day > 20:
			remaining_days = self.days - turn.day + 1
			remaining_avg = turn.budget_remaining / remaining_days
			total_avg = self.total_budget / self.days
			# Underspending
			if remaining_avg > total_avg:
				self.threshold = max(MIN_THRESHOLD, self.threshold - 1)
			# Overspending
			elif remaining_avg < total_avg:
				self.threshold = min(BASE_THRESHOLD, self.threshold + 1)

		dis = []

		# If our budget is less than 10, I argue there's no point in discarding
		if turn.budget_remaining >= 10:
			leftovers = [k for k in range(len(offered)) if k not in (left, right)]
			if leftovers:

				def dist(k):
					target = w_avg if offered[k] > 64 else b_avg
					return abs(offered[k] - target)

				# Discard the leftover furthest from its colour's average,
				# with a dynamic threshold based on spending patterns
				worst = max(leftovers, key=dist)
				if dist(worst) > self.threshold:
					dis.append(worst)

		return Selection(wear=(left, right), discard=tuple(dis))
