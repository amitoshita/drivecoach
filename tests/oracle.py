"""Independent reference implementation, written directly from the assignment text.

It deliberately shares NO code with src/drivecoach: different parser (csv module),
a different event algorithm, its own rules evaluation and state machine. Tests compare
the production code against it, so a bug has to be made twice, in two different
ways, to go unnoticed.
"""
import csv
import io

KMH_PER = {"kmh": 1.0, "mph": 1.609344}


def read_lesson(raw: bytes):
    text = raw.decode("utf-8")
    header, table = {}, []
    for line in text.splitlines():
        if line.startswith("#"):
            k, v = line[1:].split(":", 1)
            header[k.strip()] = v.strip()
        else:
            table.append(line)
    rows = list(csv.DictReader(io.StringIO("\n".join(table))))
    return header, rows


def count_events(flags, min_len=15, gap=50):
    """Spec: a run of >= min_len qualifying samples is an event; two runs separated
    by fewer than `gap` samples count as one. Implemented as: number of qualifying
    runs minus the number of 'close' joins between consecutive qualifying runs."""
    runs, i, n = [], 0, len(flags)
    while i < n:
        if not flags[i]:
            i += 1
            continue
        j = i
        while j < n and flags[j]:
            j += 1
        if j - i >= min_len:
            runs.append((i, j))
        i = j
    joins = sum(1 for (_, end), (start, _) in zip(runs, runs[1:]) if start - end < gap)
    return len(runs) - joins


def metrics(header, rows):
    hz = float(header["sample_rate_hz"])
    f = KMH_PER[header["speed_unit"]]
    speed = [float(r["speed"]) * f for r in rows]
    limit = [float(r["speed_limit_kmh"]) for r in rows]
    a_long = [float(r["accel_long_ms2"]) for r in rows]
    a_lat = [float(r["accel_lat_ms2"]) for r in rows]
    n = len(rows)
    moving = [s for s in speed if s > 2]
    m = {
        "duration_min": n / hz / 60,
        "distance_km": sum((s / 3.6) / hz for s in speed) / 1000,
        "avg_moving_speed_kmh": sum(moving) / len(moving) if moving else 0.0,
        "pct_time_over_limit": 100 * sum(1 for s, l in zip(speed, limit) if s > l + 5) / n,
        "harsh_brake_count": count_events([a <= -3.5 for a in a_long]),
        "harsh_accel_count": count_events([a >= 3.0 for a in a_long]),
        "sharp_turn_count": count_events([abs(a) >= 4.0 for a in a_lat]),
        "max_lateral_g": max(abs(a) for a in a_lat) / 9.81,
    }
    score = (100 - 2 * m["pct_time_over_limit"] - 5 * m["harsh_brake_count"]
             - 3 * m["harsh_accel_count"] - 4 * m["sharp_turn_count"])
    m["lesson_score"] = min(100, max(0, score))
    return m


def decide(m, rules):
    ops = {">": lambda a, b: a > b, ">=": lambda a, b: a >= b,
           "<": lambda a, b: a < b, "<=": lambda a, b: a <= b}
    if m["duration_min"] < rules["min_duration_min"]:
        return "INVALID"
    if any(ops[c["op"]](m[c["metric"]], c["value"]) for c in rules["fail_if_any"]):
        return "FAIL"
    if any(ops[c["op"]](m[c["metric"]], c["value"]) for c in rules["review_if_any"]):
        return "REVIEW"
    return "PASS"


def run_student(row, lessons, rules):
    """lessons: list of (started_at, lesson_id, lesson_type, metrics). Returns (state, results)."""
    s = dict(program=row["program"], done=int(row["lessons_completed"]),
             reviews=int(row["review_count"]), next=row["next_lesson_type"],
             status=row["status"], final=None)
    passed_scores, results = [], {}
    for started, lid, ltype, m in sorted(lessons):
        res = decide(m, rules)
        results[lid] = res
        if res == "INVALID" or s["status"] != "ACTIVE":
            continue
        prog = rules["programs"][s["program"]]
        if res == "REVIEW":
            s["reviews"] += 1
            s["next"] = "PRACTICE"
            if s["reviews"] > prog["max_reviews"]:
                res = "FAIL"
                results[lid] = "FAIL"
        if res == "PASS":
            s["next"] = "REGULAR"
            if ltype == "REGULAR":
                s["done"] += 1
                passed_scores.append(m["lesson_score"])
                if s["done"] >= prog["lessons"]:
                    s["status"] = "COMPLETED"
                    s["final"] = round(sum(passed_scores) / len(passed_scores), 2)
        elif res == "FAIL":
            s["next"] = "REGULAR"
            if s["program"] == "STANDARD":
                s["program"], s["reviews"] = "EXTENDED", 0
            else:
                s["status"] = "INSTRUCTOR_REVIEW"
    return s, results
