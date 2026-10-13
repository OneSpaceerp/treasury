"""Journal Entry events: cancelling a Treasury entry rolls its source back.

The checks run in `before_cancel`, before anything is written, and the
rollback of the source document in `on_cancel`, once the entry is cancelled.
"""


def _dispatch(doc, handler_name: str) -> None:
	reference_doctype, reference_name = doc.get("reference_doctype"), doc.get("reference_link")
	if not reference_doctype or not reference_name:
		return

	if reference_doctype == "Payment Entry":
		from treasury.cheques import actions as handlers
	elif reference_doctype == "Bank Guarantee":
		from treasury.guarantees.overrides import bank_guarantee as handlers
	else:
		return
	getattr(handlers, handler_name)(doc)


def before_cancel(doc, method=None):
	_dispatch(doc, "validate_journal_entry_cancel")


def on_cancel(doc, method=None):
	_dispatch(doc, "on_journal_entry_cancel")
