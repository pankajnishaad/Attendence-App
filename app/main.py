"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone, date
from typing import Optional, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, model_validator, EmailStr, StrictInt
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError
from contextlib import asynccontextmanager
from decimal import Decimal, ROUND_HALF_UP

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))
def to_epoch_ms(dt: datetime | None) -> int | None:
    if dt is None:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return int(dt.timestamp() * 1000)

# app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")
@asynccontextmanager
async def lifespan(app: FastAPI):
    db.employees.create_index(
        [("emp_code", 1)],
        unique=True,
        name="uniq_employee_emp_code"
    )

    db.employees.create_index(
        [("department", 1)],
        name="idx_employee_department"
    )

    db.attendance_logs.create_index(
        [("emp_code", 1), ("date", 1)],
        unique=True,
        name="uniq_attendance_emp_date"
    )

    db.attendance_logs.create_index(
        [("date", -1), ("emp_code", 1)],
        name="idx_attendance_date_emp"
    )

    yield

app = FastAPI(
    title="Employee Attendance & Analytics API",
    version="2.0.0",
    lifespan=lifespan
)



# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def compute_late_minutes(
    punch_in_utc: datetime,
    shift_start: str
) -> int:
    punch_in_ist = punch_in_utc.astimezone(IST)

    hour, minute = map(int, shift_start.split(":"))

    shift_start_ist = punch_in_ist.replace(
        hour=hour,
        minute=minute,
        second=0,
        microsecond=0
    )

    delta_seconds = int(
        (punch_in_ist - shift_start_ist).total_seconds()
    )

    if delta_seconds <= 600:
        return 0

    return delta_seconds // 60


def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    # return round((punch_out - punch_in).total_seconds() / 3600, 2)
    seconds = Decimal(str((punch_out - punch_in).total_seconds()))
    hours = (seconds / Decimal("3600")).quantize(
        Decimal("0.01"),
        rounding=ROUND_HALF_UP
    )
    return float(hours)


def compute_overtime(punch_out: datetime, shift_end: str, date_str: str, shift_start: str) -> int:
    # h, m = map(int, shift_end.split(":"))
    # end = datetime.fromisoformat(date_str).replace(hour=h, minute=m, tzinfo=IST)
    # return max(0, int((punch_out - end).total_seconds() / 60))
    end_hour, end_minute = map(int, shift_end.split(":"))
    start_hour, start_minute = map(int, shift_start.split(":"))

    attendance_date = date.fromisoformat(date_str)

    shift_end_dt = datetime(
        attendance_date.year,
        attendance_date.month,
        attendance_date.day,
        end_hour,
        end_minute,
        tzinfo=IST
    )

    if (end_hour, end_minute) <= (start_hour, start_minute):
        shift_end_dt += timedelta(days=1)
    
    if punch_out.tzinfo is None:
        punch_out = punch_out.replace(tzinfo=timezone.utc)

    punch_out = punch_out.astimezone(IST)


    elapsed_seconds = (punch_out - shift_end_dt).total_seconds()

    if elapsed_seconds < 1800:
        return 0

    return int(elapsed_seconds // 60)


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str
    email: EmailStr
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: date
    @model_validator(mode="after")
    def validate_shift_times(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must be different")
        return self


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

    @model_validator(mode="after")
    def validate_punched_at(self):
        if self.punched_at is not None:
            if not 100000000000 <= self.punched_at <= 4102444800000:
                raise ValueError(
                    "punched_at must be epoch milliseconds in the allowed range"
                )
        return self

class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[StrictInt] = None

    @model_validator(mode="after")
    def validate_punched_at(self):
        if self.punched_at is not None:
            if not 100000000000 <= self.punched_at <= 4102444800000:
                raise ValueError(
                    "punched_at must be epoch milliseconds in the allowed range"
                )
        return self


class RegularizeRequest(BaseModel):
    status: Optional[
        Literal["PRESENT", "WFH", "ON_DUTY", "ABSENT", "LEAVE"]
    ] = None

    punch_in: Optional[StrictInt] = None
    punch_out: Optional[StrictInt] = None

    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @model_validator(mode="before")
    @classmethod
    def validate_epoch_fields(cls, values):
        if isinstance(values, dict):
            for field in ("punch_in", "punch_out"):
                value = values.get(field)
                if value is not None and not (
                    100000000000 <= value <= 4102444800000
                ):
                    raise ValueError(
                        f"{field} must be epoch milliseconds"
                    )
        return values


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #

@app.get("/health")
def health():
    try:
        client.admin.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(
            status_code=503,
            detail="MongoDB unavailable"
        )



@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    if db.employees.find_one({"emp_code": body.emp_code}):
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists"
        )

    doc = body.model_dump()
    doc["joined_on"] = body.joined_on.isoformat()

    created_at = datetime.now(timezone.utc).replace(microsecond=0)
    doc["created_at"] = created_at

    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="emp_code already exists"
        )

    doc.pop("_id", None)
    doc["created_at"] = to_epoch_ms(created_at)

    return doc


