# Treasury

Cheque lifecycle and bank guarantee management for **ERPNext v16.50+** / **Frappe v16.51+**, by nsdLabs.

Treasury replaces three earlier apps — `ecs_cheques`, `ecs_bguarantee` and `nsd_bguarantee` — with one app that
keeps their features and data, moves every accounting decision to the server, and uses the v16 desk (app tile,
rail and module sidebars).

## What it does

### Cheques

A cheque is a Payment Entry whose **Mode of Payment** is of type **Cheque**. After it is submitted, the
**Cheque Actions** menu on the form offers only the actions that are valid for its current status. Each action
asks for a date (and whatever else it needs), posts one Journal Entry, and updates the status.

| Received cheque | From status | To status | Posting |
|---|---|---|---|
| Deposit Under Collection | In Wallet, Rejected | Under Collection | Dr under collection · Cr wallet (or rejected cheques); optional bank commission |
| Collect Cheque | Under Collection | Collected | Dr bank · Cr under collection |
| Reject Cheque | Under Collection | Rejected | Dr rejected cheques · Cr under collection; optional bank commission |
| Cash Cheque | In Wallet | Cashed | Dr cash · Cr wallet |
| Endorse Cheque | In Wallet | Endorsed | Dr endorsee party account · Cr wallet |
| Transfer to Another Wallet | In Wallet | In Wallet | Dr new wallet · Cr old wallet |
| Return to Party | In Wallet, Rejected, Partially Encashed | Returned | Dr party account · Cr wallet (or rejected cheques) |
| Return to Wallet | Rejected | In Wallet | Dr wallet · Cr rejected cheques |
| Encash Cheque | Rejected, Partially Encashed | Partially Encashed / Cashed | Dr cash · Cr rejected cheques |

| Issued cheque | From status | To status | Posting |
|---|---|---|---|
| Pay Cheque | Issued | Paid | Dr cheques payable · Cr bank |
| Withdraw Cheque | Issued | Withdrawn | Dr cheques payable · Cr party account |

**Change Due Date** records a new due date without a posting.

Cancelling an action's Journal Entry puts the cheque back exactly as it was; actions are cancelled newest first.
Cancelling the Payment Entry cancels all of its cheque entries.

**Multiple Cheque Entry** registers a batch of cheques for one party. The Payment Entries are created on the
server inside the submit, so a batch is registered completely or not at all.

Reports: **Cheques Report**, **Customer Balance with Cheque Status**.

### Bank guarantees

Treasury extends ERPNext's **Bank Guarantee** with four purposes — Bank Guarantee (with or without banking
facilities), Cheque, Cash and Deduction — each for guarantees you provide or receive.

- **Submit** issues the guarantee and posts the issue entry.
- **Actions → Extend Validity** moves the end date.
- A daily job marks guarantees past their end date as **Expired**.
- **Actions → Return Guarantee** closes it and posts the return entry.
- Cancelling the return entry re-opens the guarantee; cancelling the guarantee cancels its entries.

Report: **Bank Guarantee Report**.

## Installation

```bash
bench get-app https://github.com/OneSpaceerp/treasury
bench --site <site> install-app treasury
bench build --app treasury
```

Requires ERPNext 16.50 or later on Frappe 16.51 or later (Python 3.14).

## Setup

1. **Company → Treasury tab** — set the incoming cheques wallet, rejected cheques and bank commissions accounts,
   and the three guarantee cheque accounts.
2. **Mode of Payment** — create one per cheque wallet with type **Cheque**, and give it the wallet account for
   each company.
3. **Bank** — tick **Company Bank** on the banks your company holds accounts with.
4. **Bank Account** (company accounts) — set the **Cheques Under Collection Account** and the **Cheques Payable
   Account**, and optionally the standard collection and rejection commissions.
5. **Customer Group** — optionally set a default **Guarantee Deposit Account**.

All Treasury accounts must be in the company currency; see Limitations.

## Upgrading from the earlier apps

Run the read-only pre-flight check first. It changes nothing, and needs only `bench get-app`, not an install.
`bench execute` refuses apps that are not installed on the site, so run it from the site console:

```bash
bench --site <site> console
```

```python
from treasury.legacy import preflight; preflight()
```

It lists open cheques held in foreign-currency accounts (Treasury cannot act on these), draft cheque payments
missing a bank account, bank accounts missing cheque accounts, and whether the payback account doubles as the
incoming wallet.

**Rejected cheques account.** Treasury posts a rejected cheque to the account in *Rejected Cheques Account* (the old
*Default Payback Cheque Wallet Account* field) and takes it back out of the same account when the cheque is
redeposited, encashed or returned. The old app used two different accounts for this, which left balances behind.
If cheques rejected under the old app are still open, their value is in the old payback account: leave the field
on that account until they are settled, then switch it to a dedicated rejected-cheques account.

Treasury refuses to install while `ecs_cheques`, `ecs_bguarantee` or `nsd_bguarantee` is installed, because they
hook the same documents and every posting would be made twice. Take a backup, then:

```bash
bench --site <site> backup --with-files
bench --site <site> remove-from-installed-apps ecs_cheques
bench --site <site> remove-from-installed-apps ecs_bguarantee     # or nsd_bguarantee
bench --site <site> install-app treasury
bench --site <site> migrate
```

Use `remove-from-installed-apps`, not `uninstall-app`: it leaves the data in place. On install Treasury then

- keeps the existing fields (it uses the same fieldnames) and takes over their definitions,
- converts the Arabic cheque statuses stored by `ecs_cheques` to its own English statuses, which are shown in
  Arabic through translation,
- records the default company on existing guarantees, which previously had none,
- removes form fields that cheque actions replace (their database columns and values are kept), and the old
  workspaces.

The conversion is idempotent and can be re-run: `bench --site <site> execute treasury.legacy.migrate_legacy_data`.

Site-wide settings the old apps forced on Journal Entry and Payment Entry (a single naming series and a default
print format) are **not** shipped by Treasury. Existing sites keep whatever they have; change them in Customize
Form if you want the ERPNext defaults back.

## Limitations

- **Company currency only.** Cheque wallets, bank and party accounts used in Treasury postings must be in the
  company currency. A cheque or posting involving a foreign-currency account is refused with a clear message.
- Guarantees are booked in the company currency.

## Development

```bash
python -m unittest treasury.tests.test_cheque_state      # state machine, no site needed
bench --site <site> run-tests --app treasury             # integration tests
```

Custom fields are defined in `treasury/custom_fields.py` and applied on install and after every migrate; there
are no fixtures to export.

## License

MIT
