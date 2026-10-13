import frappe
from frappe import _

from treasury.custom_fields import setup_customizations
from treasury.legacy import LEGACY_APPS, migrate_legacy_data


def before_install():
	"""Refuse to install next to the apps Treasury replaces.

	Each of them hooks the same documents, so running side by side would post
	every cheque action and guarantee twice.
	"""
	installed = [app for app in LEGACY_APPS if app in frappe.get_installed_apps()]
	if installed:
		frappe.throw(
			_(
				"Treasury replaces {0}. Remove them from this site first with "
				"`bench --site <site> remove-from-installed-apps <app>` (this keeps their data), "
				"then install Treasury."
			).format(", ".join(installed)),
			title=_("Legacy Apps Installed"),
		)


def after_install():
	setup_customizations()
	# Patches do not run on a fresh install, so data left behind by the
	# replaced apps is brought forward here.
	migrate_legacy_data()


def after_migrate():
	setup_customizations()
