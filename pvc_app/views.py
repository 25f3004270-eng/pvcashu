# pvc_app/views.py

from flask import Blueprint, render_template
from flask_login import login_required, current_user

from .models import Item, TenderMaster, PVCResult

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
@login_required
def index():
    items = Item.query.order_by(Item.name.asc()).all()
    tenders = TenderMaster.query.order_by(TenderMaster.tender_no.asc()).all()
    return render_template("index.html", items=items, tenders=tenders)  # [file:15]


@main_bp.route("/history")
@login_required
def history():
    records = (
        PVCResult.query
        .filter_by(user_id=current_user.id)
        .order_by(PVCResult.created_at.desc())
        .all()
    )
    return render_template("history.html", records=records)  # [file:11]