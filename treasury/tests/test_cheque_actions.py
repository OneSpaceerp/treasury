import frappe
from frappe.utils import nowdate

from treasury.accounting import get_treasury_entries
from treasury.cheques import state
from treasury.cheques.actions import apply_cheque_action, get_available_actions
from treasury.tests.utils import COMPANY, CUSTOMER, SECOND_WALLET, SUPPLIER, WALLET, TreasuryTestCase


class TestChequeActions(TreasuryTestCase):
	def act(self, cheque, action, **kwargs):
		result = apply_cheque_action(cheque.name, action, posting_date=nowdate(), **kwargs)
		cheque.reload()
		return result

	def test_submit_puts_cheque_in_wallet(self):
		cheque = self.make_received_cheque(1000)
		self.assertEqual(cheque.cheque_status, state.IN_WALLET)
		self.assertEqual(cheque.cheque_wallet_account, self.accounts.wallet)
		self.assertEqual(cheque.remaining_amount, 1000)

	def test_deposit_then_collect(self):
		bank_before = self.balance(self.accounts.bank)
		wallet_before = self.balance(self.accounts.wallet)
		cheque = self.make_received_cheque(1000)

		self.act(cheque, "deposit", bank_account=self.bank_account, commission=25)
		self.assertEqual(cheque.cheque_status, state.UNDER_COLLECTION)
		self.assertEqual(cheque.bank_acc, self.bank_account)
		self.assertEqual(self.balance(self.accounts.wallet), wallet_before)

		self.act(cheque, "collect")
		self.assertEqual(cheque.cheque_status, state.COLLECTED)
		self.assertEqual(str(cheque.clearance_date), nowdate())
		# 1000 collected, less the 25 commission the bank took on deposit
		self.assertEqual(self.balance(self.accounts.bank), bank_before + 975)

	def test_action_not_valid_for_status_is_refused(self):
		cheque = self.make_received_cheque(1000)
		self.assertRaises(frappe.ValidationError, self.act, cheque, "collect")
		self.act(cheque, "deposit", bank_account=self.bank_account)
		self.act(cheque, "collect")
		# A collected cheque cannot be collected, cashed or deposited again.
		for action in ("collect", "cash", "deposit"):
			self.assertRaises(
				frappe.ValidationError, self.act, cheque, action, bank_account=self.bank_account
			)
		self.assertEqual(get_available_actions(cheque.name)["actions"], [])

	def test_rejected_cheque_returned_to_party_clears_rejected_account(self):
		rejected_before = self.balance(self.accounts.rejected)
		wallet_before = self.balance(self.accounts.wallet)
		collection_before = self.balance(self.accounts.under_collection)
		receivable_before = self.balance(self.accounts.receivable, "Customer", CUSTOMER)

		cheque = self.make_received_cheque(1000)
		self.act(cheque, "deposit", bank_account=self.bank_account)
		self.act(cheque, "reject", reason="Insufficient funds")
		self.assertEqual(cheque.cheque_status, state.REJECTED)
		self.assertEqual(self.balance(self.accounts.rejected), rejected_before + 1000)

		self.act(cheque, "return_to_party")
		self.assertEqual(cheque.cheque_status, state.RETURNED)
		# The cheque left every holding account and the customer owes again.
		self.assertEqual(self.balance(self.accounts.rejected), rejected_before)
		self.assertEqual(self.balance(self.accounts.wallet), wallet_before)
		self.assertEqual(self.balance(self.accounts.under_collection), collection_before)
		self.assertEqual(self.balance(self.accounts.receivable, "Customer", CUSTOMER), receivable_before)

	def test_rejected_cheque_returned_to_wallet(self):
		rejected_before = self.balance(self.accounts.rejected)
		wallet_before = self.balance(self.accounts.wallet)
		cheque = self.make_received_cheque(1000)
		self.act(cheque, "deposit", bank_account=self.bank_account)
		self.act(cheque, "reject", reason="Signature mismatch")
		self.act(cheque, "return_to_wallet")
		self.assertEqual(cheque.cheque_status, state.IN_WALLET)
		self.assertEqual(self.balance(self.accounts.rejected), rejected_before)
		self.assertEqual(self.balance(self.accounts.wallet), wallet_before + 1000)

	def test_partial_then_full_encashment(self):
		cheque = self.make_received_cheque(1000)
		self.act(cheque, "deposit", bank_account=self.bank_account)
		self.act(cheque, "reject", reason="Insufficient funds")

		self.act(cheque, "encash", amount=400)
		self.assertEqual(cheque.cheque_status, state.PARTIALLY_ENCASHED)
		self.assertEqual(cheque.encashed_amount, 400)
		self.assertEqual(cheque.remaining_amount, 600)
		self.assertRaises(frappe.ValidationError, self.act, cheque, "encash", amount=601)
		self.assertRaises(frappe.ValidationError, self.act, cheque, "deposit", bank_account=self.bank_account)

		self.act(cheque, "encash", amount=600)
		self.assertEqual(cheque.cheque_status, state.CASHED)
		self.assertEqual(cheque.remaining_amount, 0)

	def test_cancelling_entry_restores_previous_state(self):
		cheque = self.make_received_cheque(1000)
		deposit = self.act(cheque, "deposit", bank_account=self.bank_account)["journal_entry"]
		collect = self.act(cheque, "collect")["journal_entry"]

		# Newest first: the deposit cannot be cancelled while the collection stands.
		self.assertRaises(frappe.ValidationError, frappe.get_doc("Journal Entry", deposit).cancel)

		frappe.get_doc("Journal Entry", collect).cancel()
		cheque.reload()
		self.assertEqual(cheque.cheque_status, state.UNDER_COLLECTION)
		self.assertIsNone(cheque.clearance_date)

		frappe.get_doc("Journal Entry", deposit).cancel()
		cheque.reload()
		self.assertEqual(cheque.cheque_status, state.IN_WALLET)
		self.assertFalse(cheque.bank_acc)

	def test_cancelling_cheque_cancels_its_entries(self):
		wallet_before = self.balance(self.accounts.wallet)
		collection_before = self.balance(self.accounts.under_collection)
		cheque = self.make_received_cheque(1000)
		self.act(cheque, "deposit", bank_account=self.bank_account)
		self.act(cheque, "collect")
		cheque.cancel()
		self.assertEqual(get_treasury_entries("Payment Entry", cheque.name), [])
		self.assertEqual(self.balance(self.accounts.wallet), wallet_before)
		self.assertEqual(self.balance(self.accounts.under_collection), collection_before)

	def test_transfer_moves_the_wallet_account(self):
		second_before = self.balance(self.accounts.second_wallet)
		cheque = self.make_received_cheque(1000)
		self.act(cheque, "transfer", mode_of_payment=SECOND_WALLET)
		self.assertEqual(cheque.new_mode_of_payment, SECOND_WALLET)
		self.assertEqual(cheque.cheque_wallet_account, self.accounts.second_wallet)
		self.assertEqual(self.balance(self.accounts.second_wallet), second_before + 1000)
		# Later actions draw on the wallet the cheque is actually in.
		self.act(cheque, "cash")
		self.assertEqual(self.balance(self.accounts.second_wallet), second_before)

	def test_endorse(self):
		payable_before = self.balance(self.accounts.payable_party, "Supplier", SUPPLIER)
		cheque = self.make_received_cheque(1000)
		self.act(cheque, "endorse", party_type="Supplier", party=SUPPLIER)
		self.assertEqual(cheque.cheque_status, state.ENDORSED)
		self.assertEqual(cheque.party_, SUPPLIER)
		self.assertEqual(
			self.balance(self.accounts.payable_party, "Supplier", SUPPLIER), payable_before + 1000
		)

	def test_issued_cheque_paid(self):
		bank_before = self.balance(self.accounts.bank)
		payable_before = self.balance(self.accounts.payable)
		cheque = self.make_issued_cheque(1000)
		self.assertEqual(cheque.cheque_status_pay, state.ISSUED)
		self.assertEqual(self.balance(self.accounts.payable), payable_before - 1000)
		self.act(cheque, "pay")
		self.assertEqual(cheque.cheque_status_pay, state.PAID)
		self.assertEqual(self.balance(self.accounts.payable), payable_before)
		self.assertEqual(self.balance(self.accounts.bank), bank_before - 1000)

	def test_issued_cheque_withdrawn(self):
		payable_before = self.balance(self.accounts.payable)
		supplier_before = self.balance(self.accounts.payable_party, "Supplier", SUPPLIER)
		cheque = self.make_issued_cheque(1000)
		self.act(cheque, "withdraw")
		self.assertEqual(cheque.cheque_status_pay, state.WITHDRAWN)
		self.assertEqual(self.balance(self.accounts.payable), payable_before)
		self.assertEqual(self.balance(self.accounts.payable_party, "Supplier", SUPPLIER), supplier_before)

	def test_duplicate_cheque_is_refused(self):
		cheque = self.make_received_cheque(1000)
		self.assertRaises(
			frappe.ValidationError, self.make_received_cheque, 1000, reference_no=cheque.reference_no
		)

	def test_copy_does_not_inherit_cheque_state(self):
		cheque = self.make_received_cheque(1000)
		self.act(cheque, "deposit", bank_account=self.bank_account)
		self.act(cheque, "collect")
		copy = frappe.copy_doc(cheque)
		copy.reference_no = self.next_cheque_no()
		copy.insert()
		self.assertFalse(copy.cheque_status)
		self.assertFalse(copy.bank_acc)
		copy.submit()
		copy.reload()
		self.assertEqual(copy.cheque_status, state.IN_WALLET)


