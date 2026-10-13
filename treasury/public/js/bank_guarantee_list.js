// Copyright (c) 2026, nsdLabs and contributors
// For license information, please see license.txt

frappe.listview_settings["Bank Guarantee"] = {
	add_fields: ["bank_guarantee_status", "docstatus"],
	get_indicator(doc) {
		if (doc.docstatus !== 1 || !doc.bank_guarantee_status) return;
		const color = {
			Issued: "blue",
			Extended: "purple",
			Expired: "red",
			Returned: "green",
		}[doc.bank_guarantee_status];
		if (!color) return;
		return [
			__(doc.bank_guarantee_status),
			color,
			`bank_guarantee_status,=,${doc.bank_guarantee_status}`,
		];
	},
};
