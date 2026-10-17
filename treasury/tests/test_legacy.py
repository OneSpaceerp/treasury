import frappe

from treasury.legacy import migrate_legacy_data, preflight
from treasury.tests.utils import TreasuryTestCase


class TestLegacy(TreasuryTestCase):
	def test_preflight_runs(self):
		# Read-only report: it must run on any site and change nothing.
		self.make_received_cheque(1000)
		before = frappe.db.count("Payment Entry")
		preflight()
		self.assertEqual(frappe.db.count("Payment Entry"), before)

	def test_legacy_statuses_are_translated(self):
		cheque = self.make_received_cheque(1000)
		frappe.db.set_value("Payment Entry", cheque.name, "cheque_status", "تحت التحصيل")
		migrate_legacy_data()
		self.assertEqual(
			frappe.db.get_value("Payment Entry", cheque.name, "cheque_status"), "Under Collection"
		)

	def test_fully_encashed_legacy_cheque_becomes_cashed(self):
		cheque = self.make_received_cheque(1000)
		frappe.db.set_value(
			"Payment Entry", cheque.name, {"cheque_status": "حافظة شيكات مرجعة", "encashed_amount": 1000}
		)
		migrate_legacy_data()
		self.assertEqual(frappe.db.get_value("Payment Entry", cheque.name, "cheque_status"), "Cashed")
