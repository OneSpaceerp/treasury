// Copyright (c) 2026, nsdLabs and contributors
// For license information, please see license.txt

// The Payment Entries of a batch are created by the server on submit; this
// script only helps fill the form.

function set_accounts(frm) {
	if (frm.doc.docstatus !== 0 || !frm.doc.company || !frm.doc.payment_type) return;
	frappe.call({
		method: "treasury.cheques.doctype.multiple_cheque_entry.multiple_cheque_entry.get_cheque_accounts",
		args: {
			company: frm.doc.company,
			payment_type: frm.doc.payment_type,
			party_type: frm.doc.party_type,
			party: frm.doc.party,
			mode_of_payment: frm.doc.mode_of_payment,
			bank_acc: frm.doc.bank_acc,
		},
		callback(r) {
			const accounts = r.message || {};
			["paid_from", "paid_to", "account", "payable_account", "collection_fee_account"].forEach((f) =>
				frm.set_value(f, accounts[f] || "")
			);
			if (accounts.cheque_bank && frm.doc.cheque_bank !== accounts.cheque_bank) {
				frm.doc.cheque_bank = accounts.cheque_bank;
				frm.refresh_field("cheque_bank");
			}
		},
	});
}

function set_total(frm) {
	const rows = (frm.doc.payment_type === "Receive" ? frm.doc.cheque_table : frm.doc.cheque_table_2) || [];
	frm.set_value(
		"total_amount",
		rows.reduce((total, row) => total + flt(row.paid_amount), 0)
	);
}

function set_row_names(frm, cdt, cdn) {
	// Who a cheque is made out to, and by whom, follows from the direction of
	// the payment when the first beneficiary is the company itself.
	const row = locals[cdt][cdn];
	const party = frm.doc.party_name || frm.doc.party || "";
	const receiving = frm.doc.payment_type === "Receive";
	if (row.first_beneficiary === "Company") {
		frappe.model.set_value(cdt, cdn, "person_name", receiving ? frm.doc.company : party);
		frappe.model.set_value(cdt, cdn, "issuer_name", receiving ? party : frm.doc.company);
	} else if (row.first_beneficiary === "Personal") {
		frappe.model.set_value(cdt, cdn, "person_name", "");
		frappe.model.set_value(cdt, cdn, "issuer_name", receiving ? party : frm.doc.company);
	}
}

frappe.ui.form.on("Multiple Cheque Entry", {
	setup(frm) {
		frm.set_query("mode_of_payment", () => ({ filters: { type: "Cheque", enabled: 1 } }));
		frm.set_query("party_type", () => ({ filters: { name: ["in", ["Customer", "Supplier"]] } }));
		frm.set_query("cheque_bank", () => ({ filters: { company_bank: 1 } }));
		frm.set_query("bank_acc", () => {
			const filters = { is_company_account: 1, company: frm.doc.company };
			if (frm.doc.cheque_bank) filters.bank = frm.doc.cheque_bank;
			return { filters };
		});
	},

	refresh(frm) {
		if (frm.doc.docstatus === 1) {
			frm.add_custom_button(
				__("Payment Entries"),
				() => frappe.set_route("List", "Payment Entry", { reference_link: frm.doc.name }),
				__("View")
			);
		}
	},

	company: set_accounts,
	mode_of_payment: set_accounts,
	bank_acc: set_accounts,

	payment_type(frm) {
		frm.set_value("party_type", frm.doc.payment_type === "Pay" ? "Supplier" : "Customer");
		set_accounts(frm);
		set_total(frm);
	},

	party_type(frm) {
		frm.set_value("party", "");
		frm.set_value("party_name", "");
	},

	party(frm) {
		if (!frm.doc.party) {
			frm.set_value("party_name", "");
			return;
		}
		const field = frm.doc.party_type === "Customer" ? "customer_name" : "supplier_name";
		frappe.db.get_value(frm.doc.party_type, frm.doc.party, field).then((r) => {
			frm.set_value("party_name", (r.message && r.message[field]) || frm.doc.party);
		});
		set_accounts(frm);
	},

	cheque_bank(frm) {
		if (frm.doc.bank_acc) frm.set_value("bank_acc", "");
	},
});

["Cheque Table Receive", "Cheque Table Pay"].forEach((child) => {
	frappe.ui.form.on(child, {
		paid_amount: set_total,
		cheque_table_remove: set_total,
		cheque_table_2_remove: set_total,
		first_beneficiary: set_row_names,
	});
});
