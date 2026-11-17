# Design Decisions

The README leaves these cases open. Each one is a rule the system enforces and a test case.

| # | Situation | Invoices | Decision | Why |
|---|-----------|----------|----------|-----|
| 1 | Quantity exceeds stock (summed per item across lines) | 1002, 1005, 1007, 1013 | Hold for human review | Vendor may ship the balance later; high-value ambiguity goes to a person |
| 2 | Same invoice number, original and revised | 1004, 1004_revised | Pay the revised one after normal checks; void the original with a log entry | Acme never pays twice |
| 3 | Non-USD currency | 1014 (EUR) | Hold for human review | Approval rules are in USD and the system runs offline with no live FX rate |
| 4 | Stated total does not match line items + tax (under or over) | 1007 (-$110), 1013 (+$50) | Reject, reason shows the exact difference | Small mismatches add up across thousands of invoices |
| 5 | Item not in inventory, or zero-stock item | 1008, 1016, 1003 | Reject as suspicious, reason shown | Billing for never-ordered goods is a common invoice scam |
| 6 | Negative quantity / broken totals | 1009 | Reject (data integrity) | Quantities must be positive integers |
| 7 | OCR errors (letter O for zero, "Widget A", "INV 1012") | 1012 | Agent normalizes, logs every correction, then normal checks | Cleaning messy data is the agent's job; corrections stay auditable |
| 8 | Same invoice in two file formats | 1011 (txt+pdf), 1013 (json+pdf) | Group by invoice number, process once, fill missing fields from the other copy; hold if copies disagree | No double payment, no lost data |
| 9 | Same item at different prices; shipping line | 1010 | Shipping allowed as non-stock line; unit price >10% above catalog -> hold | $300 WidgetA is 20% above the $250 catalog price |
| 10 | Suspicious vendor address | 1005 (1600 Pennsylvania Ave), 1004 (742 Evergreen Terrace) | LLM flags a warning -> human review, never auto-reject | Plain code can't recognize a famous fake address; the AI can |
| 11 | All due dates in the past | all | `--as-of` option, default = invoice date; overdue is marked "late" but still payable | Reproducible results; late isn't a reason to refuse a valid bill |
| 12 | Total over $10K | 1002, 1003, 1005, 1007, 1013 | Human approval step with LLM summary + recommendation; `--auto-approve` for automated runs | Human-in-the-loop for high-stakes payments |

Deterministic code makes every money, stock and date decision. The LLM extracts, normalizes, summarizes and flags; it never moves money.