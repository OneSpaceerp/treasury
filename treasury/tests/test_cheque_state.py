"""Tests of the cheque state machine.

`treasury.cheques.state` imports nothing from Frappe, so these run anywhere:

    python -m unittest treasury.tests.test_cheque_state
"""

import importlib.util
import pathlib
import unittest

# Loaded by path so the tests also run outside a bench, where importing the
# `treasury.cheques` package would pull in Frappe.
_path = pathlib.Path(__file__).resolve().parents[1] / "cheques" / "state.py"
_spec = importlib.util.spec_from_file_location("treasury_cheque_state", _path)
state = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(state)


class TestChequeState(unittest.TestCase):
	def test_new_received_cheque(self):
		self.assertEqual(state.initial_status("Receive"), state.IN_WALLET)
		self.assertEqual(
			state.allowed_actions("Receive", state.IN_WALLET, amount=1000),
			["deposit", "cash", "endorse", "transfer", "return_to_party", "reschedule"],
		)

	def test_new_issued_cheque(self):
		for payment_type in ("Pay", "Internal Transfer"):
			self.assertEqual(state.initial_status(payment_type), state.ISSUED)
			self.assertEqual(
				state.allowed_actions(payment_type, state.ISSUED, amount=1000),
				["pay", "withdraw", "reschedule"],
			)

	def test_under_collection_can_only_be_collected_or_rejected(self):
		self.assertEqual(
			state.allowed_actions("Receive", state.UNDER_COLLECTION, amount=1000), ["collect", "reject"]
		)

	def test_final_statuses_allow_nothing(self):
		for status in (state.COLLECTED, state.CASHED, state.ENDORSED, state.RETURNED):
			self.assertEqual(state.allowed_actions("Receive", status, amount=1000), [], status)
		for status in (state.PAID, state.WITHDRAWN):
			self.assertEqual(state.allowed_actions("Pay", status, amount=1000), [], status)

	def test_a_collected_cheque_cannot_be_collected_again(self):
		self.assertFalse(state.is_allowed("collect", "Receive", state.COLLECTED, 1000))
		self.assertFalse(state.is_allowed("cash", "Receive", state.CASHED, 1000))

	def test_rejected_cheque(self):
		self.assertEqual(
			state.allowed_actions("Receive", state.REJECTED, amount=1000),
			["deposit", "return_to_party", "return_to_wallet", "encash"],
		)

	def test_partly_encashed_cheque_cannot_go_back_whole(self):
		# 400 of 1000 was encashed: redepositing or re-walleting the full
		# cheque would double count the 400.
		actions = state.allowed_actions("Receive", state.PARTIALLY_ENCASHED, amount=1000, encashed=400)
		self.assertEqual(actions, ["return_to_party", "encash"])
		self.assertFalse(state.is_allowed("deposit", "Receive", state.REJECTED, 1000, 400))
		self.assertFalse(state.is_allowed("return_to_wallet", "Receive", state.REJECTED, 1000, 400))

	def test_fully_encashed_cheque_has_nothing_left(self):
		self.assertEqual(
			state.allowed_actions("Receive", state.PARTIALLY_ENCASHED, amount=1000, encashed=1000), []
		)

	def test_actions_do_not_cross_payment_types(self):
		self.assertFalse(state.is_allowed("pay", "Receive", state.IN_WALLET, 1000))
		self.assertFalse(state.is_allowed("deposit", "Pay", state.ISSUED, 1000))
		self.assertFalse(state.is_allowed("deposit", "Pay", state.IN_WALLET, 1000))

	def test_unknown_action_or_status(self):
		self.assertFalse(state.is_allowed("launder", "Receive", state.IN_WALLET, 1000))
		self.assertEqual(state.allowed_actions("Receive", None, amount=1000), [])
		self.assertEqual(state.allowed_actions("Receive", "", amount=1000), [])

	def test_status_field(self):
		self.assertEqual(state.status_field("Receive"), "cheque_status")
		self.assertEqual(state.status_field("Pay"), "cheque_status_pay")
		self.assertEqual(state.status_field("Internal Transfer"), "cheque_status_pay")

	def test_every_action_source_is_a_known_status(self):
		known = set(state.RECEIVE_STATUSES) | set(state.PAY_STATUSES)
		for action in state.ACTIONS.values():
			self.assertTrue(set(action.sources) <= known, action.name)

	def test_legacy_statuses_map_to_known_statuses(self):
		self.assertEqual(set(state.LEGACY_RECEIVE_STATUSES.values()), set(state.RECEIVE_STATUSES))
		self.assertEqual(set(state.LEGACY_PAY_STATUSES.values()), set(state.PAY_STATUSES))
		for field, status in state.LEGACY_CANCEL_RESTORE.values():
			self.assertIn(field, ("cheque_status", "cheque_status_pay"))
			self.assertIn(status, state.RECEIVE_STATUSES + state.PAY_STATUSES)

	def test_only_reschedule_posts_no_entry(self):
		self.assertEqual([a.name for a in state.ACTIONS.values() if not a.posts_entry], ["reschedule"])


if __name__ == "__main__":
	unittest.main()
