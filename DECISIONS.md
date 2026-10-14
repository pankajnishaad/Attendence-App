# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

1. **Indexes.** Which indexes did you create, and which query or guarantee does each one serve? Which one did you consider and reject?
2. **Punch-in race.** Two identical punch-in requests arrive at the same instant. Walk through exactly what happens in your code.
3. **Ties.** Two employees are tied on late minutes at the cutoff of `limit`. What does your leaderboard return, and why?
4. **Headcount.** How does your department summary make sure employees with zero logs are counted?
5. **One thing you would change** if this had to serve 100x the data.

## 1. Which indexes did you create, and why?

I use a unique index on `employees.emp_code` to prevent duplicate employee codes and speed up employee lookups. An index on `employees.department` supports department filtering. Attendance has a unique compound index on `(emp_code, date)` so an employee cannot have two records for the same attendance date and monthly queries can find records efficiently. A second attendance index on `(date descending, emp_code ascending)` supports date filtering and attendance list ordering. Stable index names make startup creation idempotent.

## 2. What happens when two identical punch-ins arrive together?

Both requests may initially check the same employee and date, but the unique `(emp_code, date)` index is the final protection against duplicates. One insert succeeds and returns the success response. The other receives a duplicate-key error, which the API translates to HTTP 409 Conflict. Correctness does not depend on a prior read being race-free.

## 3. What happens when the leaderboard limit cuts through a tie?

Employees with the same ranking value share the same rank, and the next rank is skipped. For example, ranks can be `1, 2, 2, 4`. The rank filter is applied before final ordering. Employees tied on the ranking value are ordered by `emp_code` for predictable output.

## 4. How does department summary count employees with no attendance logs?

The employee collection is the starting point for headcount. Employees whose `joined_on` date is on or before the reporting period end are included even if they have no attendance records. Attendance is joined separately to calculate attendance-based metrics. Employees who joined after the period are excluded. Average work hours must be calculated over qualifying attendance records, not by averaging employee averages.

## 5. What would you change for 100 times the data?

I would measure query plans and latency, then review indexes against real filters and sorts. I would add load tests and monitor documents examined, keys examined, memory use, and aggregation spills. If one MongoDB deployment no longer met the workload, I would consider workload-specific read strategies, agreed retention or archival rules, and sharding only after choosing a shard key from measured query patterns. I would avoid adding indexes without evidence because each index also increases storage and write cost.