@app.get("/employees")
def list_employees(
    department: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    q = {}

    if department is not None:
        q["department"] = department

    skip = (page - 1) * page_size

    total = db.employees.count_documents(q)

    items = list(
        db.employees.find(q, {"_id": 0})
        .sort("emp_code", 1)
        .skip(skip)
        .limit(page_size)
    )
    for item in items:
        if isinstance(item.get("created_at"), datetime):
            item["created_at"] = to_epoch_ms(item["created_at"])

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if not emp:
        raise HTTPException(
            status_code=404,
            detail="Employee not found"
        )

    if body.punched_at is not None:
        ts_utc = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc
        )
    else:
        ts_utc = datetime.now(timezone.utc)

    # Truncate timestamp to whole seconds
    ts_utc = ts_utc.replace(microsecond=0)

    # Convert UTC instant to IST
    ts_ist = ts_utc.astimezone(IST)

    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]

    # Calculate attendance date
    if shift_end <= shift_start:
        hour_end, minute_end = map(int, shift_end.split(":"))

        punch_minutes = ts_ist.hour * 60 + ts_ist.minute
        end_minutes = hour_end * 60 + minute_end

        if punch_minutes < end_minutes:
            attendance_date = ts_ist.date() - timedelta(days=1)
        else:
            attendance_date = ts_ist.date()
    else:
        attendance_date = ts_ist.date()

    d = attendance_date.isoformat()

    if db.attendance_logs.find_one({
        "emp_code": body.emp_code,
        "date": d
    }):
        raise HTTPException(
            status_code=409,
            detail="already punched in for this date"
        )

    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts_utc,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(
            ts_utc, shift_start
        ),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }

    try:
        res = db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(
            status_code=409,
            detail="already punched in for this date"
        )

    doc["id"] = str(res.inserted_id)
    doc.pop("_id", None)
    doc["punch_in"] = to_epoch_ms(ts_utc)

    return doc

@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(
            status_code=422,
            detail="date_from must be on or before date_to"
        )

    query = {}

    if emp_code is not None:
        query["emp_code"] = emp_code

    if status is not None:
        query["status"] = status

    if date_from is not None or date_to is not None:
        query["date"] = {}

        if date_from is not None:
            query["date"]["$gte"] = date_from.isoformat()

        if date_to is not None:
            query["date"]["$lte"] = date_to.isoformat()

    total = db.attendance_logs.count_documents(query)
    skip = (page - 1) * page_size

    records = list(
        db.attendance_logs.find(query, {"_id": 0})
        .sort([("date", -1), ("emp_code", 1)])
        .skip(skip)
        .limit(page_size)
    )

    for record in records:
        for field in ("punch_in", "punch_out"):
            if isinstance(record.get(field), datetime):
                record[field] = to_epoch_ms(record[field])

        for entry in record.get("history", []):
            if isinstance(entry.get("at"), datetime):
                entry["at"] = to_epoch_ms(entry["at"])

            for change in entry.get("changes", {}).values():
                for field in ("from", "to"):
                    if isinstance(change.get(field), datetime):
                        change[field] = to_epoch_ms(change[field])

    return {
        "items": records,
        "total": total,
        "page": page,
        "page_size": page_size,
    }

