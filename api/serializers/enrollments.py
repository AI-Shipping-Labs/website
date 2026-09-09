"""Enrollment serializers shared by collection APIs and CRM export."""


def serialize_sprint_enrollment(enrollment):
    """Serialize a sprint enrollment without its parent sprint slug."""
    return {
        "user_email": enrollment.user.email,
        "enrolled_at": (
            enrollment.enrolled_at.isoformat() if enrollment.enrolled_at else None
        ),
        "enrolled_by": (
            enrollment.enrolled_by.email if enrollment.enrolled_by_id else None
        ),
    }


def serialize_course_enrollment(enrollment):
    """Serialize a course enrollment without its parent course slug."""
    return {
        "user_email": enrollment.user.email,
        "enrolled_at": (
            enrollment.enrolled_at.isoformat() if enrollment.enrolled_at else None
        ),
        "unenrolled_at": (
            enrollment.unenrolled_at.isoformat()
            if enrollment.unenrolled_at else None
        ),
        "source": enrollment.source,
    }
