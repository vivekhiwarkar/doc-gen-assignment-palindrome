# Internal notes: data sources

For whoever configures the report: what each source is, and how they relate.

## The sources

- **client_data_db.json**: a snapshot of the client's accounts taken from our custody systems on a
  schedule. It carries a `snapshot_date`, and every account carries its own `valuation_date`, the
  day that figure was last struck. It is the system of record for which accounts exist and who holds
  them.
- **meeting_notes.docx**: the adviser's file note from the most recent meeting. A free-form record
  of what was discussed and what the client decided.
- **report_request.docx**: the adviser's instruction for this particular report: which accounts to
  cover and the headline instruction.

## How they fit together

The db is where account structure and ownership live. The meeting note is where the conversation and
the decisions live. The two are produced at different times and for different reasons, so read the
date on a figure before you rely on it.

## Account data

Accounts are listed per holder. A jointly-held account is recorded under each holder it belongs to.
Not every field is populated for every account: a field may be blank where a figure was not captured
at snapshot time, and an account may carry a status.

## This client
The new money being invested is an inheritance the client received following the recent death of her mother. Reference its origin with appropriate sensitivity.