@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})

    if not emp:
        raise HTTPException(status_code=404, detail="Employee not found")

    if body.punched_at is None:
        punched_out_utc = datetime.now(timezone.utc).replace(microsecond=0)
    else:
        punched_out_utc = datetime.fromtimestamp(
            body.punched_at / 1000,
            tz=timezone.utc
        ).replace(microsecond=0)

    record = db.attendance_logs.find_one(
        {
            "emp_code": body.emp_code,
            "punch_in": {
                "$type": "date",
                "$lte": punched_out_utc
            }
        },
        sort=[("punch_in", -1)]
    )

    if not record:
        raise HTTPException(
            status_code=404,
            detail="No punch-in found"
        )

    if record.get("punch_out") is not None:
        raise HTTPException(
            status_code=409,
            detail="Already punched out"
        )

    punch_in_utc = record["punch_in"]

    if punch_in_utc.tzinfo is None:
        punch_in_utc = punch_in_utc.replace(tzinfo=timezone.utc)

    elapsed_seconds = (
        punched_out_utc - punch_in_utc
    ).total_seconds()

    if elapsed_seconds <= 0 or elapsed_seconds > 86400:
        raise HTTPException(
            status_code=422,
            detail="Punch-out must be after punch-in and within 24 hours"
        )

    work_hours = compute_work_hours(
        punch_in_utc,
        punched_out_utc
    )

    overtime_minutes = compute_overtime(
        punched_out_utc,
        emp["shift_end"],
        record["date"],
        emp["shift_start"]
    )

    half_day = work_hours < 4.50

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            "punch_out": None
        },
        {
            "$set": {
                "punch_out": punched_out_utc,
                "work_hours": work_hours,
                "overtime_minutes": overtime_minutes,
                "half_day": half_day
            }
        }
    )

    if result.modified_count != 1:
        result = db.attendance_logs.update_one(
            {
                "_id": record["_id"],
                "punch_out": {"$exists": False}
            },
            {
                "$set": {
                    "punch_out": punched_out_utc,
                    "work_hours": work_hours,
                    "overtime_minutes": overtime_minutes,
                    "half_day": half_day
                }
            }
        )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Already punched out"
        )

    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0}
    )

    for field in ("punch_in", "punch_out"):
        value = updated.get(field)
        if isinstance(value, datetime):
            updated[field] = to_epoch_ms(value)

    updated.setdefault("work_hours", None)
    updated.setdefault("late_minutes", 0)
    updated.setdefault("overtime_minutes", 0)
    updated.setdefault("half_day", False)
    updated.setdefault("history", [])

    for entry in updated["history"]:
        if isinstance(entry.get("at"), datetime):
            entry["at"] = to_epoch_ms(entry["at"])

        for change in entry.get("changes", {}).values():
            for field in ("from", "to"):
                if isinstance(change.get(field), datetime):
                    change[field] = to_epoch_ms(change[field])

    return updated


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(
    emp_code: str,
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$"
    )
):
    employee = db.employees.find_one(
        {"emp_code": emp_code},
        {"_id": 0, "joined_on": 1}
    )

    if employee is None:
        raise HTTPException(
            status_code=404,
            detail="Employee not found"
        )

    year = int(month[:4])
    month_number = int(month[5:7])

    month_start = date(year, month_number, 1)

    if month_number == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_number + 1, 1)

    month_end = next_month - timedelta(days=1)

    joined_value = employee["joined_on"]

    if isinstance(joined_value, str):
        joined_on = date.fromisoformat(joined_value)
    elif isinstance(joined_value, datetime):
        joined_on = joined_value.date()
    else:
        joined_on = joined_value

    first_date = max(month_start, joined_on)

    # Attendance statistics using MongoDB aggregation
    pipeline = [
        {
            "$match": {
                "emp_code": emp_code,
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lt": next_month.isoformat()
                }
            }
        },
        {
            "$addFields": {
                "_attendance_date": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "format": "%Y-%m-%d"
                    }
                }
            }
        },
        {
            "$group": {
                "_id": None,
                "present_days": {
                    "$sum": {
                        "$cond": [
                            {
                                "$and": [
                                    {
                                        "$in": [
                                            "$status",
                                            ["PRESENT", "WFH", "ON_DUTY"]
                                        ]
                                    },
                                    {
                                        "$gte": [
                                            "$_attendance_date",
                                            datetime.combine(
                                                first_date,
                                                datetime.min.time()
                                            )
                                        ]
                                    },
                                    {
                                        "$in": [
                                            {"$dayOfWeek": "$_attendance_date"},
                                            [2, 3, 4, 5, 6]
                                        ]
                                    }
                                ]
                            },
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1
                                ]
                            },
                            0
                        ]
                    }
                },
                "leave_days": {
                    "$sum": {
                        "$cond": [
                            {"$eq": ["$status", "LEAVE"]},
                            1,
                            0
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": ["$late_minutes", 0]},
                            1,
                            0
                        ]
                    }
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$late_minutes", 0]}
                },
                "total_overtime_minutes": {
                    "$sum": {"$ifNull": ["$overtime_minutes", 0]}
                }
            }
        }
    ]

    results = list(db.attendance_logs.aggregate(pipeline))

    summary = results[0] if results else {
        "present_days": 0,
        "leave_days": 0,
        "late_count": 0,
        "total_late_minutes": 0,
        "total_overtime_minutes": 0
    }

    # Working days using MongoDB aggregation
    working_days = 0

    if first_date <= month_end:
        working_days_pipeline = [
            {
                "$documents": [
                    {
                        "start": datetime.combine(
                            first_date,
                            datetime.min.time()
                        ),
                        "end": datetime.combine(
                            month_end,
                            datetime.min.time()
                        )
                    }
                ]
            },
            {
                "$project": {
                    "days": {
                        "$map": {
                            "input": {
                                "$range": [
                                    0,
                                    {
                                        "$add": [
                                            {
                                                "$dateDiff": {
                                                    "startDate": "$start",
                                                    "endDate": "$end",
                                                    "unit": "day"
                                                }
                                            },
                                            1
                                        ]
                                    }
                                ]
                            },
                            "as": "offset",
                            "in": {
                                "$dateAdd": {
                                    "startDate": "$start",
                                    "unit": "day",
                                    "amount": "$$offset"
                                }
                            }
                        }
                    }
                }
            },
            {"$unwind": "$days"},
            {
                "$match": {
                    "$expr": {
                        "$in": [
                            {"$dayOfWeek": "$days"},
                            [2, 3, 4, 5, 6]
                        ]
                    }
                }
            },
            {"$count": "working_days"}
        ]

        working_result = list(
            db.command({
                "aggregate": 1,
                "pipeline": working_days_pipeline,
                "cursor": {}
            })["cursor"]["firstBatch"]
        )

        working_days = (
            working_result[0]["working_days"]
            if working_result else 0
        )

    present_days = summary["present_days"]

    attendance_pct = (
        None
        if working_days == 0
        else float(
            (
                Decimal(str(present_days))
                / Decimal(working_days)
                * Decimal("100")
            ).quantize(
                Decimal("0.0001"),
                rounding=ROUND_HALF_UP
            )
        )
    )

    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": present_days,
        "leave_days": summary["leave_days"],
        "late_count": summary["late_count"],
        "total_late_minutes": summary["total_late_minutes"],
        "total_overtime_minutes": summary["total_overtime_minutes"],
        "attendance_pct": attendance_pct
    }


