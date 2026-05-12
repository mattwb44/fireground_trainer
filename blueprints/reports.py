from flask import Blueprint, Response, abort, render_template, request

from authz import PERM_EXPORT_REPORTS, PERM_VIEW_REPORTS, requires_permission
from helpers import build_reports_index_view_model, build_session_report_csv, build_session_report_view_model, make_report_filename
from models import TrainingSession

reports_bp = Blueprint("reports", __name__)


@reports_bp.get("/reports")
@requires_permission(PERM_VIEW_REPORTS)
def reports():
    return render_template(
        "reports.html",
        report_sessions=build_reports_index_view_model(),
    )


@reports_bp.get("/reports/sessions/<int:session_id>")
@requires_permission(PERM_VIEW_REPORTS)
def session_report(session_id: int):
    training_session = TrainingSession.query.filter_by(id=session_id).first()
    if training_session is None:
        abort(404)
    return render_template(
        "session_report.html",
        report=build_session_report_view_model(training_session),
    )


@reports_bp.get("/reports/sessions/<int:session_id>/export.csv")
@requires_permission(PERM_EXPORT_REPORTS)
def export_session_report_csv(session_id: int):
    training_session = TrainingSession.query.filter_by(id=session_id).first()
    if training_session is None:
        abort(404)

    shift_label = request.args.get("shift", "").strip() or None
    if shift_label is not None:
        available_shift_labels = {
            participant.shift_label or "Unspecified"
            for participant in training_session.participants
        }
        if shift_label not in available_shift_labels:
            abort(404)

    csv_content = build_session_report_csv(training_session, shift_label=shift_label)
    filename = make_report_filename(training_session, shift_label=shift_label)
    return Response(
        csv_content,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
