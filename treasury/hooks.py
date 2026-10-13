app_name = "treasury"
app_title = "Treasury"
app_publisher = "nsdLabs"
app_description = "Cheque lifecycle and bank guarantee management for ERPNext"
app_email = "info@nestsoftware.dev"
app_license = "mit"

# Apps
# ------------------

required_apps = ["frappe/erpnext"]

app_logo_url = "/assets/treasury/images/treasury-logo.png"
app_home = "/desk/cheques"

# Each item in the list will be shown as an app in the apps page
add_to_apps_screen = [
	{
		"name": app_name,
		"logo": app_logo_url,
		"title": app_title,
		"route": app_home,
		"has_permission": "treasury.permissions.has_app_permission",
	}
]

# Includes in <head>
# ------------------

# include js in doctype views
doctype_js = {
	"Payment Entry": "public/js/payment_entry.js",
	"Bank Guarantee": "public/js/bank_guarantee.js",
}
doctype_list_js = {"Bank Guarantee": "public/js/bank_guarantee_list.js"}

# Installation
# ------------

before_install = "treasury.install.before_install"
after_install = "treasury.install.after_install"
after_migrate = "treasury.install.after_migrate"

# DocType Class
# ---------------
# Core `BankGuarantee.on_submit` insists on a bank for every guarantee, which
# cash and deduction guarantees do not have, so the controller is extended.

override_doctype_class = {
	"Bank Guarantee": "treasury.guarantees.overrides.bank_guarantee.TreasuryBankGuarantee",
}

# Document Events
# ---------------

doc_events = {
	"Payment Entry": {
		"validate": "treasury.cheques.payment_entry.validate",
		"on_submit": "treasury.cheques.payment_entry.on_submit",
		"on_cancel": "treasury.cheques.payment_entry.on_cancel",
	},
	"Journal Entry": {
		"before_cancel": "treasury.journal_entry.before_cancel",
		"on_cancel": "treasury.journal_entry.on_cancel",
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"daily": ["treasury.guarantees.tasks.expire_guarantees"],
}
