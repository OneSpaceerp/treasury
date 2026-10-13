"""Fixtures shared by the Treasury integration tests.

Everything is created under a dedicated company so the tests do not depend on
ERPNext's own test records.
"""

# Importing ERPNext's test utilities bootstraps the master data a bare test
# site lacks: fiscal years, party types, customer/supplier groups, territories.
import erpnext.tests.utils
import frappe
from erpnext.accounts.utils import get_balance_on
from frappe.tests import IntegrationTestCase
from frappe.utils import flt, nowdate

COMPANY = "_Treasury Test Company"
ABBR = "_TTC"
BANK = "_Treasury Test Bank"
WALLET = "_Treasury Cheque Wallet"
SECOND_WALLET = "_Treasury Second Wallet"
CUSTOMER = "_Treasury Test Customer"
SUPPLIER = "_Treasury Test Supplier"


def account_name(name: str) -> str:
	return f"{name} - {ABBR}"


def make_account(name: str, parent: str, account_type: str | None = None) -> str:
	full_name = account_name(name)
	if not frappe.db.exists("Account", full_name):
		frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": name,
				"parent_account": account_name(parent),
				"company": COMPANY,
				"account_type": account_type,
				"is_group": 0,
			}
		).insert(ignore_permissions=True)
	return full_name


def make_mode_of_payment(name: str, account: str) -> str:
	if not frappe.db.exists("Mode of Payment", name):
		frappe.get_doc(
			{
				"doctype": "Mode of Payment",
				"mode_of_payment": name,
				"type": "Cheque",
				"enabled": 1,
				"accounts": [{"company": COMPANY, "default_account": account}],
			}
		).insert(ignore_permissions=True)
	return name


