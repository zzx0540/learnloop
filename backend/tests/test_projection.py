from datetime import date, timedelta

from app.services.mastery import aggregate_course_date, simulate_mastery_dates


def test_fsrs_projection_is_monotonic() -> None:
    learn_date = date(2026, 7, 20)
    basic, full = simulate_mastery_dates(learn_date)
    later_basic, later_full = simulate_mastery_dates(learn_date + timedelta(days=7))

    assert learn_date < basic < full
    assert later_basic > basic
    assert later_full > full


def test_course_projection_threshold_and_important_kps() -> None:
    base = date(2026, 8, 1)
    projections = [(base + timedelta(days=i), 3) for i in range(8)]
    projections.append((base + timedelta(days=20), 5))
    projections.append((None, 1))

    assert aggregate_course_date(projections, 0.90) == base + timedelta(days=20)
    assert aggregate_course_date(projections, 0.95) is None
    projections[-1] = (base + timedelta(days=30), 1)
    assert aggregate_course_date(projections, 0.95) == base + timedelta(days=30)