@app.get("/analytics/departments/summary")
def department_summary(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    department: Optional[str] = None
):
    year = int(month[:4])
    month_number = int(month[5:7])

    month_start = date(year, month_number, 1)

    if month_number == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_number + 1, 1)

    month_end = next_month - timedelta(days=1)

    pipeline = [
        {
            "$match": {
                "joined_on": {"$lte": month_end.isoformat()},
                **({"department": department} if department is not None else {})
            }
        },
        {
            "$lookup": {
                "from": "attendance_logs",
                "let": {"employee_code": "$emp_code"},
                "pipeline": [
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$emp_code", "$$employee_code"]},
                                    {"$gte": ["$date", month_start.isoformat()]},
                                    {"$lte": ["$date", month_end.isoformat()]}
                                ]
                            }
                        }
                    },
                    {
                        "$addFields": {
                            "_attendance_date": {
                                "$dateFromString": {
                                    "dateString": "$date",
                                    "format": "%Y-%m-%d"
                                }
                            }
                        }
                    },
                    {
                        "$group": {
                            "_id": None,
                            "present_days": {
                                "$sum": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                                {"$in": [{"$dayOfWeek": "$_attendance_date"}, [2, 3, 4, 5, 6]]}
                                            ]
                                        },
                                        {
                                            "$cond": [
                                                {"$eq": ["$half_day", True]},
                                                0.5,
                                                1
                                            ]
                                        },
                                        0
                                    ]
                                }
                            },
                            "work_hours_values": {
                                "$push": {
                                    "$cond": [
                                        {
                                            "$and": [
                                                {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                                {"$ne": ["$work_hours", None]}
                                            ]
                                        },
                                        "$work_hours",
                                        None
                                    ]
                                }
                            },
                            "late_count": {
                                "$sum": {
                                    "$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]
                                }
                            },
                            "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
                            "leave_count": {
                                "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                            },
                            "on_duty_count": {
                                "$sum": {"$cond": [{"$eq": ["$status", "ON_DUTY"]}, 1, 0]}
                            }
                        }
                    },
                    {
                        "$project": {
                            "_id": 0,
                            "present_days": 1,
                            "late_count": 1,
                            "total_late_minutes": 1,
                            "leave_count": 1,
                            "on_duty_count": 1,
                            "avg_work_hours": {
                                "$cond": [
                                    {
                                        "$gt": [
                                            {
                                                "$size": {
                                                    "$filter": {
                                                        "input": "$work_hours_values",
                                                        "as": "hours",
                                                        "cond": {"$ne": ["$$hours", None]}
                                                    }
                                                }
                                            },
                                            0
                                        ]
                                    },
                                    {
                                        "$round": [
                                            {
                                                "$avg": {
                                                    "$filter": {
                                                        "input": "$work_hours_values",
                                                        "as": "hours",
                                                        "cond": {"$ne": ["$$hours", None]}
                                                    }
                                                }
                                            },
                                            2
                                        ]
                                    },
                                    None
                                ]
                            }
                        }
                    }
                ],
                "as": "attendance_summary"
            }
        },
        {
            "$set": {
                "summary": {"$arrayElemAt": ["$attendance_summary", 0]}
            }
        },
        {
            "$group": {
                "_id": "$department",
                "headcount": {"$sum": 1},
                "present_days": {
                    "$sum": {"$ifNull": ["$summary.present_days", 0]}
                },
                "avg_work_hours_values": {
                    "$push": "$summary.avg_work_hours"
                },
                "late_count": {
                    "$sum": {"$ifNull": ["$summary.late_count", 0]}
                },
                "total_late_minutes": {
                    "$sum": {"$ifNull": ["$summary.total_late_minutes", 0]}
                },
                "leave_count": {
                    "$sum": {"$ifNull": ["$summary.leave_count", 0]}
                },
                "on_duty_count": {
                    "$sum": {"$ifNull": ["$summary.on_duty_count", 0]}
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "department": "$_id",
                "headcount": 1,
                "present_days": 1,
                "avg_work_hours": {
                    "$cond": [
                        {
                            "$gt": [
                                {
                                    "$size": {
                                        "$filter": {
                                            "input": "$avg_work_hours_values",
                                            "as": "hours",
                                            "cond": {"$ne": ["$$hours", None]}
                                        }
                                    }
                                },
                                0
                            ]
                        },
                        {
                            "$round": [
                                {
                                    "$avg": {
                                        "$filter": {
                                            "input": "$avg_work_hours_values",
                                            "as": "hours",
                                            "cond": {"$ne": ["$$hours", None]}
                                        }
                                    }
                                },
                                2
                            ]
                        },
                        None
                    ]
                },
                "late_count": 1,
                "total_late_minutes": 1,
                "leave_count": 1,
                "on_duty_count": 1
            }
        },
        {"$sort": {"department": 1}}
    ]

    departments = list(db.employees.aggregate(pipeline))

    return {
        "month": month,
        "departments": departments
    }


