# Questions and Assumptions

The brief invites questions before starting. This was a take-home, so for each question I wrote down the assumption I built on and why. Items marked ⚙️ are configuration values, so a different answer changes config, not code.

## Process and people

**Q1. Who can report that the box is empty?**
**Decision:** Anyone with a company login.
**Why:** Everyone walks past the box, but only about 35 people are volunteers. The more people who can report, the sooner an empty box is noticed. Duplicate reports merge into the open refill, so letting everyone report costs nothing.

**Q2. Is there a coordinator per office?**
**Decision:** Yes, one per office (the office manager) and one backup. ⚙️
**Why:** Automation needs a human owner for the cases it can't decide: nobody available, a purchase over budget, a claim HR rejected. Without that owner, those cases go back to "everyone assumes someone else".

**Q3. Can volunteers decline? Is there a limit?**
**Decision:** Yes, freely, without a reason and without a limit. A decline moves you to the back of the queue for *this* refill only. It doesn't count against you.
**Why:** These are volunteers spending their own money. If declining costs them something, they quit volunteering, and goodwill is the thing we're trying to protect. Fairness comes from the rotation rule, not from punishing people.

**Q4. Where does availability come from?**
**Decision:** Each volunteer sets the weekdays they're usually in the office (for example, Tue and Thu). They add overrides through chat: `/snack away 2026-12-20..2027-01-03` and `/snack visiting Oslo 2026-12-01..2026-12-12`.
**Why:** Hybrid working follows a fairly stable weekly pattern, so a pattern plus exceptions takes one minute a month to keep up to date. Linking to calendars or the HR system adds another integration that can break. If the data is wrong, the cost is small: one offer times out and goes to the next person.

**Q5. Can a visitor refill the office they're visiting?**
**Decision:** Yes, if they're a volunteer. Being a volunteer is opt-in and moves with the person. *Where* they can refill depends on where they are today. While visiting, they aren't offered refills at their home office.
**Why:** Someone 7 000 km away can't refill a box. Someone standing next to it can. Volunteering is still a personal choice, so we don't enlist visitors who haven't opted in.

**Q6. What deadlines apply?**
**Decision:** 2 working hours to accept an offer. Purchase by the end of the next working day after accepting. ⚙️
**Why:** Two hours is short enough that an empty box gets an owner the same day, and long enough to cover a meeting. "Next working day" lets people shop on the way in. A refill that is ignored at every step still finishes within about 2 days, compared with "days" today.

**Q7. Working hours and holidays?**
**Decision:** Mon–Fri, 09:00–17:00 in each office's local time zone (Europe/Oslo and Asia/Kolkata). Each office has a configured list of public holidays. Timers count only working time. ⚙️
**Why:** An offer sent on Friday at 16:30 shouldn't expire over the weekend and quietly skip people who were never online. This also means Oslo and Bengaluru run correctly in their own time zones.

## Money

**Q8. When someone buys while visiting, whose budget does it come from?**
**Decision:** The budget of the office whose box was refilled, in that office's currency.
**Why:** The budget belongs to the snack counter, not to the person. A Bengaluru employee filling the Oslo box is spending Oslo's money, in NOK.

**Q9. Which currency is the claim in?**
**Decision:** The currency on the receipt. HR converts it to the employee's payroll currency.
**Why:** The brief says HR reimburses in payroll currency, and the receipt is the evidence HR checks. If we converted first, the claim wouldn't match the receipt and we'd be taking on FX work that belongs to HR.

**Q10. Which currency and rate does the management report use?**
**Decision:** The combined total is in **NOK**, at the **monthly average rate** for INR→NOK. The rate is given to the report as an input and stored with it. Each office's total is always shown in its own currency next to it.
**Why:** Management asked for *one* total, which means one currency. NOK is the natural choice for a company with its base in Norway. The monthly average reflects a month of purchases spread across the month better than the rate on a single day. Storing the rate means the report can be reproduced later. Native totals let each office be judged against its own budget without currency movements getting in the way.

**Q11. Which month does a purchase count in?**
**Decision:** The month on the receipt.
**Why:** That's when the budget was really used, and we know it the moment the receipt is uploaded. Counting by reimbursement date would push spend into the next month whenever HR takes 1–10 days, and budget control would be wrong exactly when it matters most, at month end.

**Q12. What if a purchase goes over the cap or the remaining budget?**
**Decision:** The volunteer sees their cap *before* buying. If the receipt is over the cap, the claim waits for one-click coordinator approval before it goes to HR. The purchase is never blocked outright.
**Why:** By the time we see the receipt, the volunteer has already paid out of their own pocket. A hard block would punish them, which goes against the whole point of the system. A soft block keeps the budget visible and gives someone accountable a say, without leaving a volunteer out of pocket.

**Q13. Can HR reject a claim, or partly approve it?**
**Decision:** HR can reject, with a reason (for example, an unreadable receipt). It can't partly approve. A rejected refill goes back to the volunteer to **fix and resubmit**. The spend stays on the ledger until the claim is either resubmitted or **cancelled** by the coordinator, which releases the budget.
**Why:** The snacks were bought and eaten, so the spend really happened. Taking it off the budget automatically would understate the month. Most rejections are paperwork problems, so resubmitting is the normal path. Cancelling is an explicit decision made by a person.

**Q14. Does unused budget roll over?**
**Decision:** No. Each calendar month starts fresh.
**Why:** It's simple, it matches how monthly budgets are usually set, and it stops a quiet month turning into an unusually expensive one.

**Q15. Threshold for outstanding money, and the fairness window?** *(added by us)*
**Decision:** Skip anyone who already has more than **1 500 NOK / 4 000 INR** waiting to be reimbursed, which is roughly one maximum purchase plus a bit. Count refills over the **last 90 days**. ⚙️
**Why:** The brief says "a few people… wait weeks to get their money back". No one should be carrying the company's costs on top of an earlier claim. The 90-day window means someone who did a lot of refills last spring isn't treated as "used up" forever.

## Integration

**Q16. What does the HR system support?**
**Decision:** A REST endpoint to create a claim, which returns a claim ID, and an endpoint to check a claim's status. It has **no webhooks** and **isn't idempotent**. We check statuses every 4 hours.
**Why:** We plan for the most limited realistic third-party API. Because it isn't idempotent, we store our refill ID as the key and never resubmit a claim that already has an HR claim ID. That rules out double reimbursement. A claim takes 1–10 days, so checking every 4 hours is plenty.

**Q17. What does the chat tool support?**
**Decision:** Bots, direct messages, channel posts, interactive buttons and image uploads, like Slack or Teams.
**Why:** It's standard in every mainstream chat tool, and the brief says everyone already uses it every day.

**Q18. Receipt image size or rate limits?**
**Decision:** Out of scope for the exercise.
**Why:** It doesn't affect any of the business rules being assessed.

## Scope for the session

**Q19. Language and how it runs?**
**Decision:** Python, standard library plus pytest. Everything runs in one process with simulated chat and HR, plus a console demo script. No HTTP and no database.
**Why:** The brief asks for core logic and tests that prove the rules. An HTTP layer or a database would take time away from the rules and add nothing to the walkthrough.

**Q20. How much "which snacks get eaten" goes in the code?**
**Decision:** Store 1–3 tags per purchase. The monthly report shows the top tags and the average number of days between refills per office. No deeper analysis.
**Why:** It's about 15 lines of code and answers the fourth problem in the brief with real data. Anything more, like predictions or shopping lists, is listed under "deliberately not built".