class TestMultipleChequeEntry(TreasuryTestCase):
	def make_entry(self, payment_type="Receive", amounts=(100, 200, 300)):
		receiving = payment_type == "Receive"
		doc = frappe.new_doc("Multiple Cheque Entry")
		doc.update(
			{
				"company": COMPANY,
				"posting_date": nowdate(),
				"payment_type": payment_type,
				"mode_of_payment": WALLET,
				"party_type": "Customer" if receiving else "Supplier",
				"party": CUSTOMER if receiving else SUPPLIER,
				"bank_acc": None if receiving else self.bank_account,
			}
		)
		for amount in amounts:
			row = {
				"reference_no": self.next_cheque_no(),
				"reference_date": nowdate(),
				"paid_amount": amount,
				"cheque_type": "Crossed",
				"first_beneficiary": "Company",
				"issuer_name": "Issuer",
				"person_name": "Beneficiary",
			}
			if receiving:
				row["bank"] = frappe.db.get_value("Bank", {"company_bank": 1}, "name")
			doc.append("cheque_table" if receiving else "cheque_table_2", row)
		doc.insert()
		return doc

	def test_submit_registers_every_cheque(self):
		entry = self.make_entry("Receive")
		self.assertEqual(entry.total_amount, 600)
		entry.submit()
		entry.reload()
		names = [row.payment_entry for row in entry.cheque_table]
		self.assertEqual(len([name for name in names if name]), 3)
		for name in names:
			cheque = frappe.get_doc("Payment Entry", name)
			self.assertEqual(cheque.docstatus, 1)
			self.assertEqual(cheque.cheque_status, state.IN_WALLET)
			self.assertEqual(cheque.reference_link, entry.name)

	def test_issued_batch_uses_bank_payable_account(self):
		entry = self.make_entry("Pay", amounts=(500,))
		self.assertEqual(entry.paid_from, self.accounts.payable)
		entry.submit()
		entry.reload()
		cheque = frappe.get_doc("Payment Entry", entry.cheque_table_2[0].payment_entry)
		self.assertEqual(cheque.cheque_status_pay, state.ISSUED)
		self.assertEqual(cheque.bank_acc, self.bank_account)

	def test_cancel_cancels_untouched_cheques(self):
		entry = self.make_entry("Receive")
		entry.submit()
		entry.reload()
		names = [row.payment_entry for row in entry.cheque_table]
		entry.cancel()
		for name in names:
			self.assertEqual(frappe.db.get_value("Payment Entry", name, "docstatus"), 2)

	def test_cancel_refused_once_a_cheque_has_moved(self):
		entry = self.make_entry("Receive")
		entry.submit()
		entry.reload()
		apply_cheque_action(
			entry.cheque_table[0].payment_entry,
			"deposit",
			posting_date=nowdate(),
			bank_account=self.bank_account,
		)
		self.assertRaises(frappe.ValidationError, entry.cancel)
