import frappe
from frappe.utils import add_days, nowdate

from treasury.accounting import get_treasury_entries
from treasury.guarantees.overrides.bank_guarantee import extend_guarantee, return_guarantee
from treasury.guarantees.tasks import expire_guarantees
from treasury.tests.utils import BANK, COMPANY, CUSTOMER, SUPPLIER, TreasuryTestCase


class TestBankGuarantee(TreasuryTestCase):
	def make_guarantee(self, purpose="Cash", bg_type="Providing", amount=1000, submit=True, **values):
		doc = frappe.new_doc("Bank Guarantee")
		doc.update(
			{
				"company": COMPANY,
				"bank_guarantee_purpose": purpose,
				"bg_type": bg_type,
				"party_type": "Customer",
				"party": CUSTOMER,
				"customer_address": "1 Test Street",
				"amount": amount,
				"start_date": nowdate(),
				"end_date": add_days(nowdate(), 90),
				"posting_date": nowdate(),
				"dpaccount": self.accounts.deposits,
			}
		)
		if purpose == "Cash":
			doc.cash_account = self.accounts.cash
		elif purpose == "Bank Guarantee":
			doc.update(
				{
					"bank": BANK,
					"bank_account": self.bank_account,
					"bank_guarantee_number": frappe.generate_hash(length=8),
					"status_of_letter_of_guarantee": "Initial",
					"dpaccountc": self.accounts.fees,
				}
			)
		elif purpose == "Cheque":
			doc.update(
				{
					"bank": BANK,
					"reference_no": frappe.generate_hash(length=8),
					"reference_date": nowdate(),
				}
			)
		doc.update(values)
		doc.insert()
		if submit:
			doc.submit()
			doc.reload()
		return doc

	def test_cash_guarantee_needs_no_bank(self):
		deposits_before = self.balance(self.accounts.deposits)
		cash_before = self.balance(self.accounts.cash)
		guarantee = self.make_guarantee("Cash")
		self.assertEqual(guarantee.bank_guarantee_status, "Issued")
		self.assertEqual(guarantee.customer, CUSTOMER)
		self.assertEqual(self.balance(self.accounts.deposits), deposits_before + 1000)
		self.assertEqual(self.balance(self.accounts.cash), cash_before - 1000)

		return_guarantee(guarantee.name, nowdate())
		guarantee.reload()
		self.assertEqual(guarantee.bank_guarantee_status, "Returned")
		self.assertEqual(guarantee.returned, 1)
		self.assertEqual(self.balance(self.accounts.deposits), deposits_before)
		self.assertEqual(self.balance(self.accounts.cash), cash_before)

	def test_supplier_party(self):
		guarantee = self.make_guarantee("Cash", party_type="Supplier", party=SUPPLIER, submit=False)
		self.assertEqual(guarantee.supplier, SUPPLIER)
		self.assertFalse(guarantee.customer)

	def test_bank_guarantee_with_facilities(self):
		bank_before = self.balance(self.accounts.bank)
		facility_before = self.balance(self.accounts.facility)
		fees_before = self.balance(self.accounts.fees)
		guarantee = self.make_guarantee(
			"Bank Guarantee",
			amount=1000,
			bg_commission=50,
			banking_facilities="With Facilities",
			rate=30,
			account_for_bank_facilities=self.facility_account,
		)
		self.assertEqual(guarantee.bank_amount, 300)
		self.assertEqual(guarantee.facility_amount, 700)
		self.assertEqual(guarantee.rate2, 70)
		# cash cover and commission leave the bank, the rest draws on the facility
		self.assertEqual(self.balance(self.accounts.bank), bank_before - 350)
		self.assertEqual(self.balance(self.accounts.facility), facility_before - 700)
		self.assertEqual(self.balance(self.accounts.fees), fees_before + 50)

		return_guarantee(guarantee.name, nowdate())
		# the commission is spent; cover and facility come back
		self.assertEqual(self.balance(self.accounts.bank), bank_before - 50)
		self.assertEqual(self.balance(self.accounts.facility), facility_before)

	def test_bank_guarantee_without_facilities_ignores_client_split(self):
		guarantee = self.make_guarantee(
			"Bank Guarantee", amount=1000, rate=40, bank_amount=1, facility_amount=999
		)
		self.assertEqual(guarantee.banking_facilities, "Without Facilities")
		self.assertEqual(guarantee.rate, 100)
		self.assertEqual(guarantee.bank_amount, 1000)
		self.assertEqual(guarantee.facility_amount, 0)

	def test_deduction_guarantee(self):
		receivable_before = self.balance(self.accounts.receivable, "Customer", CUSTOMER)
		guarantee = self.make_guarantee("Deduction")
		self.assertEqual(
			self.balance(self.accounts.receivable, "Customer", CUSTOMER), receivable_before - 1000
		)
		self.assertRaises(frappe.ValidationError, return_guarantee, guarantee.name, nowdate())
		return_guarantee(guarantee.name, nowdate(), deduction_return=self.accounts.bank)
		guarantee.reload()
		self.assertEqual(guarantee.deduction_return, self.accounts.bank)

	def test_cheque_guarantee_received(self):
		received_before = self.balance(self.accounts.guarantee_cheques_received)
		guarantee = self.make_guarantee("Cheque", bg_type="Receiving")
		self.assertEqual(self.balance(self.accounts.guarantee_cheques_received), received_before + 1000)
		return_guarantee(guarantee.name, nowdate())
		self.assertEqual(self.balance(self.accounts.guarantee_cheques_received), received_before)

	def test_return_twice_is_refused(self):
		guarantee = self.make_guarantee("Cash")
		return_guarantee(guarantee.name, nowdate())
		self.assertRaises(frappe.ValidationError, return_guarantee, guarantee.name, nowdate())
		self.assertEqual(len(get_treasury_entries("Bank Guarantee", guarantee.name)), 2)

	def test_cancelling_return_entry_reopens_guarantee(self):
		guarantee = self.make_guarantee("Cash")
		entry = return_guarantee(guarantee.name, nowdate())["journal_entry"]
		frappe.get_doc("Journal Entry", entry).cancel()
		guarantee.reload()
		self.assertEqual(guarantee.returned, 0)
		self.assertEqual(guarantee.bank_guarantee_status, "Issued")

	def test_issue_entry_cannot_be_cancelled_directly(self):
		guarantee = self.make_guarantee("Cash")
		issue = get_treasury_entries("Bank Guarantee", guarantee.name)[0]
		self.assertRaises(frappe.ValidationError, frappe.get_doc("Journal Entry", issue).cancel)

	def test_cancel_cancels_entries(self):
		deposits_before = self.balance(self.accounts.deposits)
		guarantee = self.make_guarantee("Cash")
		return_guarantee(guarantee.name, nowdate())
		guarantee.reload()
		guarantee.cancel()
		guarantee.reload()
		self.assertEqual(guarantee.bank_guarantee_status, "Cancelled")
		self.assertEqual(get_treasury_entries("Bank Guarantee", guarantee.name), [])
		self.assertEqual(self.balance(self.accounts.deposits), deposits_before)

	def test_extended_guarantee_can_still_be_returned(self):
		guarantee = self.make_guarantee("Cash")
		new_end = add_days(guarantee.end_date, 30)
		extend_guarantee(guarantee.name, str(new_end))
		guarantee.reload()
		self.assertEqual(guarantee.bank_guarantee_status, "Extended")
		self.assertEqual(guarantee.no_of_extended_days, 30)
		self.assertRaises(frappe.ValidationError, extend_guarantee, guarantee.name, str(guarantee.end_date))

		return_guarantee(guarantee.name, nowdate())
		guarantee.reload()
		self.assertEqual(guarantee.bank_guarantee_status, "Returned")

	def test_expiry_job(self):
		guarantee = self.make_guarantee(
			"Cash",
			start_date=add_days(nowdate(), -60),
			end_date=add_days(nowdate(), -1),
			posting_date=add_days(nowdate(), -60),
		)
		live = self.make_guarantee("Cash")
		expire_guarantees()
		self.assertEqual(
			frappe.db.get_value("Bank Guarantee", guarantee.name, "bank_guarantee_status"), "Expired"
		)
		self.assertEqual(frappe.db.get_value("Bank Guarantee", live.name, "bank_guarantee_status"), "Issued")
		# running it again changes nothing, and an expired guarantee can still be returned
		expire_guarantees()
		return_guarantee(guarantee.name, nowdate())
		self.assertEqual(
			frappe.db.get_value("Bank Guarantee", guarantee.name, "bank_guarantee_status"), "Returned"
		)

	def test_copy_starts_as_a_new_guarantee(self):
		guarantee = self.make_guarantee("Cash")
		return_guarantee(guarantee.name, nowdate())
		guarantee.reload()
		copy = frappe.copy_doc(guarantee)
		copy.docstatus = 0  # as the desk's Duplicate does
		copy.insert()
		self.assertEqual(copy.bank_guarantee_status, "New")
		self.assertEqual(copy.issued, 0)
		self.assertEqual(copy.returned, 0)
		copy.submit()
		copy.reload()
		self.assertEqual(copy.bank_guarantee_status, "Issued")
		self.assertEqual(len(get_treasury_entries("Bank Guarantee", copy.name)), 1)