@app.get("/analytics/leaderboard/late")
def late_leaderboard(
    month: str = Query(
        ...,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$"
    ),
    limit: int = Query(default=10, ge=1, le=50),
    department: Optional[str] = None
):
    year = int(month[:4])
    month_number = int(month[5:7])

    month_start = date(year, month_number, 1)

    if month_number == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month_number + 1, 1)

    pipeline = [
        {
            "$match": {
                "date": {
                    "$gte": month_start.isoformat(),
                    "$lt": next_month.isoformat()
                },
                "late_minutes": {"$gt": 0}
            }
        },
        {
            "$group": {
                "_id": "$emp_code",
                "late_count": {"$sum": 1},
                "total_late_minutes": {"$sum": "$late_minutes"}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "localField": "_id",
                "foreignField": "emp_code",
                "as": "employee"
            }
        },
        {"$unwind": "$employee"},
        *(
            [{"$match": {"employee.department": department}}]
            if department is not None else []
        ),
        {
            "$setWindowFields": {
                "sortBy": {"total_late_minutes": -1},
                "output": {
                    "rank": {"$rank": {}}
                }
            }
        },
        {
            "$match": {
                "rank": {"$lte": limit}
            }
        },
        {
            "$sort": {
                "total_late_minutes": -1,
                "_id": 1
            }
        },
        {
            "$project": {
                "_id": 0,
                "emp_code": "$_id",
                "name": "$employee.name",
                "department": "$employee.department",
                "late_count": 1,
                "total_late_minutes": 1,
                "rank": 1
            }
        }
    ]

    results = list(db.attendance_logs.aggregate(pipeline))

    return {
        "month": month,
        "items": results
    }