def setup_treasury_fixtures() -> frappe._dict:
	if not frappe.db.exists("Company", COMPANY):
		frappe.get_doc(
			{
				"doctype": "Company",
				"company_name": COMPANY,
				"abbr": ABBR,
				"default_currency": "USD",
				"country": "United States",
				"create_chart_of_accounts_based_on": "Standard Template",
				"chart_of_accounts": "Standard",
			}
		).insert(ignore_permissions=True)

	accounts = frappe._dict(
		wallet=make_account("Cheques In Wallet", "Current Assets"),
		second_wallet=make_account("Cheques In Second Wallet", "Current Assets"),
		under_collection=make_account("Cheques Under Collection", "Current Assets"),
		rejected=make_account("Rejected Cheques", "Current Assets"),
		bank=make_account("Treasury Test Bank Current", "Bank Accounts", "Bank"),
		facility=make_account("Treasury Test Bank Facility", "Bank Accounts", "Bank"),
		payable=make_account("Cheques Payable", "Current Liabilities"),
		commissions=make_account("Bank Commissions", "Indirect Expenses"),
		deposits=make_account("Guarantee Deposits", "Current Assets"),
		fees=make_account("Guarantee Fees", "Indirect Expenses"),
		guarantee_cheques_issued=make_account("Guarantee Cheques Issued", "Current Liabilities"),
		guarantee_cheques_received=make_account("Guarantee Cheques Received", "Current Assets"),
		guarantee_cheques_contra=make_account("Guarantee Cheques Contra", "Current Liabilities"),
	)
	company = frappe.get_doc("Company", COMPANY)
	accounts.cash = company.default_cash_account
	accounts.receivable = company.default_receivable_account
	accounts.payable_party = company.default_payable_account
	company.update(
		{
			"default_incoming_cheque_wallet_account": accounts.wallet,
			"default_payback_cheque_wallet_account": accounts.rejected,
			"default_bank_commissions_account": accounts.commissions,
			"acc1": accounts.guarantee_cheques_issued,
			"acc2": accounts.guarantee_cheques_contra,
			"acc3": accounts.guarantee_cheques_received,
		}
	)
	company.save(ignore_permissions=True)

	if not frappe.db.exists("Bank", BANK):
		frappe.get_doc({"doctype": "Bank", "bank_name": BANK, "company_bank": 1}).insert(
			ignore_permissions=True
		)

	bank_account = frappe.db.get_value("Bank Account", {"account": accounts.bank}, "name")
	if not bank_account:
		bank_account = (
			frappe.get_doc(
				{
					"doctype": "Bank Account",
					"account_name": "Treasury Test Current",
					"bank": BANK,
					"is_company_account": 1,
					"company": COMPANY,
					"account": accounts.bank,
					"collection_fee_account": accounts.under_collection,
					"payable_account": accounts.payable,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)
	facility_account = frappe.db.get_value("Bank Account", {"account": accounts.facility}, "name")
	if not facility_account:
		facility_account = (
			frappe.get_doc(
				{
					"doctype": "Bank Account",
					"account_name": "Treasury Test Facility",
					"bank": BANK,
					"is_company_account": 1,
					"company": COMPANY,
					"account": accounts.facility,
				}
			)
			.insert(ignore_permissions=True)
			.name
		)

	make_mode_of_payment(WALLET, accounts.wallet)
	make_mode_of_payment(SECOND_WALLET, accounts.second_wallet)

	if not frappe.db.exists("Customer", CUSTOMER):
		frappe.get_doc(
			{
				"doctype": "Customer",
				"customer_name": CUSTOMER,
				"customer_type": "Company",
				"customer_group": frappe.db.get_value("Customer Group", {"is_group": 0}, "name"),
				"territory": frappe.db.get_value("Territory", {"is_group": 0}, "name"),
			}
		).insert(ignore_permissions=True)
	if not frappe.db.exists("Supplier", SUPPLIER):
		frappe.get_doc(
			{
				"doctype": "Supplier",
				"supplier_name": SUPPLIER,
				"supplier_group": frappe.db.get_value("Supplier Group", {"is_group": 0}, "name"),
			}
		).insert(ignore_permissions=True)

	return frappe._dict(accounts=accounts, bank_account=bank_account, facility_account=facility_account)


class TreasuryTestCase(IntegrationTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		fixtures = setup_treasury_fixtures()
		cls.accounts = fixtures.accounts
		cls.bank_account = fixtures.bank_account
		cls.facility_account = fixtures.facility_account
		cls._cheque_no = 0

	def balance(self, account: str, party_type: str | None = None, party: str | None = None) -> float:
		return flt(
			get_balance_on(
				account=account, date=nowdate(), party_type=party_type, party=party, company=COMPANY
			)
		)

	def next_cheque_no(self) -> str:
		type(self)._cheque_no += 1
		return f"TRS-{frappe.generate_hash(length=6)}-{self._cheque_no}"

	def make_received_cheque(self, amount: float = 1000, **values):
		doc = frappe.new_doc("Payment Entry")
		doc.update(
			{
				"payment_type": "Receive",
				"company": COMPANY,
				"posting_date": nowdate(),
				"mode_of_payment": WALLET,
				"party_type": "Customer",
				"party": CUSTOMER,
				"paid_from": self.accounts.receivable,
				"paid_to": self.accounts.wallet,
				"paid_amount": amount,
				"received_amount": amount,
				"source_exchange_rate": 1,
				"target_exchange_rate": 1,
				"reference_no": self.next_cheque_no(),
				"reference_date": nowdate(),
				"cheque_type": "Crossed",
				"first_beneficiary": "Company",
				"issuer_name": CUSTOMER,
				"drawn_bank": BANK,
			}
		)
		doc.update(values)
		doc.insert()
		doc.submit()
		doc.reload()
		return doc

	def make_issued_cheque(self, amount: float = 1000, **values):
		doc = frappe.new_doc("Payment Entry")
		doc.update(
			{
				"payment_type": "Pay",
				"company": COMPANY,
				"posting_date": nowdate(),
				"mode_of_payment": WALLET,
				"party_type": "Supplier",
				"party": SUPPLIER,
				"paid_from": self.accounts.payable,
				"paid_to": self.accounts.payable_party,
				"paid_amount": amount,
				"received_amount": amount,
				"source_exchange_rate": 1,
				"target_exchange_rate": 1,
				"reference_no": self.next_cheque_no(),
				"reference_date": nowdate(),
				"cheque_type": "Crossed",
				"first_beneficiary": "Company",
				"issuer_name": COMPANY,
				"bank_acc": self.bank_account,
			}
		)
		doc.update(values)
		doc.insert()
		doc.submit()
		doc.reload()
		return doc
