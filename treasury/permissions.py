import frappe

TREASURY_ROLES = {"System Manager", "Accounts Manager", "Accounts User", "Auditor"}


def has_app_permission() -> bool:
	"""Whether the Treasury tile is shown on the apps screen for the session user."""
	if frappe.session.user == "Administrator":
		return True
	return bool(TREASURY_ROLES.intersection(frappe.get_roles()))