@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_date: date = Query(..., alias="from"),
    to_date: date = Query(..., alias="to")
):
    if from_date > to_date:
        raise HTTPException(
            status_code=422,
            detail="'from' must be before or equal to 'to'"
        )

    if (to_date - from_date).days > 91:
        raise HTTPException(
            status_code=422,
            detail="Date range must not exceed 92 days"
        )

    if db.employees.count_documents({"department": department}) == 0:
        raise HTTPException(
            status_code=404,
            detail="Department not found"
        )

    start_dt = datetime.combine(
        from_date, datetime.min.time(), tzinfo=timezone.utc
    )
    end_dt = datetime.combine(
        to_date + timedelta(days=1),
        datetime.min.time(),
        tzinfo=timezone.utc
    )

    pipeline = [
        {
            "$match": {
                "department": department,
                "date": {
                    "$gte": from_date.isoformat(),
                    "$lte": to_date.isoformat()
                }
            }
        },
        {
            "$addFields": {
                "_trend_date": {
                    "$dateFromString": {
                        "dateString": "$date",
                        "format": "%Y-%m-%d"
                    }
                }
            }
        },
        {
            "$group": {
                "_id": "$_trend_date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]},
                            0
                        ]
                    }
                },
                "absent_count": {
                    "$sum": {"$cond": [{"$eq": ["$status", "ABSENT"]}, 1, 0]}
                },
                "leave_count": {
                    "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
                },
                "late_count": {
                    "$sum": {"$cond": [{"$gt": ["$late_minutes", 0]}, 1, 0]}
                }
            }
        },
        {"$sort": {"_id": 1}},
        {
            "$densify": {
                "field": "_id",
                "range": {
                    "step": 1,
                    "unit": "day",
                    "bounds": [start_dt, end_dt]
                }
            }
        },
        {
            "$set": {
                "present_count": {"$ifNull": ["$present_count", 0]},
                "absent_count": {"$ifNull": ["$absent_count", 0]},
                "leave_count": {"$ifNull": ["$leave_count", 0]},
                "late_count": {"$ifNull": ["$late_count", 0]}
            }
        },
        {
            "$lookup": {
                "from": "employees",
                "let": {"trend_date": "$_id"},
                "pipeline": [
                    {
                        "$addFields": {
                            "_joined_date": {
                                "$dateFromString": {
                                    "dateString": "$joined_on",
                                    "format": "%Y-%m-%d"
                                }
                            }
                        }
                    },
                    {
                        "$match": {
                            "$expr": {
                                "$and": [
                                    {"$eq": ["$department", department]},
                                    {"$lte": ["$_joined_date", "$$trend_date"]}
                                ]
                            }
                        }
                    },
                    {"$count": "headcount"}
                ],
                "as": "_headcount"
            }
        },
        {
            "$set": {
                "headcount": {
                    "$ifNull": [
                        {"$arrayElemAt": ["$_headcount.headcount", 0]},
                        0
                    ]
                },
                "is_working_day": {
                    "$and": [
                        {"$gte": [{"$dayOfWeek": "$_id"}, 2]},
                        {"$lte": [{"$dayOfWeek": "$_id"}, 6]}
                    ]
                }
            }
        },
        {
            "$set": {
                "attendance_rate": {
                    "$cond": [
                        {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                        {"$round": [{"$divide": ["$present_count", "$headcount"]}, 4]},
                        None
                    ]
                }
            }
        },
        {
            "$setWindowFields": {
                "sortBy": {"_id": 1},
                "output": {
                    "_rate_window": {
                        "$push": "$attendance_rate",
                        "window": {"documents": [-6, 0]}
                    }
                }
            }
        },
        {
            "$set": {
                "moving_avg_7d": {
                    "$cond": [
                        {
                            "$gt": [
                                {
                                    "$size": {
                                        "$filter": {
                                            "input": "$_rate_window",
                                            "as": "rate",
                                            "cond": {"$ne": ["$$rate", None]}
                                        }
                                    }
                                },
                                0
                            ]
                        },
                        {
                            "$round": [
                                {
                                    "$avg": {
                                        "$filter": {
                                            "input": "$_rate_window",
                                            "as": "rate",
                                            "cond": {"$ne": ["$$rate", None]}
                                        }
                                    }
                                },
                                4
                            ]
                        },
                        None
                    ]
                }
            }
        },
        {
            "$project": {
                "_id": 0,
                "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$_id"}},
                "is_working_day": 1,
                "headcount": 1,
                "present_count": 1,
                "absent_count": 1,
                "leave_count": 1,
                "late_count": 1,
                "attendance_rate": 1,
                "moving_avg_7d": 1
            }
        },
        {"$sort": {"date": 1}}
    ]

    items = list(db.attendance_logs.aggregate(pipeline))

    return {
        "department": department,
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "items": items
    }


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(
    emp_code: str,
    date: date,
    payload: RegularizeRequest
):
    employee = db.employees.find_one({"emp_code": emp_code})

    if employee is None:
        raise HTTPException(status_code=404, detail="Employee not found")

    record = db.attendance_logs.find_one({
        "emp_code": emp_code,
        "date": date.isoformat()
    })

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Attendance record not found"
        )

    changes_requested = payload.model_dump(exclude_unset=True)
    changes_requested.pop("reason", None)
    changes_requested.pop("regularized_by", None)

    old_status = record.get("status")
    old_in = record.get("punch_in")
    old_out = record.get("punch_out")

    new_status = changes_requested.get("status", old_status)
    new_in = changes_requested.get("punch_in", old_in)
    new_out = changes_requested.get("punch_out", old_out)

    if new_status in ("ABSENT", "LEAVE"):
        if "punch_in" in changes_requested or "punch_out" in changes_requested:
            raise HTTPException(
                status_code=422,
                detail="ABSENT or LEAVE cannot include punch times"
            )
        new_in = None
        new_out = None

    elif new_in is None:
        raise HTTPException(
            status_code=422,
            detail="Presence status requires punch_in"
        )

    def parse_epoch(value):
        if value is None:
            return None
        return datetime.fromtimestamp(
            value // 1000,
            tz=timezone.utc
        )

    if "punch_in" in changes_requested:
        new_in = parse_epoch(changes_requested["punch_in"])

    if "punch_out" in changes_requested:
        new_out = parse_epoch(changes_requested["punch_out"])

    if new_in is not None:
        punch_in_date = new_in.astimezone(IST).date()
        shift_start = employee.get("shift_start", "09:30")
        shift_end = employee.get("shift_end", "18:30")

        punch_time = new_in.astimezone(IST).strftime("%H:%M")
        if shift_end <= shift_start and punch_time < shift_end:
            punch_in_date -= timedelta(days=1)

        if punch_in_date != date:
            raise HTTPException(
                status_code=422,
                detail="punch_in must belong to the attendance date"
            )

    if new_out is not None:
        if new_in is None:
            raise HTTPException(
                status_code=422,
                detail="punch_out requires punch_in"
            )
        new_in = new_in.replace(tzinfo=timezone.utc) if new_in.tzinfo is None else new_in
        new_out = new_out.replace(tzinfo=timezone.utc) if new_out.tzinfo is None else new_out

        elapsed = (new_out - new_in).total_seconds()

        if elapsed <= 0 or elapsed > 86400:
            raise HTTPException(
                status_code=422,
                detail="punch_out must be after punch_in and within 24 hours"
            )

    shift_start = employee.get("shift_start", "09:30")
    shift_end = employee.get("shift_end", "18:30")

    if new_status in ("ABSENT", "LEAVE"):
        new_late = 0
        new_hours = None
        new_overtime = 0
        new_half_day = False
    else:
        # new_late = compute_late_minutes(new_in, shift_start)
        def same_datetime(a, b):
            if a is None or b is None:
                return a is b

            if a.tzinfo is None:
                a = a.replace(tzinfo=timezone.utc)

            if b.tzinfo is None:
                b = b.replace(tzinfo=timezone.utc)

            return a == b


        new_late = (
            compute_late_minutes(new_in, shift_start)
            if not same_datetime(new_in, old_in)
            else record.get("late_minutes", 0)
        )

        if new_out is not None:
            new_hours = compute_work_hours(new_in, new_out)
            new_overtime = compute_overtime(
                new_out, shift_end, date.isoformat(), shift_start
            )
            new_half_day = new_hours < 4.50
        else:
            new_hours = None
            new_overtime = 0
            new_half_day = False

    old_values = {
        "status": old_status,
        "punch_in": old_in,
        "punch_out": old_out,
        "late_minutes": record.get("late_minutes", 0),
        "work_hours": record.get("work_hours"),
        "overtime_minutes": record.get("overtime_minutes", 0),
        "half_day": record.get("half_day", False)
    }

    new_values = {
        "status": new_status,
        "punch_in": new_in,
        "punch_out": new_out,
        "late_minutes": new_late,
        "work_hours": new_hours,
        "overtime_minutes": new_overtime,
        "half_day": new_half_day
    }

    changes = {}

    for field, new_value in new_values.items():
        old_value = old_values[field]

        compare_old = old_value
        compare_new = new_value

        if isinstance(compare_old, datetime) and isinstance(compare_new, datetime):
            if compare_old.tzinfo is None:
                compare_old = compare_old.replace(tzinfo=timezone.utc)

            if compare_new.tzinfo is None:
                compare_new = compare_new.replace(tzinfo=timezone.utc)

            compare_old = compare_old.astimezone(timezone.utc).replace(microsecond=0)
            compare_new = compare_new.astimezone(timezone.utc).replace(microsecond=0)

        if compare_old != compare_new:
            if field in ("punch_in", "punch_out"):
                old_value = to_epoch_ms(old_value) if old_value is not None else None
                new_value = to_epoch_ms(new_value) if new_value is not None else None

            changes[field] = {
                "from": old_value,
                "to": new_value
            }

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="No attendance fields changed"
        )

    history_entry = {
        "at": datetime.now(timezone.utc),
        "by": payload.regularized_by,
        "reason": payload.reason,
        "changes": changes
    }

    previous_history = record.get("history", [])

    history_filter = (
        {"history": previous_history}
        if "history" in record
        else {"history": {"$exists": False}}
    )

    result = db.attendance_logs.update_one(
        {
            "_id": record["_id"],
            **history_filter,
            "status": old_status,
            "punch_in": old_in,
            "punch_out": old_out
        },
        {
            "$set": new_values,
            "$push": {"history": history_entry}
        }
    )

    if result.modified_count != 1:
        raise HTTPException(
            status_code=409,
            detail="Attendance changed concurrently. Retry the request."
        )

    updated = db.attendance_logs.find_one(
        {"_id": record["_id"]},
        {"_id": 0}
    )

    for field in ("punch_in", "punch_out"):
        if isinstance(updated.get(field), datetime):
            updated[field] = to_epoch_ms(updated[field])

    for entry in updated.get("history", []):
        if isinstance(entry.get("at"), datetime):
            entry["at"] = to_epoch_ms(entry["at"])

    return updated

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: Literal[
        "attendance_list",
        "employee_monthly",
        "department_summary",
        "late_leaderboard",
        "department_trend",
    ],
    emp_code: Optional[str] = None,
    month: Optional[str] = Query(
        default=None,
        pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
    ),
    department: Optional[str] = None,
    limit: int = Query(default=10, ge=1, le=50),
    date_from: Optional[date] = None,
    date_to: Optional[date] = None,
    status: Optional[str] = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    from_date: Optional[date] = Query(default=None, alias="from"),
    to_date: Optional[date] = Query(default=None, alias="to"),
):
    def explain_find(collection, query, sort, skip=0, limit_value=20):
        command = {
            "find": collection.name,
            "filter": query,
            "sort": dict(sort),
            "skip": skip,
            "limit": limit_value,
        }
        return db.command(
            "explain",
            command,
            verbosity="executionStats",
        )

    def explain_aggregate(collection, pipeline):
        return db.command(
            "explain",
            {
                "aggregate": collection.name,
                "pipeline": pipeline,
                "cursor": {},
            },
            verbosity="executionStats",
        )

    try:
        if endpoint == "attendance_list":
            if date_from and date_to and date_from > date_to:
                raise HTTPException(
                    status_code=422,
                    detail="date_from must be on or before date_to",
                )

            query = {}

            if emp_code is not None:
                query["emp_code"] = emp_code

            if status is not None:
                query["status"] = status

            if date_from is not None or date_to is not None:
                query["date"] = {}
                if date_from is not None:
                    query["date"]["$gte"] = date_from.isoformat()
                if date_to is not None:
                    query["date"]["$lte"] = date_to.isoformat()

            result = explain_find(
                db.attendance_logs,
                query,
                [("date", -1), ("emp_code", 1)],
                (page - 1) * page_size,
                page_size,
            )
            collection_name = "attendance_logs"

        elif endpoint == "employee_monthly":
            if not emp_code or not month:
                raise HTTPException(
                    status_code=422,
                    detail="emp_code and month are required",
                )

            year, month_number = map(int, month.split("-"))
            month_start = date(year, month_number, 1)
            next_month = (
                date(year + 1, 1, 1)
                if month_number == 12
                else date(year, month_number + 1, 1)
            )

            pipeline = [
                {
                    "$match": {
                        "emp_code": emp_code,
                        "date": {
                            "$gte": month_start.isoformat(),
                            "$lt": next_month.isoformat(),
                        },
                    }
                },
                {"$sort": {"date": 1}},
                {
                    "$group": {
                        "_id": "$emp_code",
                        "records": {"$sum": 1},
                    }
                },
            ]
            result = explain_aggregate(db.attendance_logs, pipeline)
            collection_name = "attendance_logs"

        elif endpoint == "department_summary":
            if not month:
                raise HTTPException(
                    status_code=422,
                    detail="month is required",
                )

            year, month_number = map(int, month.split("-"))
            month_start = date(year, month_number, 1)
            next_month = (
                date(year + 1, 1, 1)
                if month_number == 12
                else date(year, month_number + 1, 1)
            )
            month_end = next_month - timedelta(days=1)

            match = {"joined_on": {"$lte": month_end.isoformat()}}
            if department is not None:
                match["department"] = department

            pipeline = [
                {"$match": match},
                {"$sort": {"department": 1}},
                {
                    "$lookup": {
                        "from": "attendance_logs",
                        "let": {"code": "$emp_code"},
                        "pipeline": [
                            {
                                "$match": {
                                    "$expr": {
                                        "$and": [
                                            {"$eq": ["$emp_code", "$$code"]},
                                            {
                                                "$gte": [
                                                    "$date",
                                                    month_start.isoformat(),
                                                ]
                                            },
                                            {
                                                "$lt": [
                                                    "$date",
                                                    next_month.isoformat(),
                                                ]
                                            },
                                        ]
                                    }
                                }
                            }
                        ],
                        "as": "attendance",
                    }
                },
            ]
            result = explain_aggregate(db.employees, pipeline)
            collection_name = "employees"

        elif endpoint == "late_leaderboard":
            if not month:
                raise HTTPException(
                    status_code=422,
                    detail="month is required",
                )

            year, month_number = map(int, month.split("-"))
            month_start = date(year, month_number, 1)
            next_month = (
                date(year + 1, 1, 1)
                if month_number == 12
                else date(year, month_number + 1, 1)
            )

            pipeline = [
                {
                    "$match": {
                        "date": {
                            "$gte": month_start.isoformat(),
                            "$lt": next_month.isoformat(),
                        },
                        "late_minutes": {"$gt": 0},
                    }
                },
                {
                    "$group": {
                        "_id": "$emp_code",
                        "late_count": {"$sum": 1},
                        "total_late_minutes": {"$sum": "$late_minutes"},
                    }
                },
                {
                    "$lookup": {
                        "from": "employees",
                        "localField": "_id",
                        "foreignField": "emp_code",
                        "as": "employee",
                    }
                },
                {"$unwind": "$employee"},
            ]

            if department is not None:
                pipeline.append(
                    {"$match": {"employee.department": department}}
                )

            pipeline.extend([
                {
                    "$setWindowFields": {
                        "sortBy": {"total_late_minutes": -1},
                        "output": {"rank": {"$rank": {}}},
                    }
                },
                {"$match": {"rank": {"$lte": limit}}},
                {
                    "$sort": {
                        "total_late_minutes": -1,
                        "_id": 1,
                    }
                },
            ])

            result = explain_aggregate(db.attendance_logs, pipeline)
            collection_name = "attendance_logs"

        else:  # department_trend
            if not department or not from_date or not to_date:
                raise HTTPException(
                    status_code=422,
                    detail="department, from and to are required",
                )

            if from_date > to_date or (to_date - from_date).days > 91:
                raise HTTPException(
                    status_code=422,
                    detail="Invalid date range; maximum is 92 days",
                )

            pipeline = [
                {
                    "$match": {
                        "date": {
                            "$gte": from_date.isoformat(),
                            "$lte": to_date.isoformat(),
                        }
                    }
                },
                {"$sort": {"date": 1, "emp_code": 1}},
                {
                    "$lookup": {
                        "from": "employees",
                        "localField": "emp_code",
                        "foreignField": "emp_code",
                        "as": "employee",
                    }
                },
                {"$unwind": "$employee"},
                {"$match": {"employee.department": department}},
                {
                    "$group": {
                        "_id": "$date",
                        "present_count": {
                            "$sum": {
                                "$cond": [
                                    {
                                        "$in": [
                                            "$status",
                                            ["PRESENT", "WFH", "ON_DUTY"],
                                        ]
                                    },
                                    {
                                        "$cond": [
                                            {"$eq": ["$half_day", True]},
                                            0.5,
                                            1,
                                        ]
                                    },
                                    0,
                                ]
                            }
                        },
                        "late_count": {
                            "$sum": {
                                "$cond": [
                                    {"$gt": ["$late_minutes", 0]},
                                    1,
                                    0,
                                ]
                            }
                        },
                    }
                },
            ]
            result = explain_aggregate(db.attendance_logs, pipeline)
            collection_name = "attendance_logs"

        return {
            "endpoint": endpoint,
            "collection": collection_name,
            "explain": result,
        }

    except HTTPException:
        raise
    except PyMongoError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"MongoDB explain failed: {str(exc)}",
        )


# --------------------------------------------------------------------------- #
# TODO - the rest of the contract (see openapi.yaml):
#   POST  /attendance/punch-out
#   PATCH /attendance/{emp_code}/{date}
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
