# REVIEW.md

List every defect you found in the starter `app/main.py` (helpers and endpoints). For each one:

| # | Where (function / line) | What is wrong | How you'd notice it (test, input, or symptom) | How you fixed it |
|---|---|---|---|---|
| 1 | | | | |

Also note anything you looked at and decided was **not** a defect, and why.

## Purpose

This review records findings from checking the HROne Employee Attendance & Analytics API against the assignment contract. It separates observed behaviour from issues that still need code-level or automated-test verification. A successful HTTP response alone does not prove that an endpoint returns correct results.

## Findings

### 1. Department trend returns zero attendance counts

**Where:** `GET /analytics/departments/{department}/trend`

**What is wrong:** A request for `Engineering` covering `2026-10-01` through `2026-10-14` returned one row per date and `headcount: 2`, but `present_count`, `absent_count`, `leave_count`, and `late_count` were all zero. This may be correct if there are no attendance records for those employees in that period, so it is not yet a confirmed aggregation defect.

**How to notice:** Compare the trend response with `GET /attendance` for the same dates and employees. Include at least one known attendance record in a test dataset.

**Fix / status:** Not yet confirmed or fixed. Check the employee join, status counting, date boundaries, and stored values. Keep `$densify` and calculate the moving average in MongoDB.

### 2. Department name matching differs by case

**Where:** Department trend route and its employee lookup/filter.

**What is wrong:** `ENGINEERING` returned `404 Department not found`, while `Engineering` returned `200`. The endpoint currently expects or looks up the stored department spelling. Whether this is a defect depends on the contract and `DATA_MODEL.md`.

**How to notice:** Call the endpoint with the exact stored value and with a differently cased value. Check whether case-insensitive matching is required.

**Fix / status:** Not yet confirmed or fixed. Follow the contract. If case-insensitive matching is required, normalize consistently without changing stored values unnecessarily.

### 3. Explain pipelines are simplified rather than matching the real endpoints

**Where:** `GET /admin/explain/{endpoint}`

**What is wrong:** The explain responses show simplified commands. For example, `employee_monthly` explains a pipeline that matches an employee and month and counts records, rather than the complete monthly analytics calculation. The department trend explain pipeline does not show the complete gap filling and moving average work. The assignment requires the same main query or pipeline, filters, sorting, and pagination as the real endpoint.

**How to notice:** Compare the `command.pipeline` in each explain response with the actual pipeline used by the corresponding endpoint.

**Fix / status:** Needs correction. Reuse shared query or pipeline builders where practical so the real endpoint and explain endpoint cannot drift apart. Return MongoDB `executionStats` for the actual query or pipeline.

### 4. Department summary response key needs contract verification

**Where:** `GET /analytics/departments/summary`

**What is wrong:** An earlier code inspection indicated that the response may use `departments` while the expected contract uses `items`. A wrong response key can fail contract tests even if the calculations are correct.

**How to notice:** Compare the actual response and OpenAPI schema, including exact field names.

**Fix / status:** Verify the current `app/main.py` and `openapi.yaml`. Change the key only if the current implementation does not match the contract.

### 5. Department summary average must be calculated over records

**Where:** `GET /analytics/departments/summary`

**What is wrong:** The assignment says the average is over qualifying attendance records, not an average of employee averages. Averaging per employee first can produce a wrong result when employees have different numbers of attendance records.

**How to notice:** Test two employees with different record counts and work hours. Compare the record-weighted average with the average of employee averages.

**Fix / status:** Verify the current aggregation. Calculate the average directly across qualifying records if the existing pipeline averages employee-level averages.

### 6. Leaderboard tie handling needs regression tests

**Where:** `GET /analytics/leaderboard/late`

**What is wrong:** Tied employees must share a rank and the next rank must be skipped, for example `1, 2, 2, 4`. The limit is applied after ranking, and tied rows are ordered by employee code.

**How to notice:** Create a test where multiple employees have equal late totals, including a tie at the limit boundary.

**Fix / status:** The inspected implementation uses `$rank` through `$setWindowFields`, which is appropriate in principle. Verify ranking, cutoff filtering, and final ordering with automated tests.

### 7. Pagination, filters, and sorting need regression tests

**Where:** `GET /employees` and `GET /attendance`

**What is wrong:** An earlier audit reported concerns about skip calculation, filtered totals, deterministic sorting, maximum page size, and loading records into Python. Some may already be fixed, so they should not be called confirmed defects without checking the current code.

**How to notice:** Test page 1 and page 2, filtered and unfiltered totals, stable sorting, and `page_size` values of 100 and 101. Use a larger dataset to confirm filtering and sorting happen in MongoDB.

**Fix / status:** Recheck the current implementation and record only failures supported by tests. Expected contract: page starts at 1, default page size is 20, maximum is 100, totals respect filters, and results use the required sort.

### 8. Timestamp, rounding, grace-period, and overtime boundaries need regression tests

**Where:** `to_epoch_ms`, `compute_late_minutes`, `compute_work_hours`, `compute_overtime`, punch-in, punch-out, and regularization.

**What is wrong:** These rules have exact boundaries and are easy to implement almost correctly. The latest shared code includes updated helper logic, but full edge-case coverage has not been demonstrated by the endpoint responses shared so far.

**How to notice:** Test strict integer epoch milliseconds in the permitted range; 10 minutes exactly and 10 minutes plus one second late; overtime at 29 and 30 minutes; half-day around 4.50 rounded hours; and overnight shifts.

**Fix / status:** Add automated tests and verify the results through the API before marking this resolved.

## Items that appeared to work in observed checks

- `GET /admin/explain/attendance_list` returned `200` and showed `IXSCAN` using `idx_attendance_date_emp`.
- `GET /admin/explain/employee_monthly` returned `200` and showed a covered `IXSCAN` using `uniq_attendance_emp_date`.
- Department summary explain output showed `uniq_attendance_emp_date` for the attendance `$lookup`, with `collectionScans: 0` for that lookup.
- Late leaderboard explain output showed `idx_attendance_date_emp` for the attendance match and `uniq_employee_emp_code` for the employee lookup, with no collection scan reported for that lookup.
- Department trend explain output showed `idx_attendance_date_emp` for the date match and `uniq_employee_emp_code` for the employee lookup. However, the explained pipeline returned zero rows after the lookup and still needs comparison with the actual endpoint pipeline.

These observations are from a small local dataset. They do not prove performance or correctness on the assignment's 100,000-record dataset.

## Verification still required before submission

- Compare every endpoint request and response schema against `openapi.yaml`.
- Verify all required indexes are created idempotently at startup.
- Test duplicate punch-ins, duplicate punch-outs, and concurrent regularizations.
- Verify monthly summary and department summary calculations with hand-calculated test cases.
- Verify trend gap filling and moving average for weekends and dates with no records.
- Make every explain pipeline match the corresponding endpoint's real query or pipeline.
- Run the automated test suite and record actual pass or fail results.

## Review note

Unverified items are marked as needing verification rather than claimed as fixed. Update each status after running the relevant test against the current code.
