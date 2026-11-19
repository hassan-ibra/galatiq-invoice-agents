# Design Decisions

The README leaves these cases open. Each one is a rule the system enforces and a test case.

| # | Situation | Invoices | Decision | Why |
|---|-----------|----------|----------|-----|
| 1 | Quantity exceeds stock (summed per item across lines) | 1002, 1005, 1007, 1013 | Hold for human review | Vendor may ship the balance later; high-value ambiguity goes to a person |
| 2 | Same invoice number, original and revised | 1004, 1004_revised | Pay the revised one after normal checks; void the original with a log entry | Acme never pays twice |
| 3 | Non-USD currency | 1014 (EUR) | Hold for human review; do not convert currency | Approval rules are in USD and the system runs offline with no live FX rate |
| 4 | Stated total does not match line items + tax (under or over) | 1007 (-$110), 1013 (+$50) | Reject, reason shows the exact difference | Small mismatches add up across thousands of invoices |
| 5 | Item not in inventory, or zero-stock item | 1008, 1016, 1003 | Reject as suspicious, reason shown | Billing for never-ordered goods is a common invoice scam |
| 6 | Negative quantity / broken totals | 1009 | Reject (data integrity) | Quantities must be positive integers |
| 7 | OCR errors (letter O for zero, "Widget A", "INV 1012") | 1012 | Agent normalizes, logs every correction, then normal checks | Cleaning messy data is the agent's job; corrections stay auditable |
| 8 | Same invoice in two file formats | 1011 (txt+pdf), 1013 (json+pdf) | Group by invoice number, process once, fill missing fields from the other copy; hold if copies disagree | No double payment, no lost data |
| 9 | Same item at different prices; shipping line | 1010 | Shipping allowed as non-stock line; unit price >10% above catalog -> hold | $300 WidgetA is 20% above the $250 catalog price |
| 10 | Suspicious vendor address | 1005 (1600 Pennsylvania Ave), 1004 (742 Evergreen Terrace) | LLM flags a warning -> human review, never auto-reject | Plain code can't recognize a famous fake address; the AI can |
| 11 | All due dates in the past | all | `--as-of` option, default = invoice date; overdue is marked "late" but still payable | Reproducible results; late isn't a reason to refuse a valid bill |
| 12 | Total over $10K | 1002, 1003, 1005, 1007, 1013 | Human approval step with LLM summary + recommendation; `--auto-approve` for automated runs | Human-in-the-loop for high-stakes payments |
| 13 | Stock is valid, but the vendor address is fictional | 1004 (742 Evergreen Terrace) | Stock check passes; hold for human review, never reject | WidgetA 3, WidgetB 2, and GadgetX 5 are all within stock. The address flag is the only hold |
| 14 | Shipping or fees | 1010 | `shipping_amount` is optional. The total check is subtotal + tax + shipping, and a missing shipping amount counts as 0 | 6700.00 + 335.00 + 150.00 = 7185.00, so INV-1010 is not rejected for the total |
| 15 | Printed subtotal, tax, shipping, and total | 1013 | Copy them exactly as printed, including a Grand Total. Never recalculate. A wrong printed total is rejected | INV-1013 stays 22562.80, which is 50.00 over 22512.80 |
| 16 | Relative or unreadable date | 1003 | An unreadable date becomes null and does not fail the invoice. A relative date kept in notes, such as "due date: yesterday", is a hold, not a reject. The offline parser still converts this file's "yesterday" to 2026-01-19 | The sample is rejected for FakeItem. The hold applies when the original words are left in the notes |
| 17 | Missing subtotal | 1002, 1003, 1008 | Skip it. A missing field, None, an empty string, or the text "null", "None", or "N/A" is not a finding | Only real text that cannot be parsed as a number, such as "twelve dollars", is flagged |
| 18 | Grok sampling | every Grok call | temperature 0 | Keeps extraction consistent between runs. Address flags, summaries, and critiques use the same setting |

Deterministic code makes every money, stock and date decision. The LLM extracts, normalizes, summarizes and flags; it never moves money.